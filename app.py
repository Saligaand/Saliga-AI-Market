# ============================================================
# SALIGA AI PRO 17.7 (FULL BTC INTELLIGENCE EDITION)
# BTC-CENTRIC + OI + FUNDING + DELTA + S/R + GROQ AI + LIVE RADAR
# ============================================================

import streamlit as st
import ccxt
import requests
import pandas as pd
import numpy as np
import sqlite3
import json
import os
import time

from datetime import datetime, timezone
from openai import OpenAI


# ============================================================
# 1. CONFIGURATION
# ============================================================

st.set_page_config(
    page_title="SALIGA AI PRO 17.7 - FULL BTC",
    page_icon="⚡",
    layout="wide"
)

APP_VERSION = "SALIGA AI PRO 17.7"
DB_PATH = "saliga_ai.db"

ASSET = "BTC"
SYMBOL = "BTC/USDT:USDT"

TIMEFRAMES = {
    "M15": "15m",
    "H1": "1h",
    "H4": "4h",
    "D1": "1d"
}

OI_URL = "https://www.okx.com/api/v5/public/open-interest"
GROQ_MODEL = "openai/gpt-oss-120b"

MARKET_REFRESH = 30
MIN_SIGNAL_SCORE = 65
HTTP_TIMEOUT = 15


# ============================================================
# 2. SESSION STATE
# ============================================================

DEFAULT_STATE = {
    "last_refresh": 0,
    "btc_analysis": {},
    "ai_result": None,
    "ai_error": None,
    "auto_refresh": False
}

for key, value in DEFAULT_STATE.items():
    if key not in st.session_state:
        st.session_state[key] = value


# ============================================================
# 3. GENERAL HELPERS
# ============================================================

def now_utc():
    return datetime.now(timezone.utc)

def timestamp():
    return now_utc().isoformat()

def safe_float(value):
    try:
        result = float(value)
        if np.isfinite(result):
            return result
        return None
    except (TypeError, ValueError):
        return None

def fmt(value, decimals=2):
    if value is None:
        return "N/A"
    try:
        return f"{float(value):,.{decimals}f}"
    except (TypeError, ValueError):
        return "N/A"

def pct_change(current, previous):
    if current is None or previous is None or previous == 0:
        return None
    return ((current - previous) / previous) * 100

def get_secret(name):
    try:
        value = st.secrets.get(name)
        if value:
            return str(value)
    except Exception:
        pass
    return os.getenv(name)

def db_connection():
    return sqlite3.connect(DB_PATH, check_same_thread=False, timeout=30)

def safe_close(conn):
    try:
        conn.close()
    except Exception:
        pass


# ============================================================
# 4. DATABASE & OI HISTORY INIT
# ============================================================

def init_db():
    conn = db_connection()
    try:
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS btc_oi_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                value REAL,
                created_at TEXT
            )
        """)
        conn.commit()
    finally:
        safe_close(conn)

init_db()

def save_oi_history(value):
    if value is None:
        return
    conn = db_connection()
    try:
        cur = conn.cursor()
        cur.execute("INSERT INTO btc_oi_history (value, created_at) VALUES (?, ?)", (value, timestamp()))
        conn.commit()
    finally:
        safe_close(conn)

def calculate_oi_change(current_value):
    if current_value is None:
        return None
    conn = db_connection()
    try:
        cur = conn.cursor()
        cur.execute("SELECT value, created_at FROM btc_oi_history WHERE value IS NOT NULL ORDER BY id DESC LIMIT 50")
        rows = cur.fetchall()
    finally:
        safe_close(conn)
    if not rows:
        return None
    curr_t = now_utc()
    for val, c_at in rows:
        try:
            old_t = datetime.fromisoformat(c_at)
            if old_t.tzinfo is None:
                old_t = old_t.replace(tzinfo=timezone.utc)
            if (curr_t - old_t).total_seconds() >= 300: # 5 perces időközök
                return pct_change(current_value, float(val))
        except:
            continue
    return None


# ============================================================
# 5. OKX CONNECTION
# ============================================================

@st.cache_resource
def get_exchange():
    client = ccxt.okx({
        "enableRateLimit": True,
        "timeout": 20000,
        "options": {"defaultType": "swap"}
    })
    client.load_markets()
    return client

exchange = None
exchange_error = None
try:
    exchange = get_exchange()
except Exception as e:
    exchange_error = str(e)


# ============================================================
# 6. OKX MARKET METRICS (OI, FUNDING, DELTA, ORDERBOOK)
# ============================================================

def fetch_btc_market_data():
    if exchange is None:
        return {"error": exchange_error or "OKX offline"}
    
    res = {"error": None}

    # 1. Ticker adatok
    try:
        ticker = exchange.fetch_ticker(SYMBOL)
        res["price"] = safe_float(ticker.get("last"))
        res["percentage"] = safe_float(ticker.get("percentage"))
        res["high"] = safe_float(ticker.get("high"))
        res["low"] = safe_float(ticker.get("low"))
    except Exception as e:
        res["error"] = "Ticker: " + str(e)

    # 2. Open Interest (OI)
    try:
        market_info = exchange.market(SYMBOL)
        params = {"instType": "SWAP", "instId": market_info["id"]}
        response = requests.get(OI_URL, params=params, timeout=HTTP_TIMEOUT)
        payload = response.json()
        if str(payload.get("code")) == "0":
            for rec in payload.get("data", []):
                val = safe_float(rec.get("oiUsd"))
                if val:
                    res["oi_usd"] = val
                    break
        res["oi_change"] = calculate_oi_change(res.get("oi_usd"))
        if res.get("oi_usd"):
            save_oi_history(res["oi_usd"])
    except:
        res["oi_usd"] = None
        res["oi_change"] = None

    # 3. Funding Rate
    try:
        funding = exchange.fetch_funding_rate(SYMBOL)
        res["funding_rate"] = safe_float(funding.get("fundingRate"))
    except:
        res["funding_rate"] = None

    # 4. Order Book Imbalance (Keresleti / Kínálati egyensúly)
    try:
        orderbook = exchange.fetch_order_book(SYMBOL, limit=20)
        bids_vol = sum(float(x[0]) * float(x[1]) for x in orderbook.get("bids", []))
        asks_vol = sum(float(x[0]) * float(x[1]) for x in orderbook.get("asks", []))
        total_vol = bids_vol + asks_vol
        if total_vol > 0:
            res["orderbook_imbalance"] = ((bids_vol - asks_vol) / total_vol) * 100
        else:
            res["orderbook_imbalance"] = 0.0
    except:
        res["orderbook_imbalance"] = None

    # 5. Becsült Delta (Közeli tradek alapján)
    try:
        trades = exchange.fetch_trades(SYMBOL, limit=200)
        buy_v, sell_v = 0.0, 0.0
        for t in trades:
            amt = safe_float(t.get("amount"))
            if amt:
                if t.get("side") == "buy":
                    buy_v += amt
                else:
                    sell_v += amt
        tot_t = buy_v + sell_v
        if tot_t > 0:
            res["delta_percent"] = ((buy_v - sell_v) / tot_t) * 100
        else:
            res["delta_percent"] = 0.0
    except:
        res["delta_percent"] = None

    return res


# ============================================================
# 7. TECHNICAL & S/R ANALYSIS (BTC)
# ============================================================

def fetch_btc_candles(timeframe):
    if exchange is None:
        return None
    try:
        data = exchange.fetch_ohlcv(SYMBOL, timeframe=TIMEFRAMES[timeframe], limit=100)
        if not data or len(data) < 30:
            return None
        df = pd.DataFrame(data, columns=["timestamp", "open", "high", "low", "close", "volume"])
        for col in ["open", "high", "low", "close", "volume"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        return df.iloc[:-1].reset_index(drop=True)
    except:
        return None

def analyze_btc_structure(df):
    if df is None or len(df) < 30:
        return {"support": 0, "resistance": 0, "status": "N/A", "modifier": 0}

    last_close = df["close"].iloc[-1]
    recent = df.iloc[-20:]
    resistance = float(recent["high"].max())
    support = float(recent["low"].min())
    
    status = "RANGE (Szinten belül)"
    modifier = 0

    if last_close >= resistance:
        status = "🚀 ELLENÁLLÁS ÁTTÖRÉS (Bullish Breakout)"
        modifier = +25
    elif last_close <= support:
        status = "⚠️ TÁMASZ ÁTTÖRÉS (Bearish Breakdown)"
        modifier = -25
    elif (resistance - last_close) / last_close <= 0.01:
        status = "⚡ Közel az ellenálláshoz (Figyeld a reakciót)"
        modifier = -10
    elif (last_close - support) / last_close <= 0.01:
        status = "🛡️ Közel a támaszhoz (Visszapattanás esély)"
        modifier = +15

    return {
        "support": support,
        "resistance": resistance,
        "status": status,
        "modifier": modifier
    }

def analyze_btc_timeframe(timeframe):
    df = fetch_btc_candles(timeframe)
    if df is None:
        return {"direction": "WAIT", "score": 0, "trend_desc": "Adat hiba"}

    last_close = df["close"].iloc[-1]
    ema_20 = df["close"].ewm(span=20).mean().iloc[-1]
    ema_50 = df["close"].ewm(span=50).mean().iloc[-1]

    score = 0
    trend_desc = "Semleges"

    if last_close > ema_20 and ema_20 > ema_50:
        score += 35
        trend_desc = "Erős Bika Trend"
    elif last_close < ema_20 and ema_20 < ema_50:
        score -= 35
        trend_desc = "Erős Medve Trend"
    elif last_close > ema_20:
        score += 15
        trend_desc = "Mérsékelt Bika"
    else:
        score -= 15
        trend_desc = "Mérsékelt Medve"

    sr = analyze_btc_structure(df)
    score += sr["modifier"]

    # RSI
    delta = df["close"].diff()
    gain = delta.clip(lower=0).ewm(alpha=1/14).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1/14).mean()
    rsi = float(100 - (100 / (1 + (gain / loss.replace(0, np.nan)))).iloc[-1])

    if rsi > 70:
        score -= 10
    elif rsi < 30:
        score += 10

    direction = "LONG" if score >= 25 else ("SHORT" if score <= -25 else "WAIT")

    return {
        "direction": direction,
        "score": score,
        "price": last_close,
        "rsi": rsi,
        "trend_desc": trend_desc,
        "sr": sr
    }


# ============================================================
# 8. GROQ AI INTEGRÁCIÓ (TELJES ADATKÖRREL)
# ============================================================

def run_groq_btc_analysis(market_data, tf_data):
    key = get_secret("GROQ_API_KEY")
    if not key:
        return None, "GROQ_API_KEY hiányzik a Secrets-ből!"

    client = OpenAI(api_key=key, base_url="https://api.groq.com/openai/v1")

    payload = {
        "derivatives_market": market_data,
        "technical_timeframes": tf_data
    }

    system_prompt = """
You are SALIGA AI PRO, an elite Bitcoin (BTC) derivatives market intelligence analyst.
Analyze the provided OKX market metrics (Open Interest, Funding Rate, Order Book Imbalance, Estimated Delta), 
combined with moving averages, RSI, and Support/Resistance levels.
Provide a concise, highly professional trading intelligence report in valid JSON format only.

Required JSON structure:
{
  "bias": "BULLISH / BEARISH / NEUTRAL",
  "confidence_score": 85,
  "executive_summary": "Short professional paragraph evaluating derivatives flow and price action.",
  "position_flow_interpretation": "What do OI and funding rate tell us about current leverage?",
  "actionable_insight": "Specific recommendation on long/short positioning based on S/R levels.",
  "risks": ["Risk 1", "Risk 2"]
}
"""

    try:
        response = client.chat.completions.create(
            model=GROQ_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False, default=str)}
            ],
            temperature=0.2,
            max_tokens=1500,
            response_format={"type": "json_object"}
        )
        content = response.choices[0].message.content
        return json.loads(content), None
    except Exception as e:
        return None, str(e)


def run_full_cycle():
    market = fetch_btc_market_data()
    tf_results = {}
    for tf_key in TIMEFRAMES.keys():
        tf_results[tf_key] = analyze_btc_timeframe(tf_key)
    
    return {
        "timestamp": timestamp(),
        "market": market,
        "timeframes": tf_results
    }


# ============================================================
# 9. STREAMLIT FELÜLET (TELJES METRIKA + S/R + AI)
# ============================================================

st.title("⚡ SALIGA AI PRO — BITCOIN (BTC) TELJES DERIVATÍV MONITOR")
st.caption("OKX OI + FUNDING + DELTA + ORDER BOOK + S/R SZINTEK + GROQ AI")

if exchange_error:
    st.error(f"OKX Hiba: {exchange_error}")

# Vezérlő sáv
c1, c2, c3, c4 = st.columns(4)
with c1:
    if st.button("🔄 ADATOK FRISSÍTÉSE", use_container_width=True):
        st.session_state["btc_analysis"] = run_full_cycle()
        st.session_state["last_refresh"] = time.time()
with c2:
    if st.button("🧠 GROQ AI JELENTÉS KÉRÉSE", use_container_width=True):
        if st.session_state["btc_analysis"]:
            with st.spinner("Groq AI elemzi a derivatív áramlásokat és az S/R szinteket..."):
                res, err = run_groq_btc_analysis(
                    st.session_state["btc_analysis"]["market"],
                    st.session_state["btc_analysis"]["timeframes"]
                )
                st.session_state["ai_result"] = res
                st.session_state["ai_error"] = err
        else:
            st.warning("Előbb frissítsd az adatokat!")
with c3:
    auto_refresh = st.checkbox("🟢 Élő frissítés (30mp)", value=st.session_state["auto_refresh"])
    st.session_state["auto_refresh"] = auto_refresh
with c4:
    st.metric("Utolsó frissítés", datetime.now().strftime("%H:%M:%S"))

if not st.session_state["btc_analysis"]:
    st.session_state["btc_analysis"] = run_full_cycle()

data = st.session_state["btc_analysis"]


# ============================================================
# 10. DERIVATÍV METRIKÁK PANEL (OI, FUNDING, DELTA, BOOK)
# ============================================================

st.markdown("---")
st.header("📊 BITCOIN DERIVATÍV & POZÍCIÓ ÁRAMLÁS (OKX)")

market = data.get("market", {})
m_col1, m_col2, m_col3, m_col4 = st.columns(4)

with m_col1:
    oi_val = market.get("oi_usd")
    oi_chg = market.get("oi_change")
    st.metric("Open Interest (USD)", f"${oi_val:,.0f}" if oi_val else "N/A")
    st.metric("OI Változás", f"{oi_chg:+.2f}%" if oi_chg is not None else "Adatgyűjtés...")

with m_col2:
    f_rate = market.get("funding_rate")
    st.metric("Funding Rate", f"{f_rate * 100:.4f}%" if f_rate is not None else "N/A")
    st.caption("Pozitív = Longok fizetnek | Negatív = Shortok fizetnek")

with m_col3:
    delta = market.get("delta_percent")
    st.metric("Becsült Delta %", f"{delta:+.2f}%" if delta is not None else "N/A")
    st.caption("Legutóbbi tradek buy/sell egyensúlya")

with m_col4:
    imbalance = market.get("orderbook_imbalance")
    st.metric("Order Book Imbalance", f"{imbalance:+.2f}%" if imbalance is not None else "N/A")
    st.caption("Könyv oldali megbízási fölnyomás")


# ============================================================
# 11. ÉLŐ IRÁNYVONAL & S/R SZINTEK PANEL
# ============================================================

st.markdown("---")
st.header("🎯 ÉLŐ IRÁNYVONAL & TÁMASZ / ELLENÁLLÁS RADAR")

h1_info = data.get("timeframes", {}).get("H1", {})
sr = h1_info.get("sr", {})
price = market.get("price")
direction = h1_info.get("direction", "WAIT")

col_a, col_b = st.columns([1, 1])

with col_a:
    if direction == "LONG":
        st.success("### 🟢 BTC JELZÉS: LONG POZÍCIÓ AKTÍV")
    elif direction == "SHORT":
        st.error("### 🔴 BTC JELZÉS: SHORT POZÍCIÓ AKTÍV")
    else:
        st.warning("### 🟡 BTC JELZÉS: KIVÁRÁS (WAIT)")

    st.metric("Élő BTC Árfolyam", fmt(price, 2))
    st.metric("H1 Trend Státusz", h1_info.get("trend_desc", "N/A"))
    st.metric("Matematikai Pontszám (H1)", h1_info.get("score", 0))

with col_b:
    st.info(
        f"🧱 **H1 Ellenállás (Resistance):** `{fmt(sr.get('resistance'), 2)}`\n\n"
        f"🛡️ **H1 Támasz (Support):** `{fmt(sr.get('support'), 2)}`\n\n"
        f"📊 **Szint Státusz:**\n**{sr.get('status', 'N/A')}**\n\n"
        f"📈 **RSI (14):** `{fmt(h1_info.get('rsi'), 1)}`"
    )

st.markdown("---")


# ============================================================
# 12. GROQ AI JELENTÉS
# ============================================================

st.header("🧠 GROQ AI DERIVATÍV ÉS PIACI JELENTÉS")

ai_res = st.session_state["ai_result"]
ai_err = st.session_state["ai_error"]

if ai_err:
    st.error(f"AI Hiba: {ai_err}")

if ai_res:
    st.subheader(f"AI Piaci Irány: {ai_res.get('bias', 'N/A')} (Bizalom: {ai_res.get('confidence_score', 'N/A')}%)")
    st.write(f"**Összegzés:** {ai_res.get('executive_summary', '')}")
    st.write(f"**Pozíció Áramlás & Tőke:** {ai_res.get('position_flow_interpretation', '')}")
    st.success(f"**Ajánlott Művelet (Actionable):** {ai_res.get('actionable_insight', '')}")
    
    with st.expander("⚠️ Figyelembe veendő kockázatok"):
        for risk in ai_res.get("risks", []):
            st.write(f"• {risk}")
else:
    st.info("Kattints a **'GROQ AI JELENTÉS KÉRÉSE'** gombra, hogy az AI kiértékelje az OI-t, Funding Rate-et, Deltát és az S/R szinteket.")


# ============================================================
# 13. MULTI-TIMEFRAME TÁBLÁZAT
# ============================================================

st.markdown("---")
st.subheader("🧭 BTC Időkeretek Összegzése (M15, H1, H4, D1)")

tf_rows = []
for tf_name in TIMEFRAMES.keys():
    res = data["timeframes"].get(tf_name, {})
    s_data = res.get("sr", {})
    tf_rows.append({
        "Időkeret": tf_name,
        "Irány": res.get("direction", "WAIT"),
        "Pontszám": res.get("score", 0),
        "Trend": res.get("trend_desc", "N/A"),
        "Támasz": fmt(s_data.get("support"), 2),
        "Ellenállás": fmt(s_data.get("resistance"), 2),
        "RSI": fmt(res.get("rsi"), 1)
    })

st.dataframe(pd.DataFrame(tf_rows), use_container_width=True, hide_index=True)


# ============================================================
# 14. AUTOMATIKUS FRISSÍTÉS LOGIKA
# ============================================================

if st.session_state["auto_refresh"]:
    time.sleep(MARKET_REFRESH)
    st.session_state["btc_analysis"] = run_full_cycle()
    st.rerun()
