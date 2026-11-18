# ============================================================
# SALIGA AI PRO 17.8 (FULL PATTERN & DERIVATIVE INTELLIGENCE)
# BTC-CENTRIC + PATTERN RECOGNITION (H&S, DOUBLE TOP, SIDEWAYS) 
# + OI + FUNDING + DELTA + S/R + GROQ AI
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
    page_title="SALIGA AI PRO 17.8 - PATTERN BTC",
    page_icon="⚡",
    layout="wide"
)

APP_VERSION = "SALIGA AI PRO 17.8"
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
            if (curr_t - old_t).total_seconds() >= 300:
                return pct_change(current_value, float(val))
        except:
            continue
    return None


# ============================================================
# 5. OKX CONNECTION & MARKET METRICS
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

def fetch_btc_market_data():
    if exchange is None:
        return {"error": exchange_error or "OKX offline"}
    
    res = {"error": None}
    try:
        ticker = exchange.fetch_ticker(SYMBOL)
        res["price"] = safe_float(ticker.get("last"))
        res["percentage"] = safe_float(ticker.get("percentage"))
        res["high"] = safe_float(ticker.get("high"))
        res["low"] = safe_float(ticker.get("low"))
    except Exception as e:
        res["error"] = "Ticker: " + str(e)

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

    try:
        funding = exchange.fetch_funding_rate(SYMBOL)
        res["funding_rate"] = safe_float(funding.get("fundingRate"))
    except:
        res["funding_rate"] = None

    try:
        orderbook = exchange.fetch_order_book(SYMBOL, limit=20)
        bids_vol = sum(float(x[0]) * float(x[1]) for x in orderbook.get("bids", []))
        asks_vol = sum(float(x[0]) * float(x[1]) for x in orderbook.get("asks", []))
        total_vol = bids_vol + asks_vol
        res["orderbook_imbalance"] = ((bids_vol - asks_vol) / total_vol) * 100 if total_vol > 0 else 0.0
    except:
        res["orderbook_imbalance"] = None

    try:
        trades = exchange.fetch_trades(SYMBOL, limit=200)
        buy_v, sell_v = 0.0, 0.0
        for t in trades:
            amt = safe_float(t.get("amount"))
            if amt:
                if t.get("side") == "buy": buy_v += amt
                else: sell_v += amt
        tot_t = buy_v + sell_v
        res["delta_percent"] = ((buy_v - sell_v) / tot_t) * 100 if tot_t > 0 else 0.0
    except:
        res["delta_percent"] = None

    return res


# ============================================================
# 6. ADVANCED PATTERN RECOGNITION (ALAKZATOK & OLDALAZÁS)
# ============================================================

def fetch_btc_candles(timeframe):
    if exchange is None:
        return None
    try:
        data = exchange.fetch_ohlcv(SYMBOL, timeframe=TIMEFRAMES[timeframe], limit=120)
        if not data or len(data) < 40:
            return None
        df = pd.DataFrame(data, columns=["timestamp", "open", "high", "low", "close", "volume"])
        for col in ["open", "high", "low", "close", "volume"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        return df.iloc[:-1].reset_index(drop=True)
    except:
        return None

def detect_chart_patterns(df):
    """
    Keresi a Dupla Csúcsot, Dupla Aljat, Fej-Vállat, Fordított Fej-Vállat 
    és az Oldalazó / Konszolidációs fázisokat.
    """
    if df is None or len(df) < 40:
        return {
            "pattern": "Nincs elegendő adat",
            "type": "NEUTRAL",
            "modifier": 0
        }

    highs = df["high"].values
    lows = df["low"].values
    closes = df["close"].values
    
    # 1. OLDALAZÁS / KONSZOLIDÁCIÓ VIZSGÁLAT
    # Ha az utolsó 15 gyertya ártartománya (max - min) viszonylag szűk (< 2.5%)
    recent_high = highs[-15:].max()
    recent_low = lows[-15:].min()
    current_price = closes[-1]
    range_pct = ((recent_high - recent_low) / current_price) * 100
    
    if range_pct < 2.2:
        return {
            "pattern": "⚖️ OLDALAZÁS / KONSZOLIDÁCIÓ (Várj a kitörésre)",
            "type": "SIDEWAYS",
            "modifier": 0
        }

    # Lokális csúcsok és völgyek keresése egyszerű ablakos módszerrel
    window = 5
    peaks = []
    troughs = []
    
    for i in range(window, len(df) - window):
        if highs[i] == max(highs[i-window:i+window+1]):
            peaks.append((i, highs[i]))
        if lows[i] == min(lows[i-window:i+window+1]):
            troughs.append((i, lows[i]))

    # 2. DUPLA CSÚCS (DOUBLE TOP) ÉS DUPLA ALJ (DOUBLE BOTTOM)
    if len(peaks) >= 2:
        p1, p2 = peaks[-2], peaks[-1]
        # Ha a két utolsó csúcs ára nagyon közel van egymáshoz (0.8%-on belül)
        if abs(p1[1] - p2[1]) / p1[1] <= 0.008:
            return {
                "pattern": "🔴 DUPLA CSÚCS (Double Top - Medve alakzat)",
                "type": "BEARISH",
                "modifier": -30
            }

    if len(troughs) >= 2:
        t1, t2 = troughs[-2], troughs[-1]
        # Ha a két utolsó alj ára nagyon közel van egymáshoz (0.8%-on belül)
        if abs(t1[1] - t2[1]) / t1[1] <= 0.008:
            return {
                "pattern": "🟢 DUPLA ALJ (Double Bottom - Bika alakzat)",
                "type": "BULLISH",
                "modifier": +30
            }

    # 3. FEJ ÉS VÁLL (HEAD AND SHOULDERS) & FORDÍTOTT FEJ ÉS VÁLL
    if len(peaks) >= 3:
        s1, head, s2 = peaks[-3], peaks[-2], peaks[-1]
        # Fej magasabb a vállaknál, és a két váll nagyjából azonos magasságú
        if head[1] > s1[1] and head[1] > s2[1] and abs(s1[1] - s2[1]) / s1[1] <= 0.012:
            return {
                "pattern": "⚠️ FEJ ÉS VÁLL (Head & Shoulders - Fordulós medve)",
                "type": "BEARISH",
                "modifier": -35
            }

    if len(troughs) >= 3:
        ts1, thead, ts2 = troughs[-3], troughs[-2], troughs[-1]
        # Fej mélyebb a vállaknál, és a két váll nagyjából azonos szinten van
        if thead[1] < ts1[1] and thead[1] < ts2[1] and abs(ts1[1] - ts2[1]) / ts1[1] <= 0.012:
            return {
                "pattern": "🚀 FORDÍTOTT FEJ ÉS VÁLL (Inverse H&S - Fordulós bika)",
                "type": "BULLISH",
                "modifier": +35
            }

    return {
        "pattern": "Normál Trend / Nincs specifikus alakzat",
        "type": "NEUTRAL",
        "modifier": 0
    }

def analyze_btc_structure(df):
    if df is None or len(df) < 30:
        return {"support": 0, "resistance": 0, "status": "N/A", "modifier": 0}

    last_close = df["close"].iloc[-1]
    recent = df.iloc[-20:]
    resistance = float(recent["high"].max())
    support = float(recent["low"].min())
    
    status = "Normál S/R sáv"
    modifier = 0

    if last_close >= resistance:
        status = "🚀 ELLENÁLLÁS ÁTTÖRÉS (Breakout)"
        modifier = +20
    elif last_close <= support:
        status = "⚠️ TÁMASZ ÁTTÖRÉS (Breakdown)"
        modifier = -20

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
        score += 30
        trend_desc = "Erős Bika Trend"
    elif last_close < ema_20 and ema_20 < ema_50:
        score -= 30
        trend_desc = "Erős Medve Trend"
    elif last_close > ema_20:
        score += 15
        trend_desc = "Mérsékelt Bika"
    else:
        score -= 15
        trend_desc = "Mérsékelt Medve"

    # Alakzat felismerés integrálása a pontszámba
    pattern_info = detect_chart_patterns(df)
    score += pattern_info["modifier"]

    # S/R szintek integrálása
    sr = analyze_btc_structure(df)
    score += sr["modifier"]

    # RSI
    delta = df["close"].diff()
    gain = delta.clip(lower=0).ewm(alpha=1/14).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1/14).mean()
    rsi = float(100 - (100 / (1 + (gain / loss.replace(0, np.nan)))).iloc[-1])

    if rsi > 70: score -= 10
    elif rsi < 30: score += 10

    direction = "LONG" if score >= 25 else ("SHORT" if score <= -25 else "WAIT")

    return {
        "direction": direction,
        "score": score,
        "price": last_close,
        "rsi": rsi,
        "trend_desc": trend_desc,
        "sr": sr,
        "pattern": pattern_info
    }


# ============================================================
# 7. GROQ AI INTEGRÁCIÓ (ALAKZATOKKAL KIEGÉSZÍTVE)
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
You are SALIGA AI PRO, an elite Bitcoin (BTC) market intelligence and chart pattern analyst.
Analyze OKX derivatives (OI, funding rate, delta), S/R levels, and detected chart patterns 
(Head and Shoulders, Double Top/Bottom, Sideways/Consolidation).
Provide a concise, professional trading report in valid JSON format only.

Required JSON structure:
{
  "bias": "BULLISH / BEARISH / NEUTRAL",
  "confidence_score": 85,
  "executive_summary": "Professional summary focusing on pattern recognition and derivative flow.",
  "pattern_interpretation": "What does the current chart pattern (or sideways range) mean for upcoming price action?",
  "actionable_insight": "Specific recommendation on long/short positioning or waiting for breakout.",
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
# 8. STREAMLIT UI (TELJES ALAKZAT & DERIVATÍV MONITOR)
# ============================================================

st.title("⚡ SALIGA AI PRO — BITCOIN (BTC) ALAKZAT & DERIVATÍV MONITOR")
st.caption("OKX OI + FUNDING + DELTA + ALAKZATOK (FEJ-VÁLL, DUPLA CSÚCS, OLDALAZÁS) + GROQ AI")

if exchange_error:
    st.error(f"OKX Hiba: {exchange_error}")

# Vezérlők
c1, c2, c3, c4 = st.columns(4)
with c1:
    if st.button("🔄 ADATOK FRISSÍTÉSE", use_container_width=True):
        st.session_state["btc_analysis"] = run_full_cycle()
        st.session_state["last_refresh"] = time.time()
with c2:
    if st.button("🧠 GROQ AI JELENTÉS KÉRÉSE", use_container_width=True):
        if st.session_state["btc_analysis"]:
            with st.spinner("Groq AI elemzi az alakzatokat és a derivatív áramlást..."):
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
# 9. DERIVATÍV METRIKÁK PANEL
# ============================================================

st.markdown("---")
st.header("📊 BITCOIN DERIVATÍV & POZÍCIÓ ÁRAMLÁS")

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

with m_col3:
    delta = market.get("delta_percent")
    st.metric("Becsült Delta %", f"{delta:+.2f}%" if delta is not None else "N/A")

with m_col4:
    imbalance = market.get("orderbook_imbalance")
    st.metric("Order Book Imbalance", f"{imbalance:+.2f}%" if imbalance is not None else "N/A")


# ============================================================
# 10. ÉLŐ ALAKZAT ÉS S/R RADAR PANEL
# ============================================================

st.markdown("---")
st.header("🎯 ÉLŐ ALAKZAT ÉS IRÁNYVONAL RADAR")

h1_info = data.get("timeframes", {}).get("H1", {})
sr = h1_info.get("sr", {})
pattern = h1_info.get("pattern", {})
price = market.get("price")
direction = h1_info.get("direction", "WAIT")

col_a, col_b = st.columns([1, 1])

with col_a:
    if direction == "LONG":
        st.success("### 🟢 BTC JELZÉS: LONG POZÍCIÓ")
    elif direction == "SHORT":
        st.error("### 🔴 BTC JELZÉS: SHORT POZÍCIÓ")
    else:
        st.warning("### 🟡 BTC JELZÉS: KIVÁRÁS (WAIT)")

    st.metric("Élő BTC Árfolyam", fmt(price, 2))
    st.metric("H1 Trend Státusz", h1_info.get("trend_desc", "N/A"))
    st.metric("Kombinált Pontszám (H1)", h1_info.get("score", 0))

with col_b:
    st.info(
        f"🔍 **Felismerett Alakzat / Fázis:**\n**{pattern.get('pattern', 'N/A')}**\n\n"
        f"🧱 **H1 Ellenállás:** `{fmt(sr.get('resistance'), 2)}`\n\n"
        f"🛡️ **H1 Támasz:** `{fmt(sr.get('support'), 2)}`\n\n"
        f"📈 **RSI (14):** `{fmt(h1_info.get('rsi'), 1)}`"
    )

st.markdown("---")


# ============================================================
# 11. GROQ AI JELENTÉS
# ============================================================

st.header("🧠 GROQ AI ALAKZAT & PIACI JELENTÉS")

ai_res = st.session_state["ai_result"]
ai_err = st.session_state["ai_error"]

if ai_err:
    st.error(f"AI Hiba: {ai_err}")

if ai_res:
    st.subheader(f"AI Piaci Irány: {ai_res.get('bias', 'N/A')} (Bizalom: {ai_res.get('confidence_score', 'N/A')}%)")
    st.write(f"**Összegzés:** {ai_res.get('executive_summary', '')}")
    st.write(f"**Alakzat Értelmezés:** {ai_res.get('pattern_interpretation', '')}")
    st.success(f"**Ajánlott Művelet:** {ai_res.get('actionable_insight', '')}")
    
    with st.expander("⚠️ Kockázatok"):
        for risk in ai_res.get("risks", []):
            st.write(f"• {risk}")
else:
    st.info("Kattints a **'GROQ AI JELENTÉS KÉRÉSE'** gombra az alakzatok és az AI elemzéséhez.")


# ============================================================
# 12. MULTI-TIMEFRAME TÁBLÁZAT ALAKZATOKKAL
# ============================================================

st.markdown("---")
st.subheader("🧭 BTC Időkeretek & Alakzatok Összegzése")

tf_rows = []
for tf_name in TIMEFRAMES.keys():
    res = data["timeframes"].get(tf_name, {})
    p_data = res.get("pattern", {})
    tf_rows.append({
        "Időkeret": tf_name,
        "Irány": res.get("direction", "WAIT"),
        "Pontszám": res.get("score", 0),
        "Felismerett Alakzat / Állapot": p_data.get("pattern", "N/A"),
        "Trend": res.get("trend_desc", "N/A"),
        "RSI": fmt(res.get("rsi"), 1)
    })

st.dataframe(pd.DataFrame(tf_rows), use_container_width=True, hide_index=True)


# ============================================================
# 13. AUTOMATIKUS FRISSÍTÉS LOGIKA
# ============================================================

if st.session_state["auto_refresh"]:
    time.sleep(MARKET_REFRESH)
    st.session_state["btc_analysis"] = run_full_cycle()
    st.rerun()
