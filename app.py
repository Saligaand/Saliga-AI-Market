# ================================================================
# SALIGA AI PRO 32.0
# MULTI-ASSET INTELLIGENCE ENGINE
#
# CRYPTO: OKX
# STOCKS / INDICES: YAHOO FINANCE
# AI: GROQ
#
# NO EMA
# NO SMA
#
# FEATURES:
# - Multi-asset selector
# - OKX Spot / Perpetual
# - Stocks and indices
# - Multi-timeframe analysis
# - RSI / ATR / ROC
# - Market structure
# - Market regime
# - Open Interest
# - Funding Rate
# - Order Book
# - Trade Delta
# - Liquidity Sweep
# - Breakout / Retest
# - Support / Resistance
# - News Analysis
# - Groq AI Analysis
# - Market Heatmap Snapshot
# - Entry Quality
# - Risk Management
# - Paper Trading
# - SQLite Database
# - Signal Journal
# - Performance Tracking
# - CSV Export
# - Plotly Dashboard
# ================================================================

import streamlit as st
import pandas as pd
import numpy as np
import requests
import sqlite3
import json
import time
import feedparser
import yfinance as yf

from datetime import datetime, timezone
from openai import OpenAI
from plotly.subplots import make_subplots
import plotly.graph_objects as go


# ================================================================
# CONFIGURATION
# ================================================================

APP_NAME = "SALIGA AI PRO 32.0"

DB_NAME = "saliga_ai_32.db"

OKX_BASE = "https://www.okx.com"

GROQ_MODEL = "openai/gpt-oss-120b"

TIMEFRAMES = {
    "1 Minute": "1m",
    "5 Minutes": "5m",
    "15 Minutes": "15m",
    "1 Hour": "1H",
    "4 Hours": "4H"
}

st.set_page_config(
    page_title=APP_NAME,
    page_icon="⚡",
    layout="wide"
)


# ================================================================
# DESIGN
# ================================================================

st.markdown("""
<style>

.stApp {
    background-color: #080c14;
    color: #e6edf3;
}

section[data-testid="stSidebar"] {
    background-color: #101722;
}

.metric-card {
    background: #121d2b;
    padding: 15px;
    border-radius: 10px;
    border: 1px solid #25364b;
}

h1, h2, h3 {
    color: #00d4ff;
}

</style>
""", unsafe_allow_html=True)


# ================================================================
# ASSET DATABASE
# ================================================================

CRYPTO_SWAP = [
    "BTC-USDT-SWAP",
    "ETH-USDT-SWAP",
    "SOL-USDT-SWAP",
    "XRP-USDT-SWAP",
    "BNB-USDT-SWAP",
    "DOGE-USDT-SWAP",
    "ADA-USDT-SWAP",
    "AVAX-USDT-SWAP",
    "LINK-USDT-SWAP",
    "DOT-USDT-SWAP",
    "LTC-USDT-SWAP",
    "BCH-USDT-SWAP",
    "SUI-USDT-SWAP",
    "TON-USDT-SWAP",
    "TRX-USDT-SWAP",
    "PEPE-USDT-SWAP"
]

CRYPTO_SPOT = [
    "BTC-USDT",
    "ETH-USDT",
    "SOL-USDT",
    "XRP-USDT",
    "BNB-USDT",
    "DOGE-USDT",
    "ADA-USDT",
    "AVAX-USDT",
    "LINK-USDT"
]

STOCKS = [
    "NVDA",
    "TSLA",
    "AAPL",
    "MSFT",
    "AMD",
    "META",
    "AMZN",
    "GOOGL",
    "COIN",
    "MSTR",
    "PLTR",
    "NFLX"
]

INDICES = [
    "^GSPC",
    "^IXIC",
    "^DJI",
    "^NDX",
    "^VIX"
]


# ================================================================
# SIDEBAR ASSET SELECTOR
# ================================================================

st.sidebar.title("⚡ SALIGA AI PRO")

asset_category = st.sidebar.selectbox(
    "Asset Category",
    [
        "Crypto Perpetual",
        "Crypto Spot",
        "Stocks",
        "Indices",
        "Custom Instrument"
    ]
)

if asset_category == "Crypto Perpetual":

    selected_asset = st.sidebar.selectbox(
        "Select Cryptocurrency",
        CRYPTO_SWAP
    )

    asset_type = "crypto"
    market_type = "swap"
    provider = "OKX"

elif asset_category == "Crypto Spot":

    selected_asset = st.sidebar.selectbox(
        "Select Cryptocurrency",
        CRYPTO_SPOT
    )

    asset_type = "crypto"
    market_type = "spot"
    provider = "OKX"

elif asset_category == "Stocks":

    selected_asset = st.sidebar.selectbox(
        "Select Stock",
        STOCKS
    )

    asset_type = "stock"
    market_type = "equity"
    provider = "YAHOO"

elif asset_category == "Indices":

    selected_asset = st.sidebar.selectbox(
        "Select Index",
        INDICES
    )

    asset_type = "index"
    market_type = "index"
    provider = "YAHOO"

else:

    selected_asset = st.sidebar.text_input(
        "Enter Instrument",
        "BTC-USDT-SWAP"
    ).strip().upper()

    if "-USDT" in selected_asset:
        asset_type = "crypto"
        market_type = (
            "swap" if selected_asset.endswith("-SWAP")
            else "spot"
        )
        provider = "OKX"
    else:
        asset_type = "stock"
        market_type = "equity"
        provider = "YAHOO"


st.sidebar.markdown("---")

refresh_seconds = st.sidebar.slider(
    "Refresh Interval",
    10,
    120,
    30
)

show_ai = st.sidebar.checkbox(
    "Enable Groq AI",
    True
)

show_news = st.sidebar.checkbox(
    "Enable News Analysis",
    True
)

show_paper = st.sidebar.checkbox(
    "Enable Paper Trading",
    True
)


# ================================================================
# API KEYS
# ================================================================

try:
    GROQ_API_KEY = st.secrets["GROQ_API_KEY"]
except Exception:
    GROQ_API_KEY = ""

try:
    OKX_API_KEY = st.secrets["OKX_API_KEY"]
except Exception:
    OKX_API_KEY = ""


# ================================================================
# DATABASE
# ================================================================

def db():

    return sqlite3.connect(
        DB_NAME,
        check_same_thread=False
    )


def init_database():

    con = db()
    cur = con.cursor()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS signals (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp TEXT,
        instrument TEXT,
        signal TEXT,
        score REAL,
        price REAL,
        reason TEXT
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS snapshots (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp TEXT,
        instrument TEXT,
        price REAL,
        rsi REAL,
        atr REAL,
        oi REAL,
        funding REAL,
        delta REAL,
        regime TEXT
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS paper_trades (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp TEXT,
        instrument TEXT,
        direction TEXT,
        entry REAL,
        stop REAL,
        target1 REAL,
        target2 REAL,
        target3 REAL,
        quantity REAL,
        status TEXT,
        exit_price REAL,
        pnl REAL
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp TEXT,
        instrument TEXT,
        event TEXT,
        details TEXT
    )
    """)

    con.commit()
    con.close()


init_database()


# ================================================================
# HTTP REQUEST
# ================================================================

def okx_request(endpoint, params=None):

    try:

        response = requests.get(
            OKX_BASE + endpoint,
            params=params,
            timeout=12
        )

        response.raise_for_status()

        data = response.json()

        if data.get("code") != "0":
            return None

        return data.get("data")

    except Exception:
        return None


# ================================================================
# OKX TICKER
# ================================================================

def get_okx_ticker():

    data = okx_request(
        "/api/v5/market/ticker",
        {"instId": selected_asset}
    )

    if not data:
        return None

    item = data[0]

    return {
        "price": float(item["last"]),
        "bid": float(item["bidPx"]),
        "ask": float(item["askPx"]),
        "volume": float(item["vol24h"]),
        "high": float(item["high24h"]),
        "low": float(item["low24h"])
    }


# ================================================================
# YAHOO FINANCE TICKER
# ================================================================

def get_yahoo_ticker():

    try:

        ticker = yf.Ticker(selected_asset)

        history = ticker.history(
            period="5d",
            interval="5m"
        )

        if history.empty:
            return None

        last = history.iloc[-1]

        return {
            "price": float(last["Close"]),
            "bid": None,
            "ask": None,
            "volume": float(last["Volume"]),
            "high": float(history["High"].max()),
            "low": float(history["Low"].min())
        }

    except Exception:
        return None


# ================================================================
# UNIFIED TICKER
# ================================================================

def get_ticker():

    if provider == "OKX":
        return get_okx_ticker()

    return get_yahoo_ticker()


# ================================================================
# CANDLE DATA
# ================================================================

def get_okx_candles(bar, limit=300):

    data = okx_request(
        "/api/v5/market/candles",
        {
            "instId": selected_asset,
            "bar": bar,
            "limit": str(limit)
        }
    )

    if not data:
        return pd.DataFrame()

    df = pd.DataFrame(
        data,
        columns=[
            "timestamp",
            "open",
            "high",
            "low",
            "close",
            "volume",
            "volume_currency",
            "volume_quote",
            "confirmed"
        ]
    )

    for col in [
        "open",
        "high",
        "low",
        "close",
        "volume"
    ]:

        df[col] = pd.to_numeric(
            df[col],
            errors="coerce"
        )

    df["timestamp"] = pd.to_datetime(
        pd.to_numeric(df["timestamp"]),
        unit="ms",
        utc=True
    )

    df = df.sort_values("timestamp")

    df = df.dropna(
        subset=["open", "high", "low", "close"]
    )

    return df.reset_index(drop=True)


def get_yahoo_candles(bar, limit=300):

    interval_map = {
        "1m": "1m",
        "5m": "5m",
        "15m": "15m",
        "1H": "60m",
        "4H": "60m"
    }

    interval = interval_map.get(bar, "5m")

    try:

        ticker = yf.Ticker(selected_asset)

        history = ticker.history(
            period="7d",
            interval=interval
        )

        if history.empty:
            return pd.DataFrame()

        df = history.reset_index()

        time_column = (
            "Datetime"
            if "Datetime" in df.columns
            else "Date"
        )

        df = df.rename(
            columns={
                time_column: "timestamp",
                "Open": "open",
                "High": "high",
                "Low": "low",
                "Close": "close",
                "Volume": "volume"
            }
        )

        df["timestamp"] = pd.to_datetime(
            df["timestamp"],
            utc=True
        )

        if bar == "4H":

            df = df.set_index("timestamp")

            df = df.resample("4h").agg({
                "open": "first",
                "high": "max",
                "low": "min",
                "close": "last",
                "volume": "sum"
            }).dropna()

            df = df.reset_index()

        df["confirmed"] = "1"

        return df.tail(limit).reset_index(drop=True)

    except Exception:
        return pd.DataFrame()


def get_candles(bar, limit=300):

    if provider == "OKX":

        return get_okx_candles(
            bar,
            limit
        )

    return get_yahoo_candles(
        bar,
        limit
    )


# ================================================================
# INDICATORS
# NO EMA / NO SMA
# ================================================================

def calculate_rsi(series, period=14):

    delta = series.diff()

    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)

    avg_gain = gain.rolling(period).mean()
    avg_loss = loss.rolling(period).mean()

    rs = avg_gain / avg_loss.replace(0, np.nan)

    rsi = 100 - (
        100 / (1 + rs)
    )

    return rsi.fillna(50)


def calculate_atr(df, period=14):

    high = df["high"]
    low = df["low"]
    close = df["close"]

    previous_close = close.shift(1)

    tr = pd.concat(
        [
            high - low,
            abs(high - previous_close),
            abs(low - previous_close)
        ],
        axis=1
    ).max(axis=1)

    return tr.rolling(period).mean()


def calculate_roc(series, period=10):

    return (
        series.pct_change(periods=period) * 100
    )


def calculate_volume_ratio(df):

    average = df["volume"].rolling(20).mean()

    return (
        df["volume"] / average.replace(0, np.nan)
    ).fillna(1)


# ================================================================
# MARKET STRUCTURE
# ================================================================

def analyze_market_structure(df):

    if len(df) < 30:
        return "INSUFFICIENT DATA"

    recent_high = df["high"].tail(20).max()
    recent_low = df["low"].tail(20).min()

    previous_high = df["high"].iloc[-30:-10].max()
    previous_low = df["low"].iloc[-30:-10].min()

    price = df["close"].iloc[-1]

    if recent_high > previous_high and recent_low > previous_low:
        return "BULLISH STRUCTURE"

    if recent_high < previous_high and recent_low < previous_low:
        return "BEARISH STRUCTURE"

    if price > previous_high:
        return "BULLISH BREAKOUT"

    if price < previous_low:
        return "BEARISH BREAKDOWN"

    return "RANGE STRUCTURE"


# ================================================================
# MARKET REGIME
# ================================================================

def analyze_market_regime(df):

    if len(df) < 30:
        return "UNKNOWN"

    atr = calculate_atr(df)

    current_atr = atr.iloc[-1]

    average_atr = atr.tail(30).mean()

    price = df["close"].iloc[-1]

    if pd.isna(current_atr) or price == 0:
        return "UNKNOWN"

    volatility = current_atr / price * 100

    if volatility > 1.5 and current_atr > average_atr:
        return "HIGH VOLATILITY"

    if volatility < 0.3:
        return "LOW VOLATILITY"

    return "NORMAL MARKET"


# ================================================================
# SUPPORT / RESISTANCE
# ================================================================

def calculate_support_resistance(df):

    if len(df) < 20:
        return None, None

    support = df["low"].tail(50).min()

    resistance = df["high"].tail(50).max()

    return float(support), float(resistance)


# ================================================================
# LIQUIDITY SWEEP
# ================================================================

def detect_liquidity_sweep(df):

    if len(df) < 25:
        return "NONE"

    previous_high = df["high"].iloc[-21:-1].max()

    previous_low = df["low"].iloc[-21:-1].min()

    last = df.iloc[-1]

    if (
        last["high"] > previous_high
        and last["close"] < previous_high
    ):
        return "BUY-SIDE LIQUIDITY SWEEP"

    if (
        last["low"] < previous_low
        and last["close"] > previous_low
    ):
        return "SELL-SIDE LIQUIDITY SWEEP"

    return "NONE"


# ================================================================
# BREAKOUT / RETEST
# ================================================================

def detect_breakout(df):

    if len(df) < 30:
        return "NONE"

    resistance = df["high"].iloc[-21:-2].max()

    support = df["low"].iloc[-21:-2].min()

    previous = df.iloc[-2]

    last = df.iloc[-1]

    if (
        previous["close"] <= resistance
        and last["close"] > resistance
    ):
        return "BULLISH BREAKOUT"

    if (
        previous["close"] >= support
        and last["close"] < support
    ):
        return "BEARISH BREAKOUT"

    if (
        last["low"] <= resistance
        and last["close"] > resistance
    ):
        return "BULLISH RETEST"

    if (
        last["high"] >= support
        and last["close"] < support
    ):
        return "BEARISH RETEST"

    return "NONE"


# ================================================================
# OPEN INTEREST
# ================================================================

def get_open_interest():

    if provider != "OKX" or market_type != "swap":
        return None

    data = okx_request(
        "/api/v5/public/open-interest",
        {
            "instType": "SWAP",
            "instId": selected_asset
        }
    )

    if not data:
        return None

    try:
        return float(data[0]["oi"])
    except Exception:
        return None


# ================================================================
# FUNDING RATE
# ================================================================

def get_funding():

    if provider != "OKX" or market_type != "swap":
        return None

    data = okx_request(
        "/api/v5/public/funding-rate",
        {
            "instId": selected_asset
        }
    )

    if not data:
        return None

    try:
        return float(data[0]["fundingRate"]) * 100
    except Exception:
        return None


# ================================================================
# ORDER BOOK
# ================================================================

def get_orderbook():

    if provider != "OKX":
        return None

    data = okx_request(
        "/api/v5/market/books",
        {
            "instId": selected_asset,
            "sz": "20"
        }
    )

    if not data:
        return None

    try:

        bids = data[0]["bids"]
        asks = data[0]["asks"]

        bid_volume = sum(
            float(x[1]) for x in bids
        )

        ask_volume = sum(
            float(x[1]) for x in asks
        )

        total = bid_volume + ask_volume

        if total == 0:
            return None

        imbalance = (
            (bid_volume - ask_volume)
            / total
        ) * 100

        return {
            "bid_volume": bid_volume,
            "ask_volume": ask_volume,
            "imbalance": imbalance,
            "bids": bids,
            "asks": asks
        }

    except Exception:
        return None


# ================================================================
# TRADE DELTA
# ================================================================

def get_trade_delta():

    if provider != "OKX":
        return None

    data = okx_request(
        "/api/v5/market/trades",
        {
            "instId": selected_asset,
            "limit": "100"
        }
    )

    if not data:
        return None

    buy_volume = 0
    sell_volume = 0

    for trade in data:

        size = float(trade["sz"])

        if trade["side"] == "buy":
            buy_volume += size
        else:
            sell_volume += size

    total = buy_volume + sell_volume

    if total == 0:
        return None

    delta = (
        (buy_volume - sell_volume)
        / total
    ) * 100

    return {
        "buy": buy_volume,
        "sell": sell_volume,
        "delta": delta
    }


# ================================================================
# NEWS
# ================================================================

NEWS_FEEDS = [
    "https://www.coindesk.com/arc/outboundfeeds/rss/",
    "https://cointelegraph.com/rss"
]


def get_news():

    articles = []

    for feed_url in NEWS_FEEDS:

        try:

            feed = feedparser.parse(feed_url)

            for item in feed.entries[:15]:

                articles.append({
                    "title": item.get("title", ""),
                    "summary": item.get("summary", ""),
                    "link": item.get("link", "")
                })

        except Exception:
            continue

    return articles


# ================================================================
# NEWS SENTIMENT
# ================================================================

def analyze_news_sentiment(articles):

    positive_words = [
        "bullish",
        "surge",
        "growth",
        "adoption",
        "approval",
        "inflow",
        "breakout",
        "rally",
        "positive"
    ]

    negative_words = [
        "bearish",
        "crash",
        "hack",
        "lawsuit",
        "outflow",
        "liquidation",
        "recession",
        "ban",
        "negative"
    ]

    score = 0

    for article in articles:

        text_value = (
            article["title"]
            + " "
            + article["summary"]
        ).lower()

        for word in positive_words:

            if word in text_value:
                score += 1

        for word in negative_words:

            if word in text_value:
                score -= 1

    if score > 3:
        sentiment = "POSITIVE"

    elif score < -3:
        sentiment = "NEGATIVE"

    else:
        sentiment = "NEUTRAL"

    return {
        "score": score,
        "sentiment": sentiment
    }


# ================================================================
# GROQ AI
# ================================================================

def groq_analysis(payload):

    if not GROQ_API_KEY:
        return "GROQ API KEY NOT CONFIGURED"

    try:

        client = OpenAI(
            api_key=GROQ_API_KEY,
            base_url="https://api.groq.com/openai/v1"
        )

        system_prompt = """
You are SALIGA AI PRO, an institutional-style
multi-asset market intelligence analyst.

Analyze the supplied market data.

Do not invent missing information.

Do not claim that unavailable indicators are available.

Analyze:

1. Market structure
2. Momentum
3. Volatility
4. Liquidity
5. Order flow
6. Open Interest
7. Funding
8. News
9. Market regime
10. Bullish and bearish scenarios
11. Important support and resistance
12. Risk factors

Give a directional assessment:

BULLISH
BEARISH
NEUTRAL

Give confidence from 0 to 100.

Do not promise profits.

Do not use EMA or SMA.
"""

        response = client.chat.completions.create(
            model=GROQ_MODEL,
            messages=[
                {
                    "role": "system",
                    "content": system_prompt
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        payload,
                        default=str
                    )
                }
            ],
            temperature=0.2,
            max_tokens=1800
        )

        return response.choices[0].message.content

    except Exception as e:

        return f"GROQ ERROR: {str(e)}"


# ================================================================
# ENTRY QUALITY
# ================================================================

def calculate_entry_quality(
    rsi,
    roc,
    volume_ratio,
    structure,
    orderbook,
    delta,
    breakout,
    sweep
):

    score = 0

    reasons = []

    if rsi < 30:

        score += 10
        reasons.append("Oversold RSI")

    elif rsi > 70:

        score -= 10
        reasons.append("Overbought RSI")

    if roc > 0:

        score += 10
        reasons.append("Positive momentum")

    elif roc < 0:

        score -= 10
        reasons.append("Negative momentum")

    if volume_ratio > 1.5:

        score += 10
        reasons.append("High volume")

    if "BULLISH" in structure:

        score += 15
        reasons.append("Bullish market structure")

    elif "BEARISH" in structure:

        score -= 15
        reasons.append("Bearish market structure")

    if orderbook is not None:

        if orderbook["imbalance"] > 15:

            score += 10
            reasons.append("Buy-side order book imbalance")

        elif orderbook["imbalance"] < -15:

            score -= 10
            reasons.append("Sell-side order book imbalance")

    if delta is not None:

        if delta["delta"] > 15:

            score += 10
            reasons.append("Positive trade delta")

        elif delta["delta"] < -15:

            score -= 10
            reasons.append("Negative trade delta")

    if "BULLISH" in breakout:

        score += 15
        reasons.append("Bullish breakout / retest")

    elif "BEARISH" in breakout:

        score -= 15
        reasons.append("Bearish breakout / retest")

    if sweep == "SELL-SIDE LIQUIDITY SWEEP":

        score += 10
        reasons.append("Sell-side liquidity sweep")

    elif sweep == "BUY-SIDE LIQUIDITY SWEEP":

        score -= 10
        reasons.append("Buy-side liquidity sweep")

    score = max(-100, min(100, score))

    if score >= 35:
        signal = "BUY"

    elif score <= -35:
        signal = "SELL"

    else:
        signal = "NEUTRAL"

    quality = abs(score)

    return {
        "score": score,
        "quality": quality,
        "signal": signal,
        "reasons": reasons
    }


# ================================================================
# RISK MANAGEMENT
# ================================================================

def calculate_risk_levels(price, atr, direction):

    if atr is None or pd.isna(atr) or atr <= 0:
        return None

    if direction == "BUY":

        stop = price - atr * 1.5

        target1 = price + atr * 1.5

        target2 = price + atr * 2.5

        target3 = price + atr * 4

    elif direction == "SELL":

        stop = price + atr * 1.5

        target1 = price - atr * 1.5

        target2 = price - atr * 2.5

        target3 = price - atr * 4

    else:

        return None

    return {
        "entry": price,
        "stop": stop,
        "target1": target1,
        "target2": target2,
        "target3": target3
    }


# ================================================================
# DATABASE SAVE
# ================================================================

def save_signal(signal, score, price, reason):

    con = db()

    con.execute("""
    INSERT INTO signals
    (timestamp, instrument, signal, score, price, reason)
    VALUES (?, ?, ?, ?, ?, ?)
    """, (
        datetime.now(timezone.utc).isoformat(),
        selected_asset,
        signal,
        score,
        price,
        reason
    ))

    con.commit()
    con.close()


def save_snapshot(price, rsi, atr, oi, funding, delta, regime):

    con = db()

    con.execute("""
    INSERT INTO snapshots
    (timestamp, instrument, price, rsi, atr, oi,
     funding, delta, regime)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        datetime.now(timezone.utc).isoformat(),
        selected_asset,
        price,
        rsi,
        atr,
        oi,
        funding,
        delta,
        regime
    ))

    con.commit()
    con.close()


def save_event(event, details):

    con = db()

    con.execute("""
    INSERT INTO events
    (timestamp, instrument, event, details)
    VALUES (?, ?, ?, ?)
    """, (
        datetime.now(timezone.utc).isoformat(),
        selected_asset,
        event,
        str(details)
    ))

    con.commit()
    con.close()


# ================================================================
# PAPER TRADING
# ================================================================

def get_open_trade():

    con = db()

    df = pd.read_sql_query(
        """
        SELECT * FROM paper_trades
        WHERE instrument = ?
        AND status = 'OPEN'
        ORDER BY id DESC
        LIMIT 1
        """,
        con,
        params=(selected_asset,)
    )

    con.close()

    if df.empty:
        return None

    return df.iloc[0]


def open_paper_trade(direction, levels, quantity=1):

    existing = get_open_trade()

    if existing is not None:
        return False

    con = db()

    con.execute("""
    INSERT INTO paper_trades
    (timestamp, instrument, direction, entry, stop,
     target1, target2, target3, quantity, status)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'OPEN')
    """, (
        datetime.now(timezone.utc).isoformat(),
        selected_asset,
        direction,
        levels["entry"],
        levels["stop"],
        levels["target1"],
        levels["target2"],
        levels["target3"],
        quantity
    ))

    con.commit()
    con.close()

    save_event(
        "PAPER TRADE OPENED",
        direction
    )

    return True


def manage_paper_trade(price):

    trade = get_open_trade()

    if trade is None:
        return

    direction = trade["direction"]

    entry = float(trade["entry"])

    stop = float(trade["stop"])

    target1 = float(trade["target1"])

    quantity = float(trade["quantity"])

    exit_reason = None

    if direction == "BUY":

        if price <= stop:
            exit_reason = "STOP LOSS"

        elif price >= target1:
            exit_reason = "TARGET 1"

    elif direction == "SELL":

        if price >= stop:
            exit_reason = "STOP LOSS"

        elif price <= target1:
            exit_reason = "TARGET 1"

    if exit_reason:

        if direction == "BUY":
            pnl = (price - entry) * quantity
        else:
            pnl = (entry - price) * quantity

        con = db()

        con.execute("""
        UPDATE paper_trades
        SET status = 'CLOSED',
            exit_price = ?,
            pnl = ?
        WHERE id = ?
        """, (
            price,
            pnl,
            int(trade["id"])
        ))

        con.commit()
        con.close()

        save_event(
            "PAPER TRADE CLOSED",
            {
                "reason": exit_reason,
                "pnl": pnl
            }
        )


# ================================================================
# PERFORMANCE
# ================================================================

def get_performance():

    con = db()

    df = pd.read_sql_query(
        """
        SELECT * FROM paper_trades
        WHERE instrument = ?
        AND status = 'CLOSED'
        """,
        con,
        params=(selected_asset,)
    )

    con.close()

    if df.empty:
        return {
            "trades": 0,
            "win_rate": 0,
            "total_pnl": 0
        }

    wins = len(
        df[df["pnl"] > 0]
    )

    return {
        "trades": len(df),
        "win_rate": wins / len(df) * 100,
        "total_pnl": df["pnl"].sum()
    }


# ================================================================
# CHART
# ================================================================

def create_chart(df):

    fig = make_subplots(
        rows=2,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.05,
        row_heights=[0.75, 0.25]
    )

    fig.add_trace(
        go.Candlestick(
            x=df["timestamp"],
            open=df["open"],
            high=df["high"],
            low=df["low"],
            close=df["close"],
            name=selected_asset
        ),
        row=1,
        col=1
    )

    fig.add_trace(
        go.Bar(
            x=df["timestamp"],
            y=df["volume"],
            name="Volume"
        ),
        row=2,
        col=1
    )

    fig.update_layout(
        title=f"{selected_asset} MARKET CHART",
        template="plotly_dark",
        height=650,
        xaxis_rangeslider_visible=False
    )

    return fig


# ================================================================
# MAIN ENGINE
# ================================================================

st.title("⚡ SALIGA AI PRO 32.0")

st.caption(
    "MULTI-ASSET INTELLIGENCE ENGINE | "
    "NO EMA | NO SMA"
)

st.markdown(
    f"### {selected_asset} | {provider} | {market_type.upper()}"
)

ticker = get_ticker()

if ticker is None:

    st.error(
        "Market data unavailable. "
        "Check instrument selection or provider."
    )

    st.stop()


price = ticker["price"]

df_1m = get_candles("1m")

df_5m = get_candles("5m")

df_15m = get_candles("15m")

df_1h = get_candles("1H")

df_4h = get_candles("4H")


if df_1m.empty or len(df_1m) < 30:

    st.error("Insufficient candle data.")

    st.stop()


# ================================================================
# INDICATOR CALCULATIONS
# ================================================================

df = df_1m.copy()

df["rsi"] = calculate_rsi(
    df["close"]
)

df["atr"] = calculate_atr(
    df
)

df["roc"] = calculate_roc(
    df["close"]
)

df["volume_ratio"] = calculate_volume_ratio(
    df
)

rsi = float(df["rsi"].iloc[-1])

atr = float(df["atr"].iloc[-1])

roc = float(df["roc"].iloc[-1])

volume_ratio = float(
    df["volume_ratio"].iloc[-1]
)


# ================================================================
# MARKET STRUCTURE
# ================================================================

structure_1m = analyze_market_structure(df_1m)

structure_5m = (
    analyze_market_structure(df_5m)
    if not df_5m.empty
    else "NO DATA"
)

structure_15m = (
    analyze_market_structure(df_15m)
    if not df_15m.empty
    else "NO DATA"
)

structure_1h = (
    analyze_market_structure(df_1h)
    if not df_1h.empty
    else "NO DATA"
)

structure_4h = (
    analyze_market_structure(df_4h)
    if not df_4h.empty
    else "NO DATA"
)

regime = analyze_market_regime(df)


# ================================================================
# LIQUIDITY
# ================================================================

support, resistance = calculate_support_resistance(df)

sweep = detect_liquidity_sweep(df)

breakout = detect_breakout(df)


# ================================================================
# FLOW DATA
# ================================================================

oi = get_open_interest()

funding = get_funding()

orderbook = get_orderbook()

delta = get_trade_delta()


# ================================================================
# NEWS
# ================================================================

if show_news:

    news = get_news()

    news_sentiment = analyze_news_sentiment(
        news
    )

else:

    news = []

    news_sentiment = {
        "score": 0,
        "sentiment": "DISABLED"
    }


# ================================================================
# ENTRY QUALITY
# ================================================================

entry = calculate_entry_quality(
    rsi,
    roc,
    volume_ratio,
    structure_1m,
    orderbook,
    delta,
    breakout,
    sweep
)

signal = entry["signal"]

score = entry["score"]

levels = calculate_risk_levels(
    price,
    atr,
    signal
)


# ================================================================
# AI PAYLOAD
# ================================================================

payload = {

    "instrument": selected_asset,

    "asset_type": asset_type,

    "market_type": market_type,

    "provider": provider,

    "price": price,

    "rsi": rsi,

    "atr": atr,

    "roc": roc,

    "volume_ratio": volume_ratio,

    "market_regime": regime,

    "structure": {

        "1m": structure_1m,

        "5m": structure_5m,

        "15m": structure_15m,

        "1h": structure_1h,

        "4h": structure_4h

    },

    "support": support,

    "resistance": resistance,

    "liquidity_sweep": sweep,

    "breakout": breakout,

    "open_interest": oi,

    "funding": funding,

    "orderbook": (
        orderbook["imbalance"]
        if orderbook else None
    ),

    "trade_delta": (
        delta["delta"]
        if delta else None
    ),

    "news_sentiment": news_sentiment,

    "news": news[:10],

    "technical_signal": signal,

    "technical_score": score

}


# ================================================================
# GROQ ANALYSIS
# ================================================================

if show_ai:

    if (
        "last_ai_asset" not in st.session_state
        or st.session_state.last_ai_asset != selected_asset
    ):

        st.session_state.last_ai_asset = selected_asset

        st.session_state.ai_result = None

    if st.button("⚡ RUN GROQ AI ANALYSIS"):

        with st.spinner("SALIGA AI analyzing market..."):

            st.session_state.ai_result = groq_analysis(
                payload
            )

else:

    st.session_state.ai_result = None


# ================================================================
# SAVE MARKET DATA
# ================================================================

save_snapshot(
    price,
    rsi,
    atr,
    oi,
    funding,
    delta["delta"] if delta else None,
    regime
)


# ================================================================
# PAPER TRADE MANAGEMENT
# ================================================================

if show_paper:

    manage_paper_trade(price)


# ================================================================
# DASHBOARD
# ================================================================

st.markdown("---")

c1, c2, c3, c4 = st.columns(4)

c1.metric(
    "PRICE",
    f"{price:,.6f}"
)

c2.metric(
    "RSI",
    f"{rsi:.2f}"
)

c3.metric(
    "ROC",
    f"{roc:.2f}%"
)

c4.metric(
    "VOLUME RATIO",
    f"{volume_ratio:.2f}x"
)


# ================================================================
# MARKET STRUCTURE
# ================================================================

st.subheader("MULTI-TIMEFRAME MARKET STRUCTURE")

structure_data = pd.DataFrame({

    "Timeframe": [
        "1 Minute",
        "5 Minutes",
        "15 Minutes",
        "1 Hour",
        "4 Hours"
    ],

    "Structure": [
        structure_1m,
        structure_5m,
        structure_15m,
        structure_1h,
        structure_4h
    ]

})

st.dataframe(
    structure_data,
    use_container_width=True,
    hide_index=True
)


# ================================================================
# MARKET REGIME
# ================================================================

st.subheader("MARKET REGIME")

st.info(regime)


# ================================================================
# LIQUIDITY / SUPPORT / RESISTANCE
# ================================================================

st.subheader("LIQUIDITY INTELLIGENCE")

a, b, c = st.columns(3)

a.metric(
    "SUPPORT",
    f"{support:,.6f}" if support is not None else "N/A"
)

b.metric(
    "RESISTANCE",
    f"{resistance:,.6f}" if resistance is not None else "N/A"
)

c.metric(
    "LIQUIDITY SWEEP",
    sweep
)

st.write(
    "Breakout / Retest:",
    breakout
)


# ================================================================
# DERIVATIVES / ORDER FLOW
# ================================================================

st.subheader("DERIVATIVES & ORDER FLOW")

a, b, c, d = st.columns(4)

a.metric(
    "OPEN INTEREST",
    f"{oi:,.2f}" if oi is not None else "N/A"
)

b.metric(
    "FUNDING",
    f"{funding:.5f}%" if funding is not None else "N/A"
)

c.metric(
    "ORDER BOOK IMBALANCE",
    f"{orderbook['imbalance']:.2f}%"
    if orderbook else "N/A"
)

d.metric(
    "TRADE DELTA",
    f"{delta['delta']:.2f}%"
    if delta else "N/A"
)


# ================================================================
# ORDER BOOK VISUALIZATION
# ================================================================

if orderbook:

    st.subheader("ORDER BOOK DEPTH")

    bids = pd.DataFrame(
        orderbook["bids"],
        columns=[
            "Price",
            "Size",
            "Orders"
        ]
    )

    asks = pd.DataFrame(
        orderbook["asks"],
        columns=[
            "Price",
            "Size",
            "Orders"
        ]
    )

    bids["Side"] = "BID"

    asks["Side"] = "ASK"

    book_df = pd.concat(
        [bids, asks],
        ignore_index=True
    )

    st.dataframe(
        book_df,
        use_container_width=True
    )

else:

    st.info(
        "Order book data unavailable for this provider."
    )


# ================================================================
# NEWS ANALYSIS
# ================================================================

st.markdown("---")

st.subheader("NEWS INTELLIGENCE")

st.metric(
    "NEWS SENTIMENT",
    news_sentiment["sentiment"],
    news_sentiment["score"]
)

if news:

    for article in news[:10]:

        st.markdown(
            f"**{article['title']}**"
        )

        st.write(
            article["summary"][:350]
        )

        if article["link"]:

            st.markdown(
                f"[Read Article]({article['link']})"
            )

else:

    st.info("No news data available.")


# ================================================================
# GROQ AI RESULTS
# ================================================================

st.markdown("---")

st.subheader("SALIGA GROQ AI INTELLIGENCE")

if st.session_state.get("ai_result"):

    st.markdown(
        st.session_state.ai_result
    )

else:

    st.info(
        "Press RUN GROQ AI ANALYSIS to generate "
        "institutional-style market analysis."
    )


# ================================================================
# TECHNICAL SIGNAL
# ================================================================

st.markdown("---")

st.subheader("ENTRY INTELLIGENCE")

a, b, c = st.columns(3)

a.metric(
    "SIGNAL",
    signal
)

b.metric(
    "ENTRY SCORE",
    f"{score}/100"
)

c.metric(
    "QUALITY",
    f"{entry['quality']}/100"
)

for reason in entry["reasons"]:

    st.write(
        "•",
        reason
    )


# ================================================================
# RISK LEVELS
# ================================================================

st.subheader("RISK MANAGEMENT")

if levels:

    risk_df = pd.DataFrame({

        "Level": [
            "Entry",
            "Stop Loss",
            "Target 1",
            "Target 2",
            "Target 3"
        ],

        "Price": [
            levels["entry"],
            levels["stop"],
            levels["target1"],
            levels["target2"],
            levels["target3"]
        ]

    })

    st.dataframe(
        risk_df,
        use_container_width=True,
        hide_index=True
    )

else:

    st.info(
        "Risk levels unavailable for neutral signal."
    )


# ================================================================
# PAPER TRADING CONTROLS
# ================================================================

st.markdown("---")

st.subheader("PAPER TRADING")

if show_paper:

    current_trade = get_open_trade()

    if current_trade is None:

        quantity = st.number_input(
            "Position Quantity",
            min_value=0.001,
            value=1.0
        )

        if levels:

            if st.button("OPEN PAPER TRADE"):

                opened = open_paper_trade(
                    signal,
                    levels,
                    quantity
                )

                if opened:

                    st.success(
                        "Paper trade opened."
                    )

                else:

                    st.warning(
                        "An open position already exists."
                    )

        else:

            st.info(
                "No directional signal available."
            )

    else:

        st.warning(
            f"OPEN POSITION: {current_trade['direction']}"
        )

        st.write(
            "Entry:",
            current_trade["entry"]
        )

        st.write(
            "Stop:",
            current_trade["stop"]
        )

        st.write(
            "Target:",
            current_trade["target1"]
        )


# ================================================================
# PERFORMANCE TRACKING
# ================================================================

st.markdown("---")

st.subheader("PAPER TRADING PERFORMANCE")

performance = get_performance()

a, b, c = st.columns(3)

a.metric(
    "CLOSED TRADES",
    performance["trades"]
)

b.metric(
    "WIN RATE",
    f"{performance['win_rate']:.2f}%"
)

c.metric(
    "TOTAL PNL",
    f"{performance['total_pnl']:.4f}"
)


# ================================================================
# CHART
# ================================================================

st.markdown("---")

st.subheader("MARKET CHART")

st.plotly_chart(
    create_chart(df),
    use_container_width=True
)


# ================================================================
# SIGNAL JOURNAL
# ================================================================

st.markdown("---")

st.subheader("SIGNAL JOURNAL")

con = db()

signals_df = pd.read_sql_query(
    """
    SELECT *
    FROM signals
    WHERE instrument = ?
    ORDER BY id DESC
    LIMIT 100
    """,
    con,
    params=(selected_asset,)
)

con.close()

st.dataframe(
    signals_df,
    use_container_width=True
)


# ================================================================
# MARKET SNAPSHOT HISTORY
# ================================================================

st.subheader("MARKET SNAPSHOT HISTORY")

con = db()

snapshot_df = pd.read_sql_query(
    """
    SELECT *
    FROM snapshots
    WHERE instrument = ?
    ORDER BY id DESC
    LIMIT 100
    """,
    con,
    params=(selected_asset,)
)

con.close()

st.dataframe(
    snapshot_df,
    use_container_width=True
)


# ================================================================
# PAPER TRADE HISTORY
# ================================================================

st.subheader("PAPER TRADE HISTORY")

con = db()

trades_df = pd.read_sql_query(
    """
    SELECT *
    FROM paper_trades
    WHERE instrument = ?
    ORDER BY id DESC
    LIMIT 100
    """,
    con,
    params=(selected_asset,)
)

con.close()

st.dataframe(
    trades_df,
    use_container_width=True
)


# ================================================================
# CSV EXPORT
# ================================================================

st.markdown("---")

st.subheader("DATA EXPORT")

csv = signals_df.to_csv(
    index=False
).encode("utf-8")

st.download_button(
    "DOWNLOAD SIGNAL JOURNAL CSV",
    csv,
    file_name=f"{selected_asset}_signals.csv",
    mime="text/csv"
)


# ================================================================
# SYSTEM STATUS
# ================================================================

st.markdown("---")

st.subheader("SYSTEM HEALTH")

st.write(
    "Instrument:",
    selected_asset
)

st.write(
    "Provider:",
    provider
)

st.write(
    "Asset Type:",
    asset_type
)

st.write(
    "Market Type:",
    market_type
)

st.write(
    "OKX:",
    "CONNECTED" if provider == "OKX" else "NOT USED"
)

st.write(
    "Groq:",
    "CONFIGURED" if GROQ_API_KEY else "API KEY MISSING"
)

st.write(
    "Database:",
    "CONNECTED"
)

st.write(
    "Last Update:",
    datetime.now().strftime("%Y-%m-%d %H:%M:%S")
)


# ================================================================
# AUTO REFRESH
# ================================================================

st.markdown("---")

st.caption(
    "SALIGA AI PRO 32.0 | MULTI-ASSET INTELLIGENCE ENGINE"
)

time.sleep(refresh_seconds)

st.rerun()
