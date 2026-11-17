import time
import requests
import numpy as np
import pandas as pd
import streamlit as st
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import ccxt

st.set_page_config(page_title="SALIGA AI Market Intelligence", layout="wide")
st.title("SALIGA AI – Market Intelligence")
st.caption("Kriptopiaci elemző MVP • adatvezérelt iránybecslés • nem kereskedési tanács")

# --- Settings ---
with st.sidebar:
    st.header("Beállítások")
    symbol = st.selectbox("Piac", ["BTC/USDT", "SOL/USDT", "ETH/USDT"], index=0)
    refresh = st.selectbox("Frissítés", ["Kézi", "30 mp", "60 mp", "5 perc"], index=0)
    show_chart = st.checkbox("Gyertyagrafikon", value=True)
    st.markdown("---")
    st.caption("Adatforrás: Binance nyilvános OHLCV + Bybit nyilvános derivatív adatok.")
    st.caption("API-kulcs nem szükséges az alapverzióhoz.")

TIMEFRAMES = {
    "1m": "1m", "5m": "5m", "15m": "15m", "1h": "1h", "4h": "4h"
}
BYBIT_INTERVAL = {"1m": "5min", "5m": "5min", "15m": "15min", "1h": "1h", "4h": "4h"}

@st.cache_data(ttl=15)
def get_ohlcv(symbol, timeframe, limit=300):
    exchange = ccxt.binance({"enableRateLimit": True, "options": {"defaultType": "future"}})
    market_symbol = symbol.replace("/", "")
    data = exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
    df = pd.DataFrame(data, columns=["time", "open", "high", "low", "close", "volume"])
    df["time"] = pd.to_datetime(df["time"], unit="ms", utc=True)
    return df

@st.cache_data(ttl=30)
def get_bybit_derivatives(symbol):
    pair = symbol.replace("/", "")
    result = {"oi_change": None, "funding": None, "oi_now": None}
    try:
        r = requests.get(
            "https://api.bybit.com/v5/market/open-interest",
            params={"category": "linear", "symbol": pair, "intervalTime": "5min", "limit": 2},
            timeout=8
        )
        j = r.json()
        rows = j.get("result", {}).get("list", [])
        if len(rows) >= 2:
            newest = float(rows[0]["openInterest"])
            older = float(rows[1]["openInterest"])
            result["oi_now"] = newest
            result["oi_change"] = ((newest - older) / older * 100) if older else None
    except Exception:
        pass
    try:
        r = requests.get(
            "https://api.bybit.com/v5/market/funding/history",
            params={"category": "linear", "symbol": pair, "limit": 1},
            timeout=8
        )
        j = r.json()
        rows = j.get("result", {}).get("list", [])
        if rows:
            result["funding"] = float(rows[0]["fundingRate"]) * 100
    except Exception:
        pass
    return result

def add_indicators(df):
    x = df.copy()
    x["sma20"] = x["close"].rolling(20).mean()
    x["sma50"] = x["close"].rolling(50).mean()
    delta = x["close"].diff()
    gain = delta.clip(lower=0).rolling(14).mean()
    loss = (-delta.clip(upper=0)).rolling(14).mean()
    rs = gain / loss.replace(0, np.nan)
    x["rsi"] = 100 - (100 / (1 + rs))
    prev_close = x["close"].shift(1)
    tr = pd.concat([
        x["high"] - x["low"],
        (x["high"] - prev_close).abs(),
        (x["low"] - prev_close).abs()
    ], axis=1).max(axis=1)
    x["atr"] = tr.rolling(14).mean()
    # Approximate candle delta: signed volume based on candle direction.
    # This is NOT exchange-level bid/ask delta or true CVD.
    x["est_delta"] = np.where(x["close"] >= x["open"], x["volume"], -x["volume"])
    x["delta_sum"] = x["est_delta"].rolling(20).sum()
    x["vol_avg"] = x["volume"].rolling(20).mean()
    return x

def structure_score(df):
    x = add_indicators(df).dropna()
    if len(x) < 5:
        return 0, {}
    last = x.iloc[-1]
    prev = x.iloc[-2]
    score = 0
    details = {}
    # SMA used only as broad context; no EMA.
    if last.close > last.sma20: score += 1
    else: score -= 1
    if last.sma20 > last.sma50: score += 1
    else: score -= 1
    if last.close > prev.high: score += 1
    elif last.close < prev.low: score -= 1
    if last.rsi >= 55: score += 1
    elif last.rsi <= 45: score -= 1
    if last.delta_sum > 0: score += 1
    elif last.delta_sum < 0: score -= 1
    if last.volume > last.vol_avg * 1.2:
        details["volume"] = "Átlag feletti"
    else:
        details["volume"] = "Átlagos/alacsony"
    details.update({
        "RSI": round(float(last.rsi), 1),
        "SMA20": float(last.sma20),
        "SMA50": float(last.sma50),
        "Becsült delta": "Pozitív" if last.delta_sum > 0 else "Negatív",
        "ATR": float(last.atr),
        "score": score
    })
    return score, details

def label(score):
    if score >= 3: return "LONG irányú fölény", "success"
    if score >= 1: return "Enyhe LONG fölény", "success"
    if score <= -3: return "SHORT irányú fölény", "error"
    if score <= -1: return "Enyhe SHORT fölény", "error"
    return "Semleges / kivárás", "warning"

@st.cache_data(ttl=15)
def load_all(symbol):
    return {tf: get_ohlcv(symbol, tf, 300) for tf in TIMEFRAMES}

try:
    with st.spinner("Piaci adatok lekérése..."):
        frames = load_all(symbol)
        deriv = get_bybit_derivatives(symbol)
    analyzed = {tf: add_indicators(df) for tf, df in frames.items()}
    scores = {}
    details = {}
    for tf, df in frames.items():
        scores[tf], details[tf] = structure_score(df)

    # Weighted multi-timeframe score: higher TFs define bias; lower TFs refine timing.
    weighted = (
        scores["4h"] * 0.30 +
        scores["1h"] * 0.30 +
        scores["15m"] * 0.20 +
        scores["5m"] * 0.12 +
        scores["1m"] * 0.08
    )
    oi = deriv.get("oi_change")
    funding = deriv.get("funding")
    oi_note = "Nincs adat"
    if oi is not None:
        oi_note = f"{oi:+.3f}% az utolsó két 5 perces mérés között"
        if oi > 0.15 and weighted > 0: weighted += 0.35
        elif oi > 0.15 and weighted < 0: weighted -= 0.35
    if funding is not None:
        if funding > 0.03: weighted -= 0.15
        elif funding < -0.03: weighted += 0.15

    direction, status = label(weighted)
    # This is a normalized score display, NOT a statistically calibrated probability.
    confidence = min(85, 50 + abs(weighted) * 6)
    price = float(frames["1m"].iloc[-1]["close"])

    a, b, c = st.columns(3)
    a.metric("Aktuális ár", f"{price:,.4f} USDT")
    b.metric("Összesített piaci pontszám", f"{weighted:+.2f} / 5")
    c.metric("Jelzés erőssége (nem kalibrált)", f"{confidence:.0f}%")

    if status == "success": st.success(direction)
    elif status == "error": st.error(direction)
    else: st.warning(direction)

    st.subheader("Idősíkok összehasonlítása")
    rows = []
    for tf in ["4h", "1h", "15m", "5m", "1m"]:
        sc = scores[tf]
        d = details[tf]
        rows.append({
            "Idősík": tf,
            "Pontszám": sc,
            "Irány": "LONG" if sc > 0 else ("SHORT" if sc < 0 else "Semleges"),
            "RSI": d.get("RSI"),
            "Becsült delta": d.get("Becsült delta"),
            "Volumen": d.get("volume")
        })
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

    st.subheader("Derivatív piaci adatok – Bybit")
    d1, d2 = st.columns(2)
    d1.metric("Open Interest változás", oi_note)
    d2.metric("Funding rate", f"{funding:.5f}%" if funding is not None else "Nincs adat")

    st.info(
        "Értelmezés: az emelkedő ár + növekvő OI együtt új pozíciók nyílására utalhat, "
        "de önmagában nem bizonyít longépítést. A csökkenő OI zárásokat jelezhet. "
        "A funding szélsősége zsúfolt pozicionáltságra utalhat."
    )

    if show_chart:
        st.subheader("1 órás chart")
        chart = analyzed["1h"].tail(180)
        fig = make_subplots(rows=2, cols=1, shared_xaxes=True,
                            vertical_spacing=0.04, row_heights=[0.72, 0.28])
        fig.add_trace(go.Candlestick(
            x=chart["time"], open=chart["open"], high=chart["high"],
            low=chart["low"], close=chart["close"], name="BTC/SOL"
        ), row=1, col=1)
        fig.add_trace(go.Scatter(x=chart["time"], y=chart["sma20"], name="SMA20"), row=1, col=1)
        fig.add_trace(go.Scatter(x=chart["time"], y=chart["sma50"], name="SMA50"), row=1, col=1)
        fig.add_trace(go.Bar(x=chart["time"], y=chart["est_delta"], name="Becsült delta"), row=2, col=1)
        fig.update_layout(height=650, xaxis_rangeslider_visible=False)
        st.plotly_chart(fig, use_container_width=True)

    with st.expander("Részletes mutatók"):
        st.json(details)

    st.caption(
        "Fontos korlátok: a gyertyából becsült delta nem valódi order-flow adat; "
        "az OI-adat két rövid időközű mérés összevetése; a kijelzett százalék nem "
        "historikusan igazolt találati valószínűség. Éles kereskedés előtt backtest és paper trading szükséges."
    )

except Exception as e:
    st.error(f"Adatlekérési hiba: {e}")
    st.write("Ellenőrizd az internetkapcsolatot, majd próbáld újra. A Binance/Bybit nyilvános API átmenetileg korlátozhat.")

if refresh != "Kézi":
    seconds = {"30 mp": 30, "60 mp": 60, "5 perc": 300}[refresh]
    time.sleep(seconds)
    st.rerun()
