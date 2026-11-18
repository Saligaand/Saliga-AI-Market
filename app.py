from __future__ import annotations

import os
import json
import time
import sqlite3
import logging
import hashlib

from datetime import datetime, timezone, timedelta
from typing import Optional

import ccxt
import numpy as np
import pandas as pd
import requests
import streamlit as st
import plotly.graph_objects as go
import feedparser


# ============================================================
# SALIGA AI v5.0
# OKX ONLY
# ============================================================

APP_NAME = "SALIGA AI – INSTITUTIONAL PRO"
VERSION = "5.0.0"

DB_FILE = "saliga_institutional.db"

SYMBOLS = {
    "BTC": "BTC/USDT:USDT",
    "ETH": "ETH/USDT:USDT",
    "SOL": "SOL/USDT:USDT",
}

TIMEFRAMES = [
    "1m",
    "5m",
    "15m",
    "1h",
    "4h",
    "1d",
]

TF_WEIGHTS = {
    "1m": 0.05,
    "5m": 0.10,
    "15m": 0.20,
    "1h": 0.30,
    "4h": 0.35,
}

OHLCV_LIMIT = 500

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("SALIGA")


# ============================================================
# PAGE CONFIG
# ============================================================

st.set_page_config(
    page_title=APP_NAME,
    page_icon="⚡",
    layout="wide",
)

st.title("⚡ SALIGA AI")
st.caption(
    "INSTITUTIONAL CRYPTO MARKET INTELLIGENCE | "
    "POSITION FLOW | OKX ONLY"
)


# ============================================================
# DATABASE
# ============================================================

def db_connection():

    conn = sqlite3.connect(
        DB_FILE,
        check_same_thread=False,
        timeout=20,
    )

    conn.execute("PRAGMA journal_mode=WAL")

    conn.execute("""
        CREATE TABLE IF NOT EXISTS signals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            symbol TEXT NOT NULL,
            signal TEXT NOT NULL,
            price REAL NOT NULL,
            score REAL,
            confidence REAL,
            coverage REAL,
            stop_loss REAL,
            take_profit REAL,
            status TEXT DEFAULT 'OPEN',
            close_price REAL,
            result_percent REAL,
            close_time TEXT
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS oi_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT,
            timestamp INTEGER,
            oi REAL,
            price REAL,
            UNIQUE(symbol, timestamp)
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS orderbook_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT,
            timestamp INTEGER,
            imbalance REAL,
            bid_volume REAL,
            ask_volume REAL
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS trade_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT,
            trade_id TEXT,
            timestamp INTEGER,
            side TEXT,
            amount REAL,
            price REAL,
            UNIQUE(symbol, trade_id)
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS analysis_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT,
            timestamp INTEGER,
            price REAL,
            score REAL,
            signal TEXT,
            coverage REAL
        )
    """)

    conn.commit()

    return conn


DB = db_connection()


def db_execute(query, params=()):

    with sqlite3.connect(
        DB_FILE,
        timeout=20,
    ) as conn:

        return conn.execute(
            query,
            params,
        )


# ============================================================
# OKX CONNECTION
# ============================================================

@st.cache_resource
def get_exchange():

    exchange = ccxt.okx({
        "enableRateLimit": True,
        "timeout": 20000,
        "options": {
            "defaultType": "swap",
        },
    })

    exchange.load_markets()

    return exchange


def resolve_symbol(symbol):

    exchange = get_exchange()

    if symbol not in exchange.markets:

        raise ValueError(
            f"OKX market not found: {symbol}"
        )

    market = exchange.market(symbol)

    if not market.get("swap"):

        raise ValueError(
            "A kiválasztott piac nem perpetual swap."
        )

    return symbol


# ============================================================
# MARKET DATA
# ============================================================

def fetch_ohlcv(symbol, timeframe):

    exchange = get_exchange()

    symbol = resolve_symbol(symbol)

    data = exchange.fetch_ohlcv(
        symbol,
        timeframe=timeframe,
        limit=OHLCV_LIMIT,
    )

    if not data:

        raise RuntimeError(
            f"No OHLCV data for {symbol} {timeframe}"
        )

    df = pd.DataFrame(
        data,
        columns=[
            "timestamp",
            "open",
            "high",
            "low",
            "close",
            "volume",
        ],
    )

    df["datetime"] = pd.to_datetime(
        df["timestamp"],
        unit="ms",
        utc=True,
    )

    numeric = [
        "open",
        "high",
        "low",
        "close",
        "volume",
    ]

    for col in numeric:

        df[col] = pd.to_numeric(
            df[col],
            errors="coerce",
        )

    df = df.dropna(
        subset=numeric
    )

    df = df.sort_values(
        "timestamp"
    ).drop_duplicates(
        "timestamp"
    ).reset_index(
        drop=True
    )

    return df


def fetch_ticker(symbol):

    return get_exchange().fetch_ticker(
        resolve_symbol(symbol)
    )


def fetch_orderbook(symbol):

    return get_exchange().fetch_order_book(
        resolve_symbol(symbol),
        limit=100,
    )


def fetch_trades(symbol):

    return get_exchange().fetch_trades(
        resolve_symbol(symbol),
        limit=500,
    )


def fetch_funding(symbol):

    try:

        return get_exchange().fetch_funding_rate(
            resolve_symbol(symbol)
        )

    except Exception as exc:

        logger.warning(
            "Funding unavailable: %s",
            exc,
        )

        return None


def fetch_open_interest(symbol):

    try:

        return get_exchange().fetch_open_interest(
            resolve_symbol(symbol)
        )

    except Exception as exc:

        logger.warning(
            "Open interest unavailable: %s",
            exc,
        )

        return None


def fetch_oi_history(symbol):

    try:

        exchange = get_exchange()

        return exchange.fetch_open_interest_history(
            resolve_symbol(symbol),
            timeframe="5m",
            limit=100,
        )

    except Exception as exc:

        logger.warning(
            "OI history unavailable: %s",
            exc,
        )

        return []


# ============================================================
# INDICATORS
# ============================================================

def calculate_rsi(series, period=14):

    delta = series.diff()

    gain = delta.clip(
        lower=0
    )

    loss = -delta.clip(
        upper=0
    )

    avg_gain = gain.ewm(
        alpha=1 / period,
        adjust=False,
        min_periods=period,
    ).mean()

    avg_loss = loss.ewm(
        alpha=1 / period,
        adjust=False,
        min_periods=period,
    ).mean()

    rs = avg_gain / avg_loss.replace(
        0,
        np.nan,
    )

    return 100 - (
        100 / (1 + rs)
    )


def calculate_atr(df, period=14):

    previous = df["close"].shift(1)

    tr = pd.concat(
        [
            df["high"] - df["low"],
            (df["high"] - previous).abs(),
            (df["low"] - previous).abs(),
        ],
        axis=1,
    ).max(axis=1)

    return tr.ewm(
        alpha=1 / period,
        adjust=False,
        min_periods=period,
    ).mean()


def calculate_adx(df, period=14):

    up = df["high"].diff()

    down = -df["low"].diff()

    plus_dm = np.where(
        (up > down) & (up > 0),
        up,
        0,
    )

    minus_dm = np.where(
        (down > up) & (down > 0),
        down,
        0,
    )

    previous = df["close"].shift(1)

    tr = pd.concat(
        [
            df["high"] - df["low"],
            (df["high"] - previous).abs(),
            (df["low"] - previous).abs(),
        ],
        axis=1,
    ).max(axis=1)

    atr = tr.ewm(
        alpha=1 / period,
        adjust=False,
    ).mean()

    plus = pd.Series(
        plus_dm,
        index=df.index,
    ).ewm(
        alpha=1 / period,
        adjust=False,
    ).mean()

    minus = pd.Series(
        minus_dm,
        index=df.index,
    ).ewm(
        alpha=1 / period,
        adjust=False,
    ).mean()

    plus_di = 100 * plus / atr.replace(
        0,
        np.nan,
    )

    minus_di = 100 * minus / atr.replace(
        0,
        np.nan,
    )

    dx = (
        (plus_di - minus_di).abs() /
        (plus_di + minus_di).replace(0, np.nan)
    ) * 100

    return dx.ewm(
        alpha=1 / period,
        adjust=False,
    ).mean()


def add_indicators(df):

    df = df.copy()

    df["RSI"] = calculate_rsi(
        df["close"]
    )

    df["ATR"] = calculate_atr(
        df
    )

    df["ADX"] = calculate_adx(
        df
    )

    df["SMA20"] = df["close"].rolling(
        20
    ).mean()

    df["SMA50"] = df["close"].rolling(
        50
    ).mean()

    df["VOL_MA"] = df["volume"].rolling(
        20
    ).mean()

    df["VOL_RATIO"] = (
        df["volume"] /
        df["VOL_MA"].replace(0, np.nan)
    )

    # Candle-based estimated delta.
    # It is NOT exchange-reported aggressor delta.

    candle_range = (
        df["high"] - df["low"]
    ).replace(
        0,
        np.nan,
    )

    close_location = (
        (df["close"] - df["low"]) -
        (df["high"] - df["close"])
    ) / candle_range

    df["EST_DELTA"] = (
        close_location.fillna(0) *
        df["volume"]
    )

    return df


# ============================================================
# MARKET STRUCTURE
# ============================================================

def find_pivots(df, left=3, right=3):

    high = df["high"]
    low = df["low"]

    pivot_high = pd.Series(
        np.nan,
        index=df.index,
    )

    pivot_low = pd.Series(
        np.nan,
        index=df.index,
    )

    for i in range(
        left,
        len(df) - right,
    ):

        h = high.iloc[i]

        l = low.iloc[i]

        if h == high.iloc[
            i-left:i+right+1
        ].max():

            pivot_high.iloc[i] = h

        if l == low.iloc[
            i-left:i+right+1
        ].min():

            pivot_low.iloc[i] = l

    return pivot_high, pivot_low


def analyze_structure(df):

    if len(df) < 40:

        return {
            "trend": "UNKNOWN",
            "bos": "NONE",
            "choch": "NONE",
            "score": 0,
        }

    ph, pl = find_pivots(
        df.tail(150).reset_index(drop=True)
    )

    highs = ph.dropna()

    lows = pl.dropna()

    if len(highs) < 2 or len(lows) < 2:

        return {
            "trend": "UNKNOWN",
            "bos": "NONE",
            "choch": "NONE",
            "score": 0,
        }

    h1, h2 = highs.iloc[-2:]

    l1, l2 = lows.iloc[-2:]

    bullish = (
        h2 > h1 and
        l2 > l1
    )

    bearish = (
        h2 < h1 and
        l2 < l1
    )

    if bullish:

        trend = "BULLISH"

    elif bearish:

        trend = "BEARISH"

    else:

        trend = "RANGE"

    close = float(
        df["close"].iloc[-1]
    )

    last_high = float(
        highs.iloc[-1]
    )

    last_low = float(
        lows.iloc[-1]
    )

    bos = "NONE"

    choch = "NONE"

    if close > last_high:

        bos = "BULLISH BOS"

    elif close < last_low:

        bos = "BEARISH BOS"

    if trend == "BEARISH" and bos == "BULLISH BOS":

        choch = "BULLISH CHoCH"

    elif trend == "BULLISH" and bos == "BEARISH BOS":

        choch = "BEARISH CHoCH"

    score = (
        1 if bullish else
        -1 if bearish else
        0
    )

    return {
        "trend": trend,
        "bos": bos,
        "choch": choch,
        "score": score,
        "last_high": last_high,
        "last_low": last_low,
    }


# ============================================================
# LIQUIDITY
# ============================================================

def analyze_liquidity(df):

    recent = df.tail(60)

    current = recent.iloc[-1]

    previous = recent.iloc[:-1]

    previous_high = previous["high"].max()

    previous_low = previous["low"].min()

    sweep_high = (
        current["high"] > previous_high and
        current["close"] < previous_high
    )

    sweep_low = (
        current["low"] < previous_low and
        current["close"] > previous_low
    )

    if sweep_high:

        status = "BUY-SIDE LIQUIDITY SWEPT"

        score = -1

    elif sweep_low:

        status = "SELL-SIDE LIQUIDITY SWEPT"

        score = 1

    else:

        status = "NO LIQUIDITY SWEEP"

        score = 0

    return {
        "status": status,
        "score": score,
        "support": float(previous_low),
        "resistance": float(previous_high),
    }


# ============================================================
# VOLUME PROFILE
# ============================================================

def calculate_volume_profile(df, bins=50):

    data = df.tail(300)

    typical = (
        data["high"] +
        data["low"] +
        data["close"]
    ) / 3

    volumes = data["volume"].to_numpy()

    if len(typical) < 20:

        return None

    hist, edges = np.histogram(
        typical,
        bins=bins,
        weights=volumes,
    )

    if hist.sum() <= 0:

        return None

    poc_index = int(
        np.argmax(hist)
    )

    poc = (
        edges[poc_index] +
        edges[poc_index + 1]
    ) / 2

    # Value Area: expand from POC until 70% volume.

    left = poc_index

    right = poc_index

    accumulated = hist[poc_index]

    target = hist.sum() * 0.70

    while accumulated < target:

        left_volume = (
            hist[left - 1]
            if left > 0
            else -1
        )

        right_volume = (
            hist[right + 1]
            if right < len(hist) - 1
            else -1
        )

        if left_volume < 0 and right_volume < 0:
            break

        if right_volume > left_volume:

            right += 1

            accumulated += hist[right]

        else:

            left -= 1

            accumulated += hist[left]

    return {
        "POC": float(poc),
        "VAH": float(edges[right + 1]),
        "VAL": float(edges[left]),
    }


# ============================================================
# MARKET REGIME
# ============================================================

def detect_market_regime(df):

    last = df.iloc[-1]

    adx = last["ADX"]

    atr = last["ATR"]

    close = last["close"]

    atr_percent = (
        atr / close * 100
        if close
        else 0
    )

    if pd.isna(adx):

        return {
            "regime": "UNKNOWN",
            "volatility": atr_percent,
        }

    if adx >= 25:

        regime = "TRENDING"

    elif adx <= 18:

        regime = "RANGING"

    else:

        regime = "TRANSITION"

    if atr_percent >= 2:

        volatility = "HIGH"

    elif atr_percent <= 0.4:

        volatility = "LOW"

    else:

        volatility = "NORMAL"

    return {
        "regime": regime,
        "volatility": volatility,
        "adx": float(adx),
        "atr_percent": float(atr_percent),
    }


# ============================================================
# POSITION FLOW – OPEN INTEREST
# ============================================================

def save_oi_snapshot(symbol, timestamp, oi, price):

    if oi is None:
        return

    db_execute(
        """
        INSERT OR IGNORE INTO oi_history
        (symbol, timestamp, oi, price)
        VALUES (?, ?, ?, ?)
        """,
        (
            symbol,
            int(timestamp),
            float(oi),
            float(price),
        ),
    )


def read_oi_history(symbol):

    return pd.read_sql_query(
        """
        SELECT timestamp, oi, price
        FROM oi_history
        WHERE symbol = ?
        ORDER BY timestamp
        """,
        sqlite3.connect(DB_FILE),
        params=(symbol,),
    )


def extract_oi_value(data):

    if not data:
        return None

    for key in (
        "openInterestAmount",
        "openInterestValue",
        "openInterest",
    ):

        value = data.get(key)

        if value is not None:

            try:

                value = float(value)

                if value > 0:
                    return value

            except (TypeError, ValueError):
                continue

    return None


def interpret_position_flow(
    price_change,
    oi_change,
):

    if price_change is None or oi_change is None:

        return {
            "state": "POSITION FLOW DATA INSUFFICIENT",
            "score": 0,
        }

    if price_change > 0.15 and oi_change > 0.5:

        state = "LONG BUILD-UP ESTIMATE"

        score = 1

    elif price_change < -0.15 and oi_change > 0.5:

        state = "SHORT BUILD-UP ESTIMATE"

        score = -1

    elif price_change > 0.15 and oi_change < -0.5:

        state = "SHORT COVERING ESTIMATE"

        score = 0.5

    elif price_change < -0.15 and oi_change < -0.5:

        state = "LONG CLOSING / LIQUIDATION ESTIMATE"

        score = -0.5

    else:

        state = "NO CLEAR POSITION FLOW"

        score = 0

    return {
        "state": state,
        "score": score,
    }


def analyze_open_interest(
    symbol,
    current_price,
    current_oi,
):

    if current_oi is None:

        return {
            "available": False,
            "state": "OI UNAVAILABLE",
            "score": 0,
        }

    now = int(
        time.time() * 1000
    )

    save_oi_snapshot(
        symbol,
        now,
        current_oi,
        current_price,
    )

    history = read_oi_history(
        symbol
    )

    if len(history) < 2:

        return {
            "available": False,
            "state": "COLLECTING OI HISTORY",
            "score": 0,
        }

    history["datetime"] = pd.to_datetime(
        history["timestamp"],
        unit="ms",
        utc=True,
    )

    cutoff = pd.Timestamp.now(
        tz="UTC"
    ) - pd.Timedelta(
        minutes=30
    )

    old = history[
        history["datetime"] <= cutoff
    ]

    if old.empty:

        return {
            "available": False,
            "state": "WAITING FOR 30-MINUTE OI HISTORY",
            "score": 0,
        }

    baseline = old.iloc[-1]

    price_change = (
        current_price - baseline["price"]
    ) / baseline["price"] * 100

    oi_change = (
        current_oi - baseline["oi"]
    ) / baseline["oi"] * 100

    flow = interpret_position_flow(
        price_change,
        oi_change,
    )

    return {
        "available": True,
        "price_change": price_change,
        "oi_change": oi_change,
        "state": flow["state"],
        "score": flow["score"],
        "current_oi": current_oi,
    }


# ============================================================
# TRADE STORAGE AND TRUE TAKER DELTA
# ============================================================

def store_trades(symbol, trades):

    if not trades:
        return

    for trade in trades:

        trade_id = str(
            trade.get("id")
            or hashlib.sha256(
                json.dumps(
                    trade,
                    sort_keys=True,
                    default=str,
                ).encode()
            ).hexdigest()
        )

        timestamp = trade.get("timestamp")

        side = trade.get("side")

        amount = trade.get("amount")

        price = trade.get("price")

        if not all(
            x is not None
            for x in (
                timestamp,
                side,
                amount,
                price,
            )
        ):

            continue

        db_execute(
            """
            INSERT OR IGNORE INTO trade_history
            (symbol, trade_id, timestamp, side, amount, price)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                symbol,
                trade_id,
                int(timestamp),
                side,
                float(amount),
                float(price),
            ),
        )


def analyze_cvd(symbol):

    history = pd.read_sql_query(
        """
        SELECT timestamp, side, amount, price
        FROM trade_history
        WHERE symbol = ?
        ORDER BY timestamp
        """,
        sqlite3.connect(DB_FILE),
        params=(symbol,),
    )

    if history.empty:

        return {
            "available": False,
            "cvd": 0,
            "delta": 0,
            "ratio": 0.5,
            "signal": "COLLECTING TRADE HISTORY",
        }

    history["signed_amount"] = np.where(
        history["side"] == "buy",
        history["amount"],
        np.where(
            history["side"] == "sell",
            -history["amount"],
            0,
        ),
    )

    now = int(
        time.time() * 1000
    )

    cutoff = now - 15 * 60 * 1000

    recent = history[
        history["timestamp"] >= cutoff
    ]

    if recent.empty:

        return {
            "available": False,
            "cvd": 0,
            "delta": 0,
            "ratio": 0.5,
            "signal": "NO RECENT TRADE DATA",
        }

    buy_volume = recent.loc[
        recent["side"] == "buy",
        "amount",
    ].sum()

    sell_volume = recent.loc[
        recent["side"] == "sell",
        "amount",
    ].sum()

    delta = buy_volume - sell_volume

    total = buy_volume + sell_volume

    ratio = (
        buy_volume / total
        if total > 0
        else 0.5
    )

    cvd = history["signed_amount"].sum()

    if ratio > 0.58:

        signal = "AGGRESSIVE BUYERS"

        score = 1

    elif ratio < 0.42:

        signal = "AGGRESSIVE SELLERS"

        score = -1

    else:

        signal = "BALANCED TAKER FLOW"

        score = 0

    return {
        "available": True,
        "cvd": float(cvd),
        "delta": float(delta),
        "buy_volume": float(buy_volume),
        "sell_volume": float(sell_volume),
        "ratio": float(ratio),
        "signal": signal,
        "score": score,
        "sample_size": len(recent),
    }


# ============================================================
# ORDER BOOK LIQUIDITY
# ============================================================

def analyze_orderbook(symbol, orderbook):

    if not orderbook:

        return {
            "available": False,
        }

    bids = orderbook.get("bids", [])

    asks = orderbook.get("asks", [])

    if not bids or not asks:

        return {
            "available": False,
        }

    bid_volume = sum(
        float(level[1])
        for level in bids[:20]
    )

    ask_volume = sum(
        float(level[1])
        for level in asks[:20]
    )

    total = bid_volume + ask_volume

    if total <= 0:

        return {
            "available": False,
        }

    imbalance = (
        bid_volume - ask_volume
    ) / total

    bid_wall = max(
        bids[:20],
        key=lambda x: float(x[1]),
    )

    ask_wall = max(
        asks[:20],
        key=lambda x: float(x[1]),
    )

    score = (
        1 if imbalance > 0.15
        else -1 if imbalance < -0.15
        else 0
    )

    timestamp = int(
        time.time() * 1000
    )

    db_execute(
        """
        INSERT INTO orderbook_history
        (symbol, timestamp, imbalance, bid_volume, ask_volume)
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            symbol,
            timestamp,
            imbalance,
            bid_volume,
            ask_volume,
        ),
    )

    return {
        "available": True,
        "imbalance": float(imbalance),
        "bid_volume": bid_volume,
        "ask_volume": ask_volume,
        "bid_wall_price": float(bid_wall[0]),
        "bid_wall_size": float(bid_wall[1]),
        "ask_wall_price": float(ask_wall[0]),
        "ask_wall_size": float(ask_wall[1]),
        "score": score,
    }


# ============================================================
# ORDER BOOK HISTORY
# ============================================================

def analyze_book_history(symbol):

    history = pd.read_sql_query(
        """
        SELECT timestamp, imbalance
        FROM orderbook_history
        WHERE symbol = ?
        ORDER BY timestamp DESC
        LIMIT 100
        """,
        sqlite3.connect(DB_FILE),
        params=(symbol,),
    )

    if len(history) < 3:

        return {
            "available": False,
            "trend": "COLLECTING HISTORY",
        }

    history = history.sort_values(
        "timestamp"
    )

    first = history["imbalance"].iloc[0]

    last = history["imbalance"].iloc[-1]

    change = last - first

    if change > 0.15:

        trend = "BUY LIQUIDITY INCREASING"

    elif change < -0.15:

        trend = "SELL LIQUIDITY INCREASING"

    else:

        trend = "BOOK IMBALANCE STABLE"

    return {
        "available": True,
        "change": float(change),
        "trend": trend,
    }


# ============================================================
# FUNDING
# ============================================================

def analyze_funding(funding):

    if not funding:

        return {
            "available": False,
        }

    rate = funding.get(
        "fundingRate"
    )

    if rate is None:

        return {
            "available": False,
        }

    rate = float(rate)

    if rate > 0.001:

        state = "EXTREME POSITIVE FUNDING"

        score = -1

    elif rate > 0.0005:

        state = "POSITIVE FUNDING"

        score = -0.5

    elif rate < -0.001:

        state = "EXTREME NEGATIVE FUNDING"

        score = 1

    elif rate < -0.0005:

        state = "NEGATIVE FUNDING"

        score = 0.5

    else:

        state = "NEUTRAL FUNDING"

        score = 0

    return {
        "available": True,
        "rate": rate,
        "state": state,
        "score": score,
    }


# ============================================================
# MACRO DATA
# ============================================================

@st.cache_data(ttl=300)
def fetch_macro():

    tickers = {
        "DXY": "DX-Y.NYB",
        "VIX": "^VIX",
        "US10Y": "^TNX",
        "NASDAQ": "^IXIC",
        "SP500": "^GSPC",
    }

    output = {}

    for name, ticker in tickers.items():

        try:

            url = (
                "https://query1.finance.yahoo.com/v8/finance/chart/"
                + ticker
                + "?range=5d&interval=1d"
            )

            response = requests.get(
                url,
                timeout=10,
                headers={
                    "User-Agent": "Mozilla/5.0"
                },
            )

            response.raise_for_status()

            data = response.json()

            result = data[
                "chart"
            ]["result"][0]

            values = result[
                "indicators"
            ]["quote"][0]["close"]

            values = [
                x for x in values
                if x is not None
            ]

            if len(values) < 2:
                continue

            change = (
                values[-1] - values[-2]
            ) / values[-2] * 100

            output[name] = {
                "value": float(values[-1]),
                "change": float(change),
            }

        except Exception as exc:

            logger.warning(
                "Macro %s unavailable: %s",
                name,
                exc,
            )

    return output


def calculate_macro_score(macro):

    values = []

    if "DXY" in macro:

        change = macro["DXY"]["change"]

        values.append(
            -1 if change > 0.15
            else 1 if change < -0.15
            else 0
        )

    if "NASDAQ" in macro:

        change = macro["NASDAQ"]["change"]

        values.append(
            1 if change > 0.3
            else -1 if change < -0.3
            else 0
        )

    if "SP500" in macro:

        change = macro["SP500"]["change"]

        values.append(
            1 if change > 0.3
            else -1 if change < -0.3
            else 0
        )

    if "VIX" in macro:

        change = macro["VIX"]["change"]

        values.append(
            -1 if change > 5
            else 1 if change < -5
            else 0
        )

    if "US10Y" in macro:

        change = macro["US10Y"]["change"]

        values.append(
            -1 if change > 1
            else 1 if change < -1
            else 0
        )

    if not values:

        return None

    return float(
        np.mean(values)
    )


# ============================================================
# NEWS
# ============================================================

@st.cache_data(ttl=300)
def fetch_news():

    feeds = [
        "https://cointelegraph.com/rss",
        "https://www.coindesk.com/arc/outboundfeeds/rss/",
    ]

    news = []

    for url in feeds:

        try:

            feed = feedparser.parse(url)

            for item in feed.entries[:8]:

                news.append({
                    "title": item.get(
                        "title",
                        "Untitled",
                    ),
                    "link": item.get(
                        "link",
                        "",
                    ),
                })

        except Exception:

            continue

    return news[:15]


# ============================================================
# MULTI TIMEFRAME
# ============================================================

def analyze_timeframe(df):

    df = add_indicators(df)

    last = df.iloc[-1]

    score = 0

    components = {}

    close = float(last["close"])

    rsi = last["RSI"]

    atr = last["ATR"]

    adx = last["ADX"]

    if pd.notna(rsi):

        if rsi > 55:

            components["RSI"] = 1

        elif rsi < 45:

            components["RSI"] = -1

        else:

            components["RSI"] = 0

    else:

        components["RSI"] = 0

    if close > last["SMA20"]:

        components["SMA20"] = 1

    else:

        components["SMA20"] = -1

    if close > last["SMA50"]:

        components["SMA50"] = 1

    else:

        components["SMA50"] = -1

    structure = analyze_structure(
        df
    )

    components["STRUCTURE"] = structure["score"]

    liquidity = analyze_liquidity(
        df
    )

    components["LIQUIDITY"] = liquidity["score"]

    if (
        pd.notna(last["VOL_RATIO"])
        and last["VOL_RATIO"] > 1.5
    ):

        components["VOLUME"] = (
            1 if close > last["open"]
            else -1 if close < last["open"]
            else 0
        )

    else:

        components["VOLUME"] = 0

    score = sum(
        components.values()
    )

    regime = detect_market_regime(
        df
    )

    return {
        "price": close,
        "RSI": float(rsi) if pd.notna(rsi) else None,
        "ATR": float(atr) if pd.notna(atr) else None,
        "ADX": float(adx) if pd.notna(adx) else None,
        "score": score,
        "components": components,
        "structure": structure,
        "liquidity": liquidity,
        "regime": regime,
        "df": df,
    }


# ============================================================
# DECISION ENGINE
# ============================================================

def weighted_decision(
    mtf,
    oi,
    cvd,
    book,
    book_history,
    funding,
    macro_score,
):

    sources = {}

    # Each source is normalized to [-1, 1].

    mtf_value = 0

    total_tf_weight = 0

    for tf, weight in TF_WEIGHTS.items():

        if tf not in mtf:
            continue

        score = mtf[tf]["score"]

        normalized = np.clip(
            score / 7,
            -1,
            1,
        )

        mtf_value += normalized * weight

        total_tf_weight += weight

    if total_tf_weight:

        mtf_value /= total_tf_weight

        sources["MTF"] = (
            mtf_value,
            35,
        )

    if oi and oi.get("available"):

        sources["OI"] = (
            oi["score"],
            20,
        )

    if cvd and cvd.get("available"):

        sources["CVD"] = (
            cvd["score"],
            15,
        )

    if book and book.get("available"):

        sources["ORDERBOOK"] = (
            book["score"],
            10,
        )

    if funding and funding.get("available"):

        sources["FUNDING"] = (
            funding["score"],
            5,
        )

    if macro_score is not None:

        sources["MACRO"] = (
            macro_score,
            10,
        )

    if (
        book_history
        and book_history.get("available")
    ):

        sources["BOOK HISTORY"] = (
            np.clip(
                book_history["change"] * 3,
                -1,
                1,
            ),
            5,
        )

    total_weight = sum(
        weight
        for _, weight in sources.values()
    )

    if total_weight <= 0:

        return {
            "signal": "KIVÁRÁS",
            "score": 0,
            "coverage": 0,
            "confidence": 0,
            "sources": sources,
        }

    score = sum(
        value * weight
        for value, weight in sources.values()
    ) / total_weight

    positive = sum(
        weight
        for value, weight in sources.values()
        if value > 0.35
    )

    negative = sum(
        weight
        for value, weight in sources.values()
        if value < -0.35
    )

    # Conflict detection.

    conflict = (
        positive >= total_weight * 0.35
        and negative >= total_weight * 0.35
    )

    coverage = total_weight

    if coverage < 65:

        signal = "KIVÁRÁS"

    elif conflict:

        signal = "KIVÁRÁS"

    elif score >= 0.40:

        signal = "LONG"

    elif score <= -0.40:

        signal = "SHORT"

    else:

        signal = "KIVÁRÁS"

    confidence = min(
        100,
        abs(score) * 100,
    )

    return {
        "signal": signal,
        "score": float(score),
        "coverage": float(coverage),
        "confidence": float(confidence),
        "conflict": conflict,
        "sources": sources,
    }


# ============================================================
# LIQUIDATION RISK ZONES – ESTIMATE ONLY
# ============================================================

def estimate_liquidation_zones(
    price,
    atr,
):

    if not atr or atr <= 0:

        return None

    return {
        "upper_risk_zone": price + atr * 3,
        "lower_risk_zone": price - atr * 3,
        "upper_extended_zone": price + atr * 5,
        "lower_extended_zone": price - atr * 5,
        "method": "ATR-based volatility estimate",
    }


# ============================================================
# RISK MANAGEMENT
# ============================================================

def risk_plan(
    signal,
    price,
    atr,
    capital,
    risk_percent,
    leverage,
):

    if signal not in (
        "LONG",
        "SHORT",
    ):

        return None

    if (
        atr is None
        or not np.isfinite(atr)
        or atr <= 0
    ):

        return None

    risk_amount = (
        capital *
        risk_percent / 100
    )

    stop_distance = atr * 1.5

    target_distance = atr * 3

    quantity = (
        risk_amount /
        stop_distance
    )

    max_notional = (
        capital * leverage
    )

    quantity = min(
        quantity,
        max_notional / price,
    )

    actual_notional = quantity * price

    actual_risk = (
        quantity * stop_distance
    )

    if signal == "LONG":

        sl = price - stop_distance

        tp = price + target_distance

    else:

        sl = price + stop_distance

        tp = price - target_distance

    return {
        "entry": price,
        "stop_loss": sl,
        "take_profit": tp,
        "quantity": quantity,
        "notional": actual_notional,
        "risk_amount": actual_risk,
        "risk_reward": 2.0,
    }


# ============================================================
# SIGNAL JOURNAL
# ============================================================

def save_signal(
    symbol,
    signal,
    price,
    score,
    confidence,
    coverage,
    plan,
):

    sl = (
        plan["stop_loss"]
        if plan
        else None
    )

    tp = (
        plan["take_profit"]
        if plan
        else None
    )

    DB.execute(
        """
        INSERT INTO signals (
            created_at,
            symbol,
            signal,
            price,
            score,
            confidence,
            coverage,
            stop_loss,
            take_profit,
            status
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'OPEN')
        """,
        (
            datetime.now(timezone.utc).isoformat(),
            symbol,
            signal,
            price,
            score,
            confidence,
            coverage,
            sl,
            tp,
        ),
    )

    DB.commit()


def update_signal_results(symbol):

    df = fetch_ohlcv(
        symbol,
        "1m",
    )

    if df.empty:
        return

    rows = DB.execute(
        """
        SELECT id, created_at, signal,
               price, stop_loss, take_profit
        FROM signals
        WHERE symbol = ?
        AND status = 'OPEN'
        AND signal IN ('LONG', 'SHORT')
        """,
        (symbol,),
    ).fetchall()

    for row in rows:

        sid, created, signal, entry, sl, tp = row

        if sl is None or tp is None:
            continue

        created_dt = pd.to_datetime(
            created,
            utc=True,
        )

        candles = df[
            df["datetime"] >= created_dt
        ]

        if candles.empty:
            continue

        exit_price = None

        for _, candle in candles.iterrows():

            if signal == "LONG":

                hit_sl = (
                    candle["low"] <= sl
                )

                hit_tp = (
                    candle["high"] >= tp
                )

            else:

                hit_sl = (
                    candle["high"] >= sl
                )

                hit_tp = (
                    candle["low"] <= tp
                )

            # Conservative assumption:
            # SL first if both are touched in one candle.

            if hit_sl:

                exit_price = sl

                break

            if hit_tp:

                exit_price = tp

                break

        if exit_price is None:
            continue

        if signal == "LONG":

            result = (
                exit_price - entry
            ) / entry * 100

        else:

            result = (
                entry - exit_price
            ) / entry * 100

        DB.execute(
            """
            UPDATE signals
            SET status = 'CLOSED',
                close_price = ?,
                result_percent = ?,
                close_time = ?
            WHERE id = ?
            """,
            (
                exit_price,
                result,
                datetime.now(timezone.utc).isoformat(),
                sid,
            ),
        )

    DB.commit()


def load_journal():

    return pd.read_sql_query(
        """
        SELECT *
        FROM signals
        ORDER BY id DESC
        LIMIT 1000
        """,
        sqlite3.connect(DB_FILE),
    )


# ============================================================
# ANALYSIS LOGGER
# ============================================================

def save_analysis(
    symbol,
    price,
    score,
    signal,
    coverage,
):

    DB.execute(
        """
        INSERT INTO analysis_history (
            symbol,
            timestamp,
            price,
            score,
            signal,
            coverage
        )
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            symbol,
            int(time.time() * 1000),
            price,
            score,
            signal,
            coverage,
        ),
    )

    DB.commit()


# ============================================================
# COMPLETE ANALYSIS
# ============================================================

def run_analysis(symbol):

    mtf = {}

    errors = []

    for tf in TIMEFRAMES:

        try:

            df = fetch_ohlcv(
                symbol,
                tf,
            )

            if len(df) < 60:
                continue

            mtf[tf] = analyze_timeframe(
                df
            )

        except Exception as exc:

            errors.append(
                f"{tf}: {exc}"
            )

    if not mtf:

        raise RuntimeError(
            "No valid timeframe data from OKX."
        )

    main_tf = (
        mtf.get("1h")
        or mtf.get("15m")
        or next(iter(mtf.values()))
    )

    price = main_tf["price"]

    ticker = fetch_ticker(
        symbol
    )

    if ticker.get("last"):

        price = float(
            ticker["last"]
        )

    oi_raw = fetch_open_interest(
        symbol
    )

    oi_value = extract_oi_value(
        oi_raw
    )

    oi = analyze_open_interest(
        symbol,
        price,
        oi_value,
    )

    trades = fetch_trades(
        symbol
    )

    store_trades(
        symbol,
        trades,
    )

    cvd = analyze_cvd(
        symbol
    )

    orderbook = fetch_orderbook(
        symbol
    )

    book = analyze_orderbook(
        symbol,
        orderbook,
    )

    book_history = analyze_book_history(
        symbol
    )

    funding_raw = fetch_funding(
        symbol
    )

    funding = analyze_funding(
        funding_raw
    )

    macro = fetch_macro()

    m_score = calculate_macro_score(
        macro
    )

    decision = weighted_decision(
        mtf,
        oi,
        cvd,
        book,
        book_history,
        funding,
        m_score,
    )

    vp = calculate_volume_profile(
        main_tf["df"]
    )

    liquidation = estimate_liquidation_zones(
        price,
        main_tf["ATR"],
    )

    save_analysis(
        symbol,
        price,
        decision["score"],
        decision["signal"],
        decision["coverage"],
    )

    return {
        "symbol": symbol,
        "price": price,
        "mtf": mtf,
        "oi": oi,
        "cvd": cvd,
        "book": book,
        "book_history": book_history,
        "funding": funding,
        "macro": macro,
        "macro_score": m_score,
        "decision": decision,
        "volume_profile": vp,
        "liquidation": liquidation,
        "errors": errors,
    }


# ============================================================
# CHART
# ============================================================

def build_chart(df, symbol):

    fig = go.Figure()

    fig.add_trace(
        go.Candlestick(
            x=df["datetime"],
            open=df["open"],
            high=df["high"],
            low=df["low"],
            close=df["close"],
            name=symbol,
        )
    )

    fig.add_trace(
        go.Scatter(
            x=df["datetime"],
            y=df["SMA20"],
            name="SMA20",
            line=dict(width=1),
        )
    )

    fig.add_trace(
        go.Scatter(
            x=df["datetime"],
            y=df["SMA50"],
            name="SMA50",
            line=dict(width=1),
        )
    )

    fig.update_layout(
        template="plotly_dark",
        height=550,
        xaxis_rangeslider_visible=False,
    )

    return fig


# ============================================================
# GROQ AI
# ============================================================

def ai_report(result):

    try:

        api_key = st.secrets.get(
            "GROQ_API_KEY",
            os.getenv("GROQ_API_KEY"),
        )

    except Exception:

        api_key = os.getenv(
            "GROQ_API_KEY"
        )

    if not api_key:

        return (
            "A GROQ_API_KEY nincs beállítva. "
            "Az elemzőrendszer ettől még működik."
        )

    prompt = f"""
You are SALIGA AI Institutional Crypto Analyst.

Write a detailed Hungarian market analysis.

MARKET:
{result['symbol']}

PRICE:
{result['price']}

DECISION:
{result['decision']}

OPEN INTEREST:
{result['oi']}

CVD:
{result['cvd']}

ORDER BOOK:
{result['book']}

ORDER BOOK HISTORY:
{result['book_history']}

FUNDING:
{result['funding']}

MACRO:
{result['macro']}

VOLUME PROFILE:
{result['volume_profile']}

MARKET STRUCTURE:
{{
    tf: data['structure']
    for tf, data in result['mtf'].items()
}}

Explain:

1. Current market direction.
2. Position building and closing estimates.
3. Aggressive buyer/seller activity.
4. Liquidity conditions.
5. Important support and resistance.
6. Macro environment.
7. Risks and invalidation levels.
8. Whether waiting is preferable.

Do not invent unavailable data.
Do not claim guaranteed accuracy.
Do not describe a score as a win probability.
"""

    try:

        response = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": "llama-3.3-70b-versatile",
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "You are a professional crypto analyst. "
                            "Respond in Hungarian."
                        ),
                    },
                    {
                        "role": "user",
                        "content": prompt,
                    },
                ],
                "temperature": 0.2,
                "max_tokens": 1800,
            },
            timeout=40,
        )

        response.raise_for_status()

        return response.json()[
            "choices"
        ][0]["message"]["content"]

    except Exception as exc:

        return (
            f"Groq AI error: {exc}"
        )


# ============================================================
# SIDEBAR
# ============================================================

st.sidebar.title(
    "SALIGA AI CONTROL"
)

asset = st.sidebar.selectbox(
    "Market",
    list(SYMBOLS.keys()),
)

symbol = SYMBOLS[asset]

capital = st.sidebar.number_input(
    "Account capital (USDT)",
    min_value=10.0,
    value=1000.0,
    step=100.0,
)

risk_percent = st.sidebar.slider(
    "Risk per trade (%)",
    0.25,
    3.0,
    1.0,
    0.25,
)

leverage = st.sidebar.slider(
    "Maximum leverage",
    1,
    20,
    5,
)

st.sidebar.caption(
    "Data source: OKX only."
)

run_button = st.sidebar.button(
    "⚡ RUN FULL ANALYSIS",
    type="primary",
)


# ============================================================
# SESSION
# ============================================================

if "last_result" not in st.session_state:

    st.session_state.last_result = None

if run_button:

    st.cache_data.clear()

    with st.spinner(
        "SALIGA AI is analyzing OKX markets..."
    ):

        try:

            st.session_state.last_result = run_analysis(
                symbol
            )

            update_signal_results(
                symbol
            )

        except Exception as exc:

            st.error(
                f"Analysis failed: {exc}"
            )


# ============================================================
# MAIN DASHBOARD
# ============================================================

result = st.session_state.last_result

if result is None:

    st.info(
        "Nyomd meg a RUN FULL ANALYSIS gombot "
        "az első elemzés elindításához."
    )

else:

    decision = result["decision"]

    st.subheader(
        "MARKET OVERVIEW"
    )

    c1, c2, c3, c4 = st.columns(4)

    c1.metric(
        "PRICE",
        f"{result['price']:,.4f}",
    )

    c2.metric(
        "DECISION",
        decision["signal"],
    )

    c3.metric(
        "SCORE",
        f"{decision['score']:.3f}",
    )

    c4.metric(
        "DATA COVERAGE",
        f"{decision['coverage']:.0f}%",
    )

    st.caption(
        "A confidence/score nem nyerési valószínűség. "
        "Az adatlefedettség azt mutatja, hogy a döntési "
        "modell súlyainak mekkora része állt rendelkezésre."
    )

    # --------------------------------------------------------
    # POSITION FLOW
    # --------------------------------------------------------

    st.subheader(
        "POSITION FLOW INTELLIGENCE"
    )

    a, b, c = st.columns(3)

    a.metric(
        "OPEN INTEREST",
        result["oi"]["state"],
    )

    b.metric(
        "CVD",
        result["cvd"]["signal"],
    )

    c.metric(
        "ORDER BOOK",
        (
            f"{result['book']['imbalance']:.3f}"
            if result["book"].get("available")
            else "UNAVAILABLE"
        ),
    )

    st.write(
        "OI change:",
        result["oi"].get(
            "oi_change",
            "N/A",
        ),
    )

    st.write(
        "CVD delta:",
        result["cvd"].get(
            "delta",
            "N/A",
        ),
    )

    st.write(
        "Order Book history:",
        result["book_history"]["trend"],
    )

    # --------------------------------------------------------
    # DECISION COMPONENTS
    # --------------------------------------------------------

    st.subheader(
        "DECISION ENGINE"
    )

    rows = []

    for name, values in decision["sources"].items():

        value, weight = values

        rows.append({
            "Module": name,
            "Score": round(
                float(value),
                3,
            ),
            "Weight": weight,
        })

    st.dataframe(
        pd.DataFrame(rows),
        use_container_width=True,
        hide_index=True,
    )

    if decision.get("conflict"):

        st.warning(
            "A fő adatforrások egymásnak ellentmondanak. "
            "A rendszer ezért KIVÁRÁS döntést alkalmaz."
        )

    # --------------------------------------------------------
    # MULTI TIMEFRAME
    # --------------------------------------------------------

    st.subheader(
        "MULTI-TIMEFRAME ANALYSIS"
    )

    tf_rows = []

    for tf, data in result["mtf"].items():

        tf_rows.append({
            "Timeframe": tf,
            "Price": data["price"],
            "Score": data["score"],
            "RSI": data["RSI"],
            "ADX": data["ADX"],
            "Structure": data["structure"]["trend"],
            "BOS": data["structure"]["bos"],
            "CHoCH": data["structure"]["choch"],
            "Regime": data["regime"]["regime"],
            "Volatility": data["regime"]["volatility"],
        })

    st.dataframe(
        pd.DataFrame(tf_rows),
        use_container_width=True,
        hide_index=True,
    )

    # --------------------------------------------------------
    # VOLUME PROFILE
    # --------------------------------------------------------

    st.subheader(
        "VOLUME PROFILE"
    )

    vp = result["volume_profile"]

    if vp:

        x1, x2, x3 = st.columns(3)

        x1.metric(
            "POC",
            f"{vp['POC']:,.4f}",
        )

        x2.metric(
            "VAH",
            f"{vp['VAH']:,.4f}",
        )

        x3.metric(
            "VAL",
            f"{vp['VAL']:,.4f}",
        )

    else:

        st.warning(
            "Volume Profile nem számítható."
        )

    # --------------------------------------------------------
    # FUNDING
    # --------------------------------------------------------

    st.subheader(
        "FUNDING INTELLIGENCE"
    )

    if result["funding"].get("available"):

        st.metric(
            "Funding rate",
            f"{result['funding']['rate'] * 100:.5f}%",
        )

        st.write(
            result["funding"]["state"]
        )

    else:

        st.warning(
            "Funding adat nem elérhető."
        )

    # --------------------------------------------------------
    # LIQUIDATION RISK ZONES
    # --------------------------------------------------------

    st.subheader(
        "LIQUIDATION RISK ZONES – ESTIMATE"
    )

    zones = result["liquidation"]

    if zones:

        z1, z2 = st.columns(2)

        z1.metric(
            "Upper volatility zone",
            f"{zones['upper_risk_zone']:,.4f}",
        )

        z2.metric(
            "Lower volatility zone",
            f"{zones['lower_risk_zone']:,.4f}",
        )

        st.caption(
            "ATR-alapú volatilitási becslés, "
            "nem tényleges likvidációs heatmap."
        )

    # --------------------------------------------------------
    # MACRO
    # --------------------------------------------------------

    st.subheader(
        "MACRO INTELLIGENCE"
    )

    if result["macro"]:

        macro_rows = []

        for name, data in result["macro"].items():

            macro_rows.append({
                "Asset": name,
                "Value": data["value"],
                "Change (%)": round(
                    data["change"],
                    3,
                ),
            })

        st.dataframe(
            pd.DataFrame(macro_rows),
            use_container_width=True,
            hide_index=True,
        )

    else:

        st.warning(
            "Makroadatok nem érhetők el."
        )

    # --------------------------------------------------------
    # CHART
    # --------------------------------------------------------

    st.subheader(
        "PRICE CHART"
    )

    chart_tf = st.selectbox(
        "Timeframe",
        TIMEFRAMES,
        index=3,
    )

    chart_data = add_indicators(
        fetch_ohlcv(
            symbol,
            chart_tf,
        )
    )

    st.plotly_chart(
        build_chart(
            chart_data,
            symbol,
        ),
        use_container_width=True,
    )

    # --------------------------------------------------------
    # RISK MANAGEMENT
    # --------------------------------------------------------

    st.subheader(
        "RISK MANAGEMENT"
    )

    risk_source = (
        result["mtf"].get("1h")
        or result["mtf"].get("15m")
        or next(iter(result["mtf"].values()))
    )

    plan = risk_plan(
        decision["signal"],
        result["price"],
        risk_source["ATR"],
        capital,
        risk_percent,
        leverage,
    )

    if plan:

        r1, r2, r3 = st.columns(3)

        r1.metric(
            "STOP LOSS",
            f"{plan['stop_loss']:,.4f}",
        )

        r2.metric(
            "TAKE PROFIT",
            f"{plan['take_profit']:,.4f}",
        )

        r3.metric(
            "POSITION SIZE",
            f"{plan['quantity']:.5f}",
        )

        st.write(
            f"Notional: {plan['notional']:.2f} USDT"
        )

        st.write(
            f"Planned risk: {plan['risk_amount']:.2f} USDT"
        )

        st.write(
            f"Risk/Reward: 1:{plan['risk_reward']:.2f}"
        )

        st.caption(
            "A pozícióméret becslés. A tényleges kötésnél "
            "figyelembe kell venni a contract size-t, "
            "a minimum mennyiséget, díjakat és csúszást."
        )

    else:

        st.info(
            "KIVÁRÁS: nincs irányított pozícióterv."
        )

    # --------------------------------------------------------
    # SAVE SIGNAL
    # --------------------------------------------------------

    st.subheader(
        "SIGNAL JOURNAL"
    )

    if st.button(
        "SAVE CURRENT SIGNAL",
        use_container_width=True,
    ):

        save_signal(
            symbol,
            decision["signal"],
            result["price"],
            decision["score"],
            decision["confidence"],
            decision["coverage"],
            plan,
        )

        st.success(
            "Signal saved."
        )

    # --------------------------------------------------------
    # AI REPORT
    # --------------------------------------------------------

    st.subheader(
        "SALIGA AI ANALYST"
    )

    if st.button(
        "GENERATE AI REPORT",
        use_container_width=True,
    ):

        with st.spinner(
            "AI analysis..."
        ):

            report = ai_report(
                result
            )

        st.markdown(
            report
        )

    # --------------------------------------------------------
    # NEWS
    # --------------------------------------------------------

    st.subheader(
        "CRYPTO NEWS"
    )

    for item in fetch_news():

        st.markdown(
            f"- [{item['title']}]({item['link']})"
        )

    # --------------------------------------------------------
    # JOURNAL
    # --------------------------------------------------------

    update_signal_results(
        symbol
    )

    journal = load_journal()

    if not journal.empty:

        st.dataframe(
            journal,
            use_container_width=True,
            hide_index=True,
        )

        closed = journal[
            journal["status"] == "CLOSED"
        ]

        if not closed.empty:

            wins = (
                closed["result_percent"] > 0
            ).sum()

            total = len(closed)

            winrate = (
                wins / total * 100
            )

            avg_result = (
                closed["result_percent"].mean()
            )

            a, b, c = st.columns(3)

            a.metric(
                "WIN RATE",
                f"{winrate:.1f}%",
            )

            b.metric(
                "CLOSED SIGNALS",
                total,
            )

            c.metric(
                "AVERAGE RESULT",
                f"{avg_result:.3f}%",
            )

        csv = journal.to_csv(
            index=False
        ).encode("utf-8")

        st.download_button(
            "DOWNLOAD JOURNAL CSV",
            csv,
            "saliga_ai_journal.csv",
            "text/csv",
        )

    # --------------------------------------------------------
    # ERRORS
    # --------------------------------------------------------

    if result["errors"]:

        with st.expander(
            "DATA WARNINGS"
        ):

            for error in result["errors"]:

                st.warning(error)


# ============================================================
# FOOTER
# ============================================================

st.divider()

st.caption(
    f"{APP_NAME} | v{VERSION} | "
    "OKX ONLY | NO EMA | "
    "Analytical system – not financial advice."
)
