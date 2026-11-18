from __future__ import annotations

import os
import time
import sqlite3
import logging
from datetime import datetime, timezone

import ccxt
import feedparser
import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

try:
    from streamlit_autorefresh import st_autorefresh
except ImportError:
    st_autorefresh = None


# ============================================================
# SALIGA AI — MARKET INTELLIGENCE PRO
# OKX ONLY
# NO EMA
# NO SMA
# MULTI-TIMEFRAME MARKET STRUCTURE
# ============================================================

VERSION = "SALIGA AI PRO 8.0"

ASSETS = {
    "BTC": "BTC/USDT:USDT",
    "ETH": "ETH/USDT:USDT",
    "SOL": "SOL/USDT:USDT",
}

TIMEFRAMES = ["5m", "15m", "1h", "4h", "1d"]

DB_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "saliga_ai_history.db"
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("SALIGA_AI")


# ============================================================
# STREAMLIT CONFIGURATION
# ============================================================

st.set_page_config(
    page_title="SALIGA AI PRO",
    page_icon="📊",
    layout="wide"
)

st.markdown("""
<style>

.stApp {
    background-color: #f3f5f9;
    color: #172033;
}

[data-testid="stSidebar"] {
    background-color: #e8edf5;
}

.block-container {
    padding-top: 1.2rem;
    max-width: 1600px;
}

[data-testid="stMetric"] {
    background: white;
    border: 1px solid #dce3ed;
    padding: 12px;
    border-radius: 12px;
}

.signal-card {
    background: white;
    border: 1px solid #dce3ed;
    border-radius: 14px;
    padding: 18px;
}

.long {
    color: #07834c;
    font-weight: bold;
}

.short {
    color: #d12626;
    font-weight: bold;
}

.wait {
    color: #a66b00;
    font-weight: bold;
}

</style>
""", unsafe_allow_html=True)


# ============================================================
# DATABASE
# ============================================================

def init_database():

    with sqlite3.connect(DB_PATH) as db:

        db.execute("""
            CREATE TABLE IF NOT EXISTS market_history (

                id INTEGER PRIMARY KEY AUTOINCREMENT,

                symbol TEXT,
                timestamp INTEGER,

                open_interest REAL,
                price REAL,

                cvd REAL,
                delta REAL

            )
        """)

        db.execute("""
            CREATE INDEX IF NOT EXISTS
            idx_market_history_symbol_time

            ON market_history(symbol, timestamp)
        """)

        db.commit()


def save_market_snapshot(
    symbol,
    oi,
    price,
    cvd,
    delta
):

    if oi is None:
        return

    timestamp = int(time.time())

    with sqlite3.connect(DB_PATH) as db:

        db.execute("""
            INSERT INTO market_history
            (
                symbol,
                timestamp,
                open_interest,
                price,
                cvd,
                delta
            )

            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            symbol,
            timestamp,
            oi,
            price,
            cvd,
            delta
        ))

        db.execute("""
            DELETE FROM market_history
            WHERE timestamp < ?
        """, (timestamp - 60 * 86400,))

        db.commit()


def get_previous_snapshot(symbol, minutes=15):

    cutoff = int(time.time()) - minutes * 60

    with sqlite3.connect(DB_PATH) as db:

        row = db.execute("""
            SELECT
                open_interest,
                price,
                cvd,
                delta

            FROM market_history

            WHERE symbol = ?
            AND timestamp <= ?

            ORDER BY timestamp DESC

            LIMIT 1
        """, (symbol, cutoff)).fetchone()

    if not row:
        return None

    return {
        "oi": row[0],
        "price": row[1],
        "cvd": row[2],
        "delta": row[3]
    }


# ============================================================
# OKX CONNECTION
# ============================================================

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


@st.cache_data(ttl=15, show_spinner=False)
def get_ohlcv(symbol, timeframe, limit=350):

    exchange = get_exchange()

    candles = exchange.fetch_ohlcv(
        symbol,
        timeframe=timeframe,
        limit=limit
    )

    df = pd.DataFrame(
        candles,
        columns=[
            "timestamp",
            "open",
            "high",
            "low",
            "close",
            "volume"
        ]
    )

    df["timestamp"] = pd.to_datetime(
        df["timestamp"],
        unit="ms",
        utc=True
    )

    return df


@st.cache_data(ttl=15, show_spinner=False)
def get_ticker(symbol):

    return get_exchange().fetch_ticker(symbol)


@st.cache_data(ttl=15, show_spinner=False)
def get_orderbook(symbol):

    return get_exchange().fetch_order_book(
        symbol,
        limit=50
    )


@st.cache_data(ttl=15, show_spinner=False)
def get_trades(symbol):

    return get_exchange().fetch_trades(
        symbol,
        limit=300
    )


@st.cache_data(ttl=30, show_spinner=False)
def get_open_interest(symbol):

    exchange = get_exchange()

    try:

        data = exchange.fetch_open_interest(symbol)

        value = data.get("openInterestAmount")

        if value is None:
            value = data.get("openInterest")

        if value is None:
            return None

        return float(value)

    except Exception as error:

        logger.warning(
            "Open Interest error: %s",
            error
        )

        return None


@st.cache_data(ttl=30, show_spinner=False)
def get_funding(symbol):

    try:

        data = get_exchange().fetch_funding_rate(symbol)

        value = data.get("fundingRate")

        if value is None:
            return None

        return float(value)

    except Exception:

        return None


# ============================================================
# PRICE STRUCTURE
# ============================================================

def calculate_atr(df, period=14):

    previous_close = df["close"].shift(1)

    tr = pd.concat([

        df["high"] - df["low"],

        (df["high"] - previous_close).abs(),

        (df["low"] - previous_close).abs()

    ], axis=1).max(axis=1)

    return tr.rolling(period).mean()


def calculate_rsi(df, period=14):

    change = df["close"].diff()

    gain = change.clip(lower=0)

    loss = -change.clip(upper=0)

    avg_gain = gain.rolling(period).mean()

    avg_loss = loss.rolling(period).mean()

    rs = avg_gain / avg_loss.replace(0, np.nan)

    return 100 - (100 / (1 + rs))


def calculate_vwap(df):

    typical_price = (
        df["high"] +
        df["low"] +
        df["close"]
    ) / 3

    numerator = (
        typical_price * df["volume"]
    ).rolling(48).sum()

    denominator = (
        df["volume"]
    ).rolling(48).sum()

    return numerator / denominator.replace(0, np.nan)


def prepare_data(df):

    df = df.copy()

    df["atr"] = calculate_atr(df)

    df["rsi"] = calculate_rsi(df)

    df["vwap"] = calculate_vwap(df)

    df["volume_median"] = (
        df["volume"]
        .rolling(30)
        .median()
    )

    df["volume_ratio"] = (
        df["volume"] /
        df["volume_median"].replace(0, np.nan)
    )

    return df


# ============================================================
# MARKET STRUCTURE
# ============================================================

def detect_structure(df):

    if len(df) < 40:

        return {
            "bias": 0,
            "event": "Insufficient data",
            "support": None,
            "resistance": None
        }

    high = df["high"].to_numpy()

    low = df["low"].to_numpy()

    close = float(df["close"].iloc[-1])

    swing_high = max(high[-25:-1])

    swing_low = min(low[-25:-1])

    bias = 0

    event = "No confirmed structure break"

    if close > swing_high:

        bias = 2

        event = "BULLISH BOS"

    elif close < swing_low:

        bias = -2

        event = "BEARISH BOS"

    return {
        "bias": bias,
        "event": event,
        "support": swing_low,
        "resistance": swing_high
    }


# ============================================================
# LIQUIDITY SWEEPS
# ============================================================

def detect_liquidity_sweep(df):

    if len(df) < 30:

        return "Insufficient data"

    candle = df.iloc[-1]

    previous = df.iloc[-25:-1]

    previous_high = previous["high"].max()

    previous_low = previous["low"].min()

    if (
        candle["high"] > previous_high
        and candle["close"] < previous_high
    ):

        return "UPPER LIQUIDITY SWEEP"

    if (
        candle["low"] < previous_low
        and candle["close"] > previous_low
    ):

        return "LOWER LIQUIDITY SWEEP"

    return "No fresh liquidity sweep"


# ============================================================
# BUY / SELL FLOW
# ============================================================

def calculate_delta(trades):

    buy_volume = 0.0

    sell_volume = 0.0

    for trade in trades:

        amount = float(
            trade.get("amount") or 0
        )

        side = trade.get("side")

        if side == "buy":

            buy_volume += amount

        elif side == "sell":

            sell_volume += amount

    delta = buy_volume - sell_volume

    total = buy_volume + sell_volume

    buy_percentage = (
        buy_volume / total * 100
        if total > 0 else 50
    )

    return {
        "buy": buy_volume,
        "sell": sell_volume,
        "delta": delta,
        "buy_percentage": buy_percentage,
        "total": total
    }


# ============================================================
# ORDER BOOK
# ============================================================

def analyze_orderbook(book):

    bids = book.get("bids", [])

    asks = book.get("asks", [])

    bid_volume = sum(
        float(level[1])
        for level in bids[:20]
    )

    ask_volume = sum(
        float(level[1])
        for level in asks[:20]
    )

    total = bid_volume + ask_volume

    imbalance = (
        bid_volume / total * 100
        if total > 0 else 50
    )

    if imbalance > 55:

        state = "BUYER DOMINANCE"

    elif imbalance < 45:

        state = "SELLER DOMINANCE"

    else:

        state = "BALANCED ORDER BOOK"

    return {
        "bid": bid_volume,
        "ask": ask_volume,
        "imbalance": imbalance,
        "state": state
    }


# ============================================================
# OPEN INTEREST FLOW
# ============================================================

def analyze_oi(current_oi, previous, current_price):

    if current_oi is None or previous is None:

        return {
            "state": "COLLECTING OI HISTORY",
            "bias": 0,
            "change": None
        }

    old_oi = previous["oi"]

    old_price = previous["price"]

    if not old_oi or not old_price:

        return {
            "state": "INSUFFICIENT OI HISTORY",
            "bias": 0,
            "change": None
        }

    oi_change = (
        (current_oi - old_oi) /
        abs(old_oi) * 100
    )

    price_change = (
        (current_price - old_price) /
        old_price * 100
    )

    if oi_change > 0.15 and price_change > 0.05:

        state = "LONG BUILDUP — ESTIMATED"

        bias = 1

    elif oi_change > 0.15 and price_change < -0.05:

        state = "SHORT BUILDUP — ESTIMATED"

        bias = -1

    elif oi_change < -0.15 and price_change > 0.05:

        state = "SHORT CLOSING — ESTIMATED"

        bias = 1

    elif oi_change < -0.15 and price_change < -0.05:

        state = "LONG CLOSING — ESTIMATED"

        bias = -1

    else:

        state = "MIXED OI FLOW"

        bias = 0

    return {
        "state": state,
        "bias": bias,
        "change": oi_change,
        "price_change": price_change
    }


# ============================================================
# TRADE PLAN
# ============================================================

def calculate_trade_plan(
    side,
    price,
    atr,
    support,
    resistance,
    account,
    risk_percent
):

    if side not in ["LONG", "SHORT"]:

        return None

    if atr is None or not np.isfinite(atr) or atr <= 0:

        return None

    minimum_distance = max(
        atr * 1.5,
        price * 0.0015
    )

    if side == "LONG":

        stop = price - minimum_distance

        if support is not None and support < price:

            structural_stop = support - atr * 0.15

            stop = min(stop, structural_stop)

        distance = price - stop

        direction = 1

    else:

        stop = price + minimum_distance

        if resistance is not None and resistance > price:

            structural_stop = resistance + atr * 0.15

            stop = max(stop, structural_stop)

        distance = stop - price

        direction = -1

    if distance <= 0:

        return None

    risk_amount = account * risk_percent / 100

    position_size = risk_amount / distance

    return {

        "entry": price,

        "stop": stop,

        "target1": price + direction * distance,

        "target2": price + direction * distance * 2,

        "target3": price + direction * distance * 3,

        "risk_amount": risk_amount,

        "position_size": position_size,

        "notional": position_size * price,

        "risk_distance": distance

    }


# ============================================================
# MULTI-TIMEFRAME ANALYSIS
# ============================================================

def calculate_multitimeframe(symbol):

    result = {}

    for timeframe in TIMEFRAMES:

        df = prepare_data(
            get_ohlcv(symbol, timeframe)
        )

        result[timeframe] = {
            "data": df,
            "structure": detect_structure(df)
        }

    return result


# ============================================================
# INTERNAL INTELLIGENCE ENGINE
# ============================================================

def run_engine(asset, symbol, account, risk_percent):

    frames = calculate_multitimeframe(symbol)

    main = frames["15m"]["data"]

    ticker = get_ticker(symbol)

    price = float(
        ticker.get("last") or
        main["close"].iloc[-1]
    )

    current_oi = get_open_interest(symbol)

    trades = calculate_delta(
        get_trades(symbol)
    )

    book = analyze_orderbook(
        get_orderbook(symbol)
    )

    previous = get_previous_snapshot(
        symbol,
        15
    )

    oi = analyze_oi(
        current_oi,
        previous,
        price
    )

    save_market_snapshot(
        symbol,
        current_oi,
        price,
        None,
        trades["delta"]
    )

    structures = {
        tf: frames[tf]["structure"]
        for tf in TIMEFRAMES
    }

    sweep = detect_liquidity_sweep(main)

    last = main.iloc[-1]

    rsi = float(last["rsi"])

    atr = float(last["atr"])

    vwap = float(last["vwap"])

    score = 0.0

    weights = {
        "5m": 0.10,
        "15m": 0.15,
        "1h": 0.20,
        "4h": 0.30,
        "1d": 0.25
    }

    for timeframe, weight in weights.items():

        score += (
            structures[timeframe]["bias"]
            * weight
            * 12
        )

    score += np.clip(
        (trades["buy_percentage"] - 50) / 5,
        -2,
        2
    ) * 4

    score += np.clip(
        (book["imbalance"] - 50) / 5,
        -2,
        2
    ) * 2

    score += oi["bias"] * 5

    if price > vwap:

        score += 3

    else:

        score -= 3

    if rsi > 75:

        score -= 3

    elif rsi < 25:

        score += 3

    if sweep == "LOWER LIQUIDITY SWEEP":

        score += 3

    elif sweep == "UPPER LIQUIDITY SWEEP":

        score -= 3

    # Reduce the signal strength if major timeframes conflict.
    major_bias = [
        np.sign(structures["1h"]["bias"]),
        np.sign(structures["4h"]["bias"]),
        np.sign(structures["1d"]["bias"])
    ]

    conflict = (
        1 in major_bias and
        -1 in major_bias
    )

    if conflict:

        score *= 0.65

    if score >= 12:

        signal = "LONG"

    elif score <= -12:

        signal = "SHORT"

    else:

        signal = "WAIT"

    plan = calculate_trade_plan(
        signal,
        price,
        atr,
        structures["1h"]["support"],
        structures["1h"]["resistance"],
        account,
        risk_percent
    )

    return {

        "asset": asset,

        "price": price,

        "signal": signal,

        "score": score,

        "rsi": rsi,

        "atr": atr,

        "vwap": vwap,

        "frames": frames,

        "structures": structures,

        "oi": oi,

        "trades": trades,

        "book": book,

        "sweep": sweep,

        "plan": plan,

        "conflict": conflict

    }


# ============================================================
# NEWS
# ============================================================

@st.cache_data(ttl=300)
def get_news():

    feeds = [
        "https://www.coindesk.com/arc/outboundfeeds/rss/",
        "https://cointelegraph.com/rss"
    ]

    news = []

    for feed in feeds:

        try:

            parsed = feedparser.parse(feed)

            for item in parsed.entries[:8]:

                news.append({
                    "title": item.get("title", ""),
                    "link": item.get("link", ""),
                    "source": feed
                })

        except Exception:

            continue

    return news[:15]


# ============================================================
# GROQ AI
# ============================================================

def groq_chat(question, context):

    api_key = st.secrets.get(
        "GROQ_API_KEY",
        os.getenv("GROQ_API_KEY", "")
    )

    if not api_key:

        return (
            "A Groq API-kulcs nincs beállítva. "
            "A belső elemzőmotor ettől függetlenül működik."
        )

    try:

        from openai import OpenAI

        client = OpenAI(
            api_key=api_key,
            base_url="https://api.groq.com/openai/v1"
        )

        response = client.chat.completions.create(

            model="openai/gpt-oss-120b",

            temperature=0.2,

            max_tokens=1500,

            messages=[

                {
                    "role": "system",

                    "content": (
                        "Te a SALIGA AI professzionális kriptopiaci "
                        "elemzőasszisztense vagy. Magyarul válaszolj. "
                        "Ne találj ki adatokat. Különítsd el a tényeket "
                        "és a becsléseket. Magyarázd el a piaci "
                        "ellentmondásokat. Ne ígérj biztos nyereséget."
                    )
                },

                {
                    "role": "user",

                    "content": (
                        f"PIACI ADATOK:\n{context}\n\n"
                        f"KÉRDÉS:\n{question}"
                    )
                }

            ]
        )

        return response.choices[0].message.content

    except Exception as error:

        return f"Groq hiba: {error}"


# ============================================================
# SIDEBAR
# ============================================================

st.sidebar.title("SALIGA AI PRO")

selected_assets = st.sidebar.multiselect(
    "Piacok",
    list(ASSETS.keys()),
    default=["BTC", "ETH", "SOL"]
)

auto_refresh = st.sidebar.toggle(
    "Automatikus frissítés",
    value=True
)

refresh_time = st.sidebar.selectbox(
    "Frissítés másodpercben",
    [15, 30, 60, 120],
    index=1
)

account = st.sidebar.number_input(
    "Számlaegyenleg USDT",
    min_value=10.0,
    value=10000.0,
    step=100.0
)

risk_percent = st.sidebar.number_input(
    "Kockázat pozíciónként (%)",
    min_value=0.1,
    max_value=5.0,
    value=1.0,
    step=0.1
)

if auto_refresh and st_autorefresh:

    st_autorefresh(
        interval=refresh_time * 1000,
        key="saliga_refresh"
    )


# ============================================================
# MAIN DASHBOARD
# ============================================================

init_database()

st.title("⚡ SALIGA AI — MARKET INTELLIGENCE PRO")

st.caption(
    f"{VERSION} | OKX ONLY | INTERNAL ENGINE + GROQ AI"
)

st.info(
    "Az elemzőmotor nem használ EMA-t vagy SMA-t. "
    "A döntéseket több idősík, piaci struktúra, volumen, "
    "Open Interest, delta, order book és likviditás alapján hozza."
)

if not selected_assets:

    st.warning("Válassz ki legalább egy piacot.")

    st.stop()


results = {}

errors = {}

with st.spinner("OKX piaci adatok elemzése..."):

    for asset in selected_assets:

        try:

            results[asset] = run_engine(
                asset,
                ASSETS[asset],
                account,
                risk_percent
            )

        except Exception as error:

            errors[asset] = str(error)


for asset, error in errors.items():

    st.error(f"{asset}: {error}")


if not results:

    st.error(
        "Nem érkezett használható adat az OKX-ről."
    )

    st.stop()


# ============================================================
# SIGNAL CARDS
# ============================================================

columns = st.columns(len(results))

for column, (asset, data) in zip(columns, results.items()):

    signal = data["signal"]

    css = (
        "long" if signal == "LONG"
        else "short" if signal == "SHORT"
        else "wait"
    )

    column.markdown(
        f"""
        <div class="signal-card">

        <h3>{asset}</h3>

        <h2 class="{css}">{signal}</h2>

        <p>Ár: {data["price"]:,.4f}</p>

        <p>Score: {data["score"]:+.1f}</p>

        <p>RSI: {data["rsi"]:.1f}</p>

        </div>
        """,
        unsafe_allow_html=True
    )


# ============================================================
# TABS
# ============================================================

tab1, tab2, tab3, tab4, tab5 = st.tabs([

    "📊 Piaci elemzés",

    "🎯 Target / Stop Loss",

    "🧭 Multi-Timeframe",

    "📰 Hírek / AI",

    "💬 SALIGA AI Chat"

])


# ============================================================
# MARKET ANALYSIS
# ============================================================

with tab1:

    for asset, data in results.items():

        st.subheader(asset)

        c1, c2, c3, c4 = st.columns(4)

        c1.metric(
            "Ár",
            f"{data['price']:,.4f}"
        )

        c2.metric(
            "RSI",
            f"{data['rsi']:.1f}"
        )

        c3.metric(
            "ATR",
            f"{data['atr']:.4f}"
        )

        c4.metric(
            "Order Book",
            data["book"]["state"]
        )

        st.write(
            "**Open Interest:**",
            data["oi"]["state"]
        )

        if data["oi"]["change"] is not None:

            st.write(
                f"OI változás: {data['oi']['change']:+.3f}%"
            )

        st.write(
            "**Becsült delta:**",
            f"{data['trades']['delta']:+.4f}"
        )

        st.write(
            "**Vevői arány:**",
            f"{data['trades']['buy_percentage']:.1f}%"
        )

        st.write(
            "**Order Book imbalance:**",
            f"{data['book']['imbalance']:.1f}%"
        )

        st.write(
            "**Likviditás:**",
            data["sweep"]
        )

        st.write(
            "**1H struktúra:**",
            data["structures"]["1h"]["event"]
        )

        if data["conflict"]:

            st.warning(
                "A magasabb idősíkok között irányellentmondás van."
            )

        df = data["frames"]["15m"]["data"].tail(150)

        fig = go.Figure()

        fig.add_trace(
            go.Candlestick(

                x=df["timestamp"],

                open=df["open"],

                high=df["high"],

                low=df["low"],

                close=df["close"],

                name=asset

            )
        )

        fig.add_trace(
            go.Scatter(

                x=df["timestamp"],

                y=df["vwap"],

                name="VWAP",

                line=dict(width=1.5)

            )
        )

        fig.update_layout(

            template="plotly_white",

            height=450,

            xaxis_rangeslider_visible=False

        )

        st.plotly_chart(
            fig,
            use_container_width=True
        )

        st.divider()


# ============================================================
# TARGETS AND STOP LOSS
# ============================================================

with tab2:

    st.subheader(
        "Kereskedési terv — belépő / stop / target"
    )

    for asset, data in results.items():

        st.markdown(f"### {asset}")

        plan = data["plan"]

        if plan is None:

            st.warning(
                "Nincs megfelelő LONG vagy SHORT jelzés. "
                "A rendszer kivár."
            )

            continue

        st.write(
            f"**Irány:** {data['signal']}"
        )

        st.write(
            f"**Referencia belépő:** {plan['entry']:,.6f}"
        )

        a, b, c, d = st.columns(4)

        a.metric(
            "STOP LOSS",
            f"{plan['stop']:,.6f}"
        )

        b.metric(
            "TARGET 1 — 1R",
            f"{plan['target1']:,.6f}"
        )

        c.metric(
            "TARGET 2 — 2R",
            f"{plan['target2']:,.6f}"
        )

        d.metric(
            "TARGET 3 — 3R",
            f"{plan['target3']:,.6f}"
        )

        st.write(
            f"**Kockáztatott összeg:** "
            f"{plan['risk_amount']:.2f} USDT"
        )

        st.write(
            f"**Becsült pozícióméret:** "
            f"{plan['position_size']:.6f} {asset}"
        )

        st.write(
            f"**Névleges pozícióérték:** "
            f"{plan['notional']:.2f} USDT"
        )

        st.caption(
            "A belépő referenciaár, nem automatikus megbízás. "
            "A pozícióméret becslés, amelyet az OKX kontraktusméretéhez, "
            "tőkeáttételéhez, díjaihoz és csúszásához igazítani kell."
        )

        st.divider()


# ============================================================
# MULTI-TIMEFRAME
# ============================================================

with tab3:

    st.subheader(
        "Több idősíkú piaci struktúra"
    )

    for asset, data in results.items():

        st.markdown(f"### {asset}")

        rows = []

        for timeframe in TIMEFRAMES:

            structure = data["structures"][timeframe]

            rows.append({

                "Idősík": timeframe,

                "Irány": (
                    "Bullish"
                    if structure["bias"] > 0
                    else "Bearish"
                    if structure["bias"] < 0
                    else "Semleges"
                ),

                "Esemény": structure["event"],

                "Támasz": structure["support"],

                "Ellenállás": structure["resistance"]

            })

        st.dataframe(
            pd.DataFrame(rows),
            use_container_width=True,
            hide_index=True
        )


# ============================================================
# NEWS AND AI
# ============================================================

with tab4:

    st.subheader("Kriptopiaci hírek")

    news = get_news()

    for item in news:

        if item["link"]:

            st.markdown(
                f"- [{item['title']}]({item['link']})"
            )

        else:

            st.write(item["title"])

    if st.button("SALIGA AI — piaci összefoglaló"):

        context = []

        for asset, data in results.items():

            context.append(
                f"{asset}: ár={data['price']}, "
                f"jelzés={data['signal']}, "
                f"score={data['score']}, "
                f"OI={data['oi']['state']}, "
                f"delta={data['trades']['delta']}, "
                f"struktúra={data['structures']['1h']['event']}"
            )

        question = (
            "Elemezd a teljes piaci helyzetet. "
            "Mutasd be az irányokat, az ellentmondásokat, "
            "a lehetséges kockázatokat és a kivárási helyzeteket."
        )

        with st.spinner("SALIGA AI elemzés..."):

            answer = groq_chat(
                question,
                "\n".join(context)
            )

        st.markdown(answer)


# ============================================================
# CHAT
# ============================================================

with tab5:

    st.subheader(
        "SALIGA AI — Piaci elemző chat"
    )

    if "chat_history" not in st.session_state:

        st.session_state.chat_history = []

    for message in st.session_state.chat_history:

        with st.chat_message(message["role"]):

            st.markdown(message["content"])

    question = st.chat_input(
        "Kérdezz a piacról..."
    )

    if question:

        st.session_state.chat_history.append({

            "role": "user",

            "content": question

        })

        context = []

        for asset, data in results.items():

            context.append(
                f"{asset}: ár={data['price']}, "
                f"jelzés={data['signal']}, "
                f"score={data['score']}, "
                f"OI={data['oi']['state']}, "
                f"delta={data['trades']['delta']}, "
                f"orderbook={data['book']['state']}"
            )

        with st.chat_message("user"):

            st.markdown(question)

        with st.chat_message("assistant"):

            with st.spinner("SALIGA AI válaszol..."):

                answer = groq_chat(
                    question,
                    "\n".join(context)
                )

                st.markdown(answer)

        st.session_state.chat_history.append({

            "role": "assistant",

            "content": answer

        })


# ============================================================
# FOOTER
# ============================================================

st.divider()

st.caption(
    f"{VERSION} | "
    f"Utolsó frissítés: "
    f"{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}"
)

st.caption(
    "Az OI-alapú long/short épülés és zárás becslés. "
    "A delta a lekért kötésekből számított becslés. "
    "A rendszer nem garantál nyereséget."
)
