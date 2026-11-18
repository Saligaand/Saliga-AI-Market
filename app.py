# ============================================================
# SALIGA AI PRO 18.1 (PURE PATTERN & HISTORICAL MATCH EDITION)
# BTC-CENTRIC + AUTO-REFRESH + HISTORICAL PATTERN MATCHING + GROQ AI
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
    page_title="SALIGA AI PRO 18.1 - PATTERN MATCH",
    page_icon="⚡",
    layout="wide"
)

APP_VERSION = "SALIGA AI PRO 18.1"
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

DEFAULT_REFRESH_INTERVAL = 300 
HTTP_TIMEOUT = 15


# ============================================================
# 2. SESSION STATE
# ============================================================

DEFAULT_STATE = {
    "last_refresh": 0,
    "btc_analysis": {},
    "ai_result": None,
    "ai_error": None,
    "auto_refresh_active": True,
    "refresh_interval": DEFAULT_REFRESH_INTERVAL,
    "lookback_candles": 2000  # Hány gyertyát nézzen vissza a történetből
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
# 6. HISTORICAL PATTERN MATCHING ENGINE (TÖBB EZER GYERTYA VISSZANEZÉSE)
# ============================================================

def fetch_deep_btc_candles(timeframe, limit=2000):
    if exchange is None:
        return None
    try:
        # Az OKX legfeljebb egyszerre 300-1000 gyertyát ad, így lapozással vagy nagyobb limiten kérjük le
        data = exchange.fetch_ohlcv(SYMBOL, timeframe=TIMEFRAMES[timeframe], limit=min(limit, 1500))
        if not data or len(data) < 50:
            return None
        df = pd.DataFrame(data, columns=["timestamp", "open", "high", "low", "close", "volume"])
        for col in ["open", "high", "low", "close", "volume"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        return df.iloc[:-1].reset_index(drop=True)
    except:
        return None

def find_similar_historical_patterns(df, window_size=15):
    """
    Összehasonlítja a legfrissebb mintát a múltbeli gyertyákkal (akár ezer gyertya visszamenőleg),
    hogy találjon hasonló ármozgási struktúrákat, és kiszámolja a történelmi utókövetési irányt.
    """
    if df is None or len(df) < window_size * 3:
        return {"matched_pattern_desc": "Nincs elegendő adat", "score_modifier": 0, "similarity": 0}

    closes = df["close"].values
    
    # A legutolsó minta (aktuális mozgás) normalize-álva (százalékos elmozdulás a kezdőponthoz képest)
    recent_window = closes[-window_size:]
    recent_normalized = (recent_window - recent_window[0]) / recent_window[0]

    best_correlation = -1.0
    subsequent_movements = []

    # Végigmegyünk a múltbeli gyertyákon (több ezer gyertya visszamenőleg)
    for i in range(window_size, len(df) - window_size - 5):
        historical_window = closes[i : i + window_size]
        hist_normalized = (historical_window - historical_window[0]) / historical_window[0]

        # Korreláció számítása a jelenlegi minta és a múltbeli minta között
        correlation = np.corrcoef(recent_normalized, hist_normalized)[0, 1]
        
        if np.isnan(correlation):
            continue

        if correlation > best_correlation:
            best_correlation = correlation
            # Megnézzük, mi történt a múltban *a minta után* (következő 5 gyertya)
            future_price_after_pattern = closes[i + window_size + 5]
            pattern_end_price = historical_window[-1]
            move_pct = ((future_price_after_pattern - pattern_end_price) / pattern_end_price) * 100
            subsequent_movements.append(move_pct)

    score_modifier = 0
    desc = "Gyenge mintaegyezés"
    
    if best_correlation > 0.75 and subsequent_movements:
        avg_future_move = np.mean(subsequent_movements)
        if avg_future_move > 0.5:
            score_modifier = 25
            desc = f"🟢 Múltbeli minták alapján BIKA folytatás várható (Hasonlóság: {best_correlation*100:.1f}%)"
        elif avg_future_move < -0.5:
            score_modifier = -25
            desc = f"🔴 Múltbeli minták alapján MEDVE esés várható (Hasonlóság: {best_correlation*100:.1f}%)"
        else:
            desc = f"⚖️ Oldalazó múltbeli minta (Hasonlóság: {best_correlation*100:.1f}%)"
    else:
        desc = f"Nincs erős történelmi egyezés (Legjobb korreláció: {best_correlation*100:.1f}%)"

    return {
        "matched_pattern_desc": desc,
        "score_modifier": score_modifier,
        "similarity": float(best_correlation) if not np.isnan(best_correlation) else 0.0
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
        status, modifier = "🚀 ELLENÁLLÁS ÁTTÖRÉS", +20
    elif last_close <= support:
        status, modifier = "⚠️ TÁMASZ ÁTTÖRÉS", -20

    return {"support": support, "resistance": resistance, "status": status, "modifier": modifier}

def analyze_btc_timeframe(timeframe, lookback_limit):
    df = fetch_deep_btc_candles(timeframe, limit=lookback_limit)
    if df is None:
        return {"direction": "WAIT", "score": 0, "trend_desc": "Adat hiba"}

    last_close = df["close"].iloc[-1]
    score = 0

    # Történelmi minta-összehasonlítás meghívása (több ezer gyertya visszamenőleg)
    pattern_match = find_similar_historical_patterns(df, window_size=15)
    score += pattern_match["score_modifier"]

    # Támasz / Ellenállás vizsgálat
    sr = analyze_btc_structure(df)
    score += sr["modifier"]

    direction = "LONG" if score >= 25 else ("SHORT" if score <= -25 else "WAIT")

    return {
        "direction": direction,
        "score": score,
        "price": last_close,
        "sr": sr,
        "pattern_match": pattern_match
    }


# ============================================================
# 7. GROQ AI INTEGRÁCIÓ
# ============================================================

def run_groq_btc_analysis(market_data, tf_data, user_news_input):
    key = get_secret("GROQ_API_KEY")
    if not key:
        return None, "GROQ_API_KEY hiányzik a Secrets-ből!"

    client = OpenAI(api_key=key, base_url="https://api.groq.com/openai/v1")

    payload = {
        "derivatives_market": market_data,
        "historical_pattern_matching": tf_data,
        "macro_and_news_input": user_news_input if user_news_input else "Nincs megadva egyedi hír, értékeld a jelenlegi makro piaci hangulatot."
    }

    system_prompt = """
You are SALIGA AI PRO, an elite Bitcoin (BTC) historical pattern and news sentiment analyst.
Analyze OKX derivatives (OI, funding rate, delta), support/resistance levels, historical pattern matching results across thousands of candles, and macro inputs.
Provide a concise, professional trading intelligence report in valid JSON format only.

Required JSON structure:
{
  "bias": "BULLISH / BEARISH / NEUTRAL",
  "confidence_score": 85,
  "executive_summary": "Professional summary combining historical patterns, S/R, and news/macro sentiment.",
  "news_and_macro_impact": "Detailed breakdown of how current macroeconomic data or news affect BTC price action.",
  "actionable_insight": "Specific recommendation on long/short positioning or waiting.",
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
            max_tokens=1800,
            response_format={"type": "json_object"}
        )
        content = response.choices[0].message.content
        return json.loads(content), None
    except Exception as e:
        return None, str(e)

def run_full_cycle(lookback_candles):
    market = fetch_btc_market_data()
    tf_results = {}
    for tf_key in TIMEFRAMES.keys():
        tf_results[tf_key] = analyze_btc_timeframe(tf_key, lookback_candles)
    
    return {
        "timestamp": timestamp(),
        "market": market,
        "timeframes": tf_results
    }


# ============================================================
# 8. STREAMLIT UI (AUTOMATIKUS HÁTTÉRFISSÍTÉSSEL)
# ============================================================

st.title("⚡ SALIGA AI PRO 18.1 — HISTORICAL PATTERN BTC RADAR")
st.caption("OKX OI + FUNDING + VISSZAMENŐLEGES MINTAKERESÉS (TÖBB EZER GYERTYA) + MAKRO HÍREK + GROQ AI")

if exchange_error:
    st.error(f"OKX Hiba: {exchange_error}")

# Beállítások a visszamenőleges gyertyák számához
lookback_candles = st.sidebar.slider("Visszamenőleges gyertyák száma a mintákhoz", min_value=500, max_value=5000, value=st.session_state["lookback_candles"], step=500)
st.session_state["lookback_candles"] = lookback_candles

c_ctrl1, c_ctrl2, c_ctrl3, c_ctrl4 = st.columns(4)
with c_ctrl1:
    if st.button("🔄 AZONNALI FRISSÍTÉS", use_container_width=True):
        st.session_state["btc_analysis"] = run_full_cycle(lookback_candles)
        st.session_state["last_refresh"] = time.time()
with c_ctrl2:
    auto_refresh_active = st.checkbox("🟢 Automatikus Háttérfrissítés", value=st.session_state["auto_refresh_active"])
    st.session_state["auto_refresh_active"] = auto_refresh_active
with c_ctrl3:
    refresh_mins = st.selectbox("Frissítési Időköz", [1, 3, 5, 10, 15], index=2)
    st.session_state["refresh_interval"] = refresh_mins * 60
with c_ctrl4:
    st.metric("Utolsó frissítés", datetime.now().strftime("%H:%M:%S"))

# Hírbeviteli mező
st.markdown("### 📰 Napi Makro & Hír Bevitele (Opcionális)")
user_news_input = st.text_area(
    "Írd be ide a mai legfontosabb híreket, makro adatokat:",
    placeholder="Pl.: CPI adatok érkeznek ma, a piaci hangulat feszült..."
)

if st.button("🧠 GROQ AI MAKRO & MINTAELEMZÉS Futtatása", use_container_width=True):
    if st.session_state["btc_analysis"]:
        with st.spinner("Groq AI elemzi a múltbeli mintákat és a friss adatokat..."):
            res, err = run_groq_btc_analysis(
                st.session_state["btc_analysis"]["market"],
                st.session_state["btc_analysis"]["timeframes"],
                user_news_input
            )
            st.session_state["ai_result"] = res
            st.session_state["ai_error"] = err
    else:
        st.warning("Előbb töltsd be az adatokat!")

if not st.session_state["btc_analysis"]:
    st.session_state["btc_analysis"] = run_full_cycle(lookback_candles)
    st.session_state["last_refresh"] = time.time()

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
# 10. HISTORICAL PATTERN MATCHING RADAR PANEL
# ============================================================

st.markdown("---")
st.header(f"🎯 TÖRTÉNELMI MINTA-ÖSSZEHASONLÍTÓ RADAR (Visszanézve: {lookback_candles} gyertya)")

h1_info = data.get("timeframes", {}).get("H1", {})
sr = h1_info.get("sr", {})
pattern_match = h1_info.get("pattern_match", {})
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
    st.metric("H1 Minta Pontszám", h1_info.get("score", 0))

with col_b:
    st.info(
        f"🔍 **Múltbeli Minta Elemzés (H1):**\n"
        f"{pattern_match.get('matched_pattern_desc', 'N/A')}\n\n"
        f"🧱 **H1 Ellenállás:** `{fmt(sr.get('resistance'), 2)}`\n\n"
        f"🛡️ **H1 Támasz:** `{fmt(sr.get('support'), 2)}`"
    )

st.markdown("---")


# ============================================================
# 11. GROQ AI JELENTÉS
# ============================================================

st.header("🧠 GROQ AI MINTAELEMZÉSI & MAKRO JELENTÉS")

ai_res = st.session_state["ai_result"]
ai_err = st.session_state["ai_error"]

if ai_err:
    st.error(f"AI Hiba: {ai_err}")

if ai_res:
    st.subheader(f"AI Piaci Irány: {ai_res.get('bias', 'N/A')} (Bizalom: {ai_res.get('confidence_score', 'N/A')}%)")
    st.write(f"**Összegzés:** {ai_res.get('executive_summary', '')}")
    st.info(f"**Makro & Történelmi Minta Hatás:** {ai_res.get('news_and_macro_impact', '')}")
    st.success(f"**Ajánlott Művelet:** {ai_res.get('actionable_insight', '')}")
    
    with st.expander("⚠️ Kockázatok"):
        for risk in ai_res.get("risks", []):
            st.write(f"• {risk}")
else:
    st.info("Írd be fent a híreket (opcionális), majd kattints a Groq AI elemzés gombra.")


# ============================================================
# 12. MULTI-TIMEFRAME TÁBLÁZAT
# ============================================================

st.markdown("---")
st.subheader("🧭 BTC Időkeretek & Minta-egyezési Összegzés")

tf_rows = []
for tf_name in TIMEFRAMES.keys():
    res = data["timeframes"].get(tf_name, {})
    pm = res.get("pattern_match", {})
    tf_rows.append({
        "Időkeret": tf_name,
        "Irány": res.get("direction", "WAIT"),
        "Pontszám": res.get("score", 0),
        "Múltbeli Minta Eredmény": pm.get("matched_pattern_desc", "N/A"),
        "Hasonlóság (Korreláció)": f"{pm.get('similarity', 0)*100:.1f}%"
    })

st.dataframe(pd.DataFrame(tf_rows), use_container_width=True, hide_index=True)


# ============================================================
# 13. AUTOMATIKUS HÁTTÉRFISSÍTÉSI LOGIKA
# ============================================================

if st.session_state["auto_refresh_active"]:
    interval = st.session_state["refresh_interval"]
    time.sleep(interval)
    st.session_state["btc_analysis"] = run_full_cycle(lookback_candles)
    st.session_state["last_refresh"] = time.time()
    st.rerun()
