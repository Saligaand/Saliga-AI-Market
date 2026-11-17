from __future__ import annotations

import os
import json
import time
import sqlite3
import logging
from datetime import datetime, timezone
from typing import Any, Optional

import ccxt
import numpy as np
import pandas as pd
import requests
import streamlit as st
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import feedparser

try:
    from streamlit_autorefresh import st_autorefresh
except Exception:
    st_autorefresh = None

# ============================================================
# SALIGA AI — MARKET INTELLIGENCE PRO (v3.0.3 with Performance Tracker)
# ============================================================

APP_VERSION = "3.0.3"
DB_PATH = "saliga_journal.sqlite3"
SYMBOLS = ["BTC/USDT", "ETH/USDT", "SOL/USDT"]
TIMEFRAMES = ["1m", "5m", "15m", "1h", "4h", "1d"]
TF_WEIGHTS = {"4h": 0.30, "1h": 0.30, "15m": 0.20, "5m": 0.12, "1m": 0.08}
EXCHANGE_ORDER = ["OKX", "Bybit"]

st.set_page_config(page_title="SALIGA AI — Market Intelligence PRO", layout="wide")

# ----------------------------
# General utilities
# ----------------------------

def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def safe_float(value: Any) -> Optional[float]:
    try:
        if value is None:
            return None
        result = float(value)
        return result if np.isfinite(result) else None
    except (TypeError, ValueError):
        return None


def fmt(value: Any, digits: int = 3) -> str:
    v = safe_float(value)
    return "N/A" if v is None else f"{v:,.{digits}f}"


def db_connect():
    con = sqlite3.connect(DB_PATH, timeout=10)
    con.execute("""CREATE TABLE IF NOT EXISTS signal_journal (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        created_at TEXT NOT NULL,
        symbol TEXT NOT NULL,
        exchange_name TEXT NOT NULL,
        price REAL,
        signal TEXT NOT NULL,
        model_score REAL,
        data_coverage REAL,
        timeframe TEXT NOT NULL,
        notes TEXT DEFAULT ''
    )""")
    con.commit()
    return con


def save_signal(row: dict, notes: str = ""):
    con = db_connect()
    con.execute(
        "INSERT INTO signal_journal(created_at,symbol,exchange_name,price,signal,model_score,data_coverage,timeframe,notes) VALUES(?,?,?,?,?,?,?,?,?)",
        (utc_now().isoformat(), row["symbol"], row["exchange"], row["price"], row["signal"], row["score"], row["coverage"], "MTF 1m–1d", notes),
    )
    con.commit()
    con.close()


def read_journal(limit: int = 100) -> pd.DataFrame:
    con = db_connect()
    df = pd.read_sql_query(
        "SELECT id, created_at, symbol, exchange_name, price, signal, model_score, data_coverage, timeframe, notes FROM signal_journal ORDER BY id DESC LIMIT ?",
        con, params=(limit,)
    )
    con.close()
    return df

# ----------------------------
# Exchange connection / symbol resolution
# ----------------------------

@st.cache_resource(show_spinner=False)
def exchange_client(name: str):
    common = {"enableRateLimit": True, "timeout": 15000}
    if name == "OKX":
        return ccxt.okx({**common, "options": {"defaultType": "swap"}})
    if name == "Bybit":
        return ccxt.bybit({**common, "options": {"defaultType": "swap"}})
    raise ValueError(f"Ismeretlen tőzsde: {name}")


def resolve_symbol(ex, name: str, symbol: str) -> str:
    markets = ex.load_markets()
    base = symbol.split("/")[0]
    candidates = [
        m for m in markets.values()
        if m.get("base") == base and m.get("quote") == "USDT" and m.get("swap") and m.get("active", True)
    ]
    if candidates:
        candidates.sort(key=lambda m: (not bool(m.get("linear")), not bool(m.get("settle") == "USDT")))
        return candidates[0]["symbol"]
    raise ValueError(f"{name}: nem található aktív USDT perpetual piac ehhez: {symbol}")


@st.cache_data(ttl=15, show_spinner=False)
def fetch_ohlcv_cached(exchange_name: str, symbol: str, timeframe: str, limit: int = 500) -> pd.DataFrame:
    ex = exchange_client(exchange_name)
    unified_symbol = resolve_symbol(ex, exchange_name, symbol)
    rows = ex.fetch_ohlcv(unified_symbol, timeframe=timeframe, limit=limit)
    if not rows or len(rows) < 60:
        raise RuntimeError(f"Kevés {timeframe} gyertya érkezett ({len(rows) if rows else 0}).")
    df = pd.DataFrame(rows, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df["time"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    for c in ["open", "high", "low", "close", "volume"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.dropna(subset=["open", "high", "low", "close", "volume"]).drop_duplicates("timestamp").sort_values("timestamp").reset_index(drop=True)
    if (df[["open", "high", "low", "close"]] <= 0).any().any():
        raise RuntimeError(f"Érvénytelen árfolyamadat ({timeframe}).")
    return df


def load_asset_market(symbol: str) -> dict:
    errors = []
    for venue in EXCHANGE_ORDER:
        try:
            ex = exchange_client(venue)
            unified_symbol = resolve_symbol(ex, venue, symbol)
            frames = {}
            for tf in TIMEFRAMES:
                frames[tf] = fetch_ohlcv_cached(venue, symbol, tf, 500 if tf != "1d" else 250)
            ticker = ex.fetch_ticker(unified_symbol)
            price = safe_float(ticker.get("last")) or safe_float(ticker.get("close"))
            if price is None or price <= 0:
                raise RuntimeError("Érvénytelen ticker ár")
            return {"ok": True, "exchange": venue, "client": ex, "market_symbol": unified_symbol, "frames": frames, "price": price, "error": None}
        except Exception as exc:
            errors.append(f"{venue}: {str(exc)[:240]}")
    return {"ok": False, "exchange": None, "frames": None, "price": None, "error": " | ".join(errors)}

# ----------------------------
# Indicators & Engine
# ----------------------------

def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    x = df.copy()
    close = x["close"]
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(14).mean()
    loss = (-delta.clip(upper=0)).rolling(14).mean()
    rs = gain / loss.replace(0, np.nan)
    x["rsi"] = 100 - (100 / (1 + rs))
    prev = close.shift(1)
    tr = pd.concat([(x.high - x.low), (x.high - prev).abs(), (x.low - prev).abs()], axis=1).max(axis=1)
    x["atr"] = tr.rolling(14).mean()
    x["sma20"] = close.rolling(20).mean()
    x["sma50"] = close.rolling(50).mean()
    x["vol_sma20"] = x.volume.rolling(20).mean()
    x["volume_ratio"] = x.volume / x.vol_sma20.replace(0, np.nan)
    x["estimated_delta"] = np.where(close >= x.open, x.volume, -x.volume)
    x["estimated_delta_20"] = x.estimated_delta.rolling(20).sum()
    return x

def swing_points(df: pd.DataFrame, left: int = 3, right: int = 3) -> tuple[list[tuple[int, float]], list[tuple[int, float]]]:
    highs, lows = [], []
    h = df.high.to_numpy()
    l = df.low.to_numpy()
    for i in range(left, len(df) - right):
        if h[i] >= np.max(h[i-left:i+right+1]) and np.sum(h[i] == h[i-left:i+right+1]) == 1:
            highs.append((i, float(h[i])))
        if l[i] <= np.min(l[i-left:i+right+1]) and np.sum(l[i] == l[i-left:i+right+1]) == 1:
            lows.append((i, float(l[i])))
    return highs, lows

def market_structure(df: pd.DataFrame) -> dict:
    x = df.iloc[:-1].copy() if len(df) > 2 else df.copy()
    if len(x) < 40:
        return {"bias": 0, "label": "Kevés adat", "bos": "N/A", "score": 0}
    highs, lows = swing_points(x.tail(180).reset_index(drop=True))
    sh = [v for _, v in highs[-4:]]
    sl = [v for _, v in lows[-4:]]
    hh = len(sh) >= 2 and sh[-1] > sh[-2]
    hl = len(sl) >= 2 and sl[-1] > sl[-2]
    lh = len(sh) >= 2 and sh[-1] < sh[-2]
    ll = len(sl) >= 2 and sl[-1] < sl[-2]
    last_close = float(x.close.iloc[-1])
    ref = x.tail(35).iloc[:-1]
    prev_high = float(ref.high.max())
    prev_low = float(ref.low.min())
    bos = "Nincs friss BOS"
    if last_close > prev_high: bos = "Bullish BOS"
    elif last_close < prev_low: bos = "Bearish BOS"
    score = (1 if hh else 0) + (1 if hl else 0) - (1 if lh else 0) - (1 if ll else 0)
    if "Bullish BOS" in bos: score += 1
    elif "Bearish BOS" in bos: score -= 1
    bias = 1 if score > 0 else -1 if score < 0 else 0
    label = "Bullish struktúra" if bias > 0 else "Bearish struktúra" if bias < 0 else "Semleges struktúra"
    return {"bias": bias, "label": label, "bos": bos, "score": score}

def liquidity_map(df: pd.DataFrame) -> dict:
    x = df.iloc[:-1].tail(150).copy()
    if len(x) < 20:
        return {"equal_high": None, "equal_low": None, "sweep": "N/A", "fvg": "N/A"}
    highs, lows = swing_points(x.reset_index(drop=True), 2, 2)
    tol = float(x.close.iloc[-1]) * 0.003
    equal_high, equal_low = None, None
    if len(highs) >= 2:
        for i in range(len(highs)-1):
            if abs(highs[i][1] - highs[-1][1]) <= tol:
                equal_high = highs[-1][1]
                break
    if len(lows) >= 2:
        for i in range(len(lows)-1):
            if abs(lows[i][1] - lows[-1][1]) <= tol:
                equal_low = lows[-1][1]
                break
    last = x.iloc[-1]
    prior_high = float(x.high.iloc[-21:-1].max())
    prior_low = float(x.low.iloc[-21:-1].min())
    sweep = "Nincs friss kisöprés"
    if last.high > prior_high and last.close < prior_high: sweep = "Felső likviditás kisöprés"
    elif last.low < prior_low and last.close > prior_low: sweep = "Alsó likviditás kisöprés"
    return {"equal_high": equal_high, "equal_low": equal_low, "sweep": sweep, "fvg": "N/A"}

def volume_profile_proxy(df: pd.DataFrame, bins: int = 24) -> dict:
    x = df.iloc[:-1].tail(250)
    if len(x) < 30: return {"poc": None, "vah": None, "val": None}
    typical = (x.high + x.low + x.close) / 3
    hist, edges = np.histogram(typical, bins=bins, weights=x.volume)
    idx = int(np.argmax(hist))
    return {"poc": float((edges[idx] + edges[idx+1]) / 2), "vah": float(edges[min(bins, idx+4)]), "val": float(edges[max(0, idx-4)])}

def fetch_oi_funding(ex, symbol: str) -> dict:
    out = {"oi_now": None, "oi_change_pct": None, "funding_pct": None, "oi_status": "Nincs adat"}
    try:
        oi = ex.fetch_open_interest(symbol)
        out["oi_now"] = safe_float(oi.get("openInterestAmount")) or safe_float(oi.get("openInterest"))
    except Exception: pass
    try:
        if ex.has.get("fetchOpenInterestHistory"):
            hist = ex.fetch_open_interest_history(symbol, timeframe="5m", limit=6)
            vals = [safe_float(item.get("openInterestAmount") or item.get("openInterest")) for item in hist or []]
            vals = [v for v in vals if v is not None]
            if len(vals) >= 2 and vals[0] != 0:
                out["oi_change_pct"] = (vals[-1] / vals[0] - 1) * 100
                out["oi_status"] = "Növekvő OI" if out["oi_change_pct"] > 0.10 else "Csökkenő OI"
    except Exception: pass
    try:
        fr = ex.fetch_funding_rate(symbol)
        rate = safe_float(fr.get("fundingRate"))
        if rate is not None: out["funding_pct"] = rate * 100
    except Exception: pass
    return out

def fetch_taker_flow(ex, symbol: str) -> dict:
    out = {"buy_quote": None, "sell_quote": None, "delta_quote": None, "delta_pct": None, "trades": 0, "status": "Nincs adat"}
    try:
        trades = ex.fetch_trades(symbol, limit=1000)
        buy = sum(safe_float(t.get("cost")) or (safe_float(t.get("amount")) * safe_float(t.get("price"))) for t in trades if t.get("side") == "buy")
        sell = sum(safe_float(t.get("cost")) or (safe_float(t.get("amount")) * safe_float(t.get("price"))) for t in trades if t.get("side") == "sell")
        total = buy + sell
        if total > 0:
            delta = buy - sell
            out = {"buy_quote": buy, "sell_quote": sell, "delta_quote": delta, "delta_pct": delta / total * 100, "trades": len(trades), "status": "OK"}
    except Exception: pass
    return out

def fetch_orderbook_imbalance(ex, symbol: str) -> dict:
    try:
        ob = ex.fetch_order_book(symbol, limit=25)
        bids = sum(float(p) * float(a) for p, a in ob.get("bids", [])[:20])
        asks = sum(float(p) * float(a) for p, a in ob.get("asks", [])[:20])
        total = bids + asks
        if total <= 0: return {"imbalance_pct": None, "label": "Kiegyensúlyozott"}
        pct = (bids - asks) / total * 100
        return {"imbalance_pct": pct, "label": "Bid fölény" if pct > 8 else "Ask fölény" if pct < -8 else "Kiegyensúlyozott"}
    except Exception:
        return {"imbalance_pct": None, "label": "API limit"}

def classify_position_flow(oi_change: Optional[float], price_change_pct: Optional[float], delta_pct: Optional[float]) -> dict:
    if oi_change is None or price_change_pct is None:
        return {"label": "OI / ár adat hiányzik", "score": None, "note": "Hiányzó adatok."}
    oi_up, oi_down = oi_change > 0.10, oi_change < -0.10
    px_up, px_down = price_change_pct > 0.03, price_change_pct < -0.03
    if oi_up and px_up: return {"label": "LONG épülés gyanúja", "score": 0.55, "note": "Ár és OI emelkedik."}
    if oi_up and px_down: return {"label": "SHORT épülés gyanúja", "score": -0.55, "note": "Ár esik, OI nő."}
    return {"label": "Semleges flow", "score": 0.0, "note": "Nincs extrém mozgás."}

@st.cache_data(ttl=300, show_spinner=False)
def yahoo_change(ticker: str) -> Optional[float]:
    try:
        r = requests.get(f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}", params={"interval": "1h", "range": "2d"}, headers={"User-Agent": "Mozilla/5.0"}, timeout=6)
        vals = [float(v) for v in r.json()["chart"]["result"][0]["indicators"]["quote"][0]["close"] if v is not None]
        return (vals[-1] / vals[-2] - 1) * 100 if len(vals) >= 2 else None
    except Exception: return None

@st.cache_data(ttl=300, show_spinner=False)
def get_macro_data() -> dict:
    return {"DXY": yahoo_change("DX-Y.NYB"), "VIX": yahoo_change("^VIX"), "US10Y": yahoo_change("^TNX"), "NASDAQ": yahoo_change("^IXIC"), "SP500": yahoo_change("^GSPC")}

@st.cache_data(ttl=300, show_spinner=False)
def get_crypto_news() -> list[str]:
    try:
        parsed = feedparser.parse("https://cointelegraph.com/rss")
        return [e.title for e in parsed.entries[:10] if hasattr(e, "title")]
    except Exception: return []

def keyword_news_score(news: list[str], symbol: str) -> tuple[float, str]:
    if not news: return 0.0, "Nincs hír."
    return 0.0, f"Elemezve {len(news)} hír."

def groq_explanation(api_key: str, symbol: str, signal: str, score: float, components: dict, macro: dict, news: list[str]) -> Optional[str]:
    if not api_key: return None
    prompt = f"Magyarul, röviden foglald össze a piacot.\nPiac: {symbol}\nSzignál: {signal}\nPontszám: {score:.3f}\nKomponensek: {json.dumps(components, ensure_ascii=False)}"
    try:
        r = requests.post("https://api.groq.com/openai/v1/chat/completions", headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}, json={"model": "openai/gpt-oss-120b", "messages": [{"role": "user", "content": prompt}], "temperature": 0.1}, timeout=12)
        return r.json()["choices"][0]["message"]["content"].strip()
    except Exception: return None

def timeframe_analysis(frames: dict) -> tuple[dict, dict]:
    analyzed = {tf: add_indicators(df) for tf, df in frames.items()}
    structures = {tf: market_structure(analyzed[tf]) for tf in ["4h", "1h", "15m", "5m"]}
    scores = {}
    details = {}
    for tf, weight in TF_WEIGHTS.items():
        df = analyzed[tf]
        row = df.iloc[-2] if len(df) > 2 else df.iloc[-1]
        struct = structures.get(tf, market_structure(df))
        s = float(struct["bias"])
        scores[tf] = float(np.clip(s, -1, 1))
        details[tf] = {"score": scores[tf], "rsi": safe_float(row.rsi), "structure": struct}
    mtf = sum(scores[tf] * TF_WEIGHTS[tf] for tf in TF_WEIGHTS)
    return analyzed, {"scores": scores, "details": details, "mtf": mtf, "structures": structures}

def macro_score(macro: dict) -> float:
    dxy = macro.get("DXY")
    return -np.sign(dxy) * min(abs(dxy) / 0.15, 1) if dxy is not None else 0.0

def make_decision(mtf: float, oi: dict, flow: dict, orderbook: dict, liquidity: dict, macro: dict, analyzed: dict) -> dict:
    last_5m = analyzed["5m"].iloc[-2]
    prev_5m = analyzed["5m"].iloc[-3]
    price_change = (float(last_5m.close) / float(prev_5m.close) - 1) * 100 if prev_5m.close else 0.0
    position = classify_position_flow(oi.get("oi_change_pct"), price_change, flow.get("delta_pct"))
    
    components = {"MTF struktúra": mtf, "Position Flow": position.get("score"), "Makró": macro_score(macro)}
    weighted = sum(v for v in components.values() if v is not None)
    score = weighted / len(components)
    
    if score >= 0.4: signal = "ERŐS LONG"
    elif score >= 0.15: signal = "LONG"
    elif score <= -0.4: signal = "ERŐS SHORT"
    elif score <= -0.15: signal = "SHORT"
    else: signal = "KIVÁRÁS"
    return {"signal": signal, "score": score, "strength": abs(score) * 100, "coverage": 90.0, "components": components, "position": position}

def risk_plan(price: float, atr: Optional[float], signal: str, balance: float, risk_pct: float) -> dict:
    if atr is None or atr <= 0 or "LONG" not in signal and "SHORT" not in signal: return {"valid": False}
    is_long = "LONG" in signal
    sl_dist = 1.5 * atr
    tp_dist = 3.0 * atr
    sl = price - sl_dist if is_long else price + sl_dist
    tp = price + tp_dist if is_long else price - tp_dist
    risk_cash = balance * risk_pct / 100
    qty = risk_cash / sl_dist if sl_dist > 0 else 0
    return {"valid": True, "sl": sl, "tp": tp, "risk_cash": risk_cash, "quantity": qty, "notional": qty * price, "rr": tp_dist / sl_dist}

# ----------------------------
# UI
# ----------------------------

with st.sidebar:
    st.header("⚙️ SALIGA AI beállítások")
    refresh_choice = st.selectbox("Automatikus frissítés", ["Kézi", "30 mp", "60 mp"], index=1)
    account_balance = st.number_input("Számlaegyenleg (USDT)", min_value=10.0, value=1000.0, step=100.0)
    risk_pct = st.slider("Kockázat / kötés (%)", 0.25, 3.0, 1.0, 0.25)
    groq_key = st.text_input("Groq API-kulcs (opcionális)", type="password")
    show_chart = st.checkbox("Grafikon", True)

if refresh_choice != "Kézi" and st_autorefresh is not None:
    st_autorefresh(interval={"30 mp": 30000, "60 mp": 60000}[refresh_choice], key="saliga_refresh")

st.title("⚡ SALIGA AI — MARKET INTELLIGENCE PRO")
st.caption(f"v{APP_VERSION} • Multi-asset Quant Engine + Performance Tracker")

with st.spinner("Elemzés futtatása..."):
    macro_data = get_macro_data()
    news = get_crypto_news()
    market_results = {}
    for symbol in SYMBOLS:
        market = load_asset_market(symbol)
        if not market["ok"]:
            market_results[symbol] = {"ok": False, "error": market["error"]}
            continue
        try:
            ex, sym = market["client"], market["market_symbol"]
            analyzed, mtf = timeframe_analysis(market["frames"])
            oi, flow, ob = fetch_oi_funding(ex, sym), fetch_taker_flow(ex, sym), fetch_orderbook_imbalance(ex, sym)
            liq, vp = liquidity_map(analyzed["1h"]), volume_profile_proxy(analyzed["1h"])
            decision = make_decision(mtf["mtf"], oi, flow, ob, liq, macro_data, analyzed)
            market_results[symbol] = {"ok": True, "symbol": symbol, "exchange": market["exchange"], "price": market["price"], "frames": analyzed, "mtf": mtf, "oi": oi, "flow": flow, "decision": decision, "liquidity": liq, "volume_profile": vp}
        except Exception as exc:
            market_results[symbol] = {"ok": False, "error": str(exc)[:200]}

# Sávos szignálok
st.subheader("🎯 Multi-asset végső szignálok")
cols = st.columns(3)
for col, symbol in zip(cols, SYMBOLS):
    d = market_results[symbol]
    with col:
        if not d["ok"]:
            st.error(f"{symbol} Hiba")
            continue
        dec = d["decision"]
        sig = dec["signal"]
        if "LONG" in sig: st.success(f"{symbol} · {sig}")
        elif "SHORT" in sig: st.error(f"{symbol} · {sig}")
        else: st.warning(f"{symbol} · {sig}")
        st.metric("Ár", f"{d['price']:,.2f} USDT")
        st.metric("Pontszám", f"{dec['score']:+.2f}")

st.markdown("---")
available = [s for s in SYMBOLS if market_results[s]["ok"]]
if available:
    selected = st.selectbox("Részletes elemzendő piac", available)
    d = market_results[selected]
    dec = d["decision"]
    
    st.header(f"📊 {selected} — részletes adatok")
    m1, m2, m3 = st.columns(3)
    m1.metric("Végső szignál", dec["signal"])
    m2.metric("Modellpontszám", f"{dec['score']:+.3f}")
    m3.metric("OI Változás", f"{d['oi']['oi_change_pct']:+.2f}%" if d['oi']['oi_change_pct'] is not None else "N/A")

    note = st.text_input("Megjegyzés a naplóbejegyzéshez", key="journal_note")
    if st.button("💾 Jelzés mentése a naplóba", type="primary"):
        save_signal({"symbol": selected, "exchange": d["exchange"], "price": d["price"], "signal": dec["signal"], "score": dec["score"], "coverage": dec["coverage"]}, note)
        st.success("Sikeresen elmentve!")

    if show_chart:
        chart = d["frames"]["1h"].tail(180)
        fig = make_subplots(rows=2, cols=1, shared_xaxes=True, row_heights=[0.75, 0.25])
        fig.add_trace(go.Candlestick(x=chart.time, open=chart.open, high=chart.high, low=chart.low, close=chart.close, name=selected), row=1, col=1)
        fig.add_trace(go.Bar(x=chart.time, y=chart.volume, name="Volume"), row=2, col=1)
        fig.update_layout(template="plotly_dark", height=500, xaxis_rangeslider_visible=False)
        st.plotly_chart(fig, use_container_width=True)

# ----------------------------
# 📈 ÚJ: Teljesítmény-követő (Performance Tracker)
# ----------------------------
st.markdown("---")
st.subheader("📈 Mentett jelek teljesítmény-kiértékelése")
journal_df = read_journal(50)

if journal_df.empty:
    st.caption("Még nincsenek mentett jelek a kiértékeléshez.")
else:
    # Élő árak lekérése a kiértékeléshez
    eval_rows = []
    for _, row in journal_df.iterrows():
        saved_price = row["price"]
        sym = row["symbol"]
        sig = row["signal"]
        
        # Megkeressük az aktuális árat a market_results-ból vagy lekérjük frissen
        curr_price = saved_price
        if sym in market_results and market_results[sym]["ok"]:
            curr_price = market_results[sym]["price"]
            
        # Változás számítása
        diff_pct = ((curr_price - saved_price) / saved_price) * 100
        
        # Sikeres-e? (Ha Long volt és nőtt, vagy Short volt és esett -> Nyert)
        is_success = False
        if "LONG" in sig and diff_pct > 0:
            is_success = True
        elif "SHORT" in sig and diff_pct < 0:
            is_success = True
            diff_pct = -diff_pct # Shortnál a pozitív elmozdulás a nyereség
            
        status_icon = "🟢 NYERŐ" if is_success else ("🔴 VESZTES" if diff_pct != 0 else "⚪ SEMLEGES")
        if "KIVÁRÁS" in sig:
            status_icon = "➖ KIVÁRÁS"

        eval_rows.append({
            "Idő": row["created_at"][:19],
            "Piac": sym,
            "Szignál": sig,
            "Mentett Ár": fmt(saved_price, 2),
            "Jelenlegi Ár": fmt(curr_price, 2),
            "Elmozdulás (%)": f"{diff_pct:+.2f}%",
            "Állapot": status_icon,
            "Megjegyzés": row["notes"]
        })
    
    st.dataframe(pd.DataFrame(eval_rows), use_container_width=True, hide_index=True)
    
    # Letöltés gomb
    csv = journal_df.to_csv(index=False).encode("utf-8-sig")
    st.download_button("Napló és statisztika letöltése CSV-ben", csv, file_name="saliga_performance_journal.csv", mime="text/csv")
