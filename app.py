from __future__ import annotations

import os
import json
import time
import sqlite3
import logging
from datetime import datetime, timezone
from typing import Any

import ccxt
import numpy as np
import pandas as pd
import requests
import streamlit as st
import plotly.graph_objects as go
import feedparser
import yfinance as yf

try:
    from streamlit_autorefresh import st_autorefresh
    AUTO_REFRESH_AVAILABLE = True
except ImportError:
    AUTO_REFRESH_AVAILABLE = False


# ============================================================
# SALIGA AI – MARKET INTELLIGENCE
# OKX ONLY | NO EMA | GROQ AI CHAT
# ============================================================

APP_NAME = "SALIGA AI"
APP_VERSION = "6.0"

GROQ_MODEL = "openai/gpt-oss-120b"
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"

SYMBOLS = {
    "BTC": "BTC/USDT:USDT",
    "ETH": "ETH/USDT:USDT",
    "SOL": "SOL/USDT:USDT",
}

TIMEFRAMES = ["1m", "5m", "15m", "1h", "4h", "1d"]

DB_FILE = "saliga_ai.db"

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("SALIGA_AI")


# ============================================================
# PAGE
# ============================================================

st.set_page_config(
    page_title="SALIGA AI – Institutional Intelligence",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="expanded",
)


# ============================================================
# STYLE
# ============================================================

st.markdown("""
<style>
    .stApp {
        background: #080d16;
        color: #e6edf7;
    }

    section[data-testid="stSidebar"] {
        background: #101827;
    }

    .main-title {
        font-size: 34px;
        font-weight: 900;
        color: #00e5a8;
        letter-spacing: 1px;
    }

    .sub-title {
        color: #8998ad;
        font-size: 13px;
    }

    .metric-card {
        background: #111c2c;
        border: 1px solid #25354a;
        border-radius: 12px;
        padding: 16px;
        margin-bottom: 8px;
    }

    .positive {
        color: #00e5a8;
        font-weight: 800;
    }

    .negative {
        color: #ff5470;
        font-weight: 800;
    }

    .neutral {
        color: #f5c451;
        font-weight: 800;
    }

    .small-muted {
        color: #8796aa;
        font-size: 12px;
    }

    div[data-testid="stMetric"] {
        background: #111c2c;
        padding: 12px;
        border-radius: 10px;
        border: 1px solid #25354a;
    }
</style>
""", unsafe_allow_html=True)


# ============================================================
# DATABASE
# ============================================================

def db_connection():
    conn = sqlite3.connect(DB_FILE, check_same_thread=False)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS oi_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT,
            timestamp INTEGER,
            open_interest REAL
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS signal_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT,
            timestamp INTEGER,
            price REAL,
            score REAL,
            direction TEXT
        )
    """)

    conn.commit()
    return conn


DB = db_connection()


def save_oi(symbol: str, value: float):
    if not value or not np.isfinite(value):
        return

    try:
        DB.execute(
            "INSERT INTO oi_history(symbol,timestamp,open_interest) VALUES(?,?,?)",
            (symbol, int(time.time()), float(value))
        )
        DB.commit()
    except Exception as e:
        logger.warning("OI save error: %s", e)


def get_previous_oi(symbol: str):
    try:
        row = DB.execute("""
            SELECT timestamp, open_interest
            FROM oi_history
            WHERE symbol = ?
            AND timestamp <= ?
            ORDER BY timestamp DESC
            LIMIT 1
        """, (symbol, int(time.time()) - 1800)).fetchone()

        if row:
            return row[1], row[0]

    except Exception as e:
        logger.warning("OI history error: %s", e)

    return None, None


def save_signal(symbol, price, score, direction):
    try:
        DB.execute(
            """INSERT INTO signal_history
            (symbol,timestamp,price,score,direction)
            VALUES(?,?,?,?,?)""",
            (
                symbol,
                int(time.time()),
                float(price),
                float(score),
                direction
            )
        )
        DB.commit()
    except Exception as e:
        logger.warning("Signal save error: %s", e)


# ============================================================
# OKX EXCHANGE
# ============================================================

@st.cache_resource
def get_exchange():
    exchange = ccxt.okx({
        "enableRateLimit": True,
        "timeout": 15000,
        "options": {
            "defaultType": "swap",
        }
    })

    exchange.load_markets()
    return exchange


def safe_float(value, default=0.0):
    try:
        result = float(value)
        if np.isfinite(result):
            return result
    except (TypeError, ValueError):
        pass
    return default


# ============================================================
# MARKET DATA
# ============================================================

@st.cache_data(ttl=15, show_spinner=False)
def fetch_ohlcv(symbol: str, timeframe: str, limit=300):
    exchange = get_exchange()

    data = exchange.fetch_ohlcv(
        symbol,
        timeframe=timeframe,
        limit=limit
    )

    if not data:
        raise ValueError("Az OKX nem adott vissza gyertyadatokat.")

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

    df["timestamp"] = pd.to_datetime(
        df["timestamp"],
        unit="ms",
        utc=True
    )

    return df


@st.cache_data(ttl=10, show_spinner=False)
def fetch_ticker(symbol: str):
    exchange = get_exchange()
    return exchange.fetch_ticker(symbol)


@st.cache_data(ttl=20, show_spinner=False)
def fetch_orderbook(symbol: str):
    exchange = get_exchange()
    return exchange.fetch_order_book(symbol, limit=50)


@st.cache_data(ttl=20, show_spinner=False)
def fetch_funding(symbol: str):
    exchange = get_exchange()

    try:
        return exchange.fetch_funding_rate(symbol)
    except Exception:
        return {}


@st.cache_data(ttl=30, show_spinner=False)
def fetch_open_interest(symbol: str):
    exchange = get_exchange()

    try:
        return exchange.fetch_open_interest(symbol)
    except Exception:
        return {}


@st.cache_data(ttl=15, show_spinner=False)
def fetch_recent_trades(symbol: str):
    exchange = get_exchange()

    try:
        return exchange.fetch_trades(symbol, limit=500)
    except Exception:
        return []


# ============================================================
# TECHNICAL INDICATORS
# ============================================================

def calculate_rsi(series, period=14):
    delta = series.diff()

    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)

    avg_gain = gain.ewm(
        alpha=1 / period,
        min_periods=period,
        adjust=False
    ).mean()

    avg_loss = loss.ewm(
        alpha=1 / period,
        min_periods=period,
        adjust=False
    ).mean()

    rs = avg_gain / avg_loss.replace(0, np.nan)

    rsi = 100 - (100 / (1 + rs))

    return rsi.fillna(50)


def calculate_atr(df, period=14):
    high = df["high"]
    low = df["low"]
    close = df["close"]

    previous_close = close.shift(1)

    tr = pd.concat([
        high - low,
        (high - previous_close).abs(),
        (low - previous_close).abs()
    ], axis=1).max(axis=1)

    return tr.ewm(
        alpha=1 / period,
        min_periods=period,
        adjust=False
    ).mean()


def calculate_adx(df, period=14):
    high = df["high"]
    low = df["low"]

    up_move = high.diff()
    down_move = -low.diff()

    plus_dm = pd.Series(
        np.where(
            (up_move > down_move) & (up_move > 0),
            up_move,
            0
        ),
        index=df.index
    )

    minus_dm = pd.Series(
        np.where(
            (down_move > up_move) & (down_move > 0),
            down_move,
            0
        ),
        index=df.index
    )

    atr = calculate_atr(df, period).replace(0, np.nan)

    plus_di = 100 * plus_dm.ewm(
        alpha=1 / period,
        adjust=False
    ).mean() / atr

    minus_di = 100 * minus_dm.ewm(
        alpha=1 / period,
        adjust=False
    ).mean() / atr

    dx = (
        (plus_di - minus_di).abs()
        / (plus_di + minus_di).replace(0, np.nan)
    ) * 100

    return dx.ewm(
        alpha=1 / period,
        adjust=False
    ).mean().fillna(0)


def calculate_indicators(df):
    df = df.copy()

    df["rsi"] = calculate_rsi(df["close"])
    df["atr"] = calculate_atr(df)
    df["adx"] = calculate_adx(df)

    # SMA használat – EMA nincs a rendszerben.
    df["sma20"] = df["close"].rolling(20).mean()
    df["sma50"] = df["close"].rolling(50).mean()

    df["volume_sma"] = df["volume"].rolling(20).mean()

    df["volume_ratio"] = (
        df["volume"] /
        df["volume_sma"].replace(0, np.nan)
    )

    df["candle_delta"] = np.where(
        df["close"] >= df["open"],
        df["volume"],
        -df["volume"]
    )

    df["estimated_cvd"] = df["candle_delta"].cumsum()

    return df


# ============================================================
# MARKET STRUCTURE
# ============================================================

def market_structure(df, window=3):
    if len(df) < window * 2 + 10:
        return "Nincs elegendő adat"

    highs = df["high"].to_numpy()
    lows = df["low"].to_numpy()

    pivot_highs = []
    pivot_lows = []

    for i in range(window, len(df) - window):
        if highs[i] == max(highs[i-window:i+window+1]):
            pivot_highs.append((i, highs[i]))

        if lows[i] == min(lows[i-window:i+window+1]):
            pivot_lows.append((i, lows[i]))

    if len(pivot_highs) < 2 or len(pivot_lows) < 2:
        return "Oldalazó / bizonytalan"

    h1 = pivot_highs[-2][1]
    h2 = pivot_highs[-1][1]

    l1 = pivot_lows[-2][1]
    l2 = pivot_lows[-1][1]

    if h2 > h1 and l2 > l1:
        return "Bullish – HH / HL"

    if h2 < h1 and l2 < l1:
        return "Bearish – LH / LL"

    return "Oldalazó / vegyes"


def liquidity_levels(df, lookback=50):
    recent = df.tail(lookback)

    return {
        "high": float(recent["high"].max()),
        "low": float(recent["low"].min()),
        "mid": float(
            (recent["high"].max() + recent["low"].min()) / 2
        )
    }


def liquidity_sweep(df):
    if len(df) < 10:
        return "Nincs adat"

    current = df.iloc[-1]
    previous = df.iloc[-8:-1]

    previous_high = previous["high"].max()
    previous_low = previous["low"].min()

    if (
        current["high"] > previous_high
        and current["close"] < previous_high
    ):
        return "Felső likviditás kisöprés – bearish jelzés"

    if (
        current["low"] < previous_low
        and current["close"] > previous_low
    ):
        return "Alsó likviditás kisöprés – bullish jelzés"

    return "Nem látható egyértelmű sweep"


# ============================================================
# ORDER BOOK ANALYSIS
# ============================================================

def analyze_orderbook(book):
    bids = book.get("bids", [])
    asks = book.get("asks", [])

    bid_volume = sum(
        safe_float(x[1]) for x in bids[:20]
    )

    ask_volume = sum(
        safe_float(x[1]) for x in asks[:20]
    )

    total = bid_volume + ask_volume

    if total <= 0:
        return {
            "imbalance": 0,
            "bid_volume": 0,
            "ask_volume": 0,
            "bias": "Nincs adat",
            "bid_wall": None,
            "ask_wall": None
        }

    imbalance = (
        (bid_volume - ask_volume) / total
    ) * 100

    bid_wall = max(
        bids[:20],
        key=lambda x: safe_float(x[1]),
        default=None
    )

    ask_wall = max(
        asks[:20],
        key=lambda x: safe_float(x[1]),
        default=None
    )

    if imbalance > 15:
        bias = "Vevői fölény"
    elif imbalance < -15:
        bias = "Eladói fölény"
    else:
        bias = "Kiegyensúlyozott"

    return {
        "imbalance": imbalance,
        "bid_volume": bid_volume,
        "ask_volume": ask_volume,
        "bias": bias,
        "bid_wall": bid_wall,
        "ask_wall": ask_wall
    }


# ============================================================
# TRADE FLOW / ESTIMATED DELTA
# ============================================================

def analyze_trades(trades):
    buy_volume = 0.0
    sell_volume = 0.0

    for trade in trades:
        side = trade.get("side")
        amount = safe_float(trade.get("amount"))

        if side == "buy":
            buy_volume += amount
        elif side == "sell":
            sell_volume += amount

    total = buy_volume + sell_volume

    if total == 0:
        return {
            "buy": 0,
            "sell": 0,
            "delta": 0,
            "buy_ratio": 0,
            "bias": "Nincs adat"
        }

    delta = buy_volume - sell_volume
    buy_ratio = buy_volume / total * 100

    if buy_ratio >= 60:
        bias = "Erős vételi aktivitás"
    elif buy_ratio <= 40:
        bias = "Erős eladói aktivitás"
    else:
        bias = "Vegyes aktivitás"

    return {
        "buy": buy_volume,
        "sell": sell_volume,
        "delta": delta,
        "buy_ratio": buy_ratio,
        "bias": bias
    }


# ============================================================
# OPEN INTEREST
# ============================================================

def extract_open_interest(data):
    if not isinstance(data, dict):
        return 0.0

    candidates = [
        data.get("openInterestAmount"),
        data.get("openInterestValue"),
        data.get("openInterest"),
        (data.get("info") or {}).get("oi"),
        (data.get("info") or {}).get("oiCcy"),
        (data.get("info") or {}).get("oiUsd"),
    ]

    for value in candidates:
        result = safe_float(value)
        if result > 0:
            return result

    return 0.0


def analyze_oi(symbol, current_oi, price_change):
    if current_oi <= 0:
        return {
            "change": None,
            "interpretation": "Nincs elérhető OI-adat"
        }

    save_oi(symbol, current_oi)

    previous, previous_time = get_previous_oi(symbol)

    if not previous or previous <= 0:
        return {
            "change": None,
            "interpretation": "Gyűjtjük az OI-előzményeket"
        }

    change = (
        (current_oi - previous) / previous
    ) * 100

    if change > 0.5 and price_change > 0:
        interpretation = "Long pozícióépülésre utalhat"

    elif change > 0.5 and price_change < 0:
        interpretation = "Short pozícióépülésre utalhat"

    elif change < -0.5 and price_change > 0:
        interpretation = "Short zárásokra utalhat"

    elif change < -0.5 and price_change < 0:
        interpretation = "Long zárásokra utalhat"

    else:
        interpretation = "Nincs jelentős OI-változás"

    return {
        "change": change,
        "interpretation": interpretation
    }


# ============================================================
# FUNDING
# ============================================================

def analyze_funding(data):
    rate = safe_float(data.get("fundingRate"))

    if rate > 0.0005:
        interpretation = "Magas pozitív funding – long oldal zsúfolt lehet"

    elif rate < -0.0005:
        interpretation = "Negatív funding – short oldal zsúfolt lehet"

    else:
        interpretation = "Funding semleges"

    return {
        "rate": rate,
        "percent": rate * 100,
        "interpretation": interpretation
    }


# ============================================================
# VOLUME PROFILE – APPROXIMATION
# ============================================================

def calculate_volume_profile(df, bins=30):
    recent = df.tail(150).copy()

    if len(recent) < 10:
        return None

    typical_price = (
        recent["high"] +
        recent["low"] +
        recent["close"]
    ) / 3

    volumes = recent["volume"].to_numpy()

    histogram, edges = np.histogram(
        typical_price.to_numpy(),
        bins=bins,
        weights=volumes
    )

    index = int(np.argmax(histogram))

    poc = (edges[index] + edges[index + 1]) / 2

    total_volume = histogram.sum()

    if total_volume <= 0:
        return None

    target = total_volume * 0.70

    sorted_indices = np.argsort(histogram)[::-1]

    accumulated = 0
    selected = []

    for idx in sorted_indices:
        accumulated += histogram[idx]
        selected.append(idx)

        if accumulated >= target:
            break

    vah = max(edges[i + 1] for i in selected)
    val = min(edges[i] for i in selected)

    return {
        "poc": float(poc),
        "vah": float(vah),
        "val": float(val)
    }


# ============================================================
# MACRO DATA
# ============================================================

@st.cache_data(ttl=300, show_spinner=False)
def fetch_macro():
    symbols = {
        "DXY": "DX-Y.NYB",
        "VIX": "^VIX",
        "US10Y": "^TNX",
        "NASDAQ": "^IXIC",
        "SP500": "^GSPC"
    }

    results = {}

    for name, ticker in symbols.items():
        try:
            data = yf.Ticker(ticker).history(period="5d")

            if data.empty or len(data) < 2:
                results[name] = None
                continue

            last = float(data["Close"].iloc[-1])
            previous = float(data["Close"].iloc[-2])

            change = (
                (last - previous) / previous
            ) * 100 if previous else 0

            results[name] = {
                "price": last,
                "change": change
            }

        except Exception as e:
            logger.warning("Macro %s error: %s", name, e)
            results[name] = None

    return results


def macro_interpretation(macro):
    score = 0
    details = []

    dxy = macro.get("DXY")
    vix = macro.get("VIX")
    nasdaq = macro.get("NASDAQ")
    sp500 = macro.get("SP500")

    if dxy:
        if dxy["change"] < 0:
            score += 1
            details.append("DXY gyengül")
        elif dxy["change"] > 0:
            score -= 1
            details.append("DXY erősödik")

    if vix:
        if vix["change"] < 0:
            score += 1
            details.append("VIX csökken")
        elif vix["change"] > 0:
            score -= 1
            details.append("VIX emelkedik")

    if nasdaq:
        if nasdaq["change"] > 0:
            score += 1
            details.append("NASDAQ emelkedik")
        elif nasdaq["change"] < 0:
            score -= 1
            details.append("NASDAQ esik")

    if sp500:
        if sp500["change"] > 0:
            score += 1
            details.append("S&P 500 emelkedik")
        elif sp500["change"] < 0:
            score -= 1
            details.append("S&P 500 esik")

    if score >= 2:
        bias = "Kedvezőbb kockázati környezet"
    elif score <= -2:
        bias = "Kockázatkerülő környezet"
    else:
        bias = "Vegyes makrokörnyezet"

    return {
        "score": score,
        "bias": bias,
        "details": details
    }


# ============================================================
# NEWS
# ============================================================

@st.cache_data(ttl=300, show_spinner=False)
def fetch_news():
    feeds = [
        "https://cointelegraph.com/rss",
        "https://www.coindesk.com/arc/outboundfeeds/rss/"
    ]

    items = []

    for url in feeds:
        try:
            feed = feedparser.parse(url)

            for entry in feed.entries[:10]:
                items.append({
                    "title": entry.get("title", ""),
                    "link": entry.get("link", ""),
                    "published": entry.get("published", ""),
                    "source": url
                })

        except Exception as e:
            logger.warning("News error: %s", e)

    return items[:15]


# ============================================================
# MULTI TIMEFRAME ANALYSIS
# ============================================================

def analyze_timeframe(symbol, timeframe):
    df = fetch_ohlcv(symbol, timeframe)
    df = calculate_indicators(df)

    last = df.iloc[-1]
    previous = df.iloc[-2]

    price = safe_float(last["close"])

    score = 0
    reasons = []

    if last["close"] > last["sma20"]:
        score += 1
        reasons.append("Ár SMA20 felett")

    else:
        score -= 1
        reasons.append("Ár SMA20 alatt")

    if last["sma20"] > last["sma50"]:
        score += 1
        reasons.append("SMA20 > SMA50")

    else:
        score -= 1
        reasons.append("SMA20 < SMA50")

    if last["rsi"] > 55:
        score += 1
        reasons.append("RSI bullish")

    elif last["rsi"] < 45:
        score -= 1
        reasons.append("RSI bearish")

    if last["close"] > previous["close"]:
        score += 0.5
    else:
        score -= 0.5

    if last["volume_ratio"] > 1.5:
        reasons.append("Emelkedett volumen")

    return {
        "df": df,
        "price": price,
        "score": score,
        "rsi": safe_float(last["rsi"]),
        "adx": safe_float(last["adx"]),
        "atr": safe_float(last["atr"]),
        "structure": market_structure(df),
        "reasons": reasons,
        "volume_ratio": safe_float(last["volume_ratio"]),
        "timestamp": str(last["timestamp"])
    }


# ============================================================
# MAIN ASSET ANALYSIS
# ============================================================

def analyze_asset(name, symbol):
    results = {}

    for timeframe in TIMEFRAMES:
        try:
            results[timeframe] = analyze_timeframe(
                symbol,
                timeframe
            )
        except Exception as e:
            logger.warning(
                "%s %s error: %s",
                name,
                timeframe,
                e
            )

    if not results:
        raise RuntimeError(
            f"{name}: nem sikerült idősík-adatokat lekérni."
        )

    primary = results.get("15m") or results.get("5m") or next(
        iter(results.values())
    )

    df = primary["df"]

    ticker = fetch_ticker(symbol)
    orderbook = fetch_orderbook(symbol)
    funding_data = fetch_funding(symbol)
    oi_data = fetch_open_interest(symbol)
    trades = fetch_recent_trades(symbol)

    order = analyze_orderbook(orderbook)
    trade_flow = analyze_trades(trades)
    funding = analyze_funding(funding_data)

    current_oi = extract_open_interest(oi_data)

    price_change = (
        (df["close"].iloc[-1] - df["close"].iloc[-2])
        / df["close"].iloc[-2]
    ) * 100

    oi = analyze_oi(
        symbol,
        current_oi,
        price_change
    )

    vp = calculate_volume_profile(df)
    levels = liquidity_levels(df)
    sweep = liquidity_sweep(df)

    # Súlyozott több idősíkos pontszám.
    weights = {
        "1m": 0.05,
        "5m": 0.10,
        "15m": 0.20,
        "1h": 0.25,
        "4h": 0.25,
        "1d": 0.15
    }

    weighted_score = 0
    total_weight = 0

    for tf, data in results.items():
        weight = weights.get(tf, 0.1)
        weighted_score += data["score"] * weight
        total_weight += weight

    if total_weight:
        weighted_score /= total_weight

    # Order book hozzájárulás.
    weighted_score += order["imbalance"] / 100 * 0.8

    # Trade flow hozzájárulás.
    weighted_score += (
        (trade_flow["buy_ratio"] - 50) / 50
    ) * 0.5

    # OI és funding nem kapnak automatikusan irányt:
    # ezek önmagukban nem bizonyítják a piaci irányt.

    if weighted_score >= 1.1:
        direction = "LONG BIAS"
    elif weighted_score <= -1.1:
        direction = "SHORT BIAS"
    else:
        direction = "NEUTRAL"

    confidence = min(
        95,
        max(5, 50 + abs(weighted_score) * 15)
    )

    last = df.iloc[-1]

    atr = safe_float(last["atr"])
    price = safe_float(last["close"])

    if direction == "LONG BIAS":
        stop = price - atr * 1.5
        target1 = price + atr * 1.5
        target2 = price + atr * 3

    elif direction == "SHORT BIAS":
        stop = price + atr * 1.5
        target1 = price - atr * 1.5
        target2 = price - atr * 3

    else:
        stop = None
        target1 = None
        target2 = None

    save_signal(
        name,
        price,
        weighted_score,
        direction
    )

    return {
        "name": name,
        "symbol": symbol,
        "price": price,
        "ticker": ticker,
        "timeframes": results,
        "score": weighted_score,
        "confidence": confidence,
        "direction": direction,
        "orderbook": order,
        "trade_flow": trade_flow,
        "funding": funding,
        "oi": oi,
        "current_oi": current_oi,
        "volume_profile": vp,
        "liquidity": levels,
        "sweep": sweep,
        "price_change": price_change,
        "stop": stop,
        "target1": target1,
        "target2": target2,
        "timestamp": datetime.now(timezone.utc).isoformat()
    }


# ============================================================
# GROQ API
# ============================================================

def get_groq_key():
    try:
        key = st.secrets.get("GROQ_API_KEY", "")
        if key:
            return str(key).strip()
    except Exception:
        pass

    return os.getenv("GROQ_API_KEY", "").strip()


def groq_chat(messages):
    api_key = get_groq_key()

    if not api_key:
        raise RuntimeError(
            "Nem található GROQ_API_KEY. "
            "Ellenőrizd a Streamlit Secrets beállítását."
        )

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }

    payload = {
        "model": GROQ_MODEL,
        "messages": messages,
        "temperature": 0.35,
        "max_completion_tokens": 1800,
        "reasoning_effort": "medium"
    }

    try:
        response = requests.post(
            GROQ_URL,
            headers=headers,
            json=payload,
            timeout=90
        )

        if response.status_code != 200:
            try:
                error_body = response.json()
            except Exception:
                error_body = response.text[:1000]

            raise RuntimeError(
                f"Groq API hiba ({response.status_code}): "
                f"{error_body}"
            )

        data = response.json()

        return data["choices"][0]["message"]["content"]

    except requests.Timeout:
        raise RuntimeError(
            "A Groq API nem válaszolt időben. Próbáld újra."
        )

    except requests.RequestException as e:
        raise RuntimeError(
            f"Kapcsolódási hiba a Groq API-hoz: {e}"
        )


# ============================================================
# AI CONTEXT
# ============================================================

def build_ai_context(all_results, macro, macro_view):
    context = {
        "generated_at": datetime.now(
            timezone.utc
        ).isoformat(),
        "assets": {},
        "macro": macro,
        "macro_interpretation": macro_view
    }

    for name, result in all_results.items():
        context["assets"][name] = {
            "price": result["price"],
            "price_change_percent": result["price_change"],
            "direction": result["direction"],
            "score": result["score"],
            "confidence_estimate": result["confidence"],
            "timeframes": {
                tf: {
                    "score": data["score"],
                    "rsi": data["rsi"],
                    "adx": data["adx"],
                    "atr": data["atr"],
                    "structure": data["structure"],
                    "volume_ratio": data["volume_ratio"]
                }
                for tf, data in result["timeframes"].items()
            },
            "orderbook": result["orderbook"],
            "trade_flow": result["trade_flow"],
            "funding": result["funding"],
            "open_interest": result["oi"],
            "liquidity": result["liquidity"],
            "liquidity_sweep": result["sweep"],
            "volume_profile": result["volume_profile"]
        }

    return json.dumps(
        context,
        ensure_ascii=False,
        default=str
    )


# ============================================================
# CHAT STATE
# ============================================================

if "chat_messages" not in st.session_state:
    st.session_state.chat_messages = []

if "market_results" not in st.session_state:
    st.session_state.market_results = {}

if "macro_data" not in st.session_state:
    st.session_state.macro_data = {}

if "last_analysis_time" not in st.session_state:
    st.session_state.last_analysis_time = None

if "analysis_errors" not in st.session_state:
    st.session_state.analysis_errors = {}


# ============================================================
# SIDEBAR
# ============================================================

st.sidebar.markdown("## ⚡ SALIGA AI")

st.sidebar.caption(
    f"Institutional Market Intelligence | v{APP_VERSION}"
)

st.sidebar.markdown("---")

auto_refresh = st.sidebar.toggle(
    "Automatikus frissítés",
    value=True
)

refresh_seconds = st.sidebar.select_slider(
    "Frissítési idő",
    options=[15, 30, 60, 120],
    value=30,
    format_func=lambda x: f"{x} másodperc"
)

if auto_refresh:
    if AUTO_REFRESH_AVAILABLE:
        refresh_count = st_autorefresh(
            interval=refresh_seconds * 1000,
            key="saliga_auto_refresh"
        )
    else:
        refresh_count = 0
        st.sidebar.error(
            "Hiányzik a streamlit-autorefresh csomag."
        )
else:
    refresh_count = 0

st.sidebar.markdown("---")

selected_assets = st.sidebar.multiselect(
    "Elemzendő instrumentumok",
    options=list(SYMBOLS.keys()),
    default=["BTC", "ETH", "SOL"]
)

manual_refresh = st.sidebar.button(
    "🔄 Piaci adatok frissítése",
    use_container_width=True
)

clear_chat = st.sidebar.button(
    "Chat beszélgetés törlése",
    use_container_width=True
)

if clear_chat:
    st.session_state.chat_messages = []
    st.rerun()


# ============================================================
# HEADER
# ============================================================

st.markdown(
    '<div class="main-title">⚡ SALIGA AI</div>',
    unsafe_allow_html=True
)

st.markdown(
    '<div class="sub-title">'
    'MULTI-ASSET INTELLIGENCE | POSITION FLOW | MARKET STRUCTURE'
    '</div>',
    unsafe_allow_html=True
)

st.markdown("---")


# ============================================================
# REFRESH ENGINE
# ============================================================

if "last_refresh_count" not in st.session_state:
    st.session_state.last_refresh_count = -1

should_run = manual_refresh

if auto_refresh and AUTO_REFRESH_AVAILABLE:
    if refresh_count != st.session_state.last_refresh_count:
        should_run = True

if should_run:
    st.cache_data.clear()

    new_results = {}
    errors = {}

    with st.spinner("OKX piaci adatok lekérése és elemzése..."):

        for asset in selected_assets:
            try:
                new_results[asset] = analyze_asset(
                    asset,
                    SYMBOLS[asset]
                )
            except Exception as e:
                errors[asset] = str(e)
                logger.exception(
                    "Asset analysis failed: %s",
                    asset
                )

        try:
            macro = fetch_macro()
        except Exception as e:
            macro = {}
            errors["MACRO"] = str(e)

        st.session_state.market_results = new_results
        st.session_state.macro_data = macro
        st.session_state.analysis_errors = errors
        st.session_state.last_analysis_time = datetime.now(
            timezone.utc
        )

    st.session_state.last_refresh_count = refresh_count


# ============================================================
# STATUS
# ============================================================

if st.session_state.last_analysis_time:
    st.caption(
        "Utolsó elemzés: "
        + st.session_state.last_analysis_time.strftime(
            "%Y-%m-%d %H:%M:%S UTC"
        )
    )

if auto_refresh and AUTO_REFRESH_AVAILABLE:
    st.caption(
        f"Automatikus frissítés aktív – {refresh_seconds} másodperc"
    )
elif not auto_refresh:
    st.caption("Automatikus frissítés kikapcsolva.")

results = st.session_state.market_results
macro = st.session_state.macro_data
errors = st.session_state.analysis_errors


# ============================================================
# ERRORS
# ============================================================

if errors:
    with st.expander("Adatlekérési hibák", expanded=True):
        for name, error in errors.items():
            st.error(f"{name}: {error}")


# ============================================================
# MARKET OVERVIEW
# ============================================================

if results:

    st.subheader("📊 Piaci áttekintés")

    columns = st.columns(len(results))

    for column, (name, result) in zip(
        columns,
        results.items()
    ):
        with column:
            direction = result["direction"]

            if direction == "LONG BIAS":
                color = "#00e5a8"
            elif direction == "SHORT BIAS":
                color = "#ff5470"
            else:
                color = "#f5c451"

            st.markdown(
                f"""
                <div class="metric-card">
                    <h3>{name}</h3>
                    <h2>${result['price']:,.4f}</h2>
                    <div style="color:{color};font-weight:900">
                        {direction}
                    </div>
                    <p class="small-muted">
                        Score: {result['score']:.2f}
                    </p>
                    <p class="small-muted">
                        Becsült jelzéserősség: {result['confidence']:.1f}%
                    </p>
                </div>
                """,
                unsafe_allow_html=True
            )


    # ========================================================
    # MACRO
    # ========================================================

    st.markdown("---")
    st.subheader("🌍 Makrogazdasági környezet")

    macro_view = macro_interpretation(macro)

    st.info(macro_view["bias"])

    macro_cols = st.columns(5)

    for col, name in zip(
        macro_cols,
        ["DXY", "VIX", "US10Y", "NASDAQ", "SP500"]
    ):
        data = macro.get(name)

        with col:
            if data:
                st.metric(
                    name,
                    f"{data['price']:.2f}",
                    f"{data['change']:+.2f}%"
                )
            else:
                st.metric(name, "N/A")


    # ========================================================
    # ASSET DETAILS
    # ========================================================

    st.markdown("---")
    st.subheader("🔬 Részletes piaci elemzés")

    tabs = st.tabs(list(results.keys()))

    for tab, (name, result) in zip(
        tabs,
        results.items()
    ):

        with tab:

            st.markdown(f"### {name} – OKX Perpetual")

            m1, m2, m3, m4 = st.columns(4)

            m1.metric(
                "Ár",
                f"${result['price']:,.4f}"
            )

            m2.metric(
                "15m RSI",
                f"{result['timeframes'].get('15m', {}).get('rsi', 0):.1f}"
            )

            m3.metric(
                "15m ADX",
                f"{result['timeframes'].get('15m', {}).get('adx', 0):.1f}"
            )

            m4.metric(
                "Funding",
                f"{result['funding']['percent']:.4f}%"
            )

            st.markdown("#### Multi-timeframe elemzés")

            timeframe_rows = []

            for tf, data in result["timeframes"].items():
                timeframe_rows.append({
                    "Idősík": tf,
                    "Score": round(data["score"], 2),
                    "RSI": round(data["rsi"], 2),
                    "ADX": round(data["adx"], 2),
                    "ATR": round(data["atr"], 6),
                    "Struktúra": data["structure"],
                    "Volumenarány": round(
                        data["volume_ratio"],
                        2
                    )
                })

            st.dataframe(
                pd.DataFrame(timeframe_rows),
                use_container_width=True,
                hide_index=True
            )


            # Chart
            chart_df = result["timeframes"].get(
                "15m",
                next(iter(result["timeframes"].values()))
            )["df"].tail(150)

            fig = go.Figure()

            fig.add_trace(
                go.Candlestick(
                    x=chart_df["timestamp"],
                    open=chart_df["open"],
                    high=chart_df["high"],
                    low=chart_df["low"],
                    close=chart_df["close"],
                    name="Price"
                )
            )

            fig.add_trace(
                go.Scatter(
                    x=chart_df["timestamp"],
                    y=chart_df["sma20"],
                    name="SMA20",
                    line=dict(width=1)
                )
            )

            fig.add_trace(
                go.Scatter(
                    x=chart_df["timestamp"],
                    y=chart_df["sma50"],
                    name="SMA50",
                    line=dict(width=1)
                )
            )

            fig.update_layout(
                height=480,
                template="plotly_dark",
                xaxis_rangeslider_visible=False,
                title=f"{name} – 15m chart"
            )

            st.plotly_chart(
                fig,
                use_container_width=True
            )


            # Position flow
            st.markdown("#### 📈 Position flow")

            flow1, flow2, flow3 = st.columns(3)

            with flow1:
                st.metric(
                    "Becsült vételi arány",
                    f"{result['trade_flow']['buy_ratio']:.1f}%"
                )

                st.write(
                    result["trade_flow"]["bias"]
                )

            with flow2:
                st.metric(
                    "Order book imbalance",
                    f"{result['orderbook']['imbalance']:+.1f}%"
                )

                st.write(
                    result["orderbook"]["bias"]
                )

            with flow3:
                oi_change = result["oi"]["change"]

                if oi_change is None:
                    st.metric("Open Interest", "Gyűjtés alatt")
                else:
                    st.metric(
                        "OI változás",
                        f"{oi_change:+.2f}%"
                    )

                st.write(
                    result["oi"]["interpretation"]
                )

            st.caption(
                "Fontos: a vételi/eladási forgalom a legutóbbi "
                "lekért kötésekből becsült érték. Nem teljes "
                "piaci CVD-adat."
            )


            # Funding
            st.markdown("#### Funding és pozicionáltság")

            st.write(
                result["funding"]["interpretation"]
            )

            # Structure
            st.markdown("#### Market structure")

            structures = []

            for tf, data in result["timeframes"].items():
                structures.append(
                    f"**{tf}:** {data['structure']}"
                )

            st.markdown("  \n".join(structures))

            st.write(
                "**Likviditási sweep:**",
                result["sweep"]
            )


            # Volume profile
            st.markdown("#### Volume profile – becslés")

            vp = result["volume_profile"]

            if vp:
                v1, v2, v3 = st.columns(3)

                v1.metric("POC", f"{vp['poc']:,.4f}")
                v2.metric("VAH", f"{vp['vah']:,.4f}")
                v3.metric("VAL", f"{vp['val']:,.4f}")


            # Risk plan
            st.markdown("#### Kockázati terv – ATR-alapú referencia")

            if result["direction"] != "NEUTRAL":

                r1, r2, r3 = st.columns(3)

                r1.metric(
                    "Stop referencia",
                    f"{result['stop']:,.4f}"
                )

                r2.metric(
                    "TP1 referencia",
                    f"{result['target1']:,.4f}"
                )

                r3.metric(
                    "TP2 referencia",
                    f"{result['target2']:,.4f}"
                )

            else:
                st.info(
                    "Nincs egyértelmű irány. "
                    "A rendszer nem ad ATR-alapú belépési tervet."
                )


    # ========================================================
    # NEWS
    # ========================================================

    st.markdown("---")
    st.subheader("📰 Kriptopiaci hírek")

    news = fetch_news()

    if news:
        for item in news:
            title = item.get("title", "")
            link = item.get("link", "")

            if title and link:
                st.markdown(f"- [{title}]({link})")
    else:
        st.info("Jelenleg nem érkezett hír.")


else:

    st.warning(
        "Még nincs piaci elemzés. "
        "Nyomd meg a frissítés gombot, vagy várd meg az automatikus frissítést."
    )


# ============================================================
# AI CHATBOT
# ============================================================

st.markdown("---")
st.subheader("🤖 SALIGA AI – Market Intelligence Chat")

st.caption(
    "Kérdezz a BTC, ETH, SOL piacáról, az OI-ról, "
    "a fundingról, a likviditásról vagy a makrogazdaságról."
)

if get_groq_key():
    st.success("Groq API-kulcs megtalálva.")
else:
    st.error(
        "Nem található GROQ_API_KEY a Streamlit Secretsben."
    )


# Previous messages
for message in st.session_state.chat_messages:
    with st.chat_message(message["role"]):
        st.markdown(message["content"])


user_prompt = st.chat_input(
    "Kérdezz a piacról vagy az aktuális elemzésről..."
)

if user_prompt:

    st.session_state.chat_messages.append({
        "role": "user",
        "content": user_prompt
    })

    with st.chat_message("user"):
        st.markdown(user_prompt)

    system_prompt = """
Te vagy a SALIGA AI, egy professzionális kriptopiaci elemző asszisztens.

Feladatod:
- BTC, ETH és SOL piaci elemzése.
- Multi-timeframe értelmezés.
- Market structure, HH, HL, LH, LL.
- BOS és CHoCH fogalmak értelmezése.
- Likviditási szintek és liquidity sweep.
- Open Interest és pozícióépülés.
- Long és short zárások lehetséges értelmezése.
- Funding rate.
- Order book.
- Volume és estimated delta.
- Makrogazdasági környezet.

FONTOS:
1. Magyarul válaszolj.
2. Legyél szakmai, konkrét és egyenes.
3. Ne találj ki olyan adatot, amely nincs a megadott kontextusban.
4. Különítsd el a tényleges adatot és a becslést.
5. Ne állítsd, hogy egy jelzés biztosan bekövetkezik.
6. Ne adj garantált profitot.
7. Ha nincs elég adat, ezt mondd ki.
8. Ne használj EMA-t. A rendszer SMA20 és SMA50 értékeket használ.
9. A felhasználó nem scalp jelzéseket keres mindenáron. A piaci környezetet és a nagyobb mozgásokat is értékeld.
10. Ne adj automatikus kereskedési utasítást. Elemzést és kockázati szempontokat adj.
"""

    if results:
        context = build_ai_context(
            results,
            macro,
            macro_interpretation(macro)
        )

        system_prompt += (
            "\n\nAKTUÁLIS PIACI ADATOK:\n" + context
        )

    else:
        system_prompt += (
            "\n\nJelenleg nincs betöltött élő piaci kontextus. "
            "Általános kérdésekre válaszolhatsz, de ne állítsd, "
            "hogy aktuális OKX-adatokat látsz."
        )

    messages = [
        {
            "role": "system",
            "content": system_prompt
        }
    ]

    # Utolsó 12 üzenet megtartása.
    messages.extend(
        st.session_state.chat_messages[-12:]
    )

    with st.chat_message("assistant"):

        with st.spinner("SALIGA AI gondolkodik..."):

            try:
                answer = groq_chat(messages)

                st.markdown(answer)

                st.session_state.chat_messages.append({
                    "role": "assistant",
                    "content": answer
                })

            except Exception as e:
                error_message = str(e)

                st.error(error_message)

                st.session_state.chat_messages.append({
                    "role": "assistant",
                    "content": (
                        "Az AI-kérés nem sikerült. "
                        f"Hiba: {error_message}"
                    )
                })


# ============================================================
# FOOTER
# ============================================================

st.markdown("---")

st.caption(
    "SALIGA AI v6.0 | OKX market data | Groq AI | "
    "Research and analytical tool – not financial advice."
)

st.caption(
    "A pontszámok és jelzések saját számítású becslések. "
    "Nem garantálnak piaci irányt vagy kereskedési eredményt."
)
