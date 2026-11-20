# ================================================================
# AI PRO by Sali
# ULTIMATE BTC INTELLIGENCE ENGINE (FIXED + MACRO SIGNAL)
#
# BTC ONLY | OKX | GROQ AI
# NO EMA | NO SMA
#
# Features:
# - Automatic API key loading from Streamlit Secrets
# - SQLite persistent storage (with safe column migration)
# - OKX BTC market data
# - Multi-timeframe analysis
# - Market structure
# - Market regime
# - Open Interest
# - Funding
# - Order Book
# - Trade Delta
# - Position Flow
# - Liquidity Sweep
# - Breakout / Retest
# - Divergence Detection
# - Historical Pattern Analysis
# - News Intelligence
# - Groq AI combined analysis (incl. Macro Analysis)
# - AI conflict & Macro gate
# - Entry Quality Score & Unified Signal
# - Risk Management
# - Paper Trading
# - Fees and slippage
# - Signal Journal
# - Event Journal
# - CSV Export
# - API health monitoring
# - No-API Macro Calendar + Signal & AI integration
#
# This system is for analysis and paper trading.
# It does not guarantee profitable signals.
# ================================================================

import streamlit as st
import pandas as pd
import numpy as np
import requests
import sqlite3
import json
import time
import hashlib
import xml.etree.ElementTree as ET
import os
import math
import calendar

from datetime import datetime, timezone, timedelta, date
from pathlib import Path

# ================================================================
# CONFIGURATION
# ================================================================

APP_NAME = "AI PRO by Sali"

INSTRUMENT = "BTC-USDT-SWAP"

BASE_URL = "https://www.okx.com"

GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"

GROQ_DEFAULT_MODEL = "openai/gpt-oss-120b"

DATABASE_FILE = "saliga_ai_31.db"

TIMEFRAMES = {
    "1m": "1m",
    "5m": "5m",
    "15m": "15m",
    "1H": "1H",
    "4H": "4H"
}

NEWS_FEEDS = {
    "CoinDesk": "https://www.coindesk.com/arc/outboundfeeds/rss/",
    "Cointelegraph": "https://cointelegraph.com/rss"
}

REQUEST_TIMEOUT = 15

# ================================================================
# STREAMLIT CONFIGURATION
# ================================================================

st.set_page_config(
    page_title=APP_NAME,
    page_icon="₿",
    layout="wide"
)

st.markdown("""
<style>
.stApp {
    background-color: #080c13;
    color: #e8edf5;
}
[data-testid="stMetric"] {
    background: #111a27;
    border: 1px solid #26364c;
    padding: 12px;
    border-radius: 10px;
}
h1, h2, h3 {
    color: #f0f4fa;
}
</style>
""", unsafe_allow_html=True)

# ================================================================
# SECRETS / API CONFIGURATION
# ================================================================

def load_secret(name, default=""):
    try:
        value = st.secrets.get(name, default)
        if value:
            return str(value)
    except Exception:
        pass

    return os.getenv(name, default)


GROQ_API_KEY = load_secret("GROQ_API_KEY", "")

GROQ_MODEL = load_secret(
    "GROQ_MODEL",
    GROQ_DEFAULT_MODEL
)

# ================================================================
# DATABASE
# ================================================================

def get_connection():
    connection = sqlite3.connect(
        DATABASE_FILE,
        check_same_thread=False
    )

    connection.row_factory = sqlite3.Row

    return connection


def _ensure_column(connection, table_name, column_name, column_def):
    cols = [
        row["name"]
        for row in connection.execute(f"PRAGMA table_info({table_name})").fetchall()
    ]
    if column_name not in cols:
        connection.execute(
            f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_def}"
        )


def initialize_database():

    connection = get_connection()

    connection.execute("""
        CREATE TABLE IF NOT EXISTS paper_trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            opened TEXT,
            closed TEXT,
            direction TEXT,
            entry REAL,
            stop REAL,
            tp1 REAL,
            tp2 REAL,
            tp3 REAL,
            quantity REAL,
            score REAL,
            status TEXT,
            exit_price REAL,
            gross_pnl REAL DEFAULT 0,
            fees REAL DEFAULT 0,
            slippage REAL DEFAULT 0,
            funding REAL DEFAULT 0,
            net_pnl REAL DEFAULT 0,
            reason TEXT
        )
    """)

    connection.execute("""
        CREATE TABLE IF NOT EXISTS signal_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT,
            price REAL,
            direction TEXT,
            score REAL,
            stage TEXT,
            regime TEXT,
            macro_status TEXT,
            signal_label TEXT,
            ai_gate INTEGER
        )
    """)

    _ensure_column(connection, "signal_history", "macro_status", "TEXT DEFAULT 'UNKNOWN'")
    _ensure_column(connection, "signal_history", "signal_label", "TEXT DEFAULT 'WAIT'")

    connection.execute("""
        CREATE TABLE IF NOT EXISTS event_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT,
            category TEXT,
            message TEXT
        )
    """)

    connection.execute("""
        CREATE TABLE IF NOT EXISTS market_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp REAL,
            price REAL,
            oi REAL,
            delta REAL,
            funding REAL,
            book_imbalance REAL
        )
    """)

    connection.execute("""
        CREATE TABLE IF NOT EXISTS macro_calendar (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_date TEXT,
            time_utc TEXT,
            currency TEXT,
            event_name TEXT,
            impact TEXT,
            notes TEXT
        )
    """)

    connection.commit()
    connection.close()


initialize_database()

# ================================================================
# SESSION STATE
# ================================================================

DEFAULT_STATE = {
    "last_ai_time": 0,
    "last_ai_result": None,
    "last_ai_error": None,
    "last_news_hash": None,
    "last_trigger_time": 0,
    "last_oi": None,
    "last_price": None,
    "last_refresh": None,
    "last_news": [],
    "last_data_errors": []
}

for key, value in DEFAULT_STATE.items():
    if key not in st.session_state:
        st.session_state[key] = value

# ================================================================
# SIDEBAR
# ================================================================

st.sidebar.title("SALIGA AI PRO 31.0")

st.sidebar.caption("ULTIMATE BTC INTELLIGENCE ENGINE")

st.sidebar.success(
    "Groq API: configured"
    if GROQ_API_KEY
    else "Groq API: missing"
)

st.sidebar.caption(f"Model: {GROQ_MODEL}")

ai_enabled = st.sidebar.toggle(
    "Groq AI enabled",
    value=True
)

macro_gate_enabled = st.sidebar.toggle(
    "Macro event gate (<=1h HIGH)",
    value=True
)

auto_refresh = st.sidebar.toggle(
    "Automatic refresh",
    value=True
)

refresh_seconds = st.sidebar.slider(
    "Refresh interval",
    15,
    120,
    30
)

ai_interval = st.sidebar.slider(
    "Normal AI interval (minutes)",
    5,
    60,
    15
)

ai_cooldown = st.sidebar.slider(
    "Event AI cooldown (minutes)",
    1,
    15,
    3
)

paper_enabled = st.sidebar.toggle(
    "Paper Trading",
    value=True
)

starting_balance = st.sidebar.number_input(
    "Starting balance USDT",
    min_value=100.0,
    max_value=1000000.0,
    value=10000.0
)

risk_percent = st.sidebar.slider(
    "Risk per trade (%)",
    0.1,
    3.0,
    0.5,
    0.1
)

fee_percent = st.sidebar.number_input(
    "Fee per side (%)",
    0.0,
    0.5,
    0.05,
    0.01
)

slippage_percent = st.sidebar.number_input(
    "Slippage per side (%)",
    0.0,
    0.5,
    0.02,
    0.01
)

# ================================================================
# GENERAL HELPERS
# ================================================================

def now_utc():
    return datetime.now(timezone.utc)


def now_string():
    return now_utc().strftime(
        "%Y-%m-%d %H:%M:%S UTC"
    )


def safe_float(value, default=0.0):
    try:
        result = float(value)

        if not np.isfinite(result):
            return default

        return result

    except Exception:
        return default


def clamp(value, low, high):
    return max(low, min(high, value))


def percentage_change(current, previous):

    if previous is None or previous == 0:
        return 0.0

    return (
        (current / previous) - 1
    ) * 100


def add_event(category, message):

    connection = get_connection()

    connection.execute(
        """
        INSERT INTO event_log
        (timestamp, category, message)
        VALUES (?, ?, ?)
        """,
        (
            now_string(),
            str(category),
            str(message)
        )
    )

    connection.commit()
    connection.close()


def database_read(query, params=()):

    connection = get_connection()

    result = pd.read_sql_query(
        query,
        connection,
        params=params
    )

    connection.close()

    return result


# ================================================================
# OKX API
# ================================================================

def okx_request(endpoint, params=None):

    response = requests.get(
        BASE_URL + endpoint,
        params=params or {},
        timeout=REQUEST_TIMEOUT,
        headers={
            "User-Agent": "SALIGA-AI-PRO-31"
        }
    )

    response.raise_for_status()

    data = response.json()

    if str(data.get("code", "0")) != "0":
        raise RuntimeError(
            data.get("msg", "OKX API error")
        )

    return data.get("data", [])


# ================================================================
# MARKET DATA
# ================================================================

def fetch_ticker():

    data = okx_request(
        "/api/v5/market/ticker",
        {"instId": INSTRUMENT}
    )

    if not data:
        raise RuntimeError("Ticker data unavailable")

    item = data[0]

    return {
        "price": safe_float(item.get("last")),
        "bid": safe_float(item.get("bidPx")),
        "ask": safe_float(item.get("askPx")),
        "high24h": safe_float(item.get("high24h")),
        "low24h": safe_float(item.get("low24h")),
        "volume24h": safe_float(item.get("vol24h")),
        "timestamp": item.get("ts")
    }


def fetch_candles(bar, limit=300):

    data = okx_request(
        "/api/v5/market/candles",
        {
            "instId": INSTRUMENT,
            "bar": bar,
            "limit": str(limit)
        }
    )

    rows = []

    for item in data:

        rows.append({
            "timestamp": int(item[0]),
            "open": safe_float(item[1]),
            "high": safe_float(item[2]),
            "low": safe_float(item[3]),
            "close": safe_float(item[4]),
            "volume": safe_float(item[5]),
            "volume_currency": safe_float(item[6]),
            "volume_quote": safe_float(item[7]),
            "confirmed": item[8] == "1"
        })

    df = pd.DataFrame(rows)

    if df.empty:
        return df

    df = df.sort_values(
        "timestamp"
    ).drop_duplicates(
        "timestamp"
    ).reset_index(drop=True)

    return df


def fetch_open_interest():

    data = okx_request(
        "/api/v5/public/open-interest",
        {
            "instType": "SWAP",
            "instId": INSTRUMENT
        }
    )

    if not data:
        return None

    item = data[0]

    return {
        "oi": safe_float(item.get("oi")),
        "oi_currency": safe_float(item.get("oiCcy")),
        "timestamp": item.get("ts")
    }


def fetch_funding():

    data = okx_request(
        "/api/v5/public/funding-rate",
        {"instId": INSTRUMENT}
    )

    if not data:
        return None

    item = data[0]

    return {
        "funding": safe_float(item.get("fundingRate")),
        "next_funding": item.get("nextFundingTime")
    }


def fetch_order_book():

    data = okx_request(
        "/api/v5/market/books",
        {
            "instId": INSTRUMENT,
            "sz": "50"
        }
    )

    if not data:
        return None

    item = data[0]

    bids = [
        (
            safe_float(x[0]),
            safe_float(x[1])
        )
        for x in item.get("bids", [])
    ]

    asks = [
        (
            safe_float(x[0]),
            safe_float(x[1])
        )
        for x in item.get("asks", [])
    ]

    bid_volume = sum(
        x[1] for x in bids
    )

    ask_volume = sum(
        x[1] for x in asks
    )

    total = bid_volume + ask_volume

    imbalance = (
        (bid_volume - ask_volume) / total
        if total > 0 else 0
    )

    return {
        "bids": bids,
        "asks": asks,
        "bid_volume": bid_volume,
        "ask_volume": ask_volume,
        "imbalance": imbalance,
        "spread": (
            asks[0][0] - bids[0][0]
            if bids and asks else 0
        )
    }


def fetch_recent_trades():

    data = okx_request(
        "/api/v5/market/trades",
        {
            "instId": INSTRUMENT,
            "limit": "500"
        }
    )

    result = []

    for item in data:

        result.append({
            "price": safe_float(item.get("px")),
            "size": safe_float(item.get("sz")),
            "side": item.get("side"),
            "timestamp": safe_float(item.get("ts"))
        })

    return result


# ================================================================
# TECHNICAL ENGINE
# No EMA / SMA
# ================================================================

def calculate_rsi(series, period=14):

    delta = series.diff()

    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)

    avg_gain = gain.ewm(
        alpha=1 / period,
        adjust=False,
        min_periods=period
    ).mean()

    avg_loss = loss.ewm(
        alpha=1 / period,
        adjust=False,
        min_periods=period
    ).mean()

    rs = (
        avg_gain /
        avg_loss.replace(0, np.nan)
    )

    return (
        100 - 100 / (1 + rs)
    ).fillna(50)


def calculate_atr(df, period=14):

    previous_close = df["close"].shift(1)

    ranges = pd.concat(
        [
            df["high"] - df["low"],
            (df["high"] - previous_close).abs(),
            (df["low"] - previous_close).abs()
        ],
        axis=1
    )

    true_range = ranges.max(axis=1)

    return true_range.ewm(
        alpha=1 / period,
        adjust=False,
        min_periods=period
    ).mean()


def add_indicators(df):

    if df.empty:
        return df

    df = df.copy()

    df["rsi"] = calculate_rsi(
        df["close"]
    )

    df["atr"] = calculate_atr(df)

    df["roc_5"] = (
        df["close"].pct_change(5) * 100
    )

    df["roc_10"] = (
        df["close"].pct_change(10) * 100
    )

    df["volume_median"] = (
        df["volume"].rolling(20).median()
    )

    df["volume_ratio"] = (
        df["volume"] /
        df["volume_median"].replace(0, np.nan)
    )

    df["atr_pct"] = (
        df["atr"] /
        df["close"].replace(0, np.nan) * 100
    )

    return df.replace(
        [np.inf, -np.inf],
        np.nan
    )


# ================================================================
# MARKET STRUCTURE
# ================================================================

def market_structure(df):

    if df.empty or len(df) < 30:

        return {
            "direction": "UNKNOWN",
            "strength": 0,
            "support": None,
            "resistance": None
        }

    recent = df.tail(30)

    highs = recent["high"].to_numpy()
    lows = recent["low"].to_numpy()

    swing_highs = []
    swing_lows = []

    for i in range(2, len(recent) - 2):

        if (
            highs[i] > highs[i - 1]
            and highs[i] > highs[i - 2]
            and highs[i] > highs[i + 1]
            and highs[i] > highs[i + 2]
        ):
            swing_highs.append(highs[i])

        if (
            lows[i] < lows[i - 1]
            and lows[i] < lows[i - 2]
            and lows[i] < lows[i + 1]
            and lows[i] < lows[i + 2]
        ):
            swing_lows.append(lows[i])

    higher_high = (
        len(swing_highs) >= 2
        and swing_highs[-1] > swing_highs[-2]
    )

    lower_high = (
        len(swing_highs) >= 2
        and swing_highs[-1] < swing_highs[-2]
    )

    higher_low = (
        len(swing_lows) >= 2
        and swing_lows[-1] > swing_lows[-2]
    )

    lower_low = (
        len(swing_lows) >= 2
        and swing_lows[-1] < swing_lows[-2]
    )

    bullish = int(higher_high) + int(higher_low)
    bearish = int(lower_high) + int(lower_low)

    if bullish > bearish:
        direction = "BULLISH"

    elif bearish > bullish:
        direction = "BEARISH"

    else:
        direction = "NEUTRAL"

    current_price = safe_float(
        recent["close"].iloc[-1]
    )

    support = max(
        (
            x for x in swing_lows
            if x < current_price
        ),
        default=None
    )

    resistance = min(
        (
            x for x in swing_highs
            if x > current_price
        ),
        default=None
    )

    return {
        "direction": direction,
        "strength": abs(bullish - bearish) * 50,
        "support": support,
        "resistance": resistance
    }


# ================================================================
# MARKET REGIME
# ================================================================

def market_regime(df):

    if df.empty or len(df) < 50:

        return {
            "regime": "UNKNOWN",
            "volatility": "UNKNOWN",
            "efficiency": 0
        }

    recent = df.tail(30)

    close = recent["close"]

    total_move = abs(
        close.iloc[-1] - close.iloc[0]
    )

    path_move = close.diff().abs().sum()

    efficiency = (
        total_move / path_move
        if path_move > 0 else 0
    )

    atr_pct = safe_float(
        recent["atr_pct"].iloc[-1]
    )

    structure = market_structure(df)

    if atr_pct > 1.0:
        volatility = "EXTREME"

    elif atr_pct > 0.45:
        volatility = "HIGH"

    elif atr_pct > 0.15:
        volatility = "NORMAL"

    else:
        volatility = "LOW"

    if efficiency > 0.55:

        if structure["direction"] == "BULLISH":
            regime = "TRENDING BULLISH"

        elif structure["direction"] == "BEARISH":
            regime = "TRENDING BEARISH"

        else:
            regime = "TRENDING"

    elif efficiency < 0.20:
        regime = "RANGE"

    else:
        regime = "TRANSITION"

    return {
        "regime": regime,
        "volatility": volatility,
        "efficiency": efficiency
    }


# ================================================================
# DELTA ENGINE
# ================================================================

def calculate_delta(trades):

    if not trades:

        return {
            "buy_volume": 0,
            "sell_volume": 0,
            "delta": 0,
            "delta_pct": 0,
            "classification": "NO DATA"
        }

    buy = sum(
        x["size"]
        for x in trades
        if x["side"] == "buy"
    )

    sell = sum(
        x["size"]
        for x in trades
        if x["side"] == "sell"
    )

    total = buy + sell

    delta = buy - sell

    delta_pct = (
        delta / total * 100
        if total > 0 else 0
    )

    if delta_pct > 15:
        classification = "STRONG BUYING"

    elif delta_pct > 4:
        classification = "BUYING"

    elif delta_pct < -15:
        classification = "STRONG SELLING"

    elif delta_pct < -4:
        classification = "SELLING"

    else:
        classification = "BALANCED"

    return {
        "buy_volume": buy,
        "sell_volume": sell,
        "delta": delta,
        "delta_pct": delta_pct,
        "classification": classification
    }


# ================================================================
# ORDER BOOK ENGINE
# ================================================================

def analyze_order_book(book):

    if not book:

        return {
            "imbalance": 0,
            "classification": "NO DATA",
            "bid_wall": None,
            "ask_wall": None
        }

    imbalance = book["imbalance"]

    if imbalance > 0.20:
        classification = "BUY SIDE DOMINANT"

    elif imbalance < -0.20:
        classification = "SELL SIDE DOMINANT"

    else:
        classification = "BALANCED"

    bid_wall = max(
        book["bids"],
        key=lambda x: x[1],
        default=None
    )

    ask_wall = max(
        book["asks"],
        key=lambda x: x[1],
        default=None
    )

    return {
        "imbalance": imbalance,
        "classification": classification,
        "bid_wall": bid_wall,
        "ask_wall": ask_wall
    }


# ================================================================
# OPEN INTEREST ENGINE
# ================================================================

def analyze_open_interest(oi):

    if oi is None:

        return {
            "oi": None,
            "change_pct": None,
            "classification": "NO DATA"
        }

    connection = get_connection()

    previous = connection.execute(
        """
        SELECT oi, price
        FROM market_snapshots
        WHERE oi IS NOT NULL
        ORDER BY id DESC
        LIMIT 1
        """
    ).fetchone()

    current_price = st.session_state.get(
        "current_price",
        None
    )

    if previous:

        previous_oi = safe_float(
            previous["oi"]
        )

        previous_price = safe_float(
            previous["price"]
        )

        oi_change = percentage_change(
            oi["oi"],
            previous_oi
        )

        price_change = percentage_change(
            current_price,
            previous_price
        ) if current_price and previous_price else 0

        if oi_change > 0.3 and price_change > 0.05:
            classification = "LONG BUILDING"

        elif oi_change > 0.3 and price_change < -0.05:
            classification = "SHORT BUILDING"

        elif oi_change < -0.3 and price_change > 0.05:
            classification = "SHORT CLOSING"

        elif oi_change < -0.3 and price_change < -0.05:
            classification = "LONG CLOSING"

        else:
            classification = "NEUTRAL / MIXED"

    else:

        oi_change = None
        classification = "WAITING FOR COMPARISON DATA"

    connection.close()

    return {
        "oi": oi["oi"],
        "change_pct": oi_change,
        "classification": classification
    }


# ================================================================
# FUNDING ENGINE
# ================================================================

def analyze_funding(funding):

    if funding is None:

        return {
            "rate": None,
            "classification": "NO DATA"
        }

    rate = funding["funding"]

    if rate > 0.001:
        classification = "EXTREME LONG CROWDING"

    elif rate > 0.0005:
        classification = "LONGS DOMINANT"

    elif rate < -0.001:
        classification = "EXTREME SHORT CROWDING"

    elif rate < -0.0005:
        classification = "SHORTS DOMINANT"

    else:
        classification = "BALANCED"

    return {
        "rate": rate,
        "classification": classification
    }


# ================================================================
# LIQUIDITY ENGINE
# ================================================================

def analyze_liquidity(df):

    if df.empty or len(df) < 30:

        return {
            "sweep": "UNKNOWN",
            "breakout": "UNKNOWN"
        }

    latest = df.iloc[-1]

    previous = df.iloc[-21:-1]

    previous_high = previous["high"].max()
    previous_low = previous["low"].min()

    bullish_sweep = (
        latest["low"] < previous_low
        and latest["close"] > previous_low
    )

    bearish_sweep = (
        latest["high"] > previous_high
        and latest["close"] < previous_high
    )

    if bullish_sweep:
        sweep = "BULLISH LIQUIDITY SWEEP"

    elif bearish_sweep:
        sweep = "BEARISH LIQUIDITY SWEEP"

    else:
        sweep = "NO CONFIRMED SWEEP"

    if latest["close"] > previous_high:
        breakout = "BULLISH BREAKOUT"

    elif latest["close"] < previous_low:
        breakout = "BEARISH BREAKDOWN"

    else:
        breakout = "NO BREAKOUT"

    return {
        "sweep": sweep,
        "breakout": breakout,
        "previous_high": previous_high,
        "previous_low": previous_low
    }


# ================================================================
# BREAKOUT / RETEST
# ================================================================

def analyze_retest(df):

    if df.empty or len(df) < 25:
        return "INSUFFICIENT DATA"

    recent = df.tail(25)

    previous = recent.iloc[:-2]

    prior = recent.iloc[-2]
    latest = recent.iloc[-1]

    high = previous["high"].max()
    low = previous["low"].min()

    atr = safe_float(latest["atr"])

    if atr <= 0:
        return "NO VALID ATR"

    bullish_break = prior["close"] > high

    bullish_retest = (
        latest["low"] <= high + atr * 0.25
        and latest["close"] > high
    )

    bearish_break = prior["close"] < low

    bearish_retest = (
        latest["high"] >= low - atr * 0.25
        and latest["close"] < low
    )

    if bullish_break and bullish_retest:
        return "BULLISH BREAKOUT RETEST"

    if bearish_break and bearish_retest:
        return "BEARISH BREAKOUT RETEST"

    return "NO CONFIRMED RETEST"


# ================================================================
# POSITION FLOW
# ================================================================

def analyze_position_flow(oi, delta, structure):

    events = []

    oi_state = oi["classification"]

    if oi_state in [
        "LONG BUILDING",
        "SHORT BUILDING",
        "LONG CLOSING",
        "SHORT CLOSING"
    ]:
        events.append(oi_state)

    if (
        structure["direction"] == "BULLISH"
        and delta["classification"] in [
            "SELLING",
            "STRONG SELLING"
        ]
    ):
        events.append(
            "BUYING WEAKNESS / SELLING PRESSURE"
        )

    if (
        structure["direction"] == "BEARISH"
        and delta["classification"] in [
            "BUYING",
            "STRONG BUYING"
        ]
    ):
        events.append(
            "SELLING WEAKNESS / BUYING PRESSURE"
        )

    if (
        oi_state == "LONG BUILDING"
        and delta["delta_pct"] < -10
    ):
        events.append(
            "LONG BUILDING WITH NEGATIVE DELTA"
        )

    if (
        oi_state == "SHORT BUILDING"
        and delta["delta_pct"] > 10
    ):
        events.append(
            "SHORT BUILDING WITH POSITIVE DELTA"
        )

    if not events:
        events.append(
            "NO CLEAR POSITION FLOW EVENT"
        )

    return events


# ================================================================
# DIVERGENCE ENGINE
# ================================================================

def analyze_divergences(df, delta):

    divergences = []

    if df.empty or len(df) < 20:
        return divergences

    recent = df.tail(20)

    price_change = percentage_change(
        recent["close"].iloc[-1],
        recent["close"].iloc[0]
    )

    rsi_change = (
        safe_float(recent["rsi"].iloc[-1])
        - safe_float(recent["rsi"].iloc[0])
    )

    if price_change > 0.4 and rsi_change < -5:
        divergences.append(
            "POSSIBLE BEARISH RSI DIVERGENCE"
        )

    if price_change < -0.4 and rsi_change > 5:
        divergences.append(
            "POSSIBLE BULLISH RSI DIVERGENCE"
        )

    if price_change > 0.4 and delta["delta_pct"] < -10:
        divergences.append(
            "PRICE UP / DELTA DOWN"
        )

    if price_change < -0.4 and delta["delta_pct"] > 10:
        divergences.append(
            "PRICE DOWN / DELTA UP"
        )

    return divergences


# ================================================================
# MULTI TIMEFRAME ENGINE
# ================================================================

def analyze_timeframes(data):

    result = {}

    for timeframe, df in data.items():

        if df.empty:

            result[timeframe] = {
                "direction": "NO DATA",
                "structure": "UNKNOWN",
                "rsi": None,
                "roc": None
            }

            continue

        latest = df.iloc[-1]

        structure = market_structure(df)

        result[timeframe] = {
            "direction": structure["direction"],
            "structure": structure["direction"],
            "rsi": safe_float(latest["rsi"]),
            "roc": safe_float(latest["roc_5"])
        }

    return result


# ================================================================
# HISTORICAL PATTERN ENGINE
# ================================================================

def analyze_historical_patterns(df):

    if df.empty or len(df) < 100:

        return {
            "matches": 0,
            "bullish_rate": None,
            "bearish_rate": None,
            "average_return": None
        }

    data = df.reset_index(drop=True)

    current = data.iloc[-1]

    current_features = np.array([
        safe_float(current["rsi"]),
        safe_float(current["roc_5"]),
        safe_float(current["atr_pct"]),
        safe_float(current["volume_ratio"], 1)
    ])

    scale = np.array([
        20,
        1,
        0.3,
        1
    ])

    matches = []

    start = max(
        30,
        len(data) - 250
    )

    for i in range(start, len(data) - 6):

        row = data.iloc[i]

        features = np.array([
            safe_float(row["rsi"]),
            safe_float(row["roc_5"]),
            safe_float(row["atr_pct"]),
            safe_float(row["volume_ratio"], 1)
        ])

        distance = np.sqrt(
            np.mean(
                ((features - current_features) / scale) ** 2
            )
        )

        if distance < 1.0:

            future_return = percentage_change(
                data.iloc[i + 5]["close"],
                row["close"]
            )

            matches.append({
                "distance": distance,
                "return": future_return
            })

    matches = sorted(
        matches,
        key=lambda x: x["distance"]
    )[:20]

    if not matches:

        return {
            "matches": 0,
            "bullish_rate": None,
            "bearish_rate": None,
            "average_return": None
        }

    returns = [
        x["return"]
        for x in matches
    ]

    return {
        "matches": len(returns),
        "bullish_rate": (
            sum(x > 0 for x in returns)
            / len(returns) * 100
        ),
        "bearish_rate": (
            sum(x < 0 for x in returns)
            / len(returns) * 100
        ),
        "average_return": float(
            np.mean(returns)
        )
    }


# ================================================================
# MACRO CALENDAR (NO API REQUIRED) & MACRO SIGNAL ENGINE
# ================================================================

def _nth_weekday_of_month(year, month, weekday, n):
    cal = calendar.monthcalendar(year, month)
    days = [row[weekday] for row in cal if row[weekday] != 0]
    if n > 0:
        idx = n - 1
        return date(year, month, days[idx]) if idx < len(days) else None
    else:
        idx = n
        return date(year, month, days[idx]) if abs(idx) <= len(days) else None


def _generate_rule_based_macro_events(start_date, end_date):
    events = []
    cur_year, cur_month = start_date.year, start_date.month
    end_year, end_month = end_date.year, end_date.month

    while (cur_year, cur_month) <= (end_year, end_month):
        # 1. US Non-Farm Payrolls (NFP): 1st Friday of month, 13:30 UTC
        nfp_day = _nth_weekday_of_month(cur_year, cur_month, calendar.FRIDAY, 1)
        if nfp_day:
            events.append({
                "id": None,
                "date": nfp_day.isoformat(),
                "time_utc": "13:30",
                "currency": "USD",
                "event": "US Non-Farm Payrolls (NFP) & Unemployment",
                "impact": "HIGH",
                "source": "RULE_BASED",
                "notes": "1st Friday of month (12:30/13:30 UTC depending on DST)"
            })

        # 2. US CPI: typically mid-month weekday, 13:30 UTC
        cpi_candidate = date(cur_year, cur_month, 12)
        while cpi_candidate.weekday() >= 5:
            cpi_candidate += timedelta(days=1)
        events.append({
            "id": None,
            "date": cpi_candidate.isoformat(),
            "time_utc": "13:30",
            "currency": "USD",
            "event": "US CPI Inflation (Est. Window)",
            "impact": "HIGH",
            "source": "RULE_BASED",
            "notes": "Typical mid-month US CPI release window"
        })

        # 3. FOMC Rate Decision: 8 scheduled meetings per year
        if cur_month in (1, 3, 5, 6, 7, 9, 11, 12):
            fomc_day = _nth_weekday_of_month(cur_year, cur_month, calendar.WEDNESDAY, 3)
            if fomc_day:
                events.append({
                    "id": None,
                    "date": fomc_day.isoformat(),
                    "time_utc": "19:00",
                    "currency": "USD",
                    "event": "FOMC Interest Rate Decision & Press Conf. (Est.)",
                    "impact": "HIGH",
                    "source": "RULE_BASED",
                    "notes": "Scheduled FOMC window (18:00/19:00 UTC)"
                })

        # 4. US Core PCE Price Index: last Friday of month, 13:30 UTC
        pce_day = _nth_weekday_of_month(cur_year, cur_month, calendar.FRIDAY, -1)
        if pce_day:
            events.append({
                "id": None,
                "date": pce_day.isoformat(),
                "time_utc": "13:30",
                "currency": "USD",
                "event": "US Core PCE Price Index",
                "impact": "HIGH",
                "source": "RULE_BASED",
                "notes": "Last Friday of month Fed-preferred inflation gauge"
            })

        # 5. ECB Interest Rate Decision
        if cur_month in (1, 3, 4, 6, 7, 9, 10, 12):
            ecb_day = _nth_weekday_of_month(cur_year, cur_month, calendar.THURSDAY, 2)
            if ecb_day:
                events.append({
                    "id": None,
                    "date": ecb_day.isoformat(),
                    "time_utc": "12:15",
                    "currency": "EUR",
                    "event": "ECB Interest Rate Decision (Est.)",
                    "impact": "MEDIUM",
                    "source": "RULE_BASED",
                    "notes": "ECB governing council rate announcement"
                })

        # 6. US GDP
        if cur_month in (1, 4, 7, 10):
            last_d = calendar.monthrange(cur_year, cur_month)[1]
            gdp_day = date(cur_year, cur_month, min(28, last_d))
            while gdp_day.weekday() >= 5:
                gdp_day -= timedelta(days=1)
            events.append({
                "id": None,
                "date": gdp_day.isoformat(),
                "time_utc": "13:30",
                "currency": "USD",
                "event": "US GDP Advance Release (Est.)",
                "impact": "MEDIUM",
                "source": "RULE_BASED",
                "notes": "Quarterly US GDP release window"
            })

        if cur_month == 12:
            cur_year += 1
            cur_month = 1
        else:
            cur_month += 1

    filtered = [
        e for e in events
        if start_date.isoformat() <= e["date"] <= end_date.isoformat()
    ]
    return filtered


def add_macro_event(event_date, time_utc, currency, event_name, impact="HIGH", notes=""):
    connection = get_connection()
    connection.execute(
        """
        INSERT INTO macro_calendar
        (event_date, time_utc, currency, event_name, impact, notes)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            str(event_date),
            str(time_utc),
            str(currency).upper(),
            str(event_name),
            str(impact).upper(),
            str(notes)
        )
    )
    connection.commit()
    connection.close()
    add_event("MACRO CALENDAR", f"Added event: {event_date} {time_utc} UTC | {currency} {event_name} ({impact})")


def delete_macro_event(event_id):
    connection = get_connection()
    connection.execute(
        "DELETE FROM macro_calendar WHERE id = ?",
        (int(event_id),)
    )
    connection.commit()
    connection.close()
    add_event("MACRO CALENDAR", f"Deleted custom event ID #{event_id}")


def get_macro_calendar_data(lookahead_days=14, lookback_days=2):
    now = now_utc()
    start_dt = (now - timedelta(days=lookback_days)).date()
    end_dt = (now + timedelta(days=lookahead_days)).date()

    combined = _generate_rule_based_macro_events(start_dt, end_dt)

    custom_rows = database_read(
        """
        SELECT id, event_date, time_utc, currency, event_name, impact, notes
        FROM macro_calendar
        WHERE event_date BETWEEN ? AND ?
        ORDER BY event_date ASC, time_utc ASC
        """,
        (start_dt.isoformat(), end_dt.isoformat())
    )

    if not custom_rows.empty:
        for _, r in custom_rows.iterrows():
            combined.append({
                "id": int(r["id"]),
                "date": str(r["event_date"]),
                "time_utc": str(r["time_utc"]),
                "currency": str(r["currency"]),
                "event": str(r["event_name"]),
                "impact": str(r["impact"]),
                "source": "CUSTOM_DB",
                "notes": str(r["notes"] or "")
            })

    enriched = []
    next_high_impact = None
    min_abs_hours = None
    imminent_high_impact = None

    for ev in combined:
        ev_date_str = ev["date"]
        ev_time_str = (ev.get("time_utc") or "00:00").strip()
        try:
            hh, mm = [int(x) for x in ev_time_str.split(":")[:2]]
        except Exception:
            hh, mm = 0, 0

        try:
            y, m, d = [int(x) for x in ev_date_str.split("-")[:3]]
            ev_dt = datetime(y, m, d, hh, mm, tzinfo=timezone.utc)
        except Exception:
            ev_dt = now

        hours_until = (ev_dt - now).total_seconds() / 3600.0
        abs_h = abs(hours_until)

        if min_abs_hours is None or abs_h < min_abs_hours:
            min_abs_hours = abs_h

        if hours_until >= -1.0 and ev.get("impact") == "HIGH" and next_high_impact is None:
            next_high_impact = {
                **ev,
                "hours_until": round(hours_until, 2)
            }

        if abs_h <= 2.0 and ev.get("impact") == "HIGH":
            if imminent_high_impact is None or abs_h < abs(imminent_high_impact["hours_until"]):
                imminent_high_impact = {
                    **ev,
                    "hours_until": round(hours_until, 2)
                }

        enriched.append({
            **ev,
            "hours_until": round(hours_until, 2)
        })

    enriched.sort(key=lambda x: (x["date"], x["time_utc"]))

    if imminent_high_impact is not None or (min_abs_hours is not None and min_abs_hours <= 2.0):
        risk_window = "IMMINENT / ACTIVE EVENT WINDOW (<=2h)"
    elif min_abs_hours is not None and min_abs_hours <= 24.0:
        risk_window = "ELEVATED MACRO DAY (<=24h)"
    else:
        risk_window = "NORMAL MACRO ENVIRONMENT"

    return {
        "mode": "NO_API_LOCAL_AND_RULE_ENGINE",
        "risk_window": risk_window,
        "next_high_impact": next_high_impact,
        "imminent_high_impact": imminent_high_impact,
        "min_abs_hours": round(min_abs_hours, 2) if min_abs_hours is not None else None,
        "events": enriched
    }


def analyze_macro_signal(macro_calendar):
    risk_window = macro_calendar.get("risk_window", "NORMAL MACRO ENVIRONMENT")
    next_hi = macro_calendar.get("next_high_impact")
    imminent_hi = macro_calendar.get("imminent_high_impact")
    min_h = macro_calendar.get("min_abs_hours")

    # Pont-módosító a szignálhoz (negatív = óvatosság/kockázatcsökkentés)
    score_adjustment = 0.0
    block_new_trade = False
    status = "CLEAR"
    bias = "NEUTRAL"
    reason = "No immediate high-impact macro event; macro environment is normal."

    if imminent_hi is not None and abs(safe_float(imminent_hi.get("hours_until"), 999)) <= 1.0:
        status = "HIGH RISK - IMMINENT HIGH-IMPACT EVENT (<=1h)"
        score_adjustment = -25.0
        block_new_trade = True
        bias = "DEFENSIVE / NO NEW ENTRY"
        reason = (
            f"High-impact event ({imminent_hi.get('currency')} {imminent_hi.get('event')}) "
            f"is within 1h ({imminent_hi.get('hours_until')}h)."
        )
    elif risk_window.startswith("IMMINENT"):
        status = "HIGH RISK - EVENT WINDOW (<=2h)"
        score_adjustment = -15.0
        bias = "DEFENSIVE"
        reason = f"Macro event within 2h ({min_h}h). Expect volatility spikes and liquidity sweeps."
    elif risk_window.startswith("ELEVATED"):
        status = "ELEVATED MACRO (<=24h)"
        score_adjustment = -5.0
        bias = "CAUTIOUS"
        if next_hi:
            reason = (
                f"Upcoming HIGH impact event in {next_hi.get('hours_until')}h: "
                f"{next_hi.get('currency')} {next_hi.get('event')}."
            )
        else:
            reason = f"Macro event within 24h ({min_h}h)."

    return {
        "status": status,
        "bias": bias,
        "score_adjustment": score_adjustment,
        "block_new_trade": block_new_trade,
        "next_high_impact": next_hi,
        "reason": reason
    }


def get_macro_status(macro_cal=None, macro_signal=None):

    if macro_cal is None:
        macro_cal = get_macro_calendar_data()

    if macro_signal is None:
        macro_signal = analyze_macro_signal(macro_cal)

    return {
        "available": False,
        "dxy": None,
        "us10y": None,
        "fed": None,
        "calendar_available": True,
        "calendar_risk_window": macro_cal["risk_window"],
        "macro_status": macro_signal["status"],
        "macro_bias": macro_signal["bias"],
        "macro_reason": macro_signal["reason"],
        "next_high_impact": macro_cal["next_high_impact"],
        "message": (
            "No verified live spot macro feed configured. "
            "No-API rule & local macro calendar is active and integrated into Signal & AI analysis."
        )
    }


# ================================================================
# ENTRY QUALITY ENGINE (WITH MACRO INTEGRATION)
# ================================================================

def calculate_entry_quality(
    df,
    structure,
    regime,
    oi,
    delta,
    book,
    liquidity,
    mtf,
    funding,
    retest,
    macro_signal=None
):

    components = {
        "structure": 0,
        "order_flow": 0,
        "open_interest": 0,
        "liquidity": 0,
        "multi_timeframe": 0,
        "breakout_retest": 0,
        "funding": 0,
        "macro": 0
    }

    if df.empty:

        return {
            "direction": "WAIT",
            "raw_score": 0,
            "score": 0,
            "quality": 0,
            "stage": "WAIT",
            "components": components
        }

    # Structure
    if structure["direction"] == "BULLISH":
        components["structure"] = 20

    elif structure["direction"] == "BEARISH":
        components["structure"] = -20

    # Delta
    components["order_flow"] = clamp(
        delta["delta_pct"] / 100 * 20,
        -20,
        20
    )

    # OI
    oi_state = oi["classification"]

    if oi_state == "LONG BUILDING":
        components["open_interest"] = 15

    elif oi_state == "SHORT BUILDING":
        components["open_interest"] = -15

    elif oi_state == "SHORT CLOSING":
        components["open_interest"] = 7

    elif oi_state == "LONG CLOSING":
        components["open_interest"] = -7

    # Liquidity / order book
    components["liquidity"] = (
        book["imbalance"] * 15
    )

    if liquidity["sweep"] == "BULLISH LIQUIDITY SWEEP":
        components["liquidity"] += 5

    elif liquidity["sweep"] == "BEARISH LIQUIDITY SWEEP":
        components["liquidity"] -= 5

    components["liquidity"] = clamp(
        components["liquidity"],
        -15,
        15
    )

    # MTF
    weights = {
        "1m": 1,
        "5m": 2,
        "15m": 3,
        "1H": 4,
        "4H": 5
    }

    mtf_score = 0

    for tf, weight in weights.items():

        item = mtf.get(tf, {})

        direction = item.get(
            "structure",
            "UNKNOWN"
        )

        if direction == "BULLISH":
            mtf_score += weight

        elif direction == "BEARISH":
            mtf_score -= weight

    components["multi_timeframe"] = (
        mtf_score / sum(weights.values()) * 15
    )

    # Retest
    if retest == "BULLISH BREAKOUT RETEST":
        components["breakout_retest"] = 10

    elif retest == "BEARISH BREAKOUT RETEST":
        components["breakout_retest"] = -10

    # Funding
    if funding["classification"] == "EXTREME LONG CROWDING":
        components["funding"] = -5

    elif funding["classification"] == "EXTREME SHORT CROWDING":
        components["funding"] = 5

    # Macro adjustment (dampens conviction toward 0 during high-risk macro windows)
    raw_score = sum(
        v for k, v in components.items() if k != "macro"
    )

    macro_adj = safe_float(
        macro_signal.get("score_adjustment", 0.0) if isinstance(macro_signal, dict) else 0.0
    )

    if macro_adj < 0 and raw_score != 0:
        # Reduce magnitude of directional score by macro_adj (e.g., -25 reduces |raw_score| by 25, floored at 0)
        reduction = min(abs(raw_score), abs(macro_adj))
        macro_component = -reduction if raw_score > 0 else reduction
    else:
        macro_component = 0.0

    components["macro"] = round(macro_component, 2)

    score = raw_score + components["macro"]

    direction = (
        "LONG" if score > 0
        else "SHORT" if score < 0
        else "WAIT"
    )

    quality = abs(score)

    if quality >= 85:
        stage = "HIGH QUALITY SETUP"

    elif quality >= 75:
        stage = "STRONG SETUP"

    elif quality >= 65:
        stage = "EARLY SETUP"

    elif quality >= 50:
        stage = "WATCH"

    else:
        stage = "WAIT"

    if macro_signal and macro_signal.get("block_new_trade"):
        stage = "MACRO EVENT HOLD (<=1h)"
    elif regime["volatility"] == "EXTREME":
        stage = "HIGH VOLATILITY - CAUTION"

    return {
        "direction": direction,
        "raw_score": round(raw_score, 2),
        "score": round(score, 2),
        "quality": round(quality, 2),
        "stage": stage,
        "components": components
    }


# ================================================================
# REVERSAL ENGINE
# ================================================================

def analyze_reversals(
    df,
    structure,
    delta,
    liquidity,
    divergences
):

    signals = []

    if df.empty:
        return signals

    latest = df.iloc[-1]

    rsi = safe_float(latest["rsi"])

    if rsi < 30 and delta["delta_pct"] > 10:
        signals.append(
            "POSSIBLE BULLISH MOMENTUM REVERSAL"
        )

    if rsi > 70 and delta["delta_pct"] < -10:
        signals.append(
            "POSSIBLE BEARISH MOMENTUM REVERSAL"
        )

    if liquidity["sweep"] == "BULLISH LIQUIDITY SWEEP":
        signals.append(
            "BULLISH LIQUIDITY REVERSAL SETUP"
        )

    if liquidity["sweep"] == "BEARISH LIQUIDITY SWEEP":
        signals.append(
            "BEARISH LIQUIDITY REVERSAL SETUP"
        )

    signals.extend(divergences)

    if not signals:
        signals.append(
            "NO CONFIRMED REVERSAL"
        )

    return signals


# ================================================================
# NEWS ENGINE
# ================================================================

def fetch_news():

    articles = []
    errors = []

    for source, url in NEWS_FEEDS.items():

        try:

            response = requests.get(
                url,
                timeout=REQUEST_TIMEOUT,
                headers={
                    "User-Agent": "Mozilla/5.0 SALIGA AI"
                }
            )

            response.raise_for_status()

            root = ET.fromstring(
                response.content
            )

            for item in root.findall(".//item")[:12]:

                title = item.findtext("title") or ""

                description = (
                    item.findtext("description") or ""
                )

                published = (
                    item.findtext("pubDate") or ""
                )

                link = item.findtext("link") or ""

                if title:

                    articles.append({
                        "source": source,
                        "title": " ".join(title.split())[:300],
                        "description": " ".join(
                            description.split()
                        )[:600],
                        "published": published,
                        "link": link
                    })

        except Exception as error:

            errors.append(
                f"{source}: {error}"
            )

    unique = {}

    for article in articles:

        key = hashlib.sha256(
            article["title"].lower().encode()
        ).hexdigest()

        unique[key] = article

    result = list(unique.values())

    return result[:20], errors


POSITIVE_WORDS = [
    "approval",
    "adoption",
    "institutional",
    "inflow",
    "bullish",
    "surge",
    "record high",
    "partnership",
    "reserve",
    "accumulation",
    "positive"
]

NEGATIVE_WORDS = [
    "hack",
    "exploit",
    "lawsuit",
    "ban",
    "outflow",
    "liquidation",
    "crash",
    "bearish",
    "fraud",
    "investigation",
    "selloff",
    "bankruptcy"
]


def analyze_news_sentiment(news):

    scored = []

    for article in news:

        content = (
            article["title"]
            + " "
            + article["description"]
        ).lower()

        positive = sum(
            word in content
            for word in POSITIVE_WORDS
        )

        negative = sum(
            word in content
            for word in NEGATIVE_WORDS
        )

        score = clamp(
            positive - negative,
            -2,
            2
        )

        scored.append({
            **article,
            "sentiment": score
        })

    if not scored:

        return {
            "score": 0,
            "classification": "NO NEWS DATA",
            "articles": []
        }

    average = float(
        np.mean([
            x["sentiment"]
            for x in scored
        ])
    )

    if average > 0.5:
        classification = "POSITIVE NEWS FLOW"

    elif average < -0.5:
        classification = "NEGATIVE NEWS FLOW"

    else:
        classification = "MIXED / NEUTRAL NEWS FLOW"

    return {
        "score": average,
        "classification": classification,
        "articles": scored
    }


def calculate_news_hash(news):

    content = "|".join(
        x["title"]
        for x in news
    )

    return hashlib.sha256(
        content.encode()
    ).hexdigest()


# ================================================================
# GROQ AI ENGINE (FIXED + MACRO ANALYSIS)
# ================================================================

def call_groq(payload):

    if not GROQ_API_KEY:
        raise RuntimeError(
            "GROQ_API_KEY is missing from secrets.toml or environment."
        )

    system_prompt = (
        "You are SALIGA AI PRO 31.0, an analytical BTC market intelligence engine. "
        "You MUST output valid JSON only. Do not include markdown code blocks like ```json or any conversational text. "
        "Carefully analyze macro_signal, macro, and macro_calendar (upcoming/recent macro dates, impact levels, hours_until, risk_window) "
        "and explicitly evaluate how macro conditions affect BTC direction, signal quality, and risk in macro_analysis, news_impact, and risk. "
        "If an imminent high-impact macro event is within 1 hour, lean toward WAIT or flag conflict=true unless a clear post-event move is established. "
        "Your response must match this exact JSON structure:\n"
        "{\n"
        ' "direction": "LONG",\n'
        ' "confidence": 0,\n'
        ' "conflict": false,\n'
        ' "summary": "string",\n'
        ' "technical_analysis": "string",\n'
        ' "order_flow_analysis": "string",\n'
        ' "liquidity_analysis": "string",\n'
        ' "macro_analysis": "string",\n'
        ' "news_impact": "string",\n'
        ' "risk": "string",\n'
        ' "invalidation": "string"\n'
        "}"
    )

    user_prompt = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":")
    )

    request_data = {
        "model": GROQ_MODEL,
        "temperature": 0.1,
        "max_tokens": 1400,
        "response_format": {
            "type": "json_object"
        },
        "messages": [
            {
                "role": "system",
                "content": system_prompt
            },
            {
                "role": "user",
                "content": user_prompt
            }
        ]
    }

    response = requests.post(
        GROQ_API_URL,
        headers={
            "Authorization": f"Bearer {GROQ_API_KEY}",
            "Content-Type": "application/json"
        },
        json=request_data,
        timeout=45
    )

    if response.status_code == 429:

        retry_after = response.headers.get(
            "Retry-After",
            "not provided"
        )

        raise RuntimeError(
            "Groq rate limit 429. "
            f"Retry-After: {retry_after}. "
            "The AI result was not marked successful."
        )

    if response.status_code >= 400:

        raise RuntimeError(
            f"Groq HTTP {response.status_code}: "
            f"{response.text[:1000]}"
        )

    result = response.json()

    content = result[
        "choices"
    ][0]["message"]["content"]

    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        clean_content = content.replace("```json", "").replace("```", "").strip()
        parsed = json.loads(clean_content)

    if not isinstance(parsed, dict):
        raise RuntimeError(
            "Groq returned an invalid response structure."
        )

    direction = str(
        parsed.get("direction", "WAIT")
    ).upper()

    if direction not in [
        "LONG",
        "SHORT",
        "WAIT"
    ]:
        direction = "WAIT"

    parsed["direction"] = direction

    parsed["conflict"] = bool(
        parsed.get("conflict", False)
    )

    if "macro_analysis" not in parsed or not parsed["macro_analysis"]:
        parsed["macro_analysis"] = "No separate macro analysis returned."

    return parsed


# ================================================================
# AI SCHEDULER
# ================================================================

def ai_due(news_id, oi, delta, liquidity, macro_signal=None):

    current = time.time()

    normal_due = (
        current - st.session_state.last_ai_time
        >= ai_interval * 60
    )

    event_detected = False

    oi_change = oi.get("change_pct") if oi else None

    if (
        oi_change is not None
        and abs(oi_change) > 0.5
    ):
        event_detected = True

    if abs(delta["delta_pct"]) > 25:
        event_detected = True

    if liquidity["sweep"] not in [
        "NO CONFIRMED SWEEP",
        "UNKNOWN"
    ]:
        event_detected = True

    if macro_signal and macro_signal.get("status", "").startswith("HIGH RISK"):
        event_detected = True

    previous_hash = st.session_state.last_news_hash

    if (
        previous_hash is not None
        and news_id != previous_hash
    ):
        event_detected = True

    cooldown_passed = (
        current - st.session_state.last_trigger_time
        >= ai_cooldown * 60
    )

    if normal_due:
        return True, "NORMAL SCHEDULE"

    if event_detected and cooldown_passed:
        return True, "MARKET / MACRO EVENT"

    return False, "NOT DUE"


# ================================================================
# AI & MACRO CONFLICT GATE + UNIFIED SIGNAL
# ================================================================

def check_ai_gate(entry, ai_result, macro_signal=None, macro_gate_active=True):

    if macro_gate_active and macro_signal and macro_signal.get("block_new_trade"):
        return {
            "allowed": False,
            "reason": f"Blocked by Macro Gate: {macro_signal.get('reason')}"
        }

    if ai_result is None:

        return {
            "allowed": False,
            "reason": "No successful AI validation available."
        }

    ai_direction = ai_result["direction"]

    if ai_result.get("conflict", False):

        return {
            "allowed": False,
            "reason": "AI detected conflicting technical/macro/flow evidence."
        }

    if ai_direction == "WAIT":

        return {
            "allowed": False,
            "reason": "AI recommends WAIT."
        }

    if (
        entry["direction"] in ["LONG", "SHORT"]
        and ai_direction != entry["direction"]
    ):

        return {
            "allowed": False,
            "reason": "AI and technical direction conflict."
        }

    return {
        "allowed": True,
        "reason": "AI, technicals, and macro gate aligned."
    }


def build_unified_signal(entry, ai_result, ai_gate, macro_signal, risk_levels):
    tech_dir = entry["direction"]
    quality = safe_float(entry["quality"])
    ai_dir = ai_result["direction"] if isinstance(ai_result, dict) else "N/A"
    macro_status = macro_signal.get("status", "CLEAR") if isinstance(macro_signal, dict) else "CLEAR"

    if ai_gate["allowed"] and tech_dir in ("LONG", "SHORT") and quality >= 75 and risk_levels is not None:
        label = f"ACTIVE {tech_dir} SIGNAL"
        actionable = True
    elif tech_dir in ("LONG", "SHORT") and quality >= 65 and not ai_gate["allowed"]:
        label = f"FILTERED {tech_dir} (GATE BLOCKED)"
        actionable = False
    elif tech_dir in ("LONG", "SHORT") and quality >= 50:
        label = f"WATCH {tech_dir}"
        actionable = False
    else:
        label = "NO TRADE / WAIT"
        actionable = False

    parts = [
        f"Tech: {tech_dir} ({quality:.1f}/100)",
        f"AI: {ai_dir}",
        f"Macro: {macro_status}"
    ]

    return {
        "label": label,
        "direction": tech_dir if actionable else "WAIT",
        "actionable": actionable,
        "quality": quality,
        "macro_status": macro_status,
        "macro_bias": macro_signal.get("bias", "NEUTRAL") if isinstance(macro_signal, dict) else "NEUTRAL",
        "gate_reason": ai_gate["reason"],
        "summary": " | ".join(parts)
    }


# ================================================================
# RISK ENGINE
# ================================================================

def calculate_risk_levels(
    price,
    atr,
    direction,
    regime
):

    if price <= 0 or atr <= 0:
        return None

    multiplier = 1.8

    if regime["volatility"] == "HIGH":
        multiplier = 2.2

    elif regime["volatility"] == "EXTREME":
        multiplier = 2.8

    elif regime["volatility"] == "LOW":
        multiplier = 1.5

    distance = atr * multiplier

    if direction == "LONG":

        stop = price - distance

        tp1 = price + distance * 1.5
        tp2 = price + distance * 2.5
        tp3 = price + distance * 3.5

    elif direction == "SHORT":

        stop = price + distance

        tp1 = price - distance * 1.5
        tp2 = price - distance * 2.5
        tp3 = price - distance * 3.5

    else:
        return None

    return {
        "entry": price,
        "stop": stop,
        "tp1": tp1,
        "tp2": tp2,
        "tp3": tp3
    }


# ================================================================
# PAPER TRADING
# ================================================================

def get_open_trade():

    connection = get_connection()

    row = connection.execute(
        """
        SELECT *
        FROM paper_trades
        WHERE status = 'OPEN'
        ORDER BY id DESC
        LIMIT 1
        """
    ).fetchone()

    connection.close()

    return dict(row) if row else None


def get_paper_equity():

    closed = database_read(
        """
        SELECT COALESCE(SUM(net_pnl), 0) AS pnl
        FROM paper_trades
        WHERE status = 'CLOSED'
        """
    )

    pnl = safe_float(
        closed.iloc[0]["pnl"]
    ) if not closed.empty else 0

    return starting_balance + pnl


def open_paper_trade(
    direction,
    levels,
    score
):

    if not paper_enabled or levels is None:
        return

    if get_open_trade() is not None:
        return

    equity = get_paper_equity()

    risk_amount = (
        equity * risk_percent / 100
    )

    distance = abs(
        levels["entry"] - levels["stop"]
    )

    if distance <= 0:
        return

    quantity = risk_amount / distance

    connection = get_connection()

    connection.execute(
        """
        INSERT INTO paper_trades (
            opened,
            direction,
            entry,
            stop,
            tp1,
            tp2,
            tp3,
            quantity,
            score,
            status
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'OPEN')
        """,
        (
            now_string(),
            direction,
            levels["entry"],
            levels["stop"],
            levels["tp1"],
            levels["tp2"],
            levels["tp3"],
            quantity,
            score
        )
    )

    connection.commit()
    connection.close()

    add_event(
        "PAPER TRADE",
        f"Opened {direction} at {levels['entry']:.2f}"
    )


def close_paper_trade(
    trade,
    exit_price,
    reason,
    funding_rate=0
):

    entry = safe_float(trade["entry"])
    quantity = safe_float(trade["quantity"])

    if trade["direction"] == "LONG":

        gross = (
            exit_price - entry
        ) * quantity

    else:

        gross = (
            entry - exit_price
        ) * quantity

    notional_entry = entry * quantity
    notional_exit = exit_price * quantity

    fee_rate = fee_percent / 100

    slippage_rate = slippage_percent / 100

    fees = (
        notional_entry + notional_exit
    ) * fee_rate

    slippage = (
        notional_entry + notional_exit
    ) * slippage_rate

    funding = (
        notional_entry * funding_rate
    )

    if trade["direction"] == "SHORT":
        funding *= -1

    net = (
        gross
        - fees
        - slippage
        - funding
    )

    connection = get_connection()

    connection.execute(
        """
        UPDATE paper_trades
        SET
            closed = ?,
            exit_price = ?,
            gross_pnl = ?,
            fees = ?,
            slippage = ?,
            funding = ?,
            net_pnl = ?,
            status = 'CLOSED',
            reason = ?
        WHERE id = ?
        """,
        (
            now_string(),
            exit_price,
            gross,
            fees,
            slippage,
            funding,
            net,
            reason,
            trade["id"]
        )
    )

    connection.commit()
    connection.close()

    add_event(
        "PAPER TRADE",
        f"Closed {trade['direction']} | {reason} | "
        f"Net PnL {net:.2f} USDT"
    )


def manage_paper_trade(df, funding_rate=0):

    trade = get_open_trade()

    if trade is None or df.empty:
        return

    latest = df.iloc[-1]

    high = safe_float(latest["high"])
    low = safe_float(latest["low"])

    if trade["direction"] == "LONG":

        stop_hit = low <= trade["stop"]
        target_hit = high >= trade["tp1"]

    else:

        stop_hit = high >= trade["stop"]
        target_hit = low <= trade["tp1"]

    if stop_hit:

        close_paper_trade(
            trade,
            trade["stop"],
            "STOP LOSS",
            funding_rate
        )

    elif target_hit:

        close_paper_trade(
            trade,
            trade["tp1"],
            "TAKE PROFIT 1",
            funding_rate
        )


# ================================================================
# PERFORMANCE ENGINE
# ================================================================

def get_performance():

    trades = database_read(
        """
        SELECT *
        FROM paper_trades
        WHERE status = 'CLOSED'
        """
    )

    if trades.empty:

        return {
            "count": 0,
            "win_rate": 0,
            "profit_factor": 0,
            "net_pnl": 0
        }

    pnls = trades["net_pnl"].astype(float)

    wins = pnls[pnls > 0]
    losses = pnls[pnls < 0]

    gross_profit = wins.sum()
    gross_loss = abs(losses.sum())

    if gross_loss > 0:

        profit_factor = (
            gross_profit / gross_loss
        )

    elif gross_profit > 0:

        profit_factor = float("inf")

    else:

        profit_factor = 0

    return {
        "count": len(trades),
        "win_rate": (
            len(wins) / len(trades) * 100
        ),
        "profit_factor": profit_factor,
        "net_pnl": pnls.sum()
    }


# ================================================================
# SAVE MARKET SNAPSHOT (FIXED)
# ================================================================

def save_market_snapshot(
    price,
    oi,
    delta,
    funding,
    book
):
    connection = get_connection()

    oi_val = oi.get("oi") if isinstance(oi, dict) else None
    delta_val = delta.get("delta") if isinstance(delta, dict) else 0.0
    funding_val = funding.get("rate") if isinstance(funding, dict) else None
    book_imb = book.get("imbalance") if isinstance(book, dict) else 0.0

    connection.execute(
        """
        INSERT INTO market_snapshots (
            timestamp,
            price,
            oi,
            delta,
            funding,
            book_imbalance
        )
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            time.time(),
            price,
            oi_val,
            delta_val,
            funding_val,
            book_imb
        )
    )

    connection.commit()
    connection.close()


# ================================================================
# MAIN APPLICATION
# ================================================================

st.title("₿ SALIGA AI PRO 31.0")

st.caption(
    "ULTIMATE BTC INTELLIGENCE ENGINE | "
    "OKX MARKET DATA | GROQ AI | MACRO SIGNAL"
)

st.write(
    f"Last update: {now_string()}"
)

errors = []

# ------------------------------------------------
# TICKER
# ------------------------------------------------

try:

    ticker = fetch_ticker()

    price = ticker["price"]

    if price <= 0:
        raise RuntimeError("Invalid BTC price")

except Exception as error:

    st.error(
        f"OKX ticker error: {error}"
    )

    st.stop()

st.session_state.current_price = price

# ------------------------------------------------
# TIMEFRAMES
# ------------------------------------------------

timeframe_data = {}

for tf, bar in TIMEFRAMES.items():

    try:

        raw = fetch_candles(bar)

        timeframe_data[tf] = add_indicators(raw)

        if timeframe_data[tf].empty:
            errors.append(
                f"{tf}: no candle data"
            )

    except Exception as error:

        timeframe_data[tf] = pd.DataFrame()

        errors.append(
            f"{tf}: {error}"
        )

df1 = timeframe_data["1m"]
df5 = timeframe_data["5m"]
df15 = timeframe_data["15m"]
df1h = timeframe_data["1H"]
df4h = timeframe_data["4H"]

main_df = (
    df5 if not df5.empty
    else df1 if not df1.empty
    else df15
)

if main_df.empty:

    st.error(
        "No usable BTC candle data."
    )

    st.stop()

# ------------------------------------------------
# DERIVATIVES
# ------------------------------------------------

try:
    oi_raw = fetch_open_interest()

except Exception as error:
    oi_raw = None
    errors.append(f"OI: {error}")

try:
    funding_raw = fetch_funding()

except Exception as error:
    funding_raw = None
    errors.append(f"Funding: {error}")

try:
    book_raw = fetch_order_book()

except Exception as error:
    book_raw = None
    errors.append(f"Order book: {error}")

try:
    trades_raw = fetch_recent_trades()

except Exception as error:
    trades_raw = []
    errors.append(f"Trades: {error}")

# ------------------------------------------------
# NEWS
# ------------------------------------------------

news_raw, news_errors = fetch_news()

st.session_state.last_news = news_raw

news_analysis = analyze_news_sentiment(
    news_raw
)

news_id = calculate_news_hash(
    news_raw
)

# ------------------------------------------------
# MACRO & ANALYSIS
# ------------------------------------------------

macro_calendar = get_macro_calendar_data()
macro_signal = analyze_macro_signal(macro_calendar)
macro = get_macro_status(macro_calendar, macro_signal)

structure = market_structure(main_df)

regime = market_regime(main_df)

delta = calculate_delta(
    trades_raw
)

book = analyze_order_book(
    book_raw
)

oi = analyze_open_interest(
    oi_raw
)

funding = analyze_funding(
    funding_raw
)

liquidity = analyze_liquidity(
    main_df
)

retest = analyze_retest(
    main_df
)

mtf = analyze_timeframes(
    timeframe_data
)

divergences = analyze_divergences(
    main_df,
    delta
)

flow = analyze_position_flow(
    oi,
    delta,
    structure
)

patterns = analyze_historical_patterns(
    main_df
)

reversals = analyze_reversals(
    main_df,
    structure,
    delta,
    liquidity,
    divergences
)

entry = calculate_entry_quality(
    main_df,
    structure,
    regime,
    oi,
    delta,
    book,
    liquidity,
    mtf,
    funding,
    retest,
    macro_signal=macro_signal
)

latest = main_df.iloc[-1]

atr = safe_float(
    latest["atr"]
)

risk_levels = calculate_risk_levels(
    price,
    atr,
    entry["direction"],
    regime
)

# ------------------------------------------------
# SAVE SNAPSHOT
# ------------------------------------------------

save_market_snapshot(
    price,
    oi_raw,
    delta,
    funding_raw,
    book_raw
)

# ------------------------------------------------
# GROQ AI PAYLOAD (WITH MACRO)
# ------------------------------------------------

market_payload = {
    "instrument": INSTRUMENT,
    "timestamp": now_string(),
    "price": price,

    "technical": {
        "direction": entry["direction"],
        "raw_score": entry["raw_score"],
        "score": entry["score"],
        "quality": entry["quality"],
        "stage": entry["stage"],
        "structure": structure,
        "regime": regime,
        "rsi": safe_float(latest["rsi"]),
        "atr": atr,
        "multi_timeframe": mtf,
        "breakout_retest": retest
    },

    "order_flow": {
        "delta": delta,
        "position_flow": flow,
        "open_interest": oi,
        "funding": funding,
        "divergences": divergences
    },

    "liquidity": {
        "order_book": book,
        "liquidity_sweep": liquidity
    },

    "historical_patterns": patterns,

    "news": {
        "classification": news_analysis["classification"],
        "score": news_analysis["score"],
        "headlines": [
            {
                "source": x["source"],
                "title": x["title"],
                "description": x["description"],
                "sentiment": x["sentiment"]
            }
            for x in news_analysis["articles"][:5]
        ]
    },

    "macro": macro,
    "macro_signal": macro_signal,

    "macro_calendar": {
        "risk_window": macro_calendar["risk_window"],
        "next_high_impact": macro_calendar["next_high_impact"],
        "imminent_high_impact": macro_calendar["imminent_high_impact"],
        "upcoming_events": macro_calendar["events"][:12]
    },

    "risk_levels": risk_levels
}

# ------------------------------------------------
# AI SCHEDULER
# ------------------------------------------------

should_run_ai, trigger_reason = ai_due(
    news_id,
    oi,
    delta,
    liquidity,
    macro_signal=macro_signal
)

if ai_enabled and GROQ_API_KEY and should_run_ai:

    try:

        with st.spinner(
            "Groq AI analyzing BTC market & macro..."
        ):

            result = call_groq(
                market_payload
            )

        st.session_state.last_ai_result = result

        st.session_state.last_ai_time = time.time()

        st.session_state.last_trigger_time = time.time()

        st.session_state.last_news_hash = news_id

        st.session_state.last_ai_error = None

        add_event(
            "GROQ AI",
            f"Successful analysis: {trigger_reason} | Macro: {macro_signal['status']}"
        )

    except Exception as error:

        st.session_state.last_ai_error = str(error)

        add_event(
            "GROQ AI ERROR",
            str(error)
        )

ai_result = st.session_state.last_ai_result

if not GROQ_API_KEY and ai_enabled:

    st.session_state.last_ai_error = (
        "Missing GROQ_API_KEY in secrets.toml."
    )

# ------------------------------------------------
# AI & MACRO VALIDATION + UNIFIED SIGNAL
# ------------------------------------------------

ai_gate = check_ai_gate(
    entry,
    ai_result,
    macro_signal=macro_signal,
    macro_gate_active=macro_gate_enabled
)

unified_signal = build_unified_signal(
    entry=entry,
    ai_result=ai_result,
    ai_gate=ai_gate,
    macro_signal=macro_signal,
    risk_levels=risk_levels
)

# ------------------------------------------------
# PAPER TRADING
# ------------------------------------------------

funding_rate = (
    funding_raw["funding"]
    if funding_raw and "funding" in funding_raw else 0
)

if paper_enabled:

    manage_paper_trade(
        df1 if not df1.empty else main_df,
        funding_rate
    )

    if unified_signal["actionable"]:

        open_paper_trade(
            unified_signal["direction"],
            risk_levels,
            entry["score"]
        )

# ------------------------------------------------
# SIGNAL JOURNAL
# ------------------------------------------------

connection = get_connection()

connection.execute(
    """
    INSERT INTO signal_history (
        timestamp,
        price,
        direction,
        score,
        stage,
        regime,
        macro_status,
        signal_label,
        ai_gate
    )
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """,
    (
        now_string(),
        price,
        entry["direction"],
        entry["score"],
        entry["stage"],
        regime["regime"],
        macro_signal["status"],
        unified_signal["label"],
        int(ai_gate["allowed"])
    )
)

connection.commit()
connection.close()

st.session_state.last_refresh = now_string()

# ================================================================
# DASHBOARD
# ================================================================

st.divider()

st.subheader("BTC Market Overview")

c1, c2, c3, c4, c5 = st.columns(5)

c1.metric(
    "BTC PRICE",
    f"${price:,.2f}"
)

c2.metric(
    "24H HIGH",
    f"${ticker['high24h']:,.2f}"
)

c3.metric(
    "24H LOW",
    f"${ticker['low24h']:,.2f}"
)

c4.metric(
    "24H VOLUME",
    f"{ticker['volume24h']:,.2f}"
)

c5.metric(
    "FUNDING",
    (
        f"{funding['rate'] * 100:.5f}%"
        if funding["rate"] is not None
        else "N/A"
    )
)

# ================================================================
# SMART ENTRY & UNIFIED SIGNAL (WITH MACRO)
# ================================================================

st.divider()

st.subheader("SMART ENTRY & SIGNAL ENGINE (TECHNICAL + MACRO + AI)")

sig1, sig2, sig3, sig4 = st.columns(4)

sig1.metric(
    "UNIFIED SIGNAL",
    unified_signal["label"]
)

sig2.metric(
    "TECHNICAL DIRECTION",
    entry["direction"]
)

sig3.metric(
    "ENTRY QUALITY",
    f"{entry['quality']:.1f}/100"
)

sig4.metric(
    "MACRO SIGNAL STATUS",
    macro_signal["status"]
)

st.write(
    f"**Signal breakdown:** {unified_signal['summary']}"
)

st.write(
    f"**Entry stage:** {entry['stage']}"
)

st.write(
    f"**Market regime:** {regime['regime']} | **Volatility:** {regime['volatility']}"
)

st.write(
    f"**Macro bias & note:** {macro_signal['bias']} — {macro_signal['reason']}"
)

st.write(
    f"**AI & Macro gate:** {ai_gate['reason']}"
)

st.bar_chart(
    pd.DataFrame(
        {
            "Score": entry["components"]
        }
    )
)

# ================================================================
# RISK LEVELS
# ================================================================

st.divider()

st.subheader("Risk Management")

if risk_levels:

    r1, r2, r3, r4 = st.columns(4)

    r1.metric(
        "ENTRY",
        f"${risk_levels['entry']:,.2f}"
    )

    r2.metric(
        "STOP LOSS",
        f"${risk_levels['stop']:,.2f}"
    )

    r3.metric(
        "TP1",
        f"${risk_levels['tp1']:,.2f}"
    )

    r4.metric(
        "TP2",
        f"${risk_levels['tp2']:,.2f}"
    )

    st.write(
        f"TP3: ${risk_levels['tp3']:,.2f}"
    )

else:

    st.info(
        "No directional risk levels."
    )

# ================================================================
# MARKET STRUCTURE
# ================================================================

st.divider()

st.subheader("Market Structure")

m1, m2, m3 = st.columns(3)

m1.metric(
    "Direction",
    structure["direction"]
)

m2.metric(
    "Strength",
    structure["strength"]
)

m3.metric(
    "Efficiency",
    f"{regime['efficiency']:.3f}"
)

st.write(
    f"Support: {structure['support']}"
)

st.write(
    f"Resistance: {structure['resistance']}"
)

# ================================================================
# MULTI TIMEFRAME
# ================================================================

st.divider()

st.subheader("Multi-Timeframe Intelligence")

mtf_rows = []

for tf, item in mtf.items():

    mtf_rows.append({
        "Timeframe": tf,
        "Direction": item["direction"],
        "Structure": item["structure"],
        "RSI": item["rsi"],
        "ROC": item["roc"]
    })

st.dataframe(
    pd.DataFrame(mtf_rows),
    use_container_width=True,
    hide_index=True
)

# ================================================================
# ORDER FLOW
# ================================================================

st.divider()

st.subheader("Institutional Order Flow")

f1, f2, f3 = st.columns(3)

f1.metric(
    "BUY VOLUME",
    f"{delta['buy_volume']:.4f}"
)

f2.metric(
    "SELL VOLUME",
    f"{delta['sell_volume']:.4f}"
)

f3.metric(
    "DELTA",
    f"{delta['delta_pct']:.2f}%"
)

st.write(
    f"Delta classification: {delta['classification']}"
)

st.caption(
    "Delta uses the latest 500 OKX trades, "
    "not a fixed-duration aggregation."
)

st.subheader("Position Flow")

for event in flow:
    st.write(f"- {event}")

st.write(
    f"Open Interest: {oi['classification']}"
)

st.write(
    f"OI change: {oi['change_pct']}"
)

# ================================================================
# LIQUIDITY
# ================================================================

st.divider()

st.subheader("Liquidity Intelligence")

l1, l2, l3 = st.columns(3)

l1.metric(
    "Order Book Imbalance",
    f"{book['imbalance'] * 100:.2f}%"
)

l2.metric(
    "Order Book",
    book["classification"]
)

l3.metric(
    "Liquidity Sweep",
    liquidity["sweep"]
)

st.write(
    f"Breakout: {liquidity['breakout']}"
)

st.write(
    f"Breakout / Retest: {retest}"
)

st.write(
    f"Bid wall: {book['bid_wall']}"
)

st.write(
    f"Ask wall: {book['ask_wall']}"
)

st.caption(
    "The order book is a current snapshot. "
    "This version does not provide a true historical heatmap "
    "or verified liquidation-level feed."
)

# ================================================================
# FUNDING
# ================================================================

st.divider()

st.subheader("Funding Intelligence")

st.write(
    funding["classification"]
)

# ================================================================
# REVERSALS AND DIVERGENCES
# ================================================================

st.divider()

st.subheader("Reversal Intelligence")

for item in reversals:
    st.write(f"- {item}")

st.subheader("Divergences")

if divergences:

    for item in divergences:
        st.warning(item)

else:

    st.info(
        "No divergence detected."
    )

# ================================================================
# HISTORICAL PATTERNS
# ================================================================

st.divider()

st.subheader("Historical Pattern Intelligence")

p1, p2, p3, p4 = st.columns(4)

p1.metric(
    "Similar Patterns",
    patterns["matches"]
)

p2.metric(
    "Bullish Rate",
    (
        f"{patterns['bullish_rate']:.1f}%"
        if patterns["bullish_rate"] is not None
        else "N/A"
    )
)

p3.metric(
    "Bearish Rate",
    (
        f"{patterns['bearish_rate']:.1f}%"
        if patterns["bearish_rate"] is not None
        else "N/A"
    )
)

p4.metric(
    "Average Return",
    (
        f"{patterns['average_return']:.3f}%"
        if patterns["average_return"] is not None
        else "N/A"
    )
)

st.caption(
    "Historical similarity is descriptive, "
    "not a calibrated prediction."
)

# ================================================================
# NEWS
# ================================================================

st.divider()

st.subheader("News Intelligence")

n1, n2 = st.columns(2)

n1.metric(
    "News Sentiment",
    news_analysis["classification"]
)

n2.metric(
    "Sentiment Score",
    f"{news_analysis['score']:.2f}"
)

if news_analysis["articles"]:

    for article in news_analysis["articles"]:

        with st.expander(
            f"{article['source']} | {article['title']}"
        ):

            st.write(
                article["description"]
            )

            st.write(
                f"Sentiment: {article['sentiment']}"
            )

            st.write(
                f"Published: {article['published']}"
            )

            if article["link"]:
                st.markdown(
                    f"[Open article]({article['link']})"
                )

else:

    st.warning(
        "No news articles available."
    )

if news_errors:

    st.warning("RSS source errors")

    for error in news_errors:
        st.write(f"- {error}")

# ================================================================
# MACRO & MACRO CALENDAR (NO API)
# ================================================================

st.divider()

st.subheader("Macro Intelligence & Macro Calendar (No API)")

st.warning(
    macro["message"]
)

mc1, mc2, mc3 = st.columns(3)

mc1.metric(
    "Macro Signal Status",
    macro_signal["status"]
)

mc2.metric(
    "Macro Risk Window",
    macro_calendar["risk_window"]
)

next_hi = macro_calendar["next_high_impact"]

mc3.metric(
    "Next High-Impact Event",
    (
        f"{next_hi['currency']} {next_hi['event']} ({next_hi['hours_until']}h)"
        if next_hi else "None in window"
    )
)

st.write(f"**Macro signal note:** {macro_signal['reason']}")

if macro_calendar["events"]:

    st.dataframe(
        pd.DataFrame(macro_calendar["events"]),
        use_container_width=True,
        hide_index=True
    )

with st.expander("Add / Manage Custom Macro Events (Saved to SQLite, No API)"):

    with st.form("add_macro_event_form", clear_on_submit=True):
        f_col1, f_col2, f_col3, f_col4 = st.columns(4)
        ev_d = f_col1.date_input("Date (UTC)", value=now_utc().date())
        ev_t = f_col2.text_input("Time UTC (HH:MM)", value="13:30")
        ev_c = f_col3.selectbox("Currency", ["USD", "EUR", "BTC", "GLOBAL"])
        ev_i = f_col4.selectbox("Impact", ["HIGH", "MEDIUM", "LOW"])
        ev_name = st.text_input("Event Name", placeholder="e.g. Fed Chair Speech / ETF Flow / CPI")
        ev_notes = st.text_input("Notes", placeholder="Optional note")
        submitted_macro = st.form_submit_button("Add Macro Event")

        if submitted_macro and ev_name.strip():
            add_macro_event(
                ev_d.isoformat(),
                ev_t.strip(),
                ev_c,
                ev_name.strip(),
                ev_i,
                ev_notes.strip()
            )
            st.rerun()

    custom_db_df = database_read(
        "SELECT id, event_date, time_utc, currency, event_name, impact, notes FROM macro_calendar ORDER BY id DESC"
    )

    if not custom_db_df.empty:
        del_id = st.selectbox(
            "Delete custom event by ID",
            options=custom_db_df["id"].tolist()
        )
        if st.button("Delete Selected Event"):
            delete_macro_event(del_id)
            st.rerun()

# ================================================================
# GROQ AI
# ================================================================

st.divider()

st.subheader("GROQ AI INTELLIGENCE")

if not GROQ_API_KEY:

    st.error(
        "Groq API key not found. "
        "Check .streamlit/secrets.toml."
    )

elif not ai_enabled:

    st.info(
        "Groq AI is disabled."
    )

else:

    if st.session_state.last_ai_error:

        st.error(
            st.session_state.last_ai_error
        )

    if ai_result:

        st.success(
            "Last successful AI analysis"
        )

        a1, a2 = st.columns(2)

        a1.metric(
            "AI Direction",
            ai_result.get("direction", "UNKNOWN")
        )

        a2.metric(
            "AI Confidence",
            ai_result.get("confidence", "N/A")
        )

        st.write(
            "**Summary:** " + ai_result.get(
                "summary",
                "No summary."
            )
        )

        st.write(
            "**Technical:** " + ai_result.get(
                "technical_analysis",
                ""
            )
        )

        st.write(
            "**Order Flow:** " + ai_result.get(
                "order_flow_analysis",
                ""
            )
        )

        st.write(
            "**Liquidity:** " + ai_result.get(
                "liquidity_analysis",
                ""
            )
        )

        st.write(
            "**Macro Analysis:** " + ai_result.get(
                "macro_analysis",
                ""
            )
        )

        st.write(
            "**News Impact:** " + ai_result.get(
                "news_impact",
                ""
            )
        )

        st.write(
            "**Risk:** " + ai_result.get(
                "risk",
                ""
            )
        )

        st.write(
            "**Invalidation:** " + ai_result.get(
                "invalidation",
                ""
            )
        )

        with st.expander("Full AI response"):

            st.json(ai_result)

    else:

        st.info(
            "No successful AI analysis yet."
        )

    st.caption(
        f"AI trigger: {trigger_reason}"
    )

    if st.session_state.last_ai_time:

        st.caption(
            "Last successful AI update: "
            + datetime.fromtimestamp(
                st.session_state.last_ai_time,
                timezone.utc
            ).strftime("%Y-%m-%d %H:%M:%S UTC")
        )

# ================================================================
# AI / MACRO / TECHNICAL AGREEMENT
# ================================================================

st.divider()

st.subheader("AI / Macro / Technical Agreement")

if ai_result:

    st.write(
        f"Technical: {entry['direction']}"
    )

    st.write(
        f"AI: {ai_result['direction']}"
    )

    st.write(
        f"Macro Signal: {macro_signal['status']} ({macro_signal['bias']})"
    )

    if ai_gate["allowed"]:

        st.success(
            ai_gate["reason"]
        )

    else:

        st.error(
            ai_gate["reason"]
        )

else:

    st.warning(
        "No successful AI validation. "
        "New paper trades are blocked."
    )

# ================================================================
# PAPER TRADING DASHBOARD
# ================================================================

st.divider()

st.subheader("Paper Trading Performance")

performance = get_performance()

equity = get_paper_equity()

q1, q2, q3, q4 = st.columns(4)

q1.metric(
    "Equity",
    f"{equity:,.2f} USDT"
)

q2.metric(
    "Closed Trades",
    performance["count"]
)

q3.metric(
    "Win Rate",
    f"{performance['win_rate']:.2f}%"
)

q4.metric(
    "Net PnL",
    f"{performance['net_pnl']:.2f} USDT"
)

if np.isfinite(performance["profit_factor"]):

    st.metric(
        "Profit Factor",
        f"{performance['profit_factor']:.3f}"
    )

else:

    st.metric(
        "Profit Factor",
        "Infinite"
    )

open_trade = get_open_trade()

if open_trade:

    st.subheader("Open Paper Position")

    st.dataframe(
        pd.DataFrame([open_trade]),
        use_container_width=True,
        hide_index=True
    )

else:

    st.info(
        "No open paper position."
    )

all_trades = database_read(
    """
    SELECT *
    FROM paper_trades
    ORDER BY id DESC
    """
)

if not all_trades.empty:

    st.subheader("Paper Trade History")

    st.dataframe(
        all_trades,
        use_container_width=True,
        hide_index=True
    )

    st.download_button(
        "Download Paper Trade CSV",
        all_trades.to_csv(
            index=False
        ).encode("utf-8"),
        file_name="saliga_paper_trades.csv",
        mime="text/csv"
    )

# ================================================================
# SIGNAL HISTORY
# ================================================================

st.divider()

st.subheader("Signal Journal")

signal_history = database_read(
    """
    SELECT *
    FROM signal_history
    ORDER BY id DESC
    LIMIT 500
    """
)

if not signal_history.empty:

    st.dataframe(
        signal_history,
        use_container_width=True,
        hide_index=True
    )

    st.download_button(
        "Download Signal Journal",
        signal_history.to_csv(
            index=False
        ).encode("utf-8"),
        file_name="saliga_signal_history.csv",
        mime="text/csv"
    )

# ================================================================
# EVENT JOURNAL
# ================================================================

st.divider()

st.subheader("System Event Journal")

event_history = database_read(
    """
    SELECT *
    FROM event_log
    ORDER BY id DESC
    LIMIT 300
    """
)

if not event_history.empty:

    st.dataframe(
        event_history,
        use_container_width=True,
        hide_index=True
    )

# ================================================================
# MARKET SNAPSHOT HISTORY
# ================================================================

st.divider()

st.subheader("Market Snapshot History")

snapshot_history = database_read(
    """
    SELECT *
    FROM market_snapshots
    ORDER BY id DESC
    LIMIT 200
    """
)

if not snapshot_history.empty:

    st.dataframe(
        snapshot_history,
        use_container_width=True,
        hide_index=True
    )

# ================================================================
# CHART
# ================================================================

st.divider()

st.subheader("BTC Chart")

try:

    import plotly.graph_objects as go

    chart_df = (
        df1h if not df1h.empty
        else main_df
    )

    if not chart_df.empty:

        chart = go.Figure()

        chart.add_trace(
            go.Candlestick(
                x=pd.to_datetime(
                    chart_df["timestamp"],
                    unit="ms"
                ),
                open=chart_df["open"],
                high=chart_df["high"],
                low=chart_df["low"],
                close=chart_df["close"],
                name="BTC"
            )
        )

        if structure["support"] is not None:

            chart.add_hline(
                y=structure["support"],
                line_dash="dot",
                annotation_text="Support"
            )

        if structure["resistance"] is not None:

            chart.add_hline(
                y=structure["resistance"],
                line_dash="dot",
                annotation_text="Resistance"
            )

        chart.update_layout(
            template="plotly_dark",
            height=600,
            xaxis_rangeslider_visible=False,
            title="BTC-USDT-SWAP"
        )

        st.plotly_chart(
            chart,
            use_container_width=True
        )

except Exception as error:

    st.warning(
        f"Chart error: {error}"
    )

# ================================================================
# SYSTEM HEALTH
# ================================================================

st.divider()

st.subheader("System Health")

health_rows = []

for tf, df in timeframe_data.items():

    health_rows.append({
        "Timeframe": tf,
        "Available": not df.empty,
        "Candles": len(df)
    })

st.dataframe(
    pd.DataFrame(health_rows),
    use_container_width=True,
    hide_index=True
)

if errors:

    st.warning(
        "Some market data sources returned errors."
    )

    for error in errors:
        st.write(f"- {error}")

else:

    st.success(
        "No market data errors detected."
    )

st.write(
    f"Last refresh: {st.session_state.last_refresh}"
)

# ================================================================
# AUTO REFRESH
# ================================================================

if auto_refresh:

    try:

        from streamlit_autorefresh import st_autorefresh

        st_autorefresh(
            interval=refresh_seconds * 1000,
            key="saliga_auto_refresh"
        )

    except ImportError:

        st.warning(
            "Install streamlit-autorefresh for automatic refresh."
        )

# ================================================================
# END OF SALIGA AI PRO 31.0
# ================================================================
