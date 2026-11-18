import os
import time
import json
import sqlite3
import logging
from datetime import datetime, timezone

import ccxt
import numpy as np
import pandas as pd
import requests
import streamlit as st
import plotly.graph_objects as go

try:
    from streamlit_autorefresh import st_autorefresh
except ImportError:
    st_autorefresh = None


# =========================================================
# SALIGA AI 7.1
# OKX ONLY | INTERNAL MARKET INTELLIGENCE
# =========================================================

APP_NAME = "SALIGA AI 7.1"
DB_FILE = "saliga_market.db"

ASSETS = {
    "BTC": "BTC/USDT:USDT",
    "ETH": "ETH/USDT:USDT",
    "SOL": "SOL/USDT:USDT",
}

TIMEFRAMES = ["1m", "5m", "15m", "1h", "4h"]

logging.basicConfig(level=logging.ERROR)


# =========================================================
# STREAMLIT CONFIG
# =========================================================

st.set_page_config(
    page_title=APP_NAME,
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded"
)


# =========================================================
# LIGHT THEME
# =========================================================

st.markdown("""
<style>

.stApp {
    background-color: #f4f7fb;
    color: #172033;
}

header[data-testid="stHeader"] {
    background-color: #f4f7fb;
}

section[data-testid="stSidebar"] {
    background-color: #ffffff;
    border-right: 1px solid #dce3ed;
}

h1, h2, h3, h4 {
    color: #172033 !important;
}

p, label, span, div {
    color: inherit;
}

[data-testid="stMetric"] {
    background: #ffffff;
    border: 1px solid #dce3ed;
    padding: 15px;
    border-radius: 12px;
}

[data-testid="stMetricValue"] {
    color: #172033 !important;
}

[data-testid="stMetricLabel"] {
    color: #64748b !important;
}

.stButton button {
    border-radius: 9px;
    border: 1px solid #cbd5e1;
    background: #ffffff;
    color: #172033;
}

.stButton button:hover {
    border-color: #2563eb;
    color: #2563eb;
}

.long-box {
    background: #e9f9ef;
    border-left: 5px solid #16a34a;
    padding: 14px;
    border-radius: 8px;
    margin-bottom: 8px;
}

.short-box {
    background: #fff0f0;
    border-left: 5px solid #dc2626;
    padding: 14px;
    border-radius: 8px;
    margin-bottom: 8px;
}

.neutral-box {
    background: #edf2f7;
    border-left: 5px solid #64748b;
    padding: 14px;
    border-radius: 8px;
    margin-bottom: 8px;
}

.small-text {
    font-size: 12px;
    color: #64748b;
}

</style>
""", unsafe_allow_html=True)


# =========================================================
# DATABASE
# =========================================================

def get_db():

    conn = sqlite3.connect(
        DB_FILE,
        check_same_thread=False,
        timeout=30
    )

    conn.execute("PRAGMA journal_mode=WAL")

    return conn


def migrate_database():

    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS oi_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            timestamp INTEGER,
            oi REAL
        )
    """)

    cur.execute("PRAGMA table_info(oi_history)")
    columns = [row[1] for row in cur.fetchall()]

    # Régi adatbázis: open_interest oszlop
    if "oi" not in columns:

        cur.execute("""
            ALTER TABLE oi_history
            ADD COLUMN oi REAL
        """)

        columns.append("oi")

    if "open_interest" in columns:

        cur.execute("""
            UPDATE oi_history
            SET oi = open_interest
            WHERE oi IS NULL
        """)

    # Régi timestamp mező kezelése
    if "timestamp" not in columns:

        cur.execute("""
            ALTER TABLE oi_history
            ADD COLUMN timestamp INTEGER
        """)

        columns.append("timestamp")

    if "ts" in columns:

        cur.execute("""
            UPDATE oi_history
            SET timestamp = ts
            WHERE timestamp IS NULL
        """)

    # Régi szimbólummező ellenőrzése
    if "symbol" not in columns:

        cur.execute("""
            ALTER TABLE oi_history
            ADD COLUMN symbol TEXT
        """)

    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_oi_symbol_time
        ON oi_history(symbol, timestamp)
    """)

    conn.commit()
    conn.close()


def save_oi(symbol, value):

    if value is None:
        return

    conn = get_db()

    now = int(time.time())

    conn.execute("""
        INSERT INTO oi_history(symbol, timestamp, oi)
        VALUES (?, ?, ?)
    """, (symbol, now, float(value)))

    conn.commit()
    conn.close()


def previous_oi(symbol, minutes=15):

    conn = get_db()

    cutoff = int(time.time()) - minutes * 60

    row = conn.execute("""
        SELECT oi, timestamp
        FROM oi_history
        WHERE symbol = ?
        AND timestamp <= ?
        AND oi IS NOT NULL
        ORDER BY timestamp DESC
        LIMIT 1
    """, (symbol, cutoff)).fetchone()

    conn.close()

    if row:
        return row[0], row[1]

    return None, None


def get_oi_history(symbol, limit=100):

    conn = get_db()

    rows = conn.execute("""
        SELECT timestamp, oi
        FROM oi_history
        WHERE symbol = ?
        AND oi IS NOT NULL
        ORDER BY timestamp DESC
        LIMIT ?
    """, (symbol, limit)).fetchall()

    conn.close()

    return rows


# =========================================================
# START DATABASE MIGRATION
# =========================================================

try:
    migrate_database()
except Exception as e:
    st.error(f"Adatbázis inicializálási hiba: {e}")
    st.stop()


# =========================================================
# OKX CONNECTION
# =========================================================

@st.cache_resource
def get_exchange():

    exchange = ccxt.okx({
        "enableRateLimit": True,
        "timeout": 20000,
        "options": {
            "defaultType": "swap"
        }
    })

    exchange.load_markets()

    return exchange


def fetch_ohlcv(symbol, timeframe="15m", limit=150):

    exchange = get_exchange()

    data = exchange.fetch_ohlcv(
        symbol,
        timeframe=timeframe,
        limit=limit
    )

    df = pd.DataFrame(
        data,
        columns=[
            "timestamp",
            "open",
            "high",
            "low",
            "close",
            "volume"
        ]
    )

    df["datetime"] = pd.to_datetime(
        df["timestamp"],
        unit="ms",
        utc=True
    )

    return df


def fetch_ticker(symbol):

    exchange = get_exchange()

    return exchange.fetch_ticker(symbol)


def fetch_orderbook(symbol):

    exchange = get_exchange()

    return exchange.fetch_order_book(
        symbol,
        limit=20
    )


def fetch_trades(symbol):

    exchange = get_exchange()

    return exchange.fetch_trades(
        symbol,
        limit=300
    )


def fetch_open_interest(symbol):

    exchange = get_exchange()

    try:

        result = exchange.fetch_open_interest(symbol)

        value = result.get("openInterestAmount")

        if value is None:
            value = result.get("openInterestValue")

        if value is None:
            value = result.get("openInterest")

        if value is None:
            info = result.get("info", {})

            value = (
                info.get("oi")
                or info.get("oiCcy")
                or info.get("oiUsd")
            )

        if value is None:
            return None

        return float(value)

    except Exception:
        return None


def fetch_funding(symbol):

    exchange = get_exchange()

    try:

        result = exchange.fetch_funding_rate(symbol)

        return float(result.get("fundingRate", 0))

    except Exception:
        return None


# =========================================================
# INDICATORS
# =========================================================

def calculate_indicators(df):

    df = df.copy()

    df["SMA20"] = df["close"].rolling(20).mean()
    df["SMA50"] = df["close"].rolling(50).mean()

    delta = df["close"].diff()

    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)

    avg_gain = gain.rolling(14).mean()
    avg_loss = loss.rolling(14).mean()

    rs = avg_gain / avg_loss.replace(0, np.nan)

    df["RSI"] = 100 - (100 / (1 + rs))

    high_low = df["high"] - df["low"]
    high_close = abs(df["high"] - df["close"].shift())
    low_close = abs(df["low"] - df["close"].shift())

    tr = pd.concat(
        [high_low, high_close, low_close],
        axis=1
    ).max(axis=1)

    df["ATR"] = tr.rolling(14).mean()

    df["volume_average"] = df["volume"].rolling(20).mean()

    return df


# =========================================================
# ORDER BOOK ANALYSIS
# =========================================================

def analyze_orderbook(symbol):

    try:

        book = fetch_orderbook(symbol)

        bids = book["bids"][:20]
        asks = book["asks"][:20]

        bid_volume = sum(
            price * amount
            for price, amount in bids
        )

        ask_volume = sum(
            price * amount
            for price, amount in asks
        )

        total = bid_volume + ask_volume

        if total == 0:
            return 0, 0, 0

        imbalance = (
            (bid_volume - ask_volume) / total
        )

        return (
            bid_volume,
            ask_volume,
            imbalance
        )

    except Exception:
        return 0, 0, 0


# =========================================================
# BUY / SELL FLOW
# =========================================================

def analyze_trades(symbol):

    try:

        trades = fetch_trades(symbol)

        buy_volume = 0
        sell_volume = 0

        for trade in trades:

            amount = float(trade.get("amount") or 0)

            side = trade.get("side")

            if side == "buy":
                buy_volume += amount

            elif side == "sell":
                sell_volume += amount

        total = buy_volume + sell_volume

        if total == 0:
            return 0, 0, 0

        delta = buy_volume - sell_volume

        delta_percent = (
            delta / total
        ) * 100

        return (
            buy_volume,
            sell_volume,
            delta_percent
        )

    except Exception:
        return 0, 0, 0


# =========================================================
# MARKET STRUCTURE
# =========================================================

def analyze_structure(df):

    if len(df) < 30:
        return "Nincs elegendő adat"

    recent = df.tail(20)

    highs = recent["high"]
    lows = recent["low"]

    higher_high = highs.iloc[-1] > highs.iloc[0]
    higher_low = lows.iloc[-1] > lows.iloc[0]

    lower_high = highs.iloc[-1] < highs.iloc[0]
    lower_low = lows.iloc[-1] < lows.iloc[0]

    if higher_high and higher_low:
        return "Emelkedő piaci struktúra"

    if lower_high and lower_low:
        return "Csökkenő piaci struktúra"

    return "Oldalazó / vegyes struktúra"


def detect_liquidity_sweep(df):

    if len(df) < 25:
        return "Nincs adat"

    previous = df.iloc[-21:-1]
    current = df.iloc[-1]

    old_high = previous["high"].max()
    old_low = previous["low"].min()

    if (
        current["high"] > old_high
        and current["close"] < old_high
    ):
        return "Felső likviditás kisöprés"

    if (
        current["low"] < old_low
        and current["close"] > old_low
    ):
        return "Alsó likviditás kisöprés"

    return "Nem észlelhető egyértelmű sweep"


# =========================================================
# OI ANALYSIS
# =========================================================

def analyze_oi(symbol, current_oi, current_price):

    if current_oi is None:
        return {
            "change": None,
            "state": "OI adat nem elérhető"
        }

    old_oi, old_timestamp = previous_oi(
        symbol,
        minutes=15
    )

    save_oi(symbol, current_oi)

    if old_oi is None or old_oi == 0:

        return {
            "change": None,
            "state": "OI adatgyűjtés folyamatban"
        }

    change = (
        (current_oi - old_oi) / abs(old_oi)
    ) * 100

    if change > 0.15:
        state = "Nyitott pozíciók épülnek"

    elif change < -0.15:
        state = "Pozíciók záródnak"

    else:
        state = "OI oldalazás"

    return {
        "change": change,
        "state": state
    }


# =========================================================
# INTERNAL MARKET INTELLIGENCE
# =========================================================

def internal_analysis(symbol, ticker, df):

    df = calculate_indicators(df)

    last = df.iloc[-1]
    previous = df.iloc[-2]

    price = float(last["close"])

    price_change = (
        (price - float(previous["close"]))
        / float(previous["close"])
    ) * 100

    rsi = float(last["RSI"]) if pd.notna(last["RSI"]) else 50

    sma20 = float(last["SMA20"]) if pd.notna(last["SMA20"]) else price
    sma50 = float(last["SMA50"]) if pd.notna(last["SMA50"]) else price

    bid_volume, ask_volume, imbalance = analyze_orderbook(symbol)

    buy_volume, sell_volume, delta = analyze_trades(symbol)

    oi_value = fetch_open_interest(symbol)

    oi = analyze_oi(
        symbol,
        oi_value,
        price
    )

    funding = fetch_funding(symbol)

    score = 0

    reasons = []

    if price > sma20:
        score += 10
        reasons.append("Ár az SMA20 felett")

    else:
        score -= 10
        reasons.append("Ár az SMA20 alatt")

    if sma20 > sma50:
        score += 15
        reasons.append("Pozitív SMA20/SMA50 viszony")

    else:
        score -= 15
        reasons.append("Negatív SMA20/SMA50 viszony")

    if rsi > 55:
        score += 10
        reasons.append("Erősödő RSI")

    elif rsi < 45:
        score -= 10
        reasons.append("Gyengülő RSI")

    if delta > 5:
        score += 15
        reasons.append("Vételi dominancia a kötésekben")

    elif delta < -5:
        score -= 15
        reasons.append("Eladási dominancia a kötésekben")

    if imbalance > 0.10:
        score += 10
        reasons.append("Vevői order book fölény")

    elif imbalance < -0.10:
        score -= 10
        reasons.append("Eladói order book fölény")

    if price_change > 0.15:
        score += 10

    elif price_change < -0.15:
        score -= 10

    if oi["change"] is not None:

        if oi["change"] > 0.15 and price_change > 0:
            score += 10
            reasons.append("Áremelkedés és OI-növekedés")

        elif oi["change"] > 0.15 and price_change < 0:
            score -= 10
            reasons.append("Áresés és OI-növekedés")

    score = max(-100, min(100, score))

    if score >= 35:
        signal = "LONG"
        status = "Vevői fölény"

    elif score <= -35:
        signal = "SHORT"
        status = "Eladói fölény"

    else:
        signal = "NEUTRAL"
        status = "Nincs egyértelmű irány"

    structure = analyze_structure(df)

    sweep = detect_liquidity_sweep(df)

    if oi["change"] is not None:

        if oi["change"] > 0.15 and price_change > 0:
            flow = "LONG ÉPÜLÉS – becslés"

        elif oi["change"] > 0.15 and price_change < 0:
            flow = "SHORT ÉPÜLÉS – becslés"

        elif oi["change"] < -0.15 and price_change > 0:
            flow = "SHORT ZÁRÁSOK – becslés"

        elif oi["change"] < -0.15 and price_change < 0:
            flow = "LONG ZÁRÁSOK – becslés"

        else:
            flow = "Nincs egyértelmű pozícióáramlás"

    else:
        flow = "OI adatgyűjtés alatt"

    return {
        "price": price,
        "change": price_change,
        "rsi": rsi,
        "sma20": sma20,
        "sma50": sma50,
        "score": score,
        "signal": signal,
        "status": status,
        "flow": flow,
        "oi": oi,
        "oi_value": oi_value,
        "funding": funding,
        "delta": delta,
        "buy_volume": buy_volume,
        "sell_volume": sell_volume,
        "imbalance": imbalance,
        "bid_volume": bid_volume,
        "ask_volume": ask_volume,
        "structure": structure,
        "sweep": sweep,
        "reasons": reasons,
        "df": df
    }


# =========================================================
# GROQ EXTERNAL AI
# =========================================================

def get_groq_key():

    try:
        return st.secrets.get("GROQ_API_KEY", "")
    except Exception:
        return os.getenv("GROQ_API_KEY", "")


def groq_analysis(results):

    api_key = get_groq_key()

    if not api_key:
        return "A Groq API-kulcs nincs beállítva."

    context = {}

    for asset, data in results.items():

        context[asset] = {
            "price": data["price"],
            "price_change": data["change"],
            "signal": data["signal"],
            "score": data["score"],
            "rsi": data["rsi"],
            "oi_state": data["oi"]["state"],
            "oi_change": data["oi"]["change"],
            "flow": data["flow"],
            "funding": data["funding"],
            "delta": data["delta"],
            "orderbook_imbalance": data["imbalance"],
            "structure": data["structure"],
            "liquidity_sweep": data["sweep"],
        }

    prompt = f"""
Te a SALIGA AI külső piaci elemzője vagy.

Értékeld az alábbi OKX piaci adatokat.

Feladat:
1. BTC, ETH és SOL irányának értékelése.
2. Long és short pozícióépülés vizsgálata.
3. OI és ármozgás összhangja.
4. Vételi és eladási nyomás.
5. Eltérő vagy ellentmondó jelzések.
6. Kockázatok.
7. Rövid összefoglaló.

Ne találj ki adatokat.
Ne állítsd, hogy a becslés biztos.
Különítsd el a tényadatokat és az értelmezést.

Adatok:
{json.dumps(context, ensure_ascii=False, default=str)}
"""

    try:

        response = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json"
            },
            json={
                "model": "openai/gpt-oss-120b",
                "messages": [
                    {
                        "role": "system",
                        "content": "You are a careful cryptocurrency market analyst."
                    },
                    {
                        "role": "user",
                        "content": prompt
                    }
                ],
                "temperature": 0.3,
                "max_completion_tokens": 1500
            },
            timeout=60
        )

        response.raise_for_status()

        return response.json()["choices"][0]["message"]["content"]

    except Exception as e:

        return f"Groq elemzési hiba: {e}"


# =========================================================
# CHATBOT
# =========================================================

def groq_chat(question, results, history):

    api_key = get_groq_key()

    if not api_key:
        return "A Groq API-kulcs nincs beállítva."

    market_context = {}

    for asset, data in results.items():

        market_context[asset] = {
            "price": data["price"],
            "signal": data["signal"],
            "score": data["score"],
            "flow": data["flow"],
            "oi": data["oi"],
            "rsi": data["rsi"],
            "delta": data["delta"],
            "funding": data["funding"]
        }

    messages = [
        {
            "role": "system",
            "content": (
                "Te a SALIGA AI kriptopiaci asszisztense vagy. "
                "Magyarul válaszolj. "
                "Csak a megadott adatokat tekintsd aktuális piaci adatnak. "
                "Ne találj ki árakat vagy eseményeket."
                "\nPiaci adatok: "
                + json.dumps(
                    market_context,
                    ensure_ascii=False,
                    default=str
                )
            )
        }
    ]

    messages.extend(history[-8:])

    messages.append({
        "role": "user",
        "content": question
    })

    try:

        response = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json"
            },
            json={
                "model": "openai/gpt-oss-120b",
                "messages": messages,
                "temperature": 0.3,
                "max_completion_tokens": 1200
            },
            timeout=60
        )

        response.raise_for_status()

        return response.json()["choices"][0]["message"]["content"]

    except Exception as e:

        return f"Chatbot hiba: {e}"


# =========================================================
# CHART
# =========================================================

def show_chart(asset, data):

    df = data["df"]

    fig = go.Figure()

    fig.add_trace(
        go.Candlestick(
            x=df["datetime"],
            open=df["open"],
            high=df["high"],
            low=df["low"],
            close=df["close"],
            name=asset
        )
    )

    fig.add_trace(
        go.Scatter(
            x=df["datetime"],
            y=df["SMA20"],
            name="SMA20",
            line=dict(width=1)
        )
    )

    fig.add_trace(
        go.Scatter(
            x=df["datetime"],
            y=df["SMA50"],
            name="SMA50",
            line=dict(width=1)
        )
    )

    fig.update_layout(
        height=450,
        template="plotly_white",
        xaxis_rangeslider_visible=False,
        margin=dict(l=10, r=10, t=30, b=10)
    )

    st.plotly_chart(
        fig,
        use_container_width=True
    )


# =========================================================
# SESSION STATE
# =========================================================

if "results" not in st.session_state:
    st.session_state.results = {}

if "chat" not in st.session_state:
    st.session_state.chat = []

if "last_internal" not in st.session_state:
    st.session_state.last_internal = None

if "last_groq" not in st.session_state:
    st.session_state.last_groq = None

if "last_errors" not in st.session_state:
    st.session_state.last_errors = {}


# =========================================================
# SIDEBAR
# =========================================================

st.sidebar.title("SALIGA AI")

st.sidebar.caption("Market Intelligence 7.1")

selected_assets = st.sidebar.multiselect(
    "Elemzett eszközök",
    list(ASSETS.keys()),
    default=["BTC", "ETH", "SOL"]
)

timeframe = st.sidebar.selectbox(
    "Elemzési idősík",
    TIMEFRAMES,
    index=2
)

refresh_seconds = st.sidebar.slider(
    "Automatikus frissítés (másodperc)",
    30,
    300,
    60,
    30
)

auto_refresh = st.sidebar.toggle(
    "Automatikus frissítés",
    value=True
)

run_groq_auto = st.sidebar.toggle(
    "Groq automatikus külső elemzés",
    value=False
)

if st.sidebar.button("MOSTANI ELEMZÉS FRISSÍTÉSE"):
    st.session_state.force_refresh = True
else:
    if "force_refresh" not in st.session_state:
        st.session_state.force_refresh = False


# =========================================================
# AUTO REFRESH
# =========================================================

if auto_refresh and st_autorefresh is not None:

    st_autorefresh(
        interval=refresh_seconds * 1000,
        key="saliga_refresh"
    )


# =========================================================
# HEADER
# =========================================================

st.title("📊 SALIGA AI – Market Intelligence")

st.caption(
    "OKX ONLY | Internal Engine + External AI"
)

st.info(
    "A belső elemzés piaci adatokból számított becsléseket használ. "
    "A jelzések nem garantálnak kereskedési eredményt."
)


# =========================================================
# DATA COLLECTION
# =========================================================

should_update = (
    not st.session_state.results
    or st.session_state.force_refresh
)

if auto_refresh:
    should_update = True

if selected_assets:

    if should_update:

        new_results = {}
        errors = {}

        progress = st.progress(0)

        for index, asset in enumerate(selected_assets):

            symbol = ASSETS[asset]

            try:

                ticker = fetch_ticker(symbol)

                df = fetch_ohlcv(
                    symbol,
                    timeframe,
                    150
                )

                if df.empty:
                    raise ValueError("Üres OHLCV adat")

                result = internal_analysis(
                    symbol,
                    ticker,
                    df
                )

                new_results[asset] = result

            except Exception as e:

                errors[asset] = str(e)

            progress.progress(
                (index + 1) / len(selected_assets)
            )

        progress.empty()

        if new_results:

            st.session_state.results = new_results

            st.session_state.last_internal = (
                datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            )

            st.session_state.last_errors = errors

            st.session_state.force_refresh = False

            if run_groq_auto:
                st.session_state.last_groq = groq_analysis(
                    new_results
                )

        else:

            st.session_state.last_errors = errors

    else:

        st.session_state.force_refresh = False

else:

    st.warning("Válassz legalább egy eszközt.")


# =========================================================
# RESULTS
# =========================================================

results = st.session_state.results

if results:

    st.success(
        "Utolsó belső elemzés: "
        + str(st.session_state.last_internal)
    )

    for asset in selected_assets:

        if asset not in results:
            continue

        data = results[asset]

        st.markdown("---")

        st.header(f"{asset} – Piaci elemzés")

        c1, c2, c3, c4 = st.columns(4)

        c1.metric(
            "Ár",
            f"{data['price']:,.4f}"
        )

        c2.metric(
            "Árváltozás",
            f"{data['change']:+.2f}%"
        )

        c3.metric(
            "Belső pontszám",
            f"{data['score']:+d}/100"
        )

        c4.metric(
            "RSI",
            f"{data['rsi']:.1f}"
        )

        if data["signal"] == "LONG":

            st.markdown(
                f"""
                <div class="long-box">
                <b>🟢 LONG – {data['status']}</b><br>
                Belső pontszám: {data['score']}
                </div>
                """,
                unsafe_allow_html=True
            )

        elif data["signal"] == "SHORT":

            st.markdown(
                f"""
                <div class="short-box">
                <b>🔴 SHORT – {data['status']}</b><br>
                Belső pontszám: {data['score']}
                </div>
                """,
                unsafe_allow_html=True
            )

        else:

            st.markdown(
                f"""
                <div class="neutral-box">
                <b>⚪ SEMLEGES – {data['status']}</b><br>
                Belső pontszám: {data['score']}
                </div>
                """,
                unsafe_allow_html=True
            )

        # POSITION FLOW

        st.subheader("Pozícióáramlás")

        st.write(data["flow"])

        st.write(
            f"OI állapot: {data['oi']['state']}"
        )

        if data["oi"]["change"] is not None:

            st.write(
                f"OI változás: {data['oi']['change']:+.3f}%"
            )

        st.write(
            f"Vételi volumen: {data['buy_volume']:.4f}"
        )

        st.write(
            f"Eladási volumen: {data['sell_volume']:.4f}"
        )

        st.write(
            f"Becsült delta: {data['delta']:+.2f}%"
        )

        st.write(
            f"Order book imbalance: {data['imbalance']:+.3f}"
        )

        if data["funding"] is not None:

            st.write(
                f"Funding: {data['funding'] * 100:.4f}%"
            )

        st.write(
            f"Piaci struktúra: {data['structure']}"
        )

        st.write(
            f"Likviditás: {data['sweep']}"
        )

        with st.expander("Belső elemzés indokai"):

            for reason in data["reasons"]:
                st.write("• " + reason)

        with st.expander("Grafikon"):

            show_chart(asset, data)

        with st.expander("OI-adatok"):

            history = get_oi_history(
                ASSETS[asset]
            )

            if history:

                history_df = pd.DataFrame(
                    history,
                    columns=["timestamp", "oi"]
                )

                history_df["datetime"] = pd.to_datetime(
                    history_df["timestamp"],
                    unit="s"
                )

                st.line_chart(
                    history_df.set_index("datetime")["oi"]
                )

            else:

                st.info("Még nincs elegendő OI-adat.")


# =========================================================
# DATA ERRORS
# =========================================================

if st.session_state.last_errors:

    st.markdown("---")

    st.subheader("Adatlekérési hibák")

    for asset, error in st.session_state.last_errors.items():

        st.error(
            f"{asset}: {error}"
        )


# =========================================================
# GROQ EXTERNAL AI
# =========================================================

st.markdown("---")

st.header("🧠 SALIGA AI – Külső AI elemzés")

if st.button("KÜLSŐ AI ELEMZÉS INDÍTÁSA"):

    if results:

        with st.spinner("A Groq elemzi a piaci adatokat..."):

            st.session_state.last_groq = groq_analysis(
                results
            )

    else:

        st.warning("Nincs elérhető piaci adat.")


if st.session_state.last_groq:

    st.markdown(
        st.session_state.last_groq
    )


# =========================================================
# CHATBOT
# =========================================================

st.markdown("---")

st.header("💬 SALIGA AI – Kérdezz az elemzésről")

for message in st.session_state.chat:

    with st.chat_message(message["role"]):

        st.markdown(message["content"])


question = st.chat_input(
    "Kérdezz a BTC, ETH vagy SOL piacáról..."
)

if question:

    st.session_state.chat.append({
        "role": "user",
        "content": question
    })

    with st.chat_message("user"):
        st.markdown(question)

    with st.chat_message("assistant"):

        with st.spinner("SALIGA AI válaszol..."):

            answer = groq_chat(
                question,
                results,
                st.session_state.chat[:-1]
            )

            st.markdown(answer)

    st.session_state.chat.append({
        "role": "assistant",
        "content": answer
    })


# =========================================================
# FOOTER
# =========================================================

st.markdown("---")

st.caption(
    "SALIGA AI 7.1 | OKX ONLY | Internal Market Intelligence"
)

st.caption(
    "A pozícióépülés, zárás, delta és likviditási értelmezés "
    "becslés, nem közvetlenül igazolt tőzsdei pozícióadat."
)
