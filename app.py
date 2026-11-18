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
import streamlit as st
import plotly.graph_objects as go
import feedparser

try:
    from openai import OpenAI
except Exception:
    OpenAI = None

try:
    from streamlit_autorefresh import st_autorefresh
except Exception:
    st_autorefresh = None


# ============================================================
# SALIGA AI PRO 9.0
# OKX ONLY | NO EMA | NO SMA
# ============================================================

APP_NAME = "SALIGA AI PRO 9.0"

SYMBOLS = {
    "BTC": "BTC/USDT:USDT",
    "ETH": "ETH/USDT:USDT",
    "SOL": "SOL/USDT:USDT",
}

TIMEFRAMES = {
    "5 perc": "5m",
    "15 perc": "15m",
    "1 óra": "1h",
    "4 óra": "4h",
    "1 nap": "1d",
}

DB_PATH = "saliga_market.db"

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("SALIGA")


# ============================================================
# STREAMLIT CONFIG
# ============================================================

st.set_page_config(
    page_title=APP_NAME,
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)


st.markdown("""
<style>
.stApp {
    background-color: #f5f7fa;
    color: #172033;
}
[data-testid="stSidebar"] {
    background-color: #ffffff;
}
.metric-card {
    background: white;
    padding: 17px;
    border-radius: 12px;
    border: 1px solid #e1e6ef;
    margin-bottom: 10px;
}
.long {
    color: #079455;
    font-weight: 800;
}
.short {
    color: #d92d20;
    font-weight: 800;
}
.neutral {
    color: #b7791f;
    font-weight: 800;
}
.small-note {
    color: #667085;
    font-size: 12px;
}
h1, h2, h3 {
    color: #172033;
}
</style>
""", unsafe_allow_html=True)


# ============================================================
# DATABASE
# ============================================================

def db_connection():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS market_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT,
            timestamp INTEGER,
            price REAL,
            open_interest REAL,
            delta REAL
        )
    """)

    # Régi adatbázisok oszlopainak javítása
    columns = {
        row[1]
        for row in conn.execute(
            "PRAGMA table_info(market_history)"
        ).fetchall()
    }

    required = {
        "symbol": "TEXT",
        "timestamp": "INTEGER",
        "price": "REAL",
        "open_interest": "REAL",
        "delta": "REAL",
    }

    for name, dtype in required.items():
        if name not in columns:
            conn.execute(
                f"ALTER TABLE market_history ADD COLUMN {name} {dtype}"
            )

    conn.commit()
    return conn


def save_snapshot(symbol, price, oi, delta):
    try:
        conn = db_connection()
        conn.execute("""
            INSERT INTO market_history
            (symbol, timestamp, price, open_interest, delta)
            VALUES (?, ?, ?, ?, ?)
        """, (
            symbol,
            int(time.time()),
            price,
            oi,
            delta,
        ))
        conn.commit()
        conn.close()
    except Exception as e:
        logger.warning("Snapshot error: %s", e)


def previous_snapshot(symbol):
    try:
        conn = db_connection()
        row = conn.execute("""
            SELECT timestamp, price, open_interest, delta
            FROM market_history
            WHERE symbol = ?
            AND open_interest IS NOT NULL
            ORDER BY timestamp DESC
            LIMIT 1
        """, (symbol,)).fetchone()
        conn.close()
        return row
    except Exception:
        return None


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


def safe_call(function, default=None):
    try:
        return function()
    except Exception as e:
        logger.warning("API error: %s", e)
        return default


# ============================================================
# MARKET DATA
# ============================================================

def fetch_ohlcv(symbol, timeframe, limit=200):
    exchange = get_exchange()

    data = exchange.fetch_ohlcv(
        symbol,
        timeframe=timeframe,
        limit=limit,
    )

    if not data:
        return None

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

    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    return df.dropna().reset_index(drop=True)


def fetch_ticker(symbol):
    exchange = get_exchange()
    return exchange.fetch_ticker(symbol)


def fetch_orderbook(symbol):
    exchange = get_exchange()
    return exchange.fetch_order_book(symbol, limit=50)


def fetch_trades(symbol):
    exchange = get_exchange()
    return exchange.fetch_trades(symbol, limit=300)


def fetch_open_interest(symbol):
    exchange = get_exchange()

    try:
        result = exchange.fetch_open_interest(symbol)
        if not result:
            return None

        value = result.get("openInterestAmount")

        if value is None:
            value = result.get("openInterest")

        if value is None:
            value = result.get("info", {}).get("oi")

        if value is None:
            return None

        return float(value)

    except Exception as e:
        logger.warning("OI error for %s: %s", symbol, e)
        return None


def fetch_funding(symbol):
    exchange = get_exchange()

    try:
        result = exchange.fetch_funding_rate(symbol)
        return float(result.get("fundingRate", 0))
    except Exception:
        return None


# ============================================================
# TECHNICAL CALCULATIONS
# EMA / SMA INTENTIONALLY NOT USED
# ============================================================

def calculate_rsi(series, period=14):
    delta = series.diff()

    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)

    avg_gain = gain.rolling(period).mean()
    avg_loss = loss.rolling(period).mean()

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
        (low - previous_close).abs(),
    ], axis=1).max(axis=1)

    return tr.rolling(period).mean()


def calculate_vwap(df):
    typical = (
        df["high"] + df["low"] + df["close"]
    ) / 3

    volume = df["volume"]

    denominator = volume.rolling(48).sum()

    return (
        (typical * volume).rolling(48).sum()
        / denominator.replace(0, np.nan)
    )


def calculate_momentum(df, periods=5):
    if len(df) <= periods:
        return 0

    old_price = float(df["close"].iloc[-periods - 1])
    current_price = float(df["close"].iloc[-1])

    if old_price == 0:
        return 0

    return ((current_price - old_price) / old_price) * 100


# ============================================================
# MARKET STRUCTURE
# ============================================================

def detect_structure(df):
    if len(df) < 30:
        return "ADAT NEM ELÉG", 0

    current = df.iloc[-1]
    previous = df.iloc[-2]

    recent = df.iloc[-26:-1]

    previous_high = float(recent["high"].max())
    previous_low = float(recent["low"].min())

    close = float(current["close"])

    if close > previous_high:
        return "BULLISH BOS", 2

    if close < previous_low:
        return "BEARISH BOS", -2

    if (
        current["high"] > previous_high
        and close < previous_high
    ):
        return "BEARISH REJECTION", -1

    if (
        current["low"] < previous_low
        and close > previous_low
    ):
        return "BULLISH REJECTION", 1

    if close > float(previous["close"]):
        return "ENYHÉN EMELKEDŐ", 1

    if close < float(previous["close"]):
        return "ENYHÉN CSÖKKENŐ", -1

    return "KONSZOLIDÁCIÓ", 0


def detect_liquidity_sweep(df):
    if len(df) < 30:
        return "NINCS ADAT"

    current = df.iloc[-1]
    recent = df.iloc[-26:-1]

    previous_high = float(recent["high"].max())
    previous_low = float(recent["low"].min())

    if (
        float(current["high"]) > previous_high
        and float(current["close"]) < previous_high
    ):
        return "FELSŐ LIKVIDITÁS KISÖPRÉSE"

    if (
        float(current["low"]) < previous_low
        and float(current["close"]) > previous_low
    ):
        return "ALSÓ LIKVIDITÁS KISÖPRÉSE"

    return "NINCS AZONOSÍTOTT SWEEP"


# ============================================================
# DELTA / ORDER FLOW
# ============================================================

def calculate_trade_delta(trades):
    if not trades:
        return {
            "delta": 0,
            "buy_volume": 0,
            "sell_volume": 0,
            "ratio": 0,
        }

    buy_volume = 0.0
    sell_volume = 0.0

    for trade in trades:
        side = trade.get("side")
        amount = float(trade.get("amount") or 0)

        if side == "buy":
            buy_volume += amount

        elif side == "sell":
            sell_volume += amount

    total = buy_volume + sell_volume

    delta = buy_volume - sell_volume

    ratio = (
        delta / total * 100
        if total > 0
        else 0
    )

    return {
        "delta": delta,
        "buy_volume": buy_volume,
        "sell_volume": sell_volume,
        "ratio": ratio,
    }


def calculate_orderbook_imbalance(orderbook):
    if not orderbook:
        return 0

    bids = orderbook.get("bids", [])[:20]
    asks = orderbook.get("asks", [])[:20]

    bid_volume = sum(
        float(x[1]) for x in bids
    )

    ask_volume = sum(
        float(x[1]) for x in asks
    )

    total = bid_volume + ask_volume

    if total <= 0:
        return 0

    return (
        (bid_volume - ask_volume) / total
    ) * 100


# ============================================================
# OPEN INTEREST ANALYSIS
# ============================================================

def analyze_oi(symbol, current_oi, current_price):
    if current_oi is None:
        return {
            "change": None,
            "state": "OI ADAT NEM ELÉRHETŐ",
            "direction": 0,
        }

    previous = previous_snapshot(symbol)

    if not previous:
        return {
            "change": None,
            "state": "OI TÖRTÉNET GYŰJTÉSE",
            "direction": 0,
        }

    old_timestamp, old_price, old_oi, old_delta = previous

    if old_oi is None or old_oi == 0:
        return {
            "change": None,
            "state": "OI ADAT NEM ELÉRHETŐ",
            "direction": 0,
        }

    elapsed = time.time() - old_timestamp

    if elapsed < 300:
        return {
            "change": None,
            "state": "VÁRAKOZÁS AZ ÚJ OI-MINTÁRA",
            "direction": 0,
        }

    oi_change = (
        (current_oi - old_oi) / abs(old_oi)
    ) * 100

    price_change = (
        (current_price - old_price) / abs(old_price)
    ) * 100 if old_price else 0

    if oi_change > 0.15 and price_change > 0.05:
        state = "LONG ÉPÜLÉS BECSLÉSE"
        direction = 1

    elif oi_change > 0.15 and price_change < -0.05:
        state = "SHORT ÉPÜLÉS BECSLÉSE"
        direction = -1

    elif oi_change < -0.15 and price_change > 0.05:
        state = "SHORT ZÁRÁS BECSLÉSE"
        direction = 1

    elif oi_change < -0.15 and price_change < -0.05:
        state = "LONG ZÁRÁS BECSLÉSE"
        direction = -1

    else:
        state = "NINCS EGYÉRTELMŰ POZÍCIÓÉPÜLÉS"
        direction = 0

    return {
        "change": oi_change,
        "state": state,
        "direction": direction,
    }


# ============================================================
# TIMEFRAME INTELLIGENCE
# ============================================================

def analyze_timeframe(df, label):
    if df is None or len(df) < 40:
        return {
            "timeframe": label,
            "direction": "NINCS ADAT",
            "score": 0,
            "strength": "NEM ÉRTÉKELHETŐ",
            "structure": "NINCS ADAT",
            "rsi": None,
            "momentum": None,
            "volume_state": "NINCS ADAT",
            "sweep": "NINCS ADAT",
        }

    close = df["close"]

    rsi_series = calculate_rsi(close)
    atr_series = calculate_atr(df)
    vwap_series = calculate_vwap(df)

    rsi = float(rsi_series.iloc[-1])
    momentum = calculate_momentum(df)

    structure, structure_score = detect_structure(df)

    current = df.iloc[-1]

    current_volume = float(current["volume"])

    avg_volume = float(
        df["volume"].iloc[-21:-1].mean()
    )

    volume_ratio = (
        current_volume / avg_volume
        if avg_volume > 0
        else 1
    )

    vwap = (
        float(vwap_series.iloc[-1])
        if pd.notna(vwap_series.iloc[-1])
        else float(current["close"])
    )

    score = 0

    # Piaci struktúra
    score += structure_score * 3

    # RSI
    if rsi >= 55:
        score += 2
    elif rsi <= 45:
        score -= 2

    # Ármozgás
    if momentum > 0.15:
        score += 2
    elif momentum < -0.15:
        score -= 2

    # VWAP helyzet
    if float(current["close"]) > vwap:
        score += 1
    elif float(current["close"]) < vwap:
        score -= 1

    # Forgalom megerősítés
    if volume_ratio > 1.3:
        if momentum > 0:
            score += 1
        elif momentum < 0:
            score -= 1

    if score >= 5:
        direction = "LONG"
    elif score <= -5:
        direction = "SHORT"
    else:
        direction = "SEMLEGES"

    abs_score = abs(score)

    if abs_score >= 9:
        strength = "ERŐS"
    elif abs_score >= 6:
        strength = "KÖZEPES"
    else:
        strength = "GYENGE"

    if volume_ratio >= 1.5:
        volume_state = "MAGAS FORGALOM"
    elif volume_ratio >= 0.8:
        volume_state = "ÁTLAGOS FORGALOM"
    else:
        volume_state = "ALACSONY FORGALOM"

    sweep = detect_liquidity_sweep(df)

    atr = (
        float(atr_series.iloc[-1])
        if pd.notna(atr_series.iloc[-1])
        else 0
    )

    return {
        "timeframe": label,
        "direction": direction,
        "score": score,
        "strength": strength,
        "structure": structure,
        "rsi": rsi,
        "momentum": momentum,
        "volume_state": volume_state,
        "volume_ratio": volume_ratio,
        "sweep": sweep,
        "atr": atr,
        "vwap": vwap,
        "price": float(current["close"]),
    }


# ============================================================
# MULTI-TIMEFRAME SUMMARY
# ============================================================

def summarize_timeframes(results):
    valid = [
        x for x in results.values()
        if x["direction"] in ("LONG", "SHORT", "SEMLEGES")
    ]

    if not valid:
        return {
            "direction": "NINCS ADAT",
            "score": 0,
            "agreement": "NEM ÉRTÉKELHETŐ",
        }

    weighted_score = 0
    total_weight = 0

    weights = {
        "5 perc": 1,
        "15 perc": 2,
        "1 óra": 3,
        "4 óra": 4,
        "1 nap": 5,
    }

    directions = []

    for name, result in results.items():
        if result["direction"] == "NINCS ADAT":
            continue

        weight = weights.get(name, 1)

        weighted_score += result["score"] * weight
        total_weight += weight

        directions.append(result["direction"])

    final_score = (
        weighted_score / total_weight
        if total_weight
        else 0
    )

    if final_score >= 3:
        direction = "LONG"
    elif final_score <= -3:
        direction = "SHORT"
    else:
        direction = "SEMLEGES"

    long_count = directions.count("LONG")
    short_count = directions.count("SHORT")

    if long_count >= 4:
        agreement = "ERŐS LONG EGYBEHANGZÁS"
    elif short_count >= 4:
        agreement = "ERŐS SHORT EGYBEHANGZÁS"
    elif long_count and short_count:
        agreement = "IDŐSÍKOK KÖZÖTTI ELLENTMONDÁS"
    else:
        agreement = "VEGYES / BIZONYTALAN PIAC"

    return {
        "direction": direction,
        "score": round(final_score, 2),
        "agreement": agreement,
        "long_count": long_count,
        "short_count": short_count,
    }


# ============================================================
# SUPPORT / RESISTANCE
# ============================================================

def calculate_levels(df):
    if df is None or len(df) < 30:
        return None, None

    recent = df.iloc[-30:-1]

    support = float(recent["low"].min())
    resistance = float(recent["high"].max())

    return support, resistance


# ============================================================
# TRADE PLAN
# ============================================================

def calculate_trade_plan(price, atr, direction, support, resistance):
    if not atr or atr <= 0:
        return None

    if direction == "LONG":
        entry = price
        stop = price - (atr * 1.5)

        if support and support < price:
            stop = min(stop, support - atr * 0.15)

        risk = entry - stop

        if risk <= 0:
            return None

        return {
            "direction": "LONG",
            "entry": entry,
            "stop": stop,
            "tp1": entry + risk,
            "tp2": entry + risk * 2,
            "tp3": entry + risk * 3,
            "risk": risk,
        }

    if direction == "SHORT":
        entry = price
        stop = price + (atr * 1.5)

        if resistance and resistance > price:
            stop = max(stop, resistance + atr * 0.15)

        risk = stop - entry

        if risk <= 0:
            return None

        return {
            "direction": "SHORT",
            "entry": entry,
            "stop": stop,
            "tp1": entry - risk,
            "tp2": entry - risk * 2,
            "tp3": entry - risk * 3,
            "risk": risk,
        }

    return None


# ============================================================
# FULL COIN ANALYSIS
# ============================================================

def analyze_coin(name, symbol):
    result = {
        "name": name,
        "symbol": symbol,
        "timeframes": {},
        "error": None,
    }

    try:
        ticker = fetch_ticker(symbol)
        price = float(ticker["last"])

        # Minden idősík önálló elemzése
        for label, timeframe in TIMEFRAMES.items():
            df = fetch_ohlcv(symbol, timeframe, 200)

            result["timeframes"][label] = analyze_timeframe(
                df,
                label,
            )

        # 15 perces chart a kiegészítő piaci adatokhoz
        df15 = fetch_ohlcv(symbol, "15m", 200)

        if df15 is None:
            raise RuntimeError("Nincs 15 perces OHLCV adat.")

        trades = safe_call(
            lambda: fetch_trades(symbol),
            [],
        )

        orderbook = safe_call(
            lambda: fetch_orderbook(symbol),
            None,
        )

        oi = safe_call(
            lambda: fetch_open_interest(symbol),
            None,
        )

        funding = safe_call(
            lambda: fetch_funding(symbol),
            None,
        )

        delta = calculate_trade_delta(trades)

        imbalance = calculate_orderbook_imbalance(
            orderbook
        )

        oi_analysis = analyze_oi(
            name,
            oi,
            price,
        )

        save_snapshot(
            name,
            price,
            oi,
            delta["delta"],
        )

        mtf = summarize_timeframes(
            result["timeframes"]
        )

        support, resistance = calculate_levels(df15)

        atr = float(
            calculate_atr(df15).iloc[-1]
        )

        plan = calculate_trade_plan(
            price,
            atr,
            mtf["direction"],
            support,
            resistance,
        )

        result.update({
            "price": price,
            "delta": delta,
            "orderbook_imbalance": imbalance,
            "oi": oi,
            "oi_analysis": oi_analysis,
            "funding": funding,
            "mtf": mtf,
            "support": support,
            "resistance": resistance,
            "atr": atr,
            "plan": plan,
            "last_update": datetime.now().strftime(
                "%Y-%m-%d %H:%M:%S"
            ),
        })

        return result

    except Exception as e:
        result["error"] = str(e)
        return result


# ============================================================
# NEWS
# ============================================================

def fetch_news():
    feeds = [
        "https://www.coindesk.com/arc/outboundfeeds/rss/",
        "https://cointelegraph.com/rss",
    ]

    news = []

    for url in feeds:
        try:
            feed = feedparser.parse(url)

            for item in feed.entries[:8]:
                news.append({
                    "title": item.get("title", ""),
                    "link": item.get("link", ""),
                    "published": item.get("published", ""),
                })

        except Exception:
            pass

    return news[:15]


# ============================================================
# GROQ AI
# ============================================================

def get_groq_client():
    if OpenAI is None:
        return None

    try:
        api_key = st.secrets.get(
            "GROQ_API_KEY",
            os.getenv("GROQ_API_KEY"),
        )

        if not api_key:
            return None

        return OpenAI(
            api_key=api_key,
            base_url="https://api.groq.com/openai/v1",
        )

    except Exception:
        return None


def ask_groq(question, market_context):
    client = get_groq_client()

    if client is None:
        return "A Groq AI nincs beállítva. A belső elemzőmotor ettől függetlenül működik."

    try:
        response = client.chat.completions.create(
            model="openai/gpt-oss-120b",
            messages=[
                {
                    "role": "system",
                    "content": """
Te a SALIGA AI kriptopiaci elemző asszisztense vagy.

Feladatod:
- értelmezd a kapott piaci adatokat;
- különítsd el az alacsony és magas idősíkokat;
- mutasd meg az ellentmondásokat;
- ne találj ki adatokat;
- ne állíts biztos nyereséget;
- jelezd, ha az adat hiányos;
- ne használj EMA-t vagy SMA-t.
"""
                },
                {
                    "role": "user",
                    "content": (
                        "PIACI ADATOK:\n"
                        + market_context
                        + "\n\nKÉRDÉS:\n"
                        + question
                    ),
                },
            ],
            temperature=0.2,
        )

        return response.choices[0].message.content

    except Exception as e:
        return f"Groq hiba: {e}"


# ============================================================
# SIDEBAR
# ============================================================

st.sidebar.title("⚡ SALIGA AI")

refresh_seconds = st.sidebar.selectbox(
    "Automatikus frissítés",
    [30, 60, 120, 300],
    index=1,
)

selected_coin = st.sidebar.selectbox(
    "Kiemelt instrumentum",
    ["BTC", "ETH", "SOL"],
)

risk_percent = st.sidebar.slider(
    "Kockázat / pozíció (%)",
    0.25,
    3.0,
    1.0,
    0.25,
)

account_balance = st.sidebar.number_input(
    "Számlaegyenleg USDT",
    min_value=0.0,
    value=10000.0,
    step=500.0,
)

if st_autorefresh:
    st_autorefresh(
        interval=refresh_seconds * 1000,
        key="saliga_refresh",
    )


# ============================================================
# HEADER
# ============================================================

st.title("⚡ SALIGA AI PRO 9.0")
st.caption(
    "OKX ONLY | MULTI-TIMEFRAME INTELLIGENCE | "
    "ORDER FLOW | INTERNAL ANALYSIS ENGINE"
)

st.info(
    "Az elemzőmotor EMA és SMA nélkül dolgozik. "
    "A LONG és SHORT jelzések piaci feltételek becslései, "
    "nem garantált kereskedési eredmények."
)


# ============================================================
# MARKET ANALYSIS
# ============================================================

@st.cache_data(ttl=20, show_spinner=False)
def load_all_market_data():
    output = {}

    for name, symbol in SYMBOLS.items():
        output[name] = analyze_coin(name, symbol)

    return output


with st.spinner("OKX piaci adatok lekérése és elemzése..."):
    market_data = load_all_market_data()


# ============================================================
# TOP MARKET SUMMARY
# ============================================================

cols = st.columns(3)

for i, name in enumerate(["BTC", "ETH", "SOL"]):
    data = market_data[name]

    with cols[i]:
        if data.get("error"):
            st.error(f"{name}: {data['error']}")
            continue

        direction = data["mtf"]["direction"]

        st.metric(
            f"{name} – ár",
            f"{data['price']:,.4f} USDT",
        )

        if direction == "LONG":
            st.success("ÖSSZESÍTETT IRÁNY: LONG")
        elif direction == "SHORT":
            st.error("ÖSSZESÍTETT IRÁNY: SHORT")
        else:
            st.warning("ÖSSZESÍTETT IRÁNY: SEMLEGES")

        st.write(
            f"MTF pontszám: {data['mtf']['score']}"
        )

        st.caption(data["mtf"]["agreement"])


# ============================================================
# MAIN TABS
# ============================================================

tabs = st.tabs([
    "📊 Piaci elemzés",
    "⏱️ Idősíkok",
    "🎯 Entry / SL / TP",
    "📈 Order Flow",
    "📰 Hírek és AI",
    "💬 Chat",
])


# ============================================================
# TAB 1 – MARKET OVERVIEW
# ============================================================

with tabs[0]:
    st.header("Piaci áttekintés")

    for name in ["BTC", "ETH", "SOL"]:
        data = market_data[name]

        if data.get("error"):
            st.warning(f"{name}: {data['error']}")
            continue

        st.subheader(name)

        a, b, c, d = st.columns(4)

        a.metric(
            "Ár",
            f"{data['price']:,.4f}",
        )

        b.metric(
            "Becsült delta",
            f"{data['delta']['delta']:,.4f}",
        )

        c.metric(
            "Order book imbalance",
            f"{data['orderbook_imbalance']:.2f}%",
        )

        funding = data["funding"]

        d.metric(
            "Funding",
            "N/A" if funding is None
            else f"{funding * 100:.4f}%",
        )

        st.write(
            "**Open Interest állapot:**",
            data["oi_analysis"]["state"],
        )

        if data["oi_analysis"]["change"] is not None:
            st.write(
                f"OI változás: "
                f"{data['oi_analysis']['change']:.3f}%"
            )

        st.write(
            f"**Támasz:** {data['support']:,.4f}"
            if data["support"] else "**Támasz:** N/A"
        )

        st.write(
            f"**Ellenállás:** {data['resistance']:,.4f}"
            if data["resistance"] else "**Ellenállás:** N/A"
        )

        st.divider()


# ============================================================
# TAB 2 – MULTI TIMEFRAME
# ============================================================

with tabs[1]:
    st.header("Multi-Timeframe Intelligence")

    st.write(
        "Minden idősík külön elemzést kap. "
        "Az egyes idősíkok jelzései nem helyettesítik egymást."
    )

    for name in ["BTC", "ETH", "SOL"]:
        data = market_data[name]

        if data.get("error"):
            st.warning(f"{name}: nincs teljes elemzés.")
            continue

        st.subheader(f"{name} – idősíkos elemzés")

        rows = []

        for timeframe, result in data["timeframes"].items():
            rows.append({
                "Idősík": timeframe,
                "Irány": result["direction"],
                "Jelerősség": result["strength"],
                "Pontszám": result["score"],
                "RSI": (
                    round(result["rsi"], 1)
                    if result["rsi"] is not None
                    else "N/A"
                ),
                "Momentum %": (
                    round(result["momentum"], 3)
                    if result["momentum"] is not None
                    else "N/A"
                ),
                "Struktúra": result["structure"],
                "Forgalom": result["volume_state"],
                "Likviditás": result["sweep"],
            })

        table = pd.DataFrame(rows)

        def color_direction(value):
            if value == "LONG":
                return "color: #079455; font-weight: bold"
            if value == "SHORT":
                return "color: #d92d20; font-weight: bold"
            if value == "SEMLEGES":
                return "color: #b7791f; font-weight: bold"
            return ""

        st.dataframe(
            table.style.map(
                color_direction,
                subset=["Irány"],
            ),
            use_container_width=True,
            hide_index=True,
        )

        mtf = data["mtf"]

        st.markdown(
            f"### Összesített irány: **{mtf['direction']}**"
        )

        st.write(
            f"MTF pontszám: {mtf['score']}"
        )

        st.write(
            f"Idősíkok közötti viszony: {mtf['agreement']}"
        )

        st.write(
            f"LONG idősíkok: {mtf.get('long_count', 0)} | "
            f"SHORT idősíkok: {mtf.get('short_count', 0)}"
        )

        st.divider()


# ============================================================
# TAB 3 – TRADE PLAN
# ============================================================

with tabs[2]:
    st.header("Belépési terv – Stop Loss – Take Profit")

    data = market_data[selected_coin]

    if data.get("error"):
        st.error(data["error"])

    elif data["plan"] is None:
        st.warning(
            "Nincs egyértelmű LONG vagy SHORT irány. "
            "A rendszer nem készít mesterséges kereskedési tervet."
        )

    else:
        plan = data["plan"]

        st.subheader(
            f"{selected_coin} – {plan['direction']}"
        )

        a, b, c = st.columns(3)

        a.metric(
            "Belépési ár",
            f"{plan['entry']:,.4f}",
        )

        b.metric(
            "Stop Loss",
            f"{plan['stop']:,.4f}",
        )

        c.metric(
            "Kockázati távolság",
            f"{plan['risk']:,.4f}",
        )

        x, y, z = st.columns(3)

        x.metric("TP1 – 1R", f"{plan['tp1']:,.4f}")
        y.metric("TP2 – 2R", f"{plan['tp2']:,.4f}")
        z.metric("TP3 – 3R", f"{plan['tp3']:,.4f}")

        risk_amount = (
            account_balance * risk_percent / 100
        )

        quantity = (
            risk_amount / plan["risk"]
            if plan["risk"] > 0
            else 0
        )

        st.info(
            f"Elméleti kockázati összeg: "
            f"{risk_amount:.2f} USDT"
        )

        st.write(
            f"Elméleti alapmennyiség: {quantity:.6f}"
        )

        st.caption(
            "Ez csak elméleti alapmennyiség. "
            "OKX kontraktusméret, lotméret, tőkeáttétel, "
            "díjak és minimum kötési mennyiség nélkül "
            "nem használható közvetlen megbízási méretként."
        )


# ============================================================
# TAB 4 – ORDER FLOW
# ============================================================

with tabs[3]:
    st.header("Order Flow Intelligence")

    data = market_data[selected_coin]

    if not data.get("error"):
        delta = data["delta"]

        a, b, c = st.columns(3)

        a.metric(
            "Vételi volumen",
            f"{delta['buy_volume']:,.4f}",
        )

        b.metric(
            "Eladási volumen",
            f"{delta['sell_volume']:,.4f}",
        )

        c.metric(
            "Becsült delta %",
            f"{delta['ratio']:.2f}%",
        )

        if delta["ratio"] > 5:
            st.success("A lekért kötésekben a vevői aktivitás dominál.")
        elif delta["ratio"] < -5:
            st.error("A lekért kötésekben az eladói aktivitás dominál.")
        else:
            st.warning("A lekért kötésekben nincs jelentős delta-eltérés.")

        st.metric(
            "Order book imbalance",
            f"{data['orderbook_imbalance']:.2f}%",
        )

        st.write(
            "**Open Interest:**",
            data["oi_analysis"]["state"],
        )

        if data["funding"] is not None:
            st.write(
                f"Funding rate: {data['funding'] * 100:.5f}%"
            )

        st.caption(
            "A delta az utoljára lekért kötésekből számított "
            "taker buy/sell becslés, nem teljes historikus CVD. "
            "Az order book pillanatfelvétel, nem garantált likviditás."
        )


# ============================================================
# TAB 5 – NEWS AND AI
# ============================================================

with tabs[4]:
    st.header("Kriptopiaci hírek")

    news = fetch_news()

    if not news:
        st.info("Jelenleg nem érhető el hír.")
    else:
        for item in news:
            st.markdown(
                f"**{item['title']}**  \n"
                f"[Hír megnyitása]({item['link']})"
            )

            if item["published"]:
                st.caption(item["published"])

            st.divider()

    st.subheader("AI piaci értelmezés")

    if st.button("SALIGA AI – Piaci összefoglaló"):
        context = json.dumps(
            {
                name: {
                    "price": data.get("price"),
                    "mtf": data.get("mtf"),
                    "timeframes": data.get("timeframes"),
                    "oi": data.get("oi_analysis"),
                    "delta": data.get("delta"),
                    "funding": data.get("funding"),
                }
                for name, data in market_data.items()
            },
            ensure_ascii=False,
            default=str,
        )

        with st.spinner("AI elemzés készül..."):
            answer = ask_groq(
                "Készíts részletes BTC, ETH és SOL piaci összefoglalót.",
                context,
            )

        st.write(answer)


# ============================================================
# TAB 6 – CHAT
# ============================================================

with tabs[5]:
    st.header("💬 SALIGA AI – Piaci asszisztens")

    question = st.text_area(
        "Mit szeretnél tudni a piacról?",
        placeholder=(
            "Például: Miért emelkedik a BTC, "
            "miközben az 1 órás idősík még short?"
        ),
    )

    if st.button("Kérdés elküldése"):
        if not question.strip():
            st.warning("Írj be egy kérdést.")
        else:
            context = json.dumps(
                {
                    name: {
                        "price": data.get("price"),
                        "mtf": data.get("mtf"),
                        "timeframes": data.get("timeframes"),
                        "oi": data.get("oi_analysis"),
                        "delta": data.get("delta"),
                        "funding": data.get("funding"),
                    }
                    for name, data in market_data.items()
                },
                ensure_ascii=False,
                default=str,
            )

            with st.spinner("SALIGA AI válaszol..."):
                answer = ask_groq(
                    question,
                    context,
                )

            st.markdown(answer)


# ============================================================
# FOOTER
# ============================================================

st.divider()

st.caption(
    f"{APP_NAME} | OKX ONLY | "
    "Internal Market Engine | "
    "No EMA / No SMA"
)

st.caption(
    "Az elemzések piaci adatokból képzett becslések. "
    "Nem pénzügyi tanácsok, és nem garantálják a jövőbeni ármozgást."
)
