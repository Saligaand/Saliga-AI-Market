# ================================================================
# SALIGA AI PRO 31.0
# ULTIMATE BTC INTELLIGENCE ENGINE (FIXED & OPTIMIZED)
#
# BTC ONLY | OKX | GROQ AI
# NO EMA | NO SMA
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

from datetime import datetime, timezone
from pathlib import Path

# ================================================================
# CONFIGURATION
# ================================================================

APP_NAME = "SALIGA AI PRO 31.0"
INSTRUMENT = "BTC-USDT-SWAP"
BASE_URL = "https://www.okx.com"
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
GROQ_MODEL = load_secret("GROQ_MODEL", GROQ_DEFAULT_MODEL)

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
            ai_gate INTEGER
        )
    """)

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

ai_enabled = st.sidebar.toggle("Groq AI enabled", value=True)
auto_refresh = st.sidebar.toggle("Automatic refresh", value=True)
refresh_seconds = st.sidebar.slider("Refresh interval", 15, 120, 30)
ai_interval = st.sidebar.slider("Normal AI interval (minutes)", 5, 60, 15)
ai_cooldown = st.sidebar.slider("Event AI cooldown (minutes)", 1, 15, 3)

paper_enabled = st.sidebar.toggle("Paper Trading", value=True)
starting_balance = st.sidebar.number_input("Starting balance USDT", min_value=100.0, max_value=1000000.0, value=10000.0)
risk_percent = st.sidebar.slider("Risk per trade (%)", 0.1, 3.0, 0.5, 0.1)
fee_percent = st.sidebar.number_input("Fee per side (%)", 0.0, 0.5, 0.05, 0.01)
slippage_percent = st.sidebar.number_input("Slippage per side (%)", 0.0, 0.5, 0.02, 0.01)

# ================================================================
# GENERAL HELPERS
# ================================================================

def now_utc():
    return datetime.now(timezone.utc)

def now_string():
    return now_utc().strftime("%Y-%m-%d %H:%M:%S UTC")

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
    return ((current / previous) - 1) * 100

def add_event(category, message):
    connection = get_connection()
    connection.execute(
        """
        INSERT INTO event_log (timestamp, category, message)
        VALUES (?, ?, ?)
        """,
        (now_string(), str(category), str(message))
    )
    connection.commit()
    connection.close()

def database_read(query, params=()):
    connection = get_connection()
    result = pd.read_sql_query(query, connection, params=params)
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
        headers={"User-Agent": "SALIGA-AI-PRO-31"}
    )
    response.raise_for_status()
    data = response.json()
    if str(data.get("code", "0")) != "0":
        raise RuntimeError(data.get("msg", "OKX API error"))
    return data.get("data", [])

# ================================================================
# MARKET DATA
# ================================================================

def fetch_ticker():
    data = okx_request("/api/v5/market/ticker", {"instId": INSTRUMENT})
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
    data = okx_request("/api/v5/market/candles", {"instId": INSTRUMENT, "bar": bar, "limit": str(limit)})
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
    df = df.sort_values("timestamp").drop_duplicates("timestamp").reset_index(drop=True)
    return df

def fetch_open_interest():
    data = okx_request("/api/v5/public/open-interest", {"instType": "SWAP", "instId": INSTRUMENT})
    if not data:
        return None
    item = data[0]
    return {
        "oi": safe_float(item.get("oi")),
        "oi_currency": safe_float(item.get("oiCcy")),
        "timestamp": item.get("ts")
    }

def fetch_funding():
    data = okx_request("/api/v5/public/funding-rate", {"instId": INSTRUMENT})
    if not data:
        return None
    item = data[0]
    return {
        "funding": safe_float(item.get("fundingRate")),
        "next_funding": item.get("nextFundingTime")
    }

def fetch_order_book():
    data = okx_request("/api/v5/market/books", {"instId": INSTRUMENT, "sz": "50"})
    if not data:
        return None
    item = data[0]
    bids = [(safe_float(x[0]), safe_float(x[1])) for x in item.get("bids", [])]
    asks = [(safe_float(x[0]), safe_float(x[1])) for x in item.get("asks", [])]
    bid_volume = sum(x[1] for x in bids)
    ask_volume = sum(x[1] for x in asks)
    total = bid_volume + ask_volume
    imbalance = ((bid_volume - ask_volume) / total) if total > 0 else 0
    return {
        "bids": bids,
        "asks": asks,
        "bid_volume": bid_volume,
        "ask_volume": ask_volume,
        "imbalance": imbalance,
        "spread": (asks[0][0] - bids[0][0]) if bids and asks else 0
    }

def fetch_recent_trades():
    data = okx_request("/api/v5/market/trades", {"instId": INSTRUMENT, "limit": "500"})
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
# TECHNICAL ENGINE (No EMA / SMA)
# ================================================================

def calculate_rsi(series, period=14):
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return (100 - 100 / (1 + rs)).fillna(50)

def calculate_atr(df, period=14):
    previous_close = df["close"].shift(1)
    ranges = pd.concat([
        df["high"] - df["low"],
        (df["high"] - previous_close).abs(),
        (df["low"] - previous_close).abs()
    ], axis=1)
    true_range = ranges.max(axis=1)
    return true_range.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()

def add_indicators(df):
    if df.empty:
        return df
    df = df.copy()
    df["rsi"] = calculate_rsi(df["close"])
    df["atr"] = calculate_atr(df)
    df["roc_5"] = df["close"].pct_change(5) * 100
    df["roc_10"] = df["close"].pct_change(10) * 100
    df["volume_median"] = df["volume"].rolling(20).median()
    df["volume_ratio"] = df["volume"] / df["volume_median"].replace(0, np.nan)
    df["atr_pct"] = df["atr"] / df["close"].replace(0, np.nan) * 100
    return df.replace([np.inf, -np.inf], np.nan)

# ================================================================
# MARKET STRUCTURE & REGIME
# ================================================================

def market_structure(df):
    if df.empty or len(df) < 30:
        return {"direction": "UNKNOWN", "strength": 0, "support": None, "resistance": None}
    recent = df.tail(30)
    highs = recent["high"].to_numpy()
    lows = recent["low"].to_numpy()
    swing_highs, swing_lows = [], []
    for i in range(2, len(recent) - 2):
        if highs[i] > highs[i - 1] and highs[i] > highs[i - 2] and highs[i] > highs[i + 1] and highs[i] > highs[i + 2]:
            swing_highs.append(highs[i])
        if lows[i] < lows[i - 1] and lows[i] < lows[i - 2] and lows[i] < lows[i + 1] and lows[i] < lows[i + 2]:
            swing_lows.append(lows[i])

    higher_high = len(swing_highs) >= 2 and swing_highs[-1] > swing_highs[-2]
    lower_high = len(swing_highs) >= 2 and swing_highs[-1] < swing_highs[-2]
    higher_low = len(swing_lows) >= 2 and swing_lows[-1] > swing_lows[-2]
    lower_low = len(swing_lows) >= 2 and swing_lows[-1] < swing_lows[-2]

    bullish = int(higher_high) + int(higher_low)
    bearish = int(lower_high) + int(lower_low)

    if bullish > bearish:
        direction = "BULLISH"
    elif bearish > bullish:
        direction = "BEARISH"
    else:
        direction = "NEUTRAL"

    current_price = safe_float(recent["close"].iloc[-1])
    support = max((x for x in swing_lows if x < current_price), default=None)
    resistance = min((x for x in swing_highs if x > current_price), default=None)

    return {
        "direction": direction,
        "strength": abs(bullish - bearish) * 50,
        "support": support,
        "resistance": resistance
    }

def market_regime(df):
    if df.empty or len(df) < 50:
        return {"regime": "UNKNOWN", "volatility": "UNKNOWN", "efficiency": 0}
    recent = df.tail(30)
    close = recent["close"]
    total_move = abs(close.iloc[-1] - close.iloc[0])
    path_move = close.diff().abs().sum()
    efficiency = (total_move / path_move) if path_move > 0 else 0
    atr_pct = safe_float(recent["atr_pct"].iloc[-1])
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
        regime = "TRENDING BULLISH" if structure["direction"] == "BULLISH" else ("TRENDING BEARISH" if structure["direction"] == "BEARISH" else "TRENDING")
    elif efficiency < 0.20:
        regime = "RANGE"
    else:
        regime = "TRANSITION"

    return {"regime": regime, "volatility": volatility, "efficiency": efficiency}

# ================================================================
# DELTA, ORDER BOOK, OI & FUNDING
# ================================================================

def calculate_delta(trades):
    if not trades:
        return {"buy_volume": 0, "sell_volume": 0, "delta": 0, "delta_pct": 0, "classification": "NO DATA"}
    buy = sum(x["size"] for x in trades if x["side"] == "buy")
    sell = sum(x["size"] for x in trades if x["side"] == "sell")
    total = buy + sell
    delta = buy - sell
    delta_pct = (delta / total * 100) if total > 0 else 0

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

    return {"buy_volume": buy, "sell_volume": sell, "delta": delta, "delta_pct": delta_pct, "classification": classification}

def analyze_order_book(book):
    if not book:
        return {"imbalance": 0, "classification": "NO DATA", "bid_wall": None, "ask_wall": None}
    imbalance = book["imbalance"]
    if imbalance > 0.20:
        classification = "BUY SIDE DOMINANT"
    elif imbalance < -0.20:
        classification = "SELL SIDE DOMINANT"
    else:
        classification = "BALANCED"

    bid_wall = max(book["bids"], key=lambda x: x[1], default=None)
    ask_wall = max(book["asks"], key=lambda x: x[1], default=None)
    return {"imbalance": imbalance, "classification": classification, "bid_wall": bid_wall, "ask_wall": ask_wall}

def analyze_open_interest(oi):
    if oi is None:
        return {"oi": None, "change_pct": None, "classification": "NO DATA"}
    connection = get_connection()
    previous = connection.execute("SELECT oi, price FROM market_snapshots WHERE oi IS NOT NULL ORDER BY id DESC LIMIT 1").fetchone()
    current_price = st.session_state.get("current_price", None)
    if previous:
        previous_oi = safe_float(previous["oi"])
        previous_price = safe_float(previous["price"])
        oi_change = percentage_change(oi["oi"], previous_oi)
        price_change = percentage_change(current_price, previous_price) if current_price and previous_price else 0

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
    return {"oi": oi["oi"], "change_pct": oi_change, "classification": classification}

def analyze_funding(funding):
    if funding is None:
        return {"rate": None, "classification": "NO DATA"}
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
    return {"rate": rate, "classification": classification}

# ================================================================
# LIQUIDITY, RETEST, POSITION FLOW & DIVERGENCES
# ================================================================

def analyze_liquidity(df):
    if df.empty or len(df) < 30:
        return {"sweep": "UNKNOWN", "breakout": "UNKNOWN"}
    latest = df.iloc[-1]
    previous = df.iloc[-21:-1]
    previous_high = previous["high"].max()
    previous_low = previous["low"].min()

    bullish_sweep = latest["low"] < previous_low and latest["close"] > previous_low
    bearish_sweep = latest["high"] > previous_high and latest["close"] < previous_high

    sweep = "BULLISH LIQUIDITY SWEEP" if bullish_sweep else ("BEARISH LIQUIDITY SWEEP" if bearish_sweep else "NO CONFIRMED SWEEP")
    breakout = "BULLISH BREAKOUT" if latest["close"] > previous_high else ("BEARISH BREAKDOWN" if latest["close"] < previous_low else "NO BREAKOUT")
    return {"sweep": sweep, "breakout": breakout, "previous_high": previous_high, "previous_low": previous_low}

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
    bullish_retest = latest["low"] <= high + atr * 0.25 and latest["close"] > high
    bearish_break = prior["close"] < low
    bearish_retest = latest["high"] >= low - atr * 0.25 and latest["close"] < low

    if bullish_break and bullish_retest:
        return "BULLISH BREAKOUT RETEST"
    if bearish_break and bearish_retest:
        return "BEARISH BREAKOUT RETEST"
    return "NO CONFIRMED RETEST"

def analyze_position_flow(oi, delta, structure):
    events = []
    oi_state = oi["classification"]
    if oi_state in ["LONG BUILDING", "SHORT BUILDING", "LONG CLOSING", "SHORT CLOSING"]:
        events.append(oi_state)
    if structure["direction"] == "BULLISH" and delta["classification"] in ["SELLING", "STRONG SELLING"]:
        events.append("BUYING WEAKNESS / SELLING PRESSURE")
    if structure["direction"] == "BEARISH" and delta["classification"] in ["BUYING", "STRONG BUYING"]:
        events.append("SELLING WEAKNESS / BUYING PRESSURE")
    if not events:
        events.append("NO CLEAR POSITION FLOW EVENT")
    return events

def analyze_divergences(df, delta):
    divergences = []
    if df.empty or len(df) < 20:
        return divergences
    recent = df.tail(20)
    price_change = percentage_change(recent["close"].iloc[-1], recent["close"].iloc[0])
    rsi_change = safe_float(recent["rsi"].iloc[-1]) - safe_float(recent["rsi"].iloc[0])

    if price_change > 0.4 and rsi_change < -5:
        divergences.append("POSSIBLE BEARISH RSI DIVERGENCE")
    if price_change < -0.4 and rsi_change > 5:
        divergences.append("POSSIBLE BULLISH RSI DIVERGENCE")
    return divergences

def analyze_timeframes(data):
    result = {}
    for timeframe, df in data.items():
        if df.empty:
            result[timeframe] = {"direction": "NO DATA", "structure": "UNKNOWN", "rsi": None, "roc": None}
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

def analyze_historical_patterns(df):
    if df.empty or len(df) < 100:
        return {"matches": 0, "bullish_rate": None, "bearish_rate": None, "average_return": None}
    data = df.reset_index(drop=True)
    current = data.iloc[-1]
    current_features = np.array([safe_float(current["rsi"]), safe_float(current["roc_5"]), safe_float(current["atr_pct"]), safe_float(current["volume_ratio"], 1)])
    scale = np.array([20, 1, 0.3, 1])
    matches = []
    start = max(30, len(data) - 250)
    for i in range(start, len(data) - 6):
        row = data.iloc[i]
        features = np.array([safe_float(row["rsi"]), safe_float(row["roc_5"]), safe_float(row["atr_pct"]), safe_float(row["volume_ratio"], 1)])
        distance = np.sqrt(np.mean(((features - current_features) / scale) ** 2))
        if distance < 1.0:
            future_return = percentage_change(data.iloc[i + 5]["close"], row["close"])
            matches.append({"distance": distance, "return": future_return})
    matches = sorted(matches, key=lambda x: x["distance"])[:20]
    if not matches:
        return {"matches": 0, "bullish_rate": None, "bearish_rate": None, "average_return": None}
    returns = [x["return"] for x in matches]
    return {
        "matches": len(returns),
        "bullish_rate": (sum(x > 0 for x in returns) / len(returns) * 100),
        "bearish_rate": (sum(x < 0 for x in returns) / len(returns) * 100),
        "average_return": float(np.mean(returns))
    }

def calculate_entry_quality(df, structure, regime, oi, delta, book, liquidity, mtf, funding, retest):
    components = {"structure": 0, "order_flow": 0, "open_interest": 0, "liquidity": 0, "multi_timeframe": 0, "breakout_retest": 0, "funding": 0}
    if df.empty:
        return {"direction": "WAIT", "score": 0, "quality": 0, "stage": "WAIT", "components": components}

    if structure["direction"] == "BULLISH":
        components["structure"] = 20
    elif structure["direction"] == "BEARISH":
        components["structure"] = -20

    components["order_flow"] = clamp(delta["delta_pct"] / 100 * 20, -20, 20)
    oi_state = oi["classification"]
    if oi_state == "LONG BUILDING":
        components["open_interest"] = 15
    elif oi_state == "SHORT BUILDING":
        components["open_interest"] = -15

    components["liquidity"] = book["imbalance"] * 15
    components["liquidity"] = clamp(components["liquidity"], -15, 15)

    weights = {"1m": 1, "5m": 2, "15m": 3, "1H": 4, "4H": 5}
    mtf_score = 0
    for tf, weight in weights.items():
        direction = mtf.get(tf, {}).get("structure", "UNKNOWN")
        if direction == "BULLISH":
            mtf_score += weight
        elif direction == "BEARISH":
            mtf_score -= weight
    components["multi_timeframe"] = mtf_score / sum(weights.values()) * 15

    score = sum(components.values())
    direction = "LONG" if score > 0 else ("SHORT" if score < 0 else "WAIT")
    quality = abs(score)

    if quality >= 85:
        stage = "HIGH QUALITY SETUP"
    elif quality >= 75:
        stage = "STRONG SETUP"
    elif quality >= 65:
        stage = "EARLY SETUP"
    else:
        stage = "WAIT"

    return {"direction": direction, "score": round(score, 2), "quality": round(quality, 2), "stage": stage, "components": components}

def analyze_reversals(df, structure, delta, liquidity, divergences):
    signals = []
    if df.empty:
        return signals
    latest = df.iloc[-1]
    rsi = safe_float(latest["rsi"])
    if rsi < 30 and delta["delta_pct"] > 10:
        signals.append("POSSIBLE BULLISH MOMENTUM REVERSAL")
    if rsi > 70 and delta["delta_pct"] < -10:
        signals.append("POSSIBLE BEARISH MOMENTUM REVERSAL")
    signals.extend(divergences)
    if not signals:
        signals.append("NO CONFIRMED REVERSAL")
    return signals

# ================================================================
# NEWS & MACRO
# ================================================================

def fetch_news():
    articles = []
    errors = []
    for source, url in NEWS_FEEDS.items():
        try:
            response = requests.get(url, timeout=REQUEST_TIMEOUT, headers={"User-Agent": "Mozilla/5.0 SALIGA AI"})
            response.raise_for_status()
            root = ET.fromstring(response.content)
            for item in root.findall(".//item")[:12]:
                title = item.findtext("title") or ""
                description = item.findtext("description") or ""
                if title:
                    articles.append({"source": source, "title": " ".join(title.split())[:300], "description": " ".join(description.split())[:600], "published": item.findtext("pubDate") or "", "link": item.findtext("link") or ""})
        except Exception as error:
            errors.append(f"{source}: {error}")
    unique = {hashlib.sha256(a["title"].lower().encode()).hexdigest(): a for a in articles}
    return list(unique.values())[:20], errors

POSITIVE_WORDS = ["approval", "adoption", "institutional", "inflow", "bullish", "surge", "record high", "partnership"]
NEGATIVE_WORDS = ["hack", "exploit", "lawsuit", "ban", "outflow", "liquidation", "crash", "bearish", "fraud"]

def analyze_news_sentiment(news):
    scored = []
    for article in news:
        content = (article["title"] + " " + article["description"]).lower()
        positive = sum(w in content for w in POSITIVE_WORDS)
        negative = sum(w in content for w in NEGATIVE_WORDS)
        scored.append({**article, "sentiment": clamp(positive - negative, -2, 2)})
    if not scored:
        return {"score": 0, "classification": "NO NEWS DATA", "articles": []}
    average = float(np.mean([x["sentiment"] for x in scored]))
    classification = "POSITIVE NEWS FLOW" if average > 0.5 else ("NEGATIVE NEWS FLOW" if average < -0.5 else "MIXED / NEUTRAL NEWS FLOW")
    return {"score": average, "classification": classification, "articles": scored}

def calculate_news_hash(news):
    return hashlib.sha256("|".join(x["title"] for x in news).encode()).hexdigest()

def get_macro_status():
    return {"available": False, "message": "No macro provider configured."}

# ================================================================
# GROQ AI ENGINE
# ================================================================

def call_groq(payload):
    if not GROQ_API_KEY:
        raise RuntimeError("GROQ_API_KEY is missing.")
    system_prompt = "You are SALIGA AI PRO 31.0. Return valid JSON only with keys: direction, confidence, conflict, summary, technical_analysis, order_flow_analysis, liquidity_analysis, news_impact, risk, invalidation."
    request_data = {
        "model": GROQ_MODEL,
        "temperature": 0.15,
        "max_tokens": 450,
        "response_format": {"type": "json_object"},
        "messages": [{"role": "system", "content": system_prompt}, {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}]
    }
    response = requests.post("https://api.groq.com/openai/v1/chat/completions", headers={"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"}, json=request_data, timeout=45)
    response.raise_for_status()
    content = response.json()["choices"][0]["message"]["content"]
    parsed = json.loads(content)
    parsed["direction"] = str(parsed.get("direction", "WAIT")).upper()
    return parsed

def ai_due(news_id, oi, delta, liquidity):
    current = time.time()
    if current - st.session_state.last_ai_time >= ai_interval * 60:
        return True, "NORMAL SCHEDULE"
    return False, "NOT DUE"

def check_ai_gate(entry, ai_result):
    if ai_result is None:
        return {"allowed": False, "reason": "No AI validation available."}
    if ai_result.get("conflict", False) or ai_result["direction"] == "WAIT":
        return {"allowed": False, "reason": "AI neutral or reports conflict."}
    if entry["direction"] in ["LONG", "SHORT"] and ai_result["direction"] != entry["direction"]:
        return {"allowed": False, "reason": "AI and technical direction conflict."}
    return {"allowed": True, "reason": "AI and technical direction aligned."}

# ================================================================
# RISK MANAGEMENT & PAPER TRADING
# ================================================================

def calculate_risk_levels(price, atr, direction, regime):
    if price <= 0 or atr <= 0:
        return None
    distance = atr * 1.8
    if direction == "LONG":
        return {"entry": price, "stop": price - distance, "tp1": price + distance * 1.5, "tp2": price + distance * 2.5, "tp3": price + distance * 3.5}
    elif direction == "SHORT":
        return {"entry": price, "stop": price + distance, "tp1": price - distance * 1.5, "tp2": price - distance * 2.5, "tp3": price - distance * 3.5}
    return None

def get_open_trade():
    connection = get_connection()
    row = connection.execute("SELECT * FROM paper_trades WHERE status = 'OPEN' ORDER BY id DESC LIMIT 1").fetchone()
    connection.close()
    return dict(row) if row else None

def get_paper_equity():
    closed = database_read("SELECT COALESCE(SUM(net_pnl), 0) AS pnl FROM paper_trades WHERE status = 'CLOSED'")
    pnl = safe_float(closed.iloc[0]["pnl"]) if not closed.empty else 0
    return starting_balance + pnl

def open_paper_trade(direction, levels, score):
    if not paper_enabled or levels is None or get_open_trade() is not None:
        return
    equity = get_paper_equity()
    risk_amount = equity * risk_percent / 100
    distance = abs(levels["entry"] - levels["stop"])
    if distance <= 0:
        return
    quantity = risk_amount / distance
    connection = get_connection()
    connection.execute(
        """
        INSERT INTO paper_trades (opened, direction, entry, stop, tp1, tp2, tp3, quantity, score, status)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'OPEN')
        """,
        (now_string(), direction, levels["entry"], levels["stop"], levels["tp1"], levels["tp2"], levels["tp3"], quantity, score)
    )
    connection.commit()
    connection.close()

def save_market_snapshot(price, oi, delta, funding, book):
    connection = get_connection()
    connection.execute(
        """
        INSERT INTO market_snapshots (timestamp, price, oi, delta, funding, book_imbalance)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (time.time(), price, oi["oi"] if oi else None, delta["delta"], funding["rate"] if funding else None, book["imbalance"] if book else None)
    )
    connection.commit()
    connection.close()

# ================================================================
# MAIN APPLICATION FLOW
# ================================================================

st.title("₿ SALIGA AI PRO 31.0")
st.caption("ULTIMATE BTC INTELLIGENCE ENGINE | OKX MARKET DATA | GROQ AI")

try:
    ticker = fetch_ticker()
    price = ticker["price"]
    if price <= 0:
        raise RuntimeError("Invalid BTC price")
except Exception as error:
    st.error(f"OKX ticker error: {error}")
    st.stop()

st.session_state.current_price = price

timeframe_data = {}
for tf, bar in TIMEFRAMES.items():
    try:
        raw = fetch_candles(bar)
        timeframe_data[tf] = add_indicators(raw)
    except Exception:
        timeframe_data[tf] = pd.DataFrame()

df1, df5, df15, df1h, df4h = timeframe_data["1m"], timeframe_data["5m"], timeframe_data["15m"], timeframe_data["1H"], timeframe_data["4H"]
main_df = df5 if not df5.empty else (df1 if not df1.empty else df15)

if main_df.empty:
    st.error("No usable BTC candle data.")
    st.stop()

oi_raw = fetch_open_interest()
funding_raw = fetch_funding()
book_raw = fetch_order_book()
trades_raw = fetch_recent_trades()
news_raw, news_errors = fetch_news()

news_analysis = analyze_news_sentiment(news_raw)
news_id = calculate_news_hash(news_raw)

structure = market_structure(main_df)
regime = market_regime(main_df)
delta = calculate_delta(trades_raw)
book = analyze_order_book(book_raw)
oi = analyze_open_interest(oi_raw)
funding = analyze_funding(funding_raw)
liquidity = analyze_liquidity(main_df)
retest = analyze_retest(main_df)
mtf = analyze_timeframes(timeframe_data)
divergences = analyze_divergences(main_df, delta)
flow = analyze_position_flow(oi, delta, structure)
patterns = analyze_historical_patterns(main_df)
reversals = analyze_reversals(main_df, structure, delta, liquidity, divergences)
entry = calculate_entry_quality(main_df, structure, regime, oi, delta, book, liquidity, mtf, funding, retest)
macro = get_macro_status()

latest = main_df.iloc[-1]
atr = safe_float(latest["atr"])
risk_levels = calculate_risk_levels(price, atr, entry["direction"], regime)

save_market_snapshot(price, oi_raw, delta, funding_raw, book_raw)

market_payload = {
    "instrument": INSTRUMENT,
    "timestamp": now_string(),
    "price": price,
    "technical": {"direction": entry["direction"], "score": entry["score"], "quality": entry["quality"], "stage": entry["stage"]},
    "order_flow": {"delta": delta, "open_interest": oi, "funding": funding}
}

should_run_ai, trigger_reason = ai_due(news_id, oi, delta, liquidity)
if ai_enabled and GROQ_API_KEY and should_run_ai:
    try:
        with st.spinner("Groq AI analyzing BTC market..."):
            result = call_groq(market_payload)
            st.session_state.last_ai_result = result
            st.session_state.last_ai_time = time.time()
            st.session_state.last_ai_error = None
    except Exception as error:
        st.session_state.last_ai_error = str(error)

ai_result = st.session_state.last_ai_result
ai_gate = check_ai_gate(entry, ai_result)

st.session_state.last_refresh = now_string()

# ================================================================
# DASHBOARD UI
# ================================================================

st.divider()
st.subheader("BTC Market Overview")

c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("BTC PRICE", f"${price:,.2f}")
c2.metric("24H HIGH", f"${ticker['high24h']:,.2f}")
c3.metric("24H LOW", f"${ticker['low24h']:,.2f}")
c4.metric("24H VOLUME", f"{ticker['volume24h']:,.2f}")
c5.metric("FUNDING", f"{funding['rate'] * 100:.5f}%" if funding["rate"] is not None else "N/A")

st.divider()
st.subheader("SMART ENTRY ENGINE")
s1, s2, s3 = st.columns(3)
s1.metric("TECHNICAL DIRECTION", entry["direction"])
s2.metric("ENTRY QUALITY", f"{entry['quality']:.1f}/100")
s3.metric("ENTRY STAGE", entry["stage"])

st.write(f"**Market regime:** {regime['regime']}")
st.write(f"**AI gate:** {ai_gate['reason']}")

st.divider()
st.subheader("GROQ AI INTELLIGENCE")
if ai_result:
    st.success("Last successful AI analysis")
    st.write(ai_result.get("summary", "No summary."))
else:
    st.info("No successful AI analysis yet.")

# ================================================================
# AUTO REFRESH (Safe Handling)
# ================================================================

if auto_refresh:
    try:
        from streamlit_autorefresh import st_autorefresh
        st_autorefresh(interval=refresh_seconds * 1000, key="saliga_auto_refresh")
    except ImportError:
        pass  # Ha nincs telepítve, nem dob összeomló hibát
