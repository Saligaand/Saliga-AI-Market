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
# SALIGA AI — MARKET INTELLIGENCE PRO
# No EMA. Exchange failover is per-asset and never mixes venues
# within a single asset analysis.
# ============================================================

APP_VERSION = "3.0.1"
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
        "SELECT created_at,symbol,exchange_name,price,signal,model_score,data_coverage,timeframe,notes FROM signal_journal ORDER BY id DESC LIMIT ?",
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


@st.cache_data(ttl=900, show_spinner=False)
def cached_market_symbols(name: str) -> list[str]:
    ex = exchange_client(name)
    markets = ex.load_markets()
    found = []
    for base in ["BTC", "ETH", "SOL"]:
        candidates = [
            m for m in markets.values()
            if m.get("base") == base and m.get("quote") == "USDT" and m.get("swap") and m.get("active", True)
        ]
        if candidates:
            candidates.sort(key=lambda m: (not bool(m.get("linear")), not bool(m.get("settle") == "USDT")))
            found.append(candidates[0]["symbol"])
        else:
            found.append("")
    return found


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
# Indicators (no EMA)
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

# ----------------------------
# Structure, liquidity and imbalance
# ----------------------------

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
        return {"bias": 0, "label": "Kevés adat", "bos": "N/A", "last_swing_high": None, "last_swing_low": None, "score": 0}
    highs, lows = swing_points(x.tail(180).reset_index(drop=True))
    sh = [v for _, v in highs[-4:]]
    sl = [v for _, v in lows[-4:]]
    hh = len(sh) >= 2 and sh[-1] > sh[-2]
    lh = len(sh) >= 2 and sh[-1] < sh[-2]
    hl = len(sl) >= 2 and sl[-1] > sl[-2]
    ll = len(sl) >= 2 and sl[-1] < sl[-2]
    last_close = float(x.close.iloc[-1])
    ref = x.tail(35).iloc[:-1]
    prev_high = float(ref.high.max())
    prev_low = float(ref.low.min())
    bos = "Nincs friss BOS"
    if last_close > prev_high:
        bos = "Bullish BOS"
    elif last_close < prev_low:
        bos = "Bearish BOS"
    score = (1 if hh else 0) + (1 if hl else 0) - (1 if lh else 0) - (1 if ll else 0)
    if "Bullish BOS" in bos:
        score += 1
    elif "Bearish BOS" in bos:
        score -= 1
    bias = 1 if score > 0 else -1 if score < 0 else 0
    label = "Bullish struktúra" if bias > 0 else "Bearish struktúra" if bias < 0 else "Semleges struktúra"
    return {"bias": bias, "label": label, "bos": bos, "hh": hh, "hl": hl, "lh": lh, "ll": ll,
            "last_swing_high": sh[-1] if sh else None, "last_swing_low": sl[-1] if sl else None, "score": score,
            "support_ref": prev_low, "resistance_ref": prev_high}


def liquidity_map(df: pd.DataFrame) -> dict:
    x = df.iloc[:-1].tail(150).copy()
    if len(x) < 20:
        return {"equal_high": None, "equal_low": None, "sweep": "N/A", "fvg": "N/A", "range_high": None, "range_low": None}
    highs, lows = swing_points(x.reset_index(drop=True), 2, 2)
    tol = float(x.close.iloc[-1]) * 0.0015
    equal_high = None
    equal_low = None
    if len(highs) >= 2:
        a, b = highs[-2][1], highs[-1][1]
        if abs(a-b) <= tol:
            equal_high = (a+b)/2
    if len(lows) >= 2:
        a, b = lows[-2][1], lows[-1][1]
        if abs(a-b) <= tol:
            equal_low = (a+b)/2
    last = x.iloc[-1]
    prior_high = float(x.high.iloc[-21:-1].max())
    prior_low = float(x.low.iloc[-21:-1].min())
    sweep = "Nincs friss kisöprés"
    if last.high > prior_high and last.close < prior_high:
        sweep = "Felső likviditás kisöprés – bearish visszazárás"
    elif last.low < prior_low and last.close > prior_low:
        sweep = "Alsó likviditás kisöprés – bullish visszazárás"
    fvg = "Nincs friss FVG"
    if len(x) >= 3:
        c1, c3 = x.iloc[-3], x.iloc[-1]
        if c3.low > c1.high:
            fvg = f"Bullish FVG: {c1.high:.6g}–{c3.low:.6g}"
        elif c3.high < c1.low:
            fvg = f"Bearish FVG: {c3.high:.6g}–{c1.low:.6g}"
    return {"equal_high": equal_high, "equal_low": equal_low, "sweep": sweep, "fvg": fvg,
            "range_high": float(x.high.max()), "range_low": float(x.low.min())}


def volume_profile_proxy(df: pd.DataFrame, bins: int = 24) -> dict:
    x = df.iloc[:-1].tail(250)
    if len(x) < 30:
        return {"poc": None, "vah": None, "val": None}
    typical = (x.high + x.low + x.close) / 3
    hist, edges = np.histogram(typical, bins=bins, weights=x.volume)
    idx = int(np.argmax(hist))
    return {"poc": float((edges[idx] + edges[idx+1]) / 2), "vah": float(edges[min(bins, idx+4)]), "val": float(edges[max(0, idx-4)])}

# ----------------------------
# Derivatives / trades / order book
# ----------------------------

def fetch_oi_funding(ex, symbol: str) -> dict:
    out = {"oi_now": None, "oi_change_pct": None, "funding_pct": None, "oi_status": "Nincs adat"}
    try:
        oi = ex.fetch_open_interest(symbol)
        out["oi_now"] = safe_float(oi.get("openInterestAmount")) or safe_float(oi.get("openInterestValue")) or safe_float(oi.get("openInterest"))
    except Exception:
        pass
    try:
        if ex.has.get("fetchOpenInterestHistory"):
            hist = ex.fetch_open_interest_history(symbol, timeframe="5m", limit=6)
            vals = []
            for item in hist or []:
                v = safe_float(item.get("openInterestAmount")) or safe_float(item.get("openInterestValue")) or safe_float(item.get("openInterest"))
                if v is not None:
                    vals.append(v)
            if len(vals) >= 2 and vals[0] != 0:
                out["oi_change_pct"] = (vals[-1] / vals[0] - 1) * 100
                out["oi_status"] = "Növekvő OI" if out["oi_change_pct"] > 0.10 else "Csökkenő OI" if out["oi_change_pct"] < -0.10 else "Stabil OI"
    except Exception:
        pass
    try:
        fr = ex.fetch_funding_rate(symbol)
        rate = safe_float(fr.get("fundingRate"))
        if rate is not None:
            out["funding_pct"] = rate * 100
    except Exception:
        pass
    return out


def fetch_taker_flow(ex, symbol: str) -> dict:
    out = {"buy_quote": None, "sell_quote": None, "delta_quote": None, "delta_pct": None, "trades": 0, "status": "Nincs adat"}
    try:
        trades = ex.fetch_trades(symbol, limit=1000)
        buy = sell = 0.0
        for trade in trades:
            side = trade.get("side")
            cost = safe_float(trade.get("cost"))
            if cost is None:
                amount, price = safe_float(trade.get("amount")), safe_float(trade.get("price"))
                cost = amount * price if amount is not None and price is not None else 0.0
            if side == "buy":
                buy += cost
            elif side == "sell":
                sell += cost
        total = buy + sell
        if total > 0:
            delta = buy - sell
            out = {"buy_quote": buy, "sell_quote": sell, "delta_quote": delta, "delta_pct": delta / total * 100, "trades": len(trades), "status": "Utolsó lekért kötések taker-flow becslése"}
    except Exception:
        pass
    return out


def fetch_orderbook_imbalance(ex, symbol: str) -> dict:
    try:
        ob = ex.fetch_order_book(symbol, limit=25)
        bids = sum(float(p) * float(a) for p, a in ob.get("bids", [])[:20])
        asks = sum(float(p) * float(a) for p, a in ob.get("asks", [])[:20])
        total = bids + asks
        if total <= 0:
            return {"imbalance_pct": None, "label": "Nincs adat"}
        pct = (bids - asks) / total * 100
        return {"imbalance_pct": pct, "label": "Bid fölény" if pct > 8 else "Ask fölény" if pct < -8 else "Kiegyensúlyozott"}
    except Exception:
        return {"imbalance_pct": None, "label": "Nincs adat"}


def classify_position_flow(oi_change: Optional[float], price_change_pct: Optional[float], delta_pct: Optional[float]) -> dict:
    if oi_change is None or price_change_pct is None:
        return {"label": "OI / ár adat hiányzik", "score": None, "note": "A pozícióépülés nem állapítható meg."}
    oi_up = oi_change > 0.10
    oi_down = oi_change < -0.10
    px_up = price_change_pct > 0.03
    px_down = price_change_pct < -0.03
    delta_pos = delta_pct is not None and delta_pct > 5
    delta_neg = delta_pct is not None and delta_pct < -5
    if oi_up and px_up:
        return {"label": "LONG épülés gyanúja", "score": 1 if delta_pos else 0.55, "note": "Ár és OI emelkedik; a taker delta megerősítheti vagy gyengítheti."}
    if oi_up and px_down:
        return {"label": "SHORT épülés gyanúja", "score": -1 if delta_neg else -0.55, "note": "Ár csökken és OI nő; új short kitettség lehetséges."}
    if oi_down and px_up:
        return {"label": "SHORT zárás / short squeeze gyanúja", "score": 0.45, "note": "Ár emelkedik, OI csökken; shortok záródhatnak."}
    if oi_down and px_down:
        return {"label": "LONG zárás gyanúja", "score": -0.45, "note": "Ár esik, OI csökken; longok záródhatnak."}
    return {"label": "Vegyes / semleges flow", "score": 0.0, "note": "Nincs egyértelmű ár–OI együttállás."}

# ----------------------------
# Macro and news context
# ----------------------------

@st.cache_data(ttl=300, show_spinner=False)
def yahoo_change(ticker: str, interval: str = "1h", range_: str = "5d") -> Optional[float]:
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
    try:
        r = requests.get(url, params={"interval": interval, "range": range_}, headers={"User-Agent": "Mozilla/5.0"}, timeout=8)
        r.raise_for_status()
        result = r.json()["chart"]["result"][0]
        values = result["indicators"]["quote"][0]["close"]
        vals = [float(v) for v in values if v is not None and np.isfinite(v)]
        if len(vals) < 2 or vals[-2] == 0:
            return None
        return (vals[-1] / vals[-2] - 1) * 100
    except Exception:
        return None


@st.cache_data(ttl=300, show_spinner=False)
def get_macro_data() -> dict:
    return {
        "DXY": yahoo_change("DX-Y.NYB", "1h", "5d"),
        "VIX": yahoo_change("^VIX", "1h", "5d"),
        "US10Y": yahoo_change("^TNX", "1h", "5d"),
        "NASDAQ": yahoo_change("^IXIC", "1h", "5d"),
        "SP500": yahoo_change("^GSPC", "1h", "5d"),
    }


@st.cache_data(ttl=300, show_spinner=False)
def get_crypto_news() -> list[str]:
    feeds = ["https://cointelegraph.com/rss", "https://www.coindesk.com/arc/outboundfeeds/rss/"]
    titles = []
    for url in feeds:
        try:
            parsed = feedparser.parse(url)
            for entry in parsed.entries[:12]:
                title = str(getattr(entry, "title", "")).strip()
                if title and title not in titles:
                    titles.append(title)
        except Exception:
            continue
    return titles[:20]


def keyword_news_score(news: list[str], symbol: str) -> tuple[Optional[float], str]:
    if not news:
        return None, "Nem érkezett hír."
    base = symbol.split("/")[0].lower()
    relevant = [n for n in news if base in n.lower() or any(k in n.lower() for k in ["bitcoin", "crypto", "cryptocurrency", "fed", "sec", "etf", "inflation"])]
    if not relevant:
        return 0.0, "Nincs egyértelműen releváns cím."
    positive = ["approval", "approved", "inflow", "adoption", "surge", "rally", "bullish", "record high", "cuts", "rate cut"]
    negative = ["hack", "exploit", "lawsuit", "ban", "outflow", "liquidation", "crash", "bearish", "investigation", "rejection"]
    score = 0
    for title in relevant:
        t = title.lower()
        score += sum(1 for k in positive if k in t)
        score -= sum(1 for k in negative if k in t)
    norm = float(np.clip(score / max(2, len(relevant) * 0.5), -1, 1))
    return norm, f"Kulcsszavas hírhangulat {len(relevant)} releváns címből; nem teljes hírszemantika."


def groq_explanation(api_key: str, symbol: str, signal: str, score: float, components: dict, macro: dict, news: list[str]) -> Optional[str]:
    if not api_key:
        return None
    prompt = f"""Magyarul, röviden foglald össze a már kiszámolt kriptopiaci elemzést. Ne adj új kereskedési jelet és ne találj ki adatot. Jelezd az ellentmondásokat és a bizonytalanságot.\nPiac: {symbol}\nModelljel: {signal}\nModellpontszám: {score:.3f} (NEM valószínűség)\nMotorok: {json.dumps(components, ensure_ascii=False)}\nMakró változások: {json.dumps(macro, ensure_ascii=False)}\nHírcímek: {json.dumps(news[:8], ensure_ascii=False)}"""
    try:
        r = requests.post("https://api.groq.com/openai/v1/chat/completions", headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}, json={"model": "openai/gpt-oss-120b", "messages": [{"role": "user", "content": prompt}], "temperature": 0.1}, timeout=18)
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"].strip()
    except Exception as exc:
        return f"AI magyarázat nem elérhető: {str(exc)[:180]}"

# ----------------------------
# Analysis and decision engine
# ----------------------------

def timeframe_analysis(frames: dict) -> tuple[dict, dict]:
    analyzed = {tf: add_indicators(df) for tf, df in frames.items()}
    structures = {tf: market_structure(analyzed[tf]) for tf in ["4h", "1h", "15m", "5m"]}
    scores = {}
    details = {}
    for tf, weight in TF_WEIGHTS.items():
        df = analyzed[tf]
        row = df.iloc[-2] if len(df) > 2 else df.iloc[-1]
        struct = structures.get(tf, market_structure(df))
        s = 0.0
        s += float(struct["bias"]) * 1.0
        rsi = safe_float(row.rsi)
        if rsi is not None:
            s += 0.5 if rsi >= 55 else -0.5 if rsi <= 45 else 0
        if safe_float(row.sma20) is not None and safe_float(row.sma50) is not None:
            s += 0.5 if row.sma20 > row.sma50 else -0.5 if row.sma20 < row.sma50 else 0
        if safe_float(row.volume_ratio) is not None and row.volume_ratio >= 1.5:
            s += 0.5 if row.close > row.open else -0.5 if row.close < row.open else 0
        scores[tf] = float(np.clip(s / 2.5, -1, 1))
        details[tf] = {"score": scores[tf], "rsi": rsi, "volume_ratio": safe_float(row.volume_ratio), "atr": safe_float(row.atr), "structure": struct}
    mtf = sum(scores[tf] * TF_WEIGHTS[tf] for tf in TF_WEIGHTS)
    return analyzed, {"scores": scores, "details": details, "mtf": mtf, "structures": structures}


def macro_score(macro: dict) -> Optional[float]:
    parts = []
    dxy = macro.get("DXY")
    vix = macro.get("VIX")
    nasdaq = macro.get("NASDAQ")
    if dxy is not None: parts.append(-np.sign(dxy) * min(abs(dxy) / 0.15, 1))
    if vix is not None: parts.append(-np.sign(vix) * min(abs(vix) / 1.5, 1))
    if nasdaq is not None: parts.append(np.sign(nasdaq) * min(abs(nasdaq) / 0.5, 1))
    return float(np.mean(parts)) if parts else None


def make_decision(mtf: float, oi: dict, flow: dict, orderbook: dict, funding: Optional[float], liquidity: dict,
                  macro: dict, news_score: Optional[float], analyzed: dict) -> dict:
    last_5m = analyzed["5m"].iloc[-2]
    prev_5m = analyzed["5m"].iloc[-3]
    price_change = (float(last_5m.close) / float(prev_5m.close) - 1) * 100 if prev_5m.close else None
    position = classify_position_flow(oi.get("oi_change_pct"), price_change, flow.get("delta_pct"))
    
    # ITT JAVÍTVA: last_1h helyett az analyzed["1h"] utolsó lezárt gyertyáját használjuk biztonságosan
    last_1h = analyzed["1h"].iloc[-2] if len(analyzed["1h"]) > 2 else analyzed["1h"].iloc[-1]
    rsi = safe_float(last_1h.rsi)
    
    momentum = None if rsi is None else float(np.clip((rsi - 50) / 20, -1, 1))
    cvd_score = None if flow.get("delta_pct") is None else float(np.clip(flow["delta_pct"] / 25, -1, 1))
    oi_score = position.get("score")
    ob_score = None if orderbook.get("imbalance_pct") is None else float(np.clip(orderbook["imbalance_pct"] / 25, -1, 1))
    funding_score = None
    if funding is not None:
        funding_score = -0.5 if funding > 0.05 else 0.5 if funding < -0.05 else 0.0
    sweep_text = liquidity.get("sweep", "")
    sweep_score = 0.6 if "Alsó" in sweep_text else -0.6 if "Felső" in sweep_text else None
    macro = macro_score(macro)
    components = {
        "MTF struktúra": mtf,
        "Position Flow": oi_score,
        "Taker CVD (rövid mintából)": cvd_score,
        "Orderbook": ob_score,
        "Momentum": momentum,
        "Funding": funding_score,
        "Likviditáskisöprés": sweep_score,
        "Makró": macro,
        "Hírek": news_score,
    }
    weights = {
        "MTF struktúra": 0.28,
        "Position Flow": 0.18,
        "Taker CVD (rövid mintából)": 0.14,
        "Orderbook": 0.08,
        "Momentum": 0.08,
        "Funding": 0.06,
        "Likviditáskisöprés": 0.06,
        "Makró": 0.07,
        "Hírek": 0.05,
    }
    weighted = 0.0
    available = 0.0
    for name, val in components.items():
        if val is not None and np.isfinite(val):
            weighted += float(val) * weights[name]
            available += weights[name]
    score = weighted / available if available else 0.0
    coverage = available * 100
    if coverage < 55 or oi.get("oi_change_pct") is None or flow.get("delta_pct") is None:
        signal = "KIVÁRÁS – HIÁNYOS ADAT"
    elif score >= 0.48:
        signal = "ERŐS LONG"
    elif score >= 0.18:
        signal = "LONG"
    elif score <= -0.48:
        signal = "ERŐS SHORT"
    elif score <= -0.18:
        signal = "SHORT"
    else:
        signal = "KIVÁRÁS"
    return {"signal": signal, "score": score, "strength": abs(score) * 100, "coverage": coverage,
            "components": components, "position": position, "price_change_1h": price_change}


def risk_plan(price: float, atr: Optional[float], signal: str, balance: float, risk_pct: float) -> dict:
    if atr is None or atr <= 0 or "LONG" not in signal and "SHORT" not in signal:
        return {"valid": False}
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
    refresh_choice = st.selectbox("Automatikus frissítés", ["Kézi", "30 mp", "60 mp", "5 perc"], index=1)
    account_balance = st.number_input("Számlaegyenleg (USDT)", min_value=10.0, value=1000.0, step=100.0)
    risk_pct = st.slider("Kockázat / kötés (%)", 0.25, 3.0, 1.0, 0.25)
    groq_key = st.text_input("Groq API-kulcs (opcionális)", type="password", help="Csak a szöveges magyarázathoz használjuk; a matematikai szignált nem az LLM dönti el.")
    show_chart = st.checkbox("Grafikon", True)
    st.caption("Adatforrás: OKX elsődleges → Bybit tartalék. Binance nincs használva.")
    st.caption("A Bybit országkorlátozása nem kerülhető meg; ilyen esetben a rendszer az OKX-re vált.")

if refresh_choice != "Kézi" and st_autorefresh is not None:
    secs = {"30 mp": 30, "60 mp": 60, "5 perc": 300}[refresh_choice]
    st_autorefresh(interval=secs * 1000, key="saliga_refresh")

st.title("⚡ SALIGA AI — MARKET INTELLIGENCE PRO")
st.caption("Multi-asset • Multi-timeframe • Position Flow • Liquidity • Macro • Risk Engine")
st.caption("Elemzőrendszer, nem automata kereskedő. A pontszám nem kalibrált találati valószínűség.")

with st.spinner("Piaci adatok gyűjtése és elemzése…"):
    macro_data = get_macro_data()
    news = get_crypto_news()
    market_results = {}
    for symbol in SYMBOLS:
        market = load_asset_market(symbol)
        if not market["ok"]:
            market_results[symbol] = {"ok": False, "error": market["error"]}
            continue
        try:
            ex = market["client"]
            sym = market["market_symbol"]
            analyzed, mtf = timeframe_analysis(market["frames"])
            oi = fetch_oi_funding(ex, sym)
            flow = fetch_taker_flow(ex, sym)
            orderbook = fetch_orderbook_imbalance(ex, sym)
            liquidity = liquidity_map(analyzed["1h"])
            vp = volume_profile_proxy(analyzed["1h"])
            news_score, news_note = keyword_news_score(news, symbol)
            decision = make_decision(mtf["mtf"], oi, flow, orderbook, oi.get("funding_pct"), liquidity, macro_data, news_score, analyzed)
            market_results[symbol] = {
                "ok": True, "symbol": symbol, "exchange": market["exchange"], "price": market["price"],
                "frames": analyzed, "mtf": mtf, "oi": oi, "flow": flow, "orderbook": orderbook,
                "liquidity": liquidity, "volume_profile": vp, "news_score": news_score,
                "news_note": news_note, "decision": decision
            }
        except Exception as exc:
            market_results[symbol] = {"ok": False, "error": f"{market['exchange']} elemzési hiba: {str(exc)[:300]}"}

# Macro overview
st.subheader("🌍 Makrogazdasági környezet")
macro_cols = st.columns(5)
for col, key, label in zip(macro_cols, ["DXY", "VIX", "US10Y", "NASDAQ", "SP500"], ["DXY", "VIX", "US 10Y", "NASDAQ", "S&P 500"]):
    val = macro_data.get(key)
    col.metric(label, f"{val:+.3f}%" if val is not None else "N/A")

st.markdown("---")
st.subheader("🎯 Multi-asset végső szignálok")
cols = st.columns(3)
for col, symbol in zip(cols, SYMBOLS):
    d = market_results[symbol]
    with col:
        if not d["ok"]:
            st.error(f"{symbol}\n\nAdatkapcsolati / elemzési hiba\n\n{d['error']}")
            continue
        dec = d["decision"]
        sig = dec["signal"]
        if "LONG" in sig:
            st.success(f"{symbol} · {sig}")
        elif "SHORT" in sig:
            st.error(f"{symbol} · {sig}")
        else:
            st.warning(f"{symbol} · {sig}")
        st.metric("Aktuális ár", f"{d['price']:,.4f} USDT")
        st.metric("Modellpontszám", f"{dec['score']:+.3f}  |  {dec['strength']:.1f}/100")
        st.metric("Adatlefedettség", f"{dec['coverage']:.0f}%")
        st.caption(f"Adatforrás: {d['exchange']} · OI: {d['oi']['oi_status']}")

st.markdown("---")
available = [s for s in SYMBOLS if market_results[s]["ok"]]
if available:
    selected = st.selectbox("Részletes elemzendő piac", available)
    d = market_results[selected]
    dec = d["decision"]
    st.header(f"📊 {selected} — részletes piaci intelligencia")
    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("Végső szignál", dec["signal"])
    m2.metric("Modellpontszám", f"{dec['score']:+.3f}")
    m3.metric("OI változás", f"{d['oi']['oi_change_pct']:+.3f}%" if d['oi']['oi_change_pct'] is not None else "N/A")
    m4.metric("Taker delta", f"{d['flow']['delta_pct']:+.2f}%" if d['flow']['delta_pct'] is not None else "N/A")
    m5.metric("Funding", f"{d['oi']['funding_pct']:+.5f}%" if d['oi']['funding_pct'] is not None else "N/A")

    st.subheader("🧭 Idősíkos piaci struktúra")
    rows = []
    for tf in ["4h", "1h", "15m", "5m", "1m"]:
        item = d["mtf"]["details"][tf]
        struct = item["structure"]
        rows.append({"Idősík": tf, "Irány": struct["label"], "BOS": struct["bos"], "HH": struct.get("hh"), "HL": struct.get("hl"), "LH": struct.get("lh"), "LL": struct.get("ll"), "RSI": round(item["rsi"], 1) if item["rsi"] is not None else None, "Motor pont": round(item["score"], 3)})
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

    st.subheader("⚡ Position Flow — ár + OI + taker delta")
    p1, p2, p3 = st.columns(3)
    p1.metric("Flow értelmezés", d["decision"]["position"]["label"])
    p2.metric("OI változás", f"{d['oi']['oi_change_pct']:+.3f}%" if d['oi']['oi_change_pct'] is not None else "N/A")
    p3.metric("Utolsó kötések delta", f"{d['flow']['delta_quote']:,.0f} USDT" if d['flow']['delta_quote'] is not None else "N/A")
    st.caption(d["decision"]["position"]["note"] + " A flow besorolás következtetés, nem bizonyíték arra, hogy melyik szereplő mit nyitott.")
    st.caption(f"Kötésminta: {d['flow']['trades']} trade · {d['flow']['status']}")

    st.subheader("💧 Likviditási térkép")
    l1, l2, l3 = st.columns(3)
    l1.metric("Equal highs", fmt(d["liquidity"]["equal_high"], 4))
    l2.metric("Equal lows", fmt(d["liquidity"]["equal_low"], 4))
    l3.metric("Orderbook", d["orderbook"]["label"] + (f" ({d['orderbook']['imbalance_pct']:+.1f}%)" if d['orderbook']['imbalance_pct'] is not None else ""))
    st.write(f"**Liquidity sweep:** {d['liquidity']['sweep']}")
    st.write(f"**Fair Value Gap:** {d['liquidity']['fvg']}")
    st.write(f"**Volume Profile proxy:** POC {fmt(d['volume_profile']['poc'], 4)} · VAH {fmt(d['volume_profile']['vah'], 4)} · VAL {fmt(d['volume_profile']['val'], 4)}")
    st.caption("A volume profile OHLCV-adatokból képzett közelítés, nem tick-szintű tőzsdei volume profile.")

    st.subheader("🧠 Döntési motor — részpontszámok")
    comp_df = pd.DataFrame([{"Elemzőmotor": k, "Pontszám (-1…+1)": v if v is not None else "N/A"} for k, v in dec["components"].items()])
    st.dataframe(comp_df, use_container_width=True, hide_index=True)

    ai_text = groq_explanation(groq_key, selected, dec["signal"], dec["score"], dec["components"], macro_data, news)
    if ai_text:
        with st.expander("🤖 AI mentor — magyarázat", expanded=True):
            st.write(ai_text)
    else:
        st.info("Az AI mentor opcionális: Groq API-kulcs megadásával szöveges magyarázatot készít. A szignált a matematikai motor számolja.")
    st.caption(f"Hírmotor: {d['news_note']} · hírhangulat: {fmt(d['news_score'], 2)}")

    st.subheader("🛡️ Kockázatkezelés — ATR-alapú terv")
    atr_1h = safe_float(d["frames"]["1h"].iloc[-2]["atr"])
    plan = risk_plan(d["price"], atr_1h, dec["signal"], account_balance, risk_pct)
    if plan["valid"]:
        r1, r2, r3, r4 = st.columns(4)
        r1.metric("Stop Loss referencia", f"{plan['sl']:,.4f}")
        r2.metric("Take Profit referencia", f"{plan['tp']:,.4f}")
        r3.metric("Kockáztatott összeg", f"{plan['risk_cash']:.2f} USDT")
        r4.metric("Risk / Reward", f"1:{plan['rr']:.2f}")
        st.write(f"Számított mennyiség: **{plan['quantity']:.6f}** {selected.split('/')[0]} · becsült névérték: **{plan['notional']:.2f} USDT**")
        st.warning("Ez matematikai pozícióméret-becslés. A tényleges kötés előtt számolj díjjal, slippage-dzsel, tick/lot mérettel, fedezeti és tőkeáttételi korlátokkal.")
    else:
        st.info("A rendszer most nem ad kereskedési tervet: KIVÁRÁS vagy hiányzó ATR-adat.")

    note = st.text_input("Megjegyzés a naplóbejegyzéshez", key="journal_note")
    if st.button("💾 Aktuális jelzés mentése a naplóba", type="primary"):
        save_signal({"symbol": selected, "exchange": d["exchange"], "price": d["price"], "signal": dec["signal"], "score": dec["score"], "coverage": dec["coverage"]}, note)
        st.success("Jelzés elmentve a helyi SQLite naplóba.")

    if show_chart:
        st.subheader("📈 Árfolyam és volumen — 1H")
        chart = d["frames"]["1h"].tail(180)
        fig = make_subplots(rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.035, row_heights=[0.72, 0.28])
        fig.add_trace(go.Candlestick(x=chart.time, open=chart.open, high=chart.high, low=chart.low, close=chart.close, name=selected), row=1, col=1)
        fig.add_trace(go.Scatter(x=chart.time, y=chart.sma20, name="SMA20", line={"width": 1}), row=1, col=1)
        fig.add_trace(go.Scatter(x=chart.time, y=chart.sma50, name="SMA50", line={"width": 1}), row=1, col=1)
        fig.add_trace(go.Bar(x=chart.time, y=chart.volume, name="Volume"), row=2, col=1)
        fig.update_layout(template="plotly_dark", height=650, xaxis_rangeslider_visible=False, legend_orientation="h")
        st.plotly_chart(fig, use_container_width=True)
else:
    st.error("Egyik támogatott adatforrás sem adott teljes adatcsomagot. A rendszer nem készít szignált.")

st.markdown("---")
st.subheader("🗃️ Tartós jelzésnapló")
journal = read_journal(100)
if journal.empty:
    st.caption("Még nincs mentett jelzés. A részletes panelen a Mentés gombbal rögzíthetsz egy elemzést.")
else:
    st.dataframe(journal, use_container_width=True, hide_index=True)
    csv = journal.to_csv(index=False).encode("utf-8-sig")
    st.download_button("Napló letöltése CSV-ben", csv, file_name="saliga_signal_journal.csv", mime="text/csv")

st.markdown("---")
st.caption(
    f"SALIGA AI Market Intelligence PRO v{APP_VERSION} · {utc_now().strftime('%Y-%m-%d %H:%M:%S UTC')} · "
    "Nincs EMA. A CVD a legutóbb lekért kötések taker oldali mintája; az OI/flow értelmezés becslés; "
    "a liquidity és volume profile OHLCV-alapú közelítés. A modellpontszám nem backtestelt és nem találati valószínűség."
)
