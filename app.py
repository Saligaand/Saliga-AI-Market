import os
import json
import time
import sqlite3
import logging
from datetime import datetime, timezone

import ccxt
import numpy as np
import pandas as pd
import requests
import streamlit as st
import plotly.graph_objects as go
import feedparser

from streamlit_autorefresh import st_autorefresh


# ============================================================
# SALIGA AI 7.0
# DUAL INTELLIGENCE ENGINE
# OKX ONLY | NO EMA | LIGHT UI
# ============================================================

APP_VERSION = "7.0"

GROQ_MODEL = "openai/gpt-oss-120b"
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"

DB_FILE = "saliga_ai.db"

SYMBOLS = {
    "BTC": "BTC/USDT:USDT",
    "ETH": "ETH/USDT:USDT",
    "SOL": "SOL/USDT:USDT",
}

TIMEFRAMES = ["1m", "5m", "15m", "1h", "4h", "1d"]

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("SALIGA_AI")


# ============================================================
# PAGE
# ============================================================

st.set_page_config(
    page_title="SALIGA AI 7.0",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="expanded"
)


# ============================================================
# LIGHT DESIGN
# ============================================================

st.markdown("""
<style>

.stApp {
    background-color: #f4f6fa !important;
    color: #172033 !important;
}

header[data-testid="stHeader"] {
    background-color: #f4f6fa !important;
}

section[data-testid="stSidebar"] {
    background-color: #e9edf4 !important;
}

section[data-testid="stSidebar"] * {
    color: #172033 !important;
}

.stApp p,
.stApp span,
.stApp label,
.stApp li,
.stApp h1,
.stApp h2,
.stApp h3,
.stApp h4 {
    color: #172033;
}

.main-title {
    color: #087f5b !important;
    font-size: 36px;
    font-weight: 900;
    letter-spacing: 1px;
}

.subtitle {
    color: #65728a !important;
    font-size: 13px;
}

.card {
    background: #ffffff;
    border: 1px solid #dce2ec;
    border-radius: 13px;
    padding: 17px;
    margin-bottom: 12px;
    box-shadow: 0 2px 8px rgba(20,30,50,0.04);
}

.card-title {
    color: #526078 !important;
    font-size: 13px;
    font-weight: 600;
}

.long {
    color: #00875a !important;
    font-weight: 900;
}

.short {
    color: #d92d45 !important;
    font-weight: 900;
}

.neutral {
    color: #b77900 !important;
    font-weight: 900;
}

.stMetric {
    background: #ffffff;
    border: 1px solid #dce2ec;
    border-radius: 10px;
    padding: 12px;
}

.stMetric label,
.stMetric [data-testid="stMetricValue"],
.stMetric [data-testid="stMetricDelta"] {
    color: #172033 !important;
}

.stTextInput input,
.stChatInput textarea {
    color: #172033 !important;
    background: #ffffff !important;
}

.stSelectbox div,
.stMultiSelect div {
    color: #172033 !important;
}

div[data-testid="stDataFrame"] {
    background: #ffffff;
}

hr {
    border-color: #dce2ec !important;
}

</style>
""", unsafe_allow_html=True)


# ============================================================
# DATABASE
# ============================================================

def db():
    conn = sqlite3.connect(
        DB_FILE,
        check_same_thread=False
    )

    conn.execute("""
        CREATE TABLE IF NOT EXISTS oi_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT,
            timestamp INTEGER,
            oi REAL
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS signals (
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


DB = db()


def save_oi(symbol, value):
    if value <= 0:
        return

    DB.execute(
        "INSERT INTO oi_history(symbol,timestamp,oi) VALUES(?,?,?)",
        (symbol, int(time.time()), float(value))
    )
    DB.commit()


def previous_oi(symbol):
    row = DB.execute("""
        SELECT timestamp, oi
        FROM oi_history
        WHERE symbol = ?
        AND timestamp <= ?
        ORDER BY timestamp DESC
        LIMIT 1
    """, (symbol, int(time.time()) - 1800)).fetchone()

    if row:
        return float(row[1])

    return None


def save_signal(symbol, price, score, direction):
    DB.execute("""
        INSERT INTO signals(symbol,timestamp,price,score,direction)
        VALUES(?,?,?,?,?)
    """, (
        symbol,
        int(time.time()),
        float(price),
        float(score),
        direction
    ))
    DB.commit()


# ============================================================
# HELPERS
# ============================================================

def num(value, default=0.0):
    try:
        result = float(value)
        if np.isfinite(result):
            return result
    except (TypeError, ValueError):
        pass
    return default


def pct_change(current, previous):
    if not previous:
        return 0.0
    return (current - previous) / previous * 100


# ============================================================
# OKX
# ============================================================

@st.cache_resource
def exchange_client():
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
def candles(symbol, timeframe):
    ex = exchange_client()

    data = ex.fetch_ohlcv(
        symbol,
        timeframe=timeframe,
        limit=300
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

    df["timestamp"] = pd.to_datetime(
        df["timestamp"],
        unit="ms",
        utc=True
    )

    return df


@st.cache_data(ttl=10, show_spinner=False)
def ticker(symbol):
    return exchange_client().fetch_ticker(symbol)


@st.cache_data(ttl=15, show_spinner=False)
def orderbook(symbol):
    return exchange_client().fetch_order_book(
        symbol,
        limit=50
    )


@st.cache_data(ttl=20, show_spinner=False)
def funding(symbol):
    try:
        return exchange_client().fetch_funding_rate(symbol)
    except Exception:
        return {}


@st.cache_data(ttl=20, show_spinner=False)
def open_interest(symbol):
    try:
        return exchange_client().fetch_open_interest(symbol)
    except Exception:
        return {}


@st.cache_data(ttl=15, show_spinner=False)
def trades(symbol):
    try:
        return exchange_client().fetch_trades(
            symbol,
            limit=500
        )
    except Exception:
        return []


# ============================================================
# INDICATORS
# ============================================================

def rsi(series, period=14):
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

    rs = avg_gain / avg_loss.replace(0, np.nan)

    return (100 - 100 / (1 + rs)).fillna(50)


def atr(df, period=14):
    previous = df["close"].shift(1)

    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - previous).abs(),
        (df["low"] - previous).abs()
    ], axis=1).max(axis=1)

    return tr.ewm(
        alpha=1 / period,
        adjust=False,
        min_periods=period
    ).mean()


def adx(df, period=14):
    up = df["high"].diff()
    down = -df["low"].diff()

    plus = pd.Series(
        np.where((up > down) & (up > 0), up, 0),
        index=df.index
    )

    minus = pd.Series(
        np.where((down > up) & (down > 0), down, 0),
        index=df.index
    )

    average_atr = atr(df, period).replace(0, np.nan)

    plus_di = (
        100 * plus.ewm(alpha=1/period, adjust=False).mean()
        / average_atr
    )

    minus_di = (
        100 * minus.ewm(alpha=1/period, adjust=False).mean()
        / average_atr
    )

    dx = (
        (plus_di - minus_di).abs()
        / (plus_di + minus_di).replace(0, np.nan)
    ) * 100

    return dx.ewm(
        alpha=1/period,
        adjust=False
    ).mean().fillna(0)


def indicators(df):
    df = df.copy()

    df["rsi"] = rsi(df["close"])
    df["atr"] = atr(df)
    df["adx"] = adx(df)

    # SMA only. EMA is intentionally not used.
    df["sma20"] = df["close"].rolling(20).mean()
    df["sma50"] = df["close"].rolling(50).mean()

    df["volume_avg"] = df["volume"].rolling(20).mean()

    df["volume_ratio"] = (
        df["volume"]
        / df["volume_avg"].replace(0, np.nan)
    )

    # Estimated candle delta, not exchange-level true delta.
    df["delta"] = np.where(
        df["close"] >= df["open"],
        df["volume"],
        -df["volume"]
    )

    df["cvd"] = df["delta"].cumsum()

    return df


# ============================================================
# MARKET STRUCTURE
# ============================================================

def structure(df, window=3):
    highs = df["high"].to_numpy()
    lows = df["low"].to_numpy()

    pivot_highs = []
    pivot_lows = []

    for i in range(window, len(df)-window):
        if highs[i] == max(highs[i-window:i+window+1]):
            pivot_highs.append(highs[i])

        if lows[i] == min(lows[i-window:i+window+1]):
            pivot_lows.append(lows[i])

    if len(pivot_highs) < 2 or len(pivot_lows) < 2:
        return "Bizonytalan struktúra"

    higher_high = pivot_highs[-1] > pivot_highs[-2]
    higher_low = pivot_lows[-1] > pivot_lows[-2]

    lower_high = pivot_highs[-1] < pivot_highs[-2]
    lower_low = pivot_lows[-1] < pivot_lows[-2]

    if higher_high and higher_low:
        return "Bullish HH / HL"

    if lower_high and lower_low:
        return "Bearish LH / LL"

    return "Oldalazó / vegyes"


def liquidity_sweep(df):
    recent = df.iloc[-8:-1]
    last = df.iloc[-1]

    if last["high"] > recent["high"].max():
        if last["close"] < recent["high"].max():
            return "Felső likviditás kisöprés"

    if last["low"] < recent["low"].min():
        if last["close"] > recent["low"].min():
            return "Alsó likviditás kisöprés"

    return "Nincs egyértelmű sweep"


# ============================================================
# ORDER BOOK
# ============================================================

def analyze_book(book):
    bids = book.get("bids", [])
    asks = book.get("asks", [])

    bid_vol = sum(num(x[1]) for x in bids[:20])
    ask_vol = sum(num(x[1]) for x in asks[:20])

    total = bid_vol + ask_vol

    if total <= 0:
        return {
            "imbalance": 0,
            "bias": "Nincs adat",
            "bid_volume": 0,
            "ask_volume": 0
        }

    imbalance = (bid_vol - ask_vol) / total * 100

    if imbalance > 15:
        bias = "Vevői fölény"
    elif imbalance < -15:
        bias = "Eladói fölény"
    else:
        bias = "Kiegyensúlyozott"

    return {
        "imbalance": imbalance,
        "bias": bias,
        "bid_volume": bid_vol,
        "ask_volume": ask_vol
    }


# ============================================================
# TRADE FLOW
# ============================================================

def analyze_trades(data):
    buy = 0.0
    sell = 0.0

    for trade in data:
        amount = num(trade.get("amount"))

        if trade.get("side") == "buy":
            buy += amount
        elif trade.get("side") == "sell":
            sell += amount

    total = buy + sell

    if total <= 0:
        return {
            "buy": 0,
            "sell": 0,
            "delta": 0,
            "buy_ratio": 50,
            "bias": "Nincs adat"
        }

    ratio = buy / total * 100

    if ratio > 60:
        bias = "Erős vételi aktivitás"
    elif ratio < 40:
        bias = "Erős eladási aktivitás"
    else:
        bias = "Vegyes aktivitás"

    return {
        "buy": buy,
        "sell": sell,
        "delta": buy - sell,
        "buy_ratio": ratio,
        "bias": bias
    }


# ============================================================
# OPEN INTEREST
# ============================================================

def extract_oi(data):
    if not isinstance(data, dict):
        return 0

    info = data.get("info") or {}

    candidates = [
        data.get("openInterestAmount"),
        data.get("openInterestValue"),
        data.get("openInterest"),
        info.get("oi"),
        info.get("oiCcy"),
        info.get("oiUsd")
    ]

    for value in candidates:
        result = num(value)
        if result > 0:
            return result

    return 0


def oi_analysis(symbol, current_oi, price_change):
    if current_oi <= 0:
        return {
            "change": None,
            "message": "Az OKX nem adott vissza használható OI-adatot."
        }

    old = previous_oi(symbol)
    save_oi(symbol, current_oi)

    if old is None or old <= 0:
        return {
            "change": None,
            "message": "OI-előzmények gyűjtése folyamatban."
        }

    change = pct_change(current_oi, old)

    if change > 0.5 and price_change > 0:
        message = "LONG ÉPÜLÉS – ár és OI emelkedik"

    elif change > 0.5 and price_change < 0:
        message = "SHORT ÉPÜLÉS – ár esik, OI emelkedik"

    elif change < -0.5 and price_change > 0:
        message = "SHORT ZÁRÁSOK – ár emelkedik, OI csökken"

    elif change < -0.5 and price_change < 0:
        message = "LONG ZÁRÁSOK – ár esik, OI csökken"

    else:
        message = "Nincs jelentős pozícióváltozás"

    return {
        "change": change,
        "message": message
    }


# ============================================================
# FUNDING
# ============================================================

def funding_analysis(data):
    rate = num(data.get("fundingRate"))

    if rate > 0.0005:
        message = "Magas pozitív funding – long oldal zsúfolt lehet"
    elif rate < -0.0005:
        message = "Negatív funding – short oldal zsúfolt lehet"
    else:
        message = "Semleges funding"

    return {
        "rate": rate,
        "percent": rate * 100,
        "message": message
    }


# ============================================================
# VOLUME PROFILE APPROXIMATION
# ============================================================

def volume_profile(df):
    recent = df.tail(150)

    typical = (
        recent["high"] +
        recent["low"] +
        recent["close"]
    ) / 3

    hist, edges = np.histogram(
        typical,
        bins=30,
        weights=recent["volume"]
    )

    if hist.sum() <= 0:
        return None

    poc_index = int(np.argmax(hist))
    poc = (edges[poc_index] + edges[poc_index+1]) / 2

    target = hist.sum() * 0.70
    order = np.argsort(hist)[::-1]

    total = 0
    selected = []

    for index in order:
        selected.append(index)
        total += hist[index]

        if total >= target:
            break

    return {
        "POC": float(poc),
        "VAH": float(max(edges[i+1] for i in selected)),
        "VAL": float(min(edges[i] for i in selected))
    }


# ============================================================
# TIMEFRAME ANALYSIS
# ============================================================

def timeframe_analysis(symbol, timeframe):
    df = indicators(candles(symbol, timeframe))

    last = df.iloc[-1]
    prev = df.iloc[-2]

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

    if last["close"] > prev["close"]:
        score += 0.5
    else:
        score -= 0.5

    return {
        "df": df,
        "price": num(last["close"]),
        "score": score,
        "rsi": num(last["rsi"]),
        "adx": num(last["adx"]),
        "atr": num(last["atr"]),
        "volume_ratio": num(last["volume_ratio"]),
        "structure": structure(df),
        "reasons": reasons
    }


# ============================================================
# FULL ASSET ANALYSIS
# ============================================================

def analyze_asset(name, symbol):

    tf_results = {}
    errors = []

    for tf in TIMEFRAMES:
        try:
            tf_results[tf] = timeframe_analysis(symbol, tf)
        except Exception as e:
            errors.append(f"{tf}: {e}")

    if not tf_results:
        raise RuntimeError("; ".join(errors))

    main = (
        tf_results.get("15m")
        or tf_results.get("5m")
        or next(iter(tf_results.values()))
    )

    price = main["price"]
    df = main["df"]

    book = analyze_book(orderbook(symbol))
    flow = analyze_trades(trades(symbol))
    fund = funding_analysis(funding(symbol))

    oi_raw = extract_oi(open_interest(symbol))

    price_change = pct_change(
        num(df["close"].iloc[-1]),
        num(df["close"].iloc[-2])
    )

    oi = oi_analysis(symbol, oi_raw, price_change)

    weights = {
        "1m": 0.05,
        "5m": 0.10,
        "15m": 0.20,
        "1h": 0.25,
        "4h": 0.25,
        "1d": 0.15
    }

    score = 0
    weight_total = 0

    for tf, result in tf_results.items():
        weight = weights[tf]
        score += result["score"] * weight
        weight_total += weight

    score /= weight_total

    # Flow inputs are deliberately limited.
    score += book["imbalance"] / 100 * 0.8
    score += (flow["buy_ratio"] - 50) / 50 * 0.5

    if score >= 1.1:
        direction = "LONG BIAS"
    elif score <= -1.1:
        direction = "SHORT BIAS"
    else:
        direction = "NEUTRAL"

    atr_value = main["atr"]

    if direction == "LONG BIAS":
        stop = price - atr_value * 1.5
        target1 = price + atr_value * 1.5
        target2 = price + atr_value * 3

    elif direction == "SHORT BIAS":
        stop = price + atr_value * 1.5
        target1 = price - atr_value * 1.5
        target2 = price - atr_value * 3

    else:
        stop = target1 = target2 = None

    vp = volume_profile(df)

    result = {
        "name": name,
        "symbol": symbol,
        "price": price,
        "price_change": price_change,
        "score": score,
        "direction": direction,
        "confidence_estimate": min(95, 50 + abs(score) * 15),
        "timeframes": tf_results,
        "orderbook": book,
        "flow": flow,
        "funding": fund,
        "oi": oi,
        "oi_raw": oi_raw,
        "structure": main["structure"],
        "sweep": liquidity_sweep(df),
        "volume_profile": vp,
        "stop": stop,
        "target1": target1,
        "target2": target2,
        "errors": errors,
        "chart": df.tail(150)
    }

    save_signal(name, price, score, direction)

    return result


# ============================================================
# MACRO DATA
# ============================================================

@st.cache_data(ttl=300, show_spinner=False)
def macro_data():
    tickers = {
        "DXY": "DX-Y.NYB",
        "VIX": "^VIX",
        "NASDAQ": "^IXIC",
        "SP500": "^GSPC",
        "US10Y": "^TNX"
    }

    output = {}

    for name, symbol in tickers.items():
        try:
            url = (
                "https://query1.finance.yahoo.com/v8/finance/chart/"
                + symbol
                + "?range=5d&interval=1d"
            )

            response = requests.get(
                url,
                timeout=10,
                headers={"User-Agent": "Mozilla/5.0"}
            )

            response.raise_for_status()
            data = response.json()["chart"]["result"][0]

            values = [
                x for x in data["indicators"]["quote"][0]["close"]
                if x is not None
            ]

            if len(values) >= 2:
                output[name] = {
                    "price": values[-1],
                    "change": pct_change(values[-1], values[-2])
                }

        except Exception:
            output[name] = None

    return output


def macro_score(data):
    score = 0
    details = []

    for name in ["DXY", "VIX"]:
        item = data.get(name)

        if item:
            if item["change"] < 0:
                score += 1
                details.append(name + " csökken")
            elif item["change"] > 0:
                score -= 1
                details.append(name + " emelkedik")

    for name in ["NASDAQ", "SP500"]:
        item = data.get(name)

        if item:
            if item["change"] > 0:
                score += 1
                details.append(name + " emelkedik")
            elif item["change"] < 0:
                score -= 1
                details.append(name + " csökken")

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
def news_data():
    feeds = [
        "https://cointelegraph.com/rss",
        "https://www.coindesk.com/arc/outboundfeeds/rss/"
    ]

    items = []

    for url in feeds:
        try:
            feed = feedparser.parse(url)

            for entry in feed.entries[:8]:
                items.append({
                    "title": entry.get("title", ""),
                    "link": entry.get("link", ""),
                    "date": entry.get("published", "")
                })
        except Exception:
            pass

    return items[:15]


# ============================================================
# GROQ AI
# ============================================================

def get_groq_key():
    try:
        key = st.secrets.get("GROQ_API_KEY", "")
        if key:
            return str(key).strip()
    except Exception:
        pass

    return os.getenv("GROQ_API_KEY", "").strip()


def groq_analysis(context):
    key = get_groq_key()

    if not key:
        raise RuntimeError(
            "Hiányzik a GROQ_API_KEY a Streamlit Secretsből."
        )

    system = """
Te vagy a SALIGA AI külső piaci elemzője.

Feladatod a beépített technikai elemzőmotor eredményeinek
mélyebb, kritikus értelmezése.

Magyarul válaszolj.

Vizsgáld:
- BTC, ETH, SOL irányát.
- Multi-timeframe összhangot és konfliktusokat.
- Long- és shortépülés lehetséges jeleit.
- Long- és shortzárások lehetséges jeleit.
- Open Interest változását.
- Order book és trade flow adatait.
- Fundingot.
- Piaci struktúrát és likviditási szinteket.
- Makrokörnyezetet.

Fontos:
- Ne találj ki hiányzó adatokat.
- Ne állíts biztos pozícióépülést, ha csak következtetés.
- Jelezd, ha a belső motor és az adatok ellentmondanak.
- Ne ígérj profitot.
- Különítsd el a tényt és a becslést.
- Ne használj EMA-t.
- Ne adj automatikus kereskedési utasítást.

A választ ebben a sorrendben készítsd:
1. Általános piaci helyzet.
2. BTC elemzés.
3. ETH elemzés.
4. SOL elemzés.
5. Pozícióáramlás.
6. Makrogazdasági háttér.
7. Ellentmondó jelek és kockázatok.
8. Összesített következtetés.
"""

    response = requests.post(
        GROQ_URL,
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json"
        },
        json={
            "model": GROQ_MODEL,
            "messages": [
                {
                    "role": "system",
                    "content": system
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        context,
                        ensure_ascii=False,
                        default=str
                    )
                }
            ],
            "temperature": 0.3,
            "max_completion_tokens": 2500
        },
        timeout=90
    )

    if response.status_code != 200:
        raise RuntimeError(
            f"Groq API {response.status_code}: "
            + response.text[:1000]
        )

    data = response.json()

    return data["choices"][0]["message"]["content"]


# ============================================================
# AI CONTEXT
# ============================================================

def make_context(results, macro, macro_view):
    context = {
        "time": datetime.now(timezone.utc).isoformat(),
        "assets": {},
        "macro": macro,
        "macro_interpretation": macro_view
    }

    for name, result in results.items():
        context["assets"][name] = {
            "price": result["price"],
            "price_change": result["price_change"],
            "score": result["score"],
            "direction": result["direction"],
            "structure": result["structure"],
            "sweep": result["sweep"],
            "orderbook": result["orderbook"],
            "trade_flow": result["flow"],
            "funding": result["funding"],
            "open_interest": result["oi"],
            "timeframes": {
                tf: {
                    "score": data["score"],
                    "rsi": data["rsi"],
                    "adx": data["adx"],
                    "structure": data["structure"],
                    "volume_ratio": data["volume_ratio"]
                }
                for tf, data in result["timeframes"].items()
            }
        }

    return context


# ============================================================
# SESSION STATE
# ============================================================

defaults = {
    "results": {},
    "macro": {},
    "errors": {},
    "last_internal": 0,
    "last_ai": 0,
    "ai_text": "",
    "chat": [],
    "last_refresh_count": -1,
    "selected_signature": ""
}

for key, value in defaults.items():
    if key not in st.session_state:
        st.session_state[key] = value


# ============================================================
# SIDEBAR
# ============================================================

st.sidebar.markdown("## ⚡ SALIGA AI 7.0")
st.sidebar.caption("Dual Market Intelligence")

st.sidebar.markdown("---")

selected = st.sidebar.multiselect(
    "Instrumentumok",
    list(SYMBOLS.keys()),
    default=["BTC", "ETH", "SOL"]
)

refresh_seconds = st.sidebar.select_slider(
    "Belső motor frissítése",
    options=[15, 30, 60, 120],
    value=30,
    format_func=lambda x: f"{x} másodperc"
)

auto_refresh = st.sidebar.toggle(
    "Automatikus frissítés",
    value=True
)

manual = st.sidebar.button(
    "🔄 Azonnali frissítés",
    use_container_width=True
)

force_ai = st.sidebar.button(
    "🤖 Külső AI újraelemzése",
    use_container_width=True
)

if st.sidebar.button("Chat törlése"):
    st.session_state.chat = []
    st.rerun()


# ============================================================
# HEADER
# ============================================================

st.markdown(
    '<div class="main-title">⚡ SALIGA AI</div>',
    unsafe_allow_html=True
)

st.markdown(
    '<div class="subtitle">'
    'DUAL INTELLIGENCE | POSITION FLOW | MARKET STRUCTURE | OKX'
    '</div>',
    unsafe_allow_html=True
)

st.markdown("---")


# ============================================================
# AUTO REFRESH
# ============================================================

if auto_refresh:
    refresh_count = st_autorefresh(
        interval=refresh_seconds * 1000,
        key="saliga_refresh"
    )
else:
    refresh_count = 0

signature = ",".join(sorted(selected))

selection_changed = (
    signature != st.session_state.selected_signature
)

internal_due = (
    time.time() - st.session_state.last_internal
    >= refresh_seconds
)

should_update = (
    manual
    or selection_changed
    or (
        auto_refresh
        and refresh_count != st.session_state.last_refresh_count
    )
    or (not auto_refresh and internal_due)
)


# ============================================================
# INTERNAL ANALYSIS
# ============================================================

if should_update and selected:

    new_results = {}
    errors = {}

    with st.spinner("SALIGA saját elemzőmotorja dolgozik..."):

        for name in selected:
            try:
                new_results[name] = analyze_asset(
                    name,
                    SYMBOLS[name]
                )
            except Exception as e:
                errors[name] = str(e)
                logger.exception("Analysis failed: %s", name)

        try:
            macro = macro_data()
        except Exception as e:
            macro = {}
            errors["MACRO"] = str(e)

    st.session_state.results = new_results
    st.session_state.macro = macro
    st.session_state.errors = errors
    st.session_state.last_internal = time.time()
    st.session_state.last_refresh_count = refresh_count
    st.session_state.selected_signature = signature

    # Clear cached market requests for the next refresh.
    st.cache_data.clear()


results = st.session_state.results
macro = st.session_state.macro
errors = st.session_state.errors


# ============================================================
# INTERNAL STATUS
# ============================================================

if auto_refresh:
    st.success(
        f"Automatikus frissítés aktív – {refresh_seconds} másodperc"
    )

if st.session_state.last_internal:
    st.caption(
        "Utolsó belső elemzés: "
        + datetime.fromtimestamp(
            st.session_state.last_internal
        ).strftime("%Y-%m-%d %H:%M:%S")
    )

for name, error in errors.items():
    st.error(f"{name}: {error}")


# ============================================================
# INTERNAL ENGINE OUTPUT
# ============================================================

if results:

    st.subheader("📊 Piaci áttekintés")

    cols = st.columns(len(results))

    for col, (name, result) in zip(cols, results.items()):

        direction = result["direction"]

        css = (
            "long" if direction == "LONG BIAS"
            else "short" if direction == "SHORT BIAS"
            else "neutral"
        )

        with col:
            st.markdown(
                f"""
                <div class="card">
                    <div class="card-title">{name} / OKX SWAP</div>
                    <h2>${result['price']:,.4f}</h2>
                    <h3 class="{css}">{direction}</h3>
                    <p>Pontszám: {result['score']:.2f}</p>
                    <p>Jelzéserősség-becslés:
                    {result['confidence_estimate']:.1f}%</p>
                    <p>Struktúra: {result['structure']}</p>
                </div>
                """,
                unsafe_allow_html=True
            )


    # ========================================================
    # POSITION FLOW PANEL
    # ========================================================

    st.markdown("---")
    st.subheader("⚡ POSITION FLOW – automatikus felismerés")

    for name, result in results.items():

        st.markdown(f"### {name}")

        oi_message = result["oi"]["message"]

        if "LONG ÉPÜLÉS" in oi_message:
            st.success("🟢 " + oi_message)

        elif "SHORT ÉPÜLÉS" in oi_message:
            st.error("🔴 " + oi_message)

        elif "SHORT ZÁRÁSOK" in oi_message:
            st.success("🟢 " + oi_message)

        elif "LONG ZÁRÁSOK" in oi_message:
            st.error("🔴 " + oi_message)

        else:
            st.info(oi_message)

        a, b, c, d = st.columns(4)

        a.metric(
            "Árváltozás",
            f"{result['price_change']:+.3f}%"
        )

        b.metric(
            "OI változás",
            (
                f"{result['oi']['change']:+.3f}%"
                if result["oi"]["change"] is not None
                else "Gyűjtés alatt"
            )
        )

        c.metric(
            "Vételi arány",
            f"{result['flow']['buy_ratio']:.1f}%"
        )

        d.metric(
            "Order book",
            f"{result['orderbook']['imbalance']:+.1f}%"
        )

        st.caption(
            f"Trade flow: {result['flow']['bias']} | "
            f"Order book: {result['orderbook']['bias']} | "
            f"Funding: {result['funding']['message']}"
        )

        st.caption(
            "A long/short épülés és zárás az ár és OI együttmozgásából "
            "becsült értelmezés, nem közvetlenül azonosított pozíció."
        )


    # ========================================================
    # TIMEFRAME TABLES AND CHARTS
    # ========================================================

    st.markdown("---")
    st.subheader("🔬 Részletes elemzés")

    tabs = st.tabs(list(results.keys()))

    for tab, (name, result) in zip(tabs, results.items()):

        with tab:

            rows = []

            for tf, data in result["timeframes"].items():
                rows.append({
                    "Idősík": tf,
                    "Pontszám": round(data["score"], 2),
                    "RSI": round(data["rsi"], 1),
                    "ADX": round(data["adx"], 1),
                    "ATR": round(data["atr"], 5),
                    "Struktúra": data["structure"],
                    "Volumenarány": round(data["volume_ratio"], 2)
                })

            st.dataframe(
                pd.DataFrame(rows),
                use_container_width=True,
                hide_index=True
            )

            chart_df = result["chart"]

            fig = go.Figure()

            fig.add_trace(go.Candlestick(
                x=chart_df["timestamp"],
                open=chart_df["open"],
                high=chart_df["high"],
                low=chart_df["low"],
                close=chart_df["close"],
                name="OKX Price"
            ))

            fig.add_trace(go.Scatter(
                x=chart_df["timestamp"],
                y=chart_df["sma20"],
                name="SMA20",
                line=dict(width=1)
            ))

            fig.add_trace(go.Scatter(
                x=chart_df["timestamp"],
                y=chart_df["sma50"],
                name="SMA50",
                line=dict(width=1)
            ))

            fig.update_layout(
                template="plotly_white",
                height=450,
                xaxis_rangeslider_visible=False
            )

            st.plotly_chart(
                fig,
                use_container_width=True
            )

            st.markdown("#### Likviditás")

            st.write(result["sweep"])

            vp = result["volume_profile"]

            if vp:
                x, y, z = st.columns(3)
                x.metric("POC", f"{vp['POC']:,.4f}")
                y.metric("VAH", f"{vp['VAH']:,.4f}")
                z.metric("VAL", f"{vp['VAL']:,.4f}")

            st.markdown("#### ATR-alapú referencia")

            if result["stop"] is not None:
                x, y, z = st.columns(3)

                x.metric("Stop referencia", f"{result['stop']:,.4f}")
                y.metric("TP1 referencia", f"{result['target1']:,.4f}")
                z.metric("TP2 referencia", f"{result['target2']:,.4f}")


    # ========================================================
    # MACRO
    # ========================================================

    st.markdown("---")
    st.subheader("🌍 Makrogazdasági háttér")

    macro_view = macro_score(macro)

    st.info(macro_view["bias"])

    cols = st.columns(5)

    for col, name in zip(
        cols,
        ["DXY", "VIX", "NASDAQ", "SP500", "US10Y"]
    ):
        item = macro.get(name)

        if item:
            col.metric(
                name,
                f"{item['price']:.2f}",
                f"{item['change']:+.2f}%"
            )
        else:
            col.metric(name, "N/A")


    # ========================================================
    # EXTERNAL AI AUTO ANALYSIS
    # ========================================================

    st.markdown("---")
    st.subheader("🤖 SALIGA AI – külső intelligencia")

    if get_groq_key():

        ai_due = (
            time.time() - st.session_state.last_ai >= 300
        )

        if force_ai or ai_due:

            with st.spinner(
                "A külső AI összeveti a piaci adatokat..."
            ):

                try:
                    context = make_context(
                        results,
                        macro,
                        macro_view
                    )

                    answer = groq_analysis(context)

                    st.session_state.ai_text = answer
                    st.session_state.last_ai = time.time()

                except Exception as e:
                    st.error(f"Külső AI hiba: {e}")

        if st.session_state.ai_text:
            st.markdown(
                f"""
                <div class="card">
                    {st.session_state.ai_text.replace(chr(10), '<br>')}
                </div>
                """,
                unsafe_allow_html=True
            )

        if st.session_state.last_ai:
            st.caption(
                "Utolsó külső AI-elemzés: "
                + datetime.fromtimestamp(
                    st.session_state.last_ai
                ).strftime("%H:%M:%S")
            )

    else:
        st.warning(
            "A belső elemzőmotor működik, de a külső AI-hoz "
            "nem található GROQ_API_KEY."
        )


    # ========================================================
    # NEWS
    # ========================================================

    st.markdown("---")
    st.subheader("📰 Kriptopiaci hírek")

    for item in news_data():
        if item["title"] and item["link"]:
            st.markdown(
                f"- [{item['title']}]({item['link']})"
            )


else:
    st.warning(
        "Még nincs elérhető piaci adat. "
        "Ellenőrizd az OKX-kapcsolatot, majd frissíts."
    )


# ============================================================
# CHAT
# ============================================================

st.markdown("---")
st.subheader("💬 SALIGA AI – kérdezz az elemzésről")

for message in st.session_state.chat:
    with st.chat_message(message["role"]):
        st.markdown(message["content"])

question = st.chat_input(
    "Kérdezz a BTC, ETH, SOL vagy a piaci folyamatok kapcsán..."
)

if question:

    st.session_state.chat.append({
        "role": "user",
        "content": question
    })

    with st.chat_message("user"):
        st.markdown(question)

    if get_groq_key():

        context = make_context(
            results,
            macro,
            macro_score(macro)
        ) if results else "Nincs betöltött élő piaci adat."

        messages = [
            {
                "role": "system",
                "content": (
                    "Te vagy a SALIGA AI. Magyarul válaszolj, "
                    "szakmailag és pontosan. Ne találj ki adatokat. "
                    "Különítsd el a tényeket és becsléseket. "
                    "Ne használj EMA-t. "
                    "Az aktuális piaci adatok:\n" + context
                )
            }
        ]

        messages.extend(st.session_state.chat[-10:])

        with st.chat_message("assistant"):

            try:
                key = get_groq_key()

                response = requests.post(
                    GROQ_URL,
                    headers={
                        "Authorization": f"Bearer {key}",
                        "Content-Type": "application/json"
                    },
                    json={
                        "model": GROQ_MODEL,
                        "messages": messages,
                        "temperature": 0.3,
                        "max_completion_tokens": 1500
                    },
                    timeout=90
                )

                if response.status_code != 200:
                    raise RuntimeError(
                        f"Groq API {response.status_code}: "
                        + response.text[:1000]
                    )

                answer = response.json()["choices"][0]["message"]["content"]

                st.markdown(answer)

                st.session_state.chat.append({
                    "role": "assistant",
                    "content": answer
                })

            except Exception as e:
                st.error(str(e))

    else:
        st.error(
            "A chatbothoz nem található GROQ_API_KEY."
        )


# ============================================================
# FOOTER
# ============================================================

st.markdown("---")

st.caption(
    f"SALIGA AI {APP_VERSION} | OKX ONLY | "
    "Internal Engine + External AI"
)

st.caption(
    "Elemzési rendszer. A jelzések és pozícióáramlási "
    "értelmezések becslések, nem garantált kereskedési eredmények."
)
