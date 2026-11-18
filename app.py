from __future__ import annotations

import os
import time
import json
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

try:
    from streamlit_autorefresh import st_autorefresh
    AUTOREFRESH_AVAILABLE = True
except ImportError:
    AUTOREFRESH_AVAILABLE = False


# ============================================================
# CONFIGURATION
# ============================================================

APP_NAME = "SALIGA AI – MARKET INTELLIGENCE PRO"
APP_VERSION = "4.0.0"

DB_FILE = "saliga_ai_journal.db"

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

OHLCV_LIMIT = 300

REQUEST_TIMEOUT = 15000

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("SALIGA_AI")


# ============================================================
# PAGE
# ============================================================

st.set_page_config(
    page_title=APP_NAME,
    page_icon="⚡",
    layout="wide",
)

st.title("⚡ SALIGA AI")
st.caption(
    "INSTITUTIONAL MARKET INTELLIGENCE | "
    "MULTI-ASSET POSITION FLOW | OKX ONLY"
)


# ============================================================
# DATABASE
# ============================================================

def get_db():

    conn = sqlite3.connect(
        DB_FILE,
        check_same_thread=False
    )

    conn.execute("""
        CREATE TABLE IF NOT EXISTS signals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT,
            symbol TEXT,
            signal TEXT,
            price REAL,
            confidence REAL,
            score REAL,
            stop_loss REAL,
            take_profit REAL,
            status TEXT DEFAULT 'OPEN',
            close_price REAL,
            result_percent REAL
        )
    """)

    conn.commit()

    return conn


DB = get_db()


# ============================================================
# OKX CONNECTION
# ============================================================

@st.cache_resource
def get_exchange():

    exchange = ccxt.okx({
        "enableRateLimit": True,
        "timeout": REQUEST_TIMEOUT,
        "options": {
            "defaultType": "swap",
        },
    })

    exchange.load_markets()

    return exchange


def get_market_symbol(symbol):

    exchange = get_exchange()

    if symbol in exchange.markets:
        return symbol

    raise ValueError(
        f"Az OKX-en nem található ez a piac: {symbol}"
    )


# ============================================================
# DATA FETCHING
# ============================================================

@st.cache_data(ttl=15)
def fetch_ohlcv(symbol, timeframe):

    exchange = get_exchange()

    market_symbol = get_market_symbol(symbol)

    data = exchange.fetch_ohlcv(
        market_symbol,
        timeframe=timeframe,
        limit=OHLCV_LIMIT,
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

    return df


@st.cache_data(ttl=10)
def fetch_ticker(symbol):

    exchange = get_exchange()

    return exchange.fetch_ticker(
        get_market_symbol(symbol)
    )


@st.cache_data(ttl=15)
def fetch_orderbook(symbol):

    exchange = get_exchange()

    return exchange.fetch_order_book(
        get_market_symbol(symbol),
        limit=50,
    )


@st.cache_data(ttl=30)
def fetch_open_interest(symbol):

    exchange = get_exchange()

    try:
        return exchange.fetch_open_interest(
            get_market_symbol(symbol)
        )
    except Exception as e:
        logger.warning("OI error: %s", e)
        return None


@st.cache_data(ttl=30)
def fetch_funding(symbol):

    exchange = get_exchange()

    try:
        return exchange.fetch_funding_rate(
            get_market_symbol(symbol)
        )
    except Exception as e:
        logger.warning("Funding error: %s", e)
        return None


@st.cache_data(ttl=15)
def fetch_trades(symbol):

    exchange = get_exchange()

    try:
        return exchange.fetch_trades(
            get_market_symbol(symbol),
            limit=500,
        )
    except Exception as e:
        logger.warning("Trade error: %s", e)
        return []


# ============================================================
# INDICATORS
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

    return 100 - (100 / (1 + rs))


def calculate_atr(df, period=14):

    high = df["high"]
    low = df["low"]
    close = df["close"]

    previous_close = close.shift(1)

    tr = pd.concat(
        [
            high - low,
            (high - previous_close).abs(),
            (low - previous_close).abs(),
        ],
        axis=1,
    ).max(axis=1)

    return tr.rolling(period).mean()


def calculate_indicators(df):

    df = df.copy()

    df["RSI"] = calculate_rsi(
        df["close"]
    )

    df["ATR"] = calculate_atr(
        df
    )

    df["SMA20"] = df["close"].rolling(20).mean()

    df["SMA50"] = df["close"].rolling(50).mean()

    df["VOL_MA"] = df["volume"].rolling(20).mean()

    df["VOL_RATIO"] = (
        df["volume"] /
        df["VOL_MA"].replace(0, np.nan)
    )

    df["CANDLE_DELTA"] = np.where(
        df["close"] > df["open"],
        df["volume"],
        np.where(
            df["close"] < df["open"],
            -df["volume"],
            0,
        ),
    )

    return df


# ============================================================
# MARKET STRUCTURE
# ============================================================

def market_structure(df):

    if len(df) < 30:
        return {
            "trend": "UNKNOWN",
            "bos": "NONE",
            "score": 0,
        }

    recent = df.tail(100)

    highs = recent["high"]
    lows = recent["low"]

    high_points = highs[
        (highs.shift(1) < highs) &
        (highs.shift(-1) < highs)
    ]

    low_points = lows[
        (lows.shift(1) > lows) &
        (lows.shift(-1) > lows)
    ]

    if len(high_points) < 2 or len(low_points) < 2:

        return {
            "trend": "UNKNOWN",
            "bos": "NONE",
            "score": 0,
        }

    last_high = high_points.iloc[-1]
    previous_high = high_points.iloc[-2]

    last_low = low_points.iloc[-1]
    previous_low = low_points.iloc[-2]

    price = df["close"].iloc[-1]

    bullish_structure = (
        last_high > previous_high and
        last_low > previous_low
    )

    bearish_structure = (
        last_high < previous_high and
        last_low < previous_low
    )

    if bullish_structure:

        trend = "BULLISH"
        score = 1

    elif bearish_structure:

        trend = "BEARISH"
        score = -1

    else:

        trend = "RANGE"
        score = 0

    if price > last_high:
        bos = "BULLISH BOS"
        score = 1

    elif price < last_low:
        bos = "BEARISH BOS"
        score = -1

    else:
        bos = "NONE"

    return {
        "trend": trend,
        "bos": bos,
        "score": score,
        "last_high": float(last_high),
        "last_low": float(last_low),
    }


# ============================================================
# LIQUIDITY ANALYSIS
# ============================================================

def liquidity_analysis(df):

    recent = df.tail(50)

    high = recent["high"]
    low = recent["low"]

    resistance = high.max()
    support = low.min()

    current = recent.iloc[-1]

    previous_high = high.iloc[:-1].max()
    previous_low = low.iloc[:-1].min()

    sweep_high = (
        current["high"] > previous_high and
        current["close"] < previous_high
    )

    sweep_low = (
        current["low"] < previous_low and
        current["close"] > previous_low
    )

    if sweep_high:

        sweep = "BUY-SIDE LIQUIDITY SWEPT"

    elif sweep_low:

        sweep = "SELL-SIDE LIQUIDITY SWEPT"

    else:

        sweep = "NO SWEEP"

    return {
        "support": float(support),
        "resistance": float(resistance),
        "sweep": sweep,
        "sweep_score": (
            -1 if sweep_high else
            1 if sweep_low else
            0
        ),
    }


# ============================================================
# VOLUME PROFILE
# ============================================================

def volume_profile(df, bins=32):

    recent = df.tail(200)

    prices = (
        recent["high"] +
        recent["low"] +
        recent["close"]
    ) / 3

    volumes = recent["volume"]

    hist, edges = np.histogram(
        prices,
        bins=bins,
        weights=volumes,
    )

    poc_index = np.argmax(hist)

    poc = (
        edges[poc_index] +
        edges[poc_index + 1]
    ) / 2

    total_volume = hist.sum()

    if total_volume <= 0:
        return None

    order = np.argsort(hist)[::-1]

    selected = []
    accumulated = 0

    for index in order:

        selected.append(index)
        accumulated += hist[index]

        if accumulated >= total_volume * 0.70:
            break

    vah = edges[max(selected) + 1]
    val = edges[min(selected)]

    return {
        "POC": float(poc),
        "VAH": float(vah),
        "VAL": float(val),
    }


# ============================================================
# OPEN INTEREST INTERPRETATION
# ============================================================

def interpret_open_interest(
    price_change,
    oi_change
):

    if oi_change is None or price_change is None:
        return "OI DATA UNAVAILABLE", 0

    if price_change > 0 and oi_change > 0:

        return "LONG BUILD-UP ESTIMATE", 1

    if price_change < 0 and oi_change > 0:

        return "SHORT BUILD-UP ESTIMATE", -1

    if price_change > 0 and oi_change < 0:

        return "SHORT COVERING ESTIMATE", 1

    if price_change < 0 and oi_change < 0:

        return "LONG CLOSING ESTIMATE", -1

    return "NO CLEAR POSITION FLOW", 0


# ============================================================
# TAKER FLOW / CVD
# ============================================================

def analyze_taker_flow(trades):

    if not trades:

        return {
            "buy": 0,
            "sell": 0,
            "delta": 0,
            "ratio": 0,
            "signal": "UNAVAILABLE",
        }

    buy = 0
    sell = 0

    for trade in trades:

        amount = float(
            trade.get("amount") or 0
        )

        side = trade.get("side")

        if side == "buy":
            buy += amount

        elif side == "sell":
            sell += amount

    total = buy + sell

    delta = buy - sell

    ratio = (
        buy / total
        if total > 0
        else 0.5
    )

    if ratio > 0.58:
        signal = "BUYERS DOMINANT"

    elif ratio < 0.42:
        signal = "SELLERS DOMINANT"

    else:
        signal = "BALANCED FLOW"

    return {
        "buy": buy,
        "sell": sell,
        "delta": delta,
        "ratio": ratio,
        "signal": signal,
    }


# ============================================================
# ORDER BOOK
# ============================================================

def analyze_orderbook(orderbook):

    if not orderbook:
        return None

    bids = orderbook.get("bids", [])
    asks = orderbook.get("asks", [])

    if not bids or not asks:
        return None

    bid_volume = sum(
        float(x[1]) for x in bids[:20]
    )

    ask_volume = sum(
        float(x[1]) for x in asks[:20]
    )

    total = bid_volume + ask_volume

    if total <= 0:
        return None

    imbalance = (
        bid_volume - ask_volume
    ) / total

    if imbalance > 0.15:
        signal = "BUY SIDE DOMINANT"

    elif imbalance < -0.15:
        signal = "SELL SIDE DOMINANT"

    else:
        signal = "BALANCED ORDER BOOK"

    return {
        "bid_volume": bid_volume,
        "ask_volume": ask_volume,
        "imbalance": imbalance,
        "signal": signal,
    }


# ============================================================
# FUNDING RATE
# ============================================================

def analyze_funding(funding):

    if not funding:
        return None

    rate = funding.get("fundingRate")

    if rate is None:
        return None

    rate = float(rate)

    if rate > 0.0005:
        signal = "LONGS PAYING HIGH FUNDING"

    elif rate < -0.0005:
        signal = "SHORTS PAYING HIGH FUNDING"

    else:
        signal = "FUNDING NEUTRAL"

    return {
        "rate": rate,
        "signal": signal,
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

    result = {}

    for name, ticker in tickers.items():

        try:

            url = (
                "https://query1.finance.yahoo.com/v8/finance/chart/"
                + ticker
                + "?range=5d&interval=1d"
            )

            response = requests.get(
                url,
                timeout=8,
                headers={
                    "User-Agent": "Mozilla/5.0"
                },
            )

            response.raise_for_status()

            data = response.json()

            values = data["chart"]["result"][0]["indicators"]["quote"][0]["close"]

            values = [
                x for x in values
                if x is not None
            ]

            if len(values) >= 2:

                change = (
                    values[-1] - values[-2]
                ) / values[-2] * 100

                result[name] = {
                    "value": values[-1],
                    "change": change,
                }

        except Exception as e:

            logger.warning(
                "Macro error %s: %s",
                name,
                e
            )

    return result


def macro_score(macro):

    score = 0

    if "DXY" in macro:

        if macro["DXY"]["change"] > 0.15:
            score -= 1

        elif macro["DXY"]["change"] < -0.15:
            score += 1

    if "NASDAQ" in macro:

        if macro["NASDAQ"]["change"] > 0.3:
            score += 1

        elif macro["NASDAQ"]["change"] < -0.3:
            score -= 1

    if "SP500" in macro:

        if macro["SP500"]["change"] > 0.3:
            score += 1

        elif macro["SP500"]["change"] < -0.3:
            score -= 1

    if "VIX" in macro:

        if macro["VIX"]["change"] > 5:
            score -= 1

        elif macro["VIX"]["change"] < -5:
            score += 1

    return score


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

            for entry in feed.entries[:8]:

                news.append({
                    "title": entry.get(
                        "title",
                        "Untitled"
                    ),
                    "link": entry.get(
                        "link",
                        ""
                    ),
                })

        except Exception:
            continue

    return news[:15]


# ============================================================
# MULTI TIMEFRAME ANALYSIS
# ============================================================

def analyze_timeframe(df, timeframe):

    df = calculate_indicators(df)

    last = df.iloc[-1]

    score = 0

    close = float(last["close"])

    rsi = float(last["RSI"]) if pd.notna(last["RSI"]) else 50

    if close > last["SMA20"]:
        score += 1

    else:
        score -= 1

    if close > last["SMA50"]:
        score += 1

    else:
        score -= 1

    if rsi > 55:
        score += 1

    elif rsi < 45:
        score -= 1

    structure = market_structure(df)

    score += structure["score"] * 2

    liquidity = liquidity_analysis(df)

    score += liquidity["sweep_score"]

    if last["VOL_RATIO"] > 1.5:

        if last["close"] > last["open"]:
            score += 1

        elif last["close"] < last["open"]:
            score -= 1

    return {
        "score": score,
        "price": close,
        "RSI": rsi,
        "ATR": float(last["ATR"]),
        "volume_ratio": float(
            last["VOL_RATIO"]
        ) if pd.notna(last["VOL_RATIO"]) else 0,
        "structure": structure,
        "liquidity": liquidity,
        "df": df,
    }


# ============================================================
# MASTER ANALYSIS
# ============================================================

def analyze_market(symbol):

    results = {}

    errors = []

    for tf in TIMEFRAMES:

        try:

            df = fetch_ohlcv(
                symbol,
                tf
            )

            if len(df) < 60:
                continue

            results[tf] = analyze_timeframe(
                df,
                tf
            )

        except Exception as e:

            errors.append(
                f"{tf}: {str(e)}"
            )

    if not results:

        raise RuntimeError(
            "Nem sikerült idősík-adatokat lekérni az OKX-ről."
        )

    current_price = results[
        "1m"
    ]["price"] if "1m" in results else list(
        results.values()
    )[-1]["price"]

    # --------------------------------------------------------
    # MTF SCORE
    # --------------------------------------------------------

    mtf_score = 0
    mtf_weight = 0

    for tf, weight in TF_WEIGHTS.items():

        if tf in results:

            mtf_score += (
                results[tf]["score"] * weight
            )

            mtf_weight += weight

    if mtf_weight > 0:
        mtf_score /= mtf_weight

    # --------------------------------------------------------
    # OPEN INTEREST
    # --------------------------------------------------------

    oi_data = fetch_open_interest(symbol)

    oi_value = None

    if oi_data:

        oi_value = (
            oi_data.get("openInterestAmount")
            or oi_data.get("openInterestValue")
            or oi_data.get("openInterest")
        )

    oi_change = None
    price_change = None

    # --------------------------------------------------------
    # PRICE CHANGE AND OI CHANGE
    # --------------------------------------------------------

    try:

        exchange = get_exchange()

        oi_history = exchange.fetch_open_interest_history(
            get_market_symbol(symbol),
            timeframe="5m",
            limit=20,
        )

        if len(oi_history) >= 2:

            old_oi = oi_history[0].get(
                "openInterestAmount"
            )

            new_oi = oi_history[-1].get(
                "openInterestAmount"
            )

            if old_oi and new_oi:

                oi_change = (
                    float(new_oi) - float(old_oi)
                ) / float(old_oi) * 100

                price_change = (
                    results["5m"]["price"] -
                    results["15m"]["price"]
                ) / results["15m"]["price"] * 100

    except Exception as e:

        logger.warning(
            "OI history unavailable: %s",
            e
        )

    oi_interpretation, oi_score = interpret_open_interest(
        price_change,
        oi_change
    )

    # --------------------------------------------------------
    # TAKER FLOW
    # --------------------------------------------------------

    trades = fetch_trades(symbol)

    taker = analyze_taker_flow(trades)

    if taker["signal"] == "BUYERS DOMINANT":
        taker_score = 1

    elif taker["signal"] == "SELLERS DOMINANT":
        taker_score = -1

    else:
        taker_score = 0

    # --------------------------------------------------------
    # ORDER BOOK
    # --------------------------------------------------------

    orderbook = fetch_orderbook(symbol)

    book = analyze_orderbook(orderbook)

    book_score = 0

    if book:

        book_score = (
            1 if book["imbalance"] > 0.15
            else -1 if book["imbalance"] < -0.15
            else 0
        )

    # --------------------------------------------------------
    # FUNDING
    # --------------------------------------------------------

    funding_raw = fetch_funding(symbol)

    funding = analyze_funding(
        funding_raw
    )

    funding_score = 0

    if funding:

        if funding["rate"] > 0.001:
            funding_score = -1

        elif funding["rate"] < -0.001:
            funding_score = 1

    # --------------------------------------------------------
    # MACRO
    # --------------------------------------------------------

    macro = fetch_macro()

    m_score = macro_score(macro)

    # --------------------------------------------------------
    # VOLUME PROFILE
    # --------------------------------------------------------

    vp = volume_profile(
        results["1h"]["df"]
        if "1h" in results
        else list(results.values())[-1]["df"]
    )

    # --------------------------------------------------------
    # WEIGHTED DECISION ENGINE
    # --------------------------------------------------------

    components = {
        "MTF": (mtf_score, 35),
        "OI": (oi_score, 20),
        "TAKER": (taker_score, 15),
        "ORDERBOOK": (book_score, 10),
        "FUNDING": (funding_score, 5),
        "MACRO": (m_score / 4, 15),
    }

    total_weight = 0
    weighted_score = 0

    for name, (value, weight) in components.items():

        available = True

        if name == "OI" and oi_change is None:
            available = False

        if name == "ORDERBOOK" and book is None:
            available = False

        if name == "FUNDING" and funding is None:
            available = False

        if name == "MACRO" and not macro:
            available = False

        if name == "TAKER" and not trades:
            available = False

        if available:

            weighted_score += value * weight

            total_weight += weight

    if total_weight:

        normalized_score = (
            weighted_score / total_weight
        )

    else:

        normalized_score = 0

    coverage = total_weight

    # --------------------------------------------------------
    # DECISION
    # --------------------------------------------------------

    if coverage < 55:

        signal = "KIVÁRÁS"

    elif normalized_score >= 0.45:

        signal = "LONG"

    elif normalized_score <= -0.45:

        signal = "SHORT"

    else:

        signal = "KIVÁRÁS"

    # Confidence is a score-strength indicator,
    # NOT a statistical probability of winning.

    confidence = min(
        95,
        abs(normalized_score) * 100
    )

    return {
        "symbol": symbol,
        "price": current_price,
        "signal": signal,
        "score": normalized_score,
        "confidence": confidence,
        "coverage": coverage,
        "mtf": results,
        "oi": oi_data,
        "oi_change": oi_change,
        "oi_interpretation": oi_interpretation,
        "taker": taker,
        "book": book,
        "funding": funding,
        "macro": macro,
        "volume_profile": vp,
        "components": components,
        "errors": errors,
    }


# ============================================================
# RISK MANAGEMENT
# ============================================================

def calculate_risk_plan(
    signal,
    price,
    atr,
    capital,
    risk_percent,
    leverage
):

    if signal not in ("LONG", "SHORT"):

        return None

    if atr is None or not np.isfinite(atr) or atr <= 0:
        return None

    risk_amount = (
        capital * risk_percent / 100
    )

    stop_distance = atr * 1.5

    target_distance = atr * 3

    if signal == "LONG":

        stop_loss = price - stop_distance

        take_profit = price + target_distance

    else:

        stop_loss = price + stop_distance

        take_profit = price - target_distance

    quantity = risk_amount / stop_distance

    max_notional = capital * leverage

    quantity = min(
        quantity,
        max_notional / price
    )

    return {
        "entry": price,
        "stop_loss": stop_loss,
        "take_profit": take_profit,
        "quantity": quantity,
        "notional": quantity * price,
        "risk_amount": risk_amount,
        "risk_reward": 2.0,
    }


# ============================================================
# JOURNAL
# ============================================================

def save_signal(
    symbol,
    signal,
    price,
    confidence,
    score,
    sl,
    tp
):

    DB.execute(
        """
        INSERT INTO signals (
            timestamp,
            symbol,
            signal,
            price,
            confidence,
            score,
            stop_loss,
            take_profit,
            status
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'OPEN')
        """,
        (
            datetime.now(timezone.utc).isoformat(),
            symbol,
            signal,
            price,
            confidence,
            score,
            sl,
            tp,
        ),
    )

    DB.commit()


def get_journal():

    return pd.read_sql_query(
        """
        SELECT *
        FROM signals
        ORDER BY id DESC
        LIMIT 500
        """,
        DB,
    )


def update_journal_outcomes(symbol):

    try:

        df = fetch_ohlcv(
            symbol,
            "1m"
        )

        if df.empty:
            return

        rows = DB.execute(
            """
            SELECT id, timestamp, signal,
                   stop_loss, take_profit
            FROM signals
            WHERE symbol = ?
              AND status = 'OPEN'
            """,
            (symbol,),
        ).fetchall()

        for row in rows:

            signal_id = row[0]
            created = row[1]
            signal = row[2]
            sl = row[3]
            tp = row[4]

            if not sl or not tp:
                continue

            created_dt = pd.to_datetime(
                created,
                utc=True
            )

            candles = df[
                df["datetime"] >= created_dt
            ]

            if candles.empty:
                continue

            exit_price = None

            for _, candle in candles.iterrows():

                if signal == "LONG":

                    hit_sl = candle["low"] <= sl

                    hit_tp = candle["high"] >= tp

                else:

                    hit_sl = candle["high"] >= sl

                    hit_tp = candle["low"] <= tp

                # Conservative rule:
                # if SL and TP occur in the same candle,
                # assume SL was hit first.

                if hit_sl:

                    exit_price = sl
                    break

                if hit_tp:

                    exit_price = tp
                    break

            if exit_price is not None:

                entry = DB.execute(
                    "SELECT price FROM signals WHERE id = ?",
                    (signal_id,),
                ).fetchone()[0]

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
                        result_percent = ?
                    WHERE id = ?
                    """,
                    (
                        exit_price,
                        result,
                        signal_id,
                    ),
                )

        DB.commit()

    except Exception as e:

        logger.warning(
            "Journal update error: %s",
            e
        )


# ============================================================
# CHART
# ============================================================

def create_chart(df, symbol):

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
        height=550,
        xaxis_rangeslider_visible=False,
        template="plotly_dark",
        title=f"{symbol} – OKX",
    )

    return fig


# ============================================================
# AI EXPLANATION
# ============================================================

def groq_explanation(result):

    api_key = st.secrets.get(
        "GROQ_API_KEY",
        os.getenv("GROQ_API_KEY")
    )

    if not api_key:
        return "Groq API kulcs nincs beállítva."

    prompt = f"""
You are SALIGA AI, an institutional crypto market analyst.

Analyze the following market data in Hungarian.

Symbol: {result['symbol']}
Price: {result['price']}
Signal: {result['signal']}
Score: {result['score']}
Confidence score: {result['confidence']}
Data coverage: {result['coverage']}%

Open Interest interpretation:
{result['oi_interpretation']}

Taker flow:
{result['taker']}

Order book:
{result['book']}

Funding:
{result['funding']}

Macro:
{result['macro']}

Volume profile:
{result['volume_profile']}

Explain:
1. Market direction.
2. Long and short positioning estimates.
3. Buyer and seller activity.
4. Liquidity situation.
5. Main risks.
6. What would invalidate the current bias.

Never invent missing data.
Never describe confidence as win probability.
Do not give guaranteed predictions.
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
                            "You are a professional crypto market analyst. "
                            "Answer in Hungarian."
                        ),
                    },
                    {
                        "role": "user",
                        "content": prompt,
                    },
                ],
                "temperature": 0.25,
                "max_tokens": 1200,
            },
            timeout=30,
        )

        response.raise_for_status()

        return response.json()[
            "choices"
        ][0]["message"]["content"]

    except Exception as e:

        return f"AI elemzési hiba: {e}"


# ============================================================
# SIDEBAR
# ============================================================

st.sidebar.header("⚙️ SALIGA AI SETTINGS")

selected_symbol = st.sidebar.selectbox(
    "Market",
    list(SYMBOLS.keys()),
)

symbol = SYMBOLS[selected_symbol]

capital = st.sidebar.number_input(
    "Capital (USDT)",
    min_value=10.0,
    value=1000.0,
    step=100.0,
)

risk_percent = st.sidebar.slider(
    "Risk per trade (%)",
    0.25,
    5.0,
    1.0,
    0.25,
)

leverage = st.sidebar.slider(
    "Maximum leverage",
    1,
    20,
    5,
)

refresh_seconds = st.sidebar.selectbox(
    "Auto refresh",
    [0, 15, 30, 60],
    index=2,
)

if refresh_seconds > 0 and AUTOREFRESH_AVAILABLE:

    st_autorefresh(
        interval=refresh_seconds * 1000,
        key="saliga_refresh",
    )


# ============================================================
# MAIN
# ============================================================

if st.button(
    "⚡ RUN FULL MARKET ANALYSIS",
    type="primary",
    use_container_width=True,
):

    st.cache_data.clear()

    st.rerun()


try:

    with st.spinner(
        "SALIGA AI – OKX market intelligence running..."
    ):

        result = analyze_market(
            symbol
        )

        update_journal_outcomes(
            symbol
        )

    # --------------------------------------------------------
    # HEADER CARDS
    # --------------------------------------------------------

    c1, c2, c3, c4 = st.columns(4)

    c1.metric(
        "CURRENT PRICE",
        f"${result['price']:,.4f}",
    )

    c2.metric(
        "MARKET DECISION",
        result["signal"],
    )

    c3.metric(
        "CONFIDENCE SCORE",
        f"{result['confidence']:.1f}%",
    )

    c4.metric(
        "DATA COVERAGE",
        f"{result['coverage']:.0f}%",
    )

    st.caption(
        "A confidence score indicates the strength of the available "
        "signals, not the probability of a profitable trade."
    )

    # --------------------------------------------------------
    # POSITION FLOW
    # --------------------------------------------------------

    st.subheader(
        "📊 POSITION FLOW INTELLIGENCE"
    )

    p1, p2, p3 = st.columns(3)

    p1.metric(
        "OPEN INTEREST",
        result["oi_interpretation"],
    )

    p2.metric(
        "TAKER FLOW",
        result["taker"]["signal"],
    )

    p3.metric(
        "ORDER BOOK",
        result["book"]["signal"]
        if result["book"]
        else "UNAVAILABLE",
    )

    # --------------------------------------------------------
    # COMPONENTS
    # --------------------------------------------------------

    st.subheader(
        "🧠 DECISION ENGINE"
    )

    component_data = []

    for name, (value, weight) in result["components"].items():

        component_data.append({
            "Component": name,
            "Score": round(value, 3),
            "Weight": weight,
        })

    st.dataframe(
        pd.DataFrame(component_data),
        use_container_width=True,
        hide_index=True,
    )

    # --------------------------------------------------------
    # MULTI TIMEFRAME
    # --------------------------------------------------------

    st.subheader(
        "⏱️ MULTI-TIMEFRAME ANALYSIS"
    )

    tf_rows = []

    for tf, data in result["mtf"].items():

        tf_rows.append({
            "Timeframe": tf,
            "Price": data["price"],
            "Score": data["score"],
            "RSI": round(data["RSI"], 2),
            "ATR": data["ATR"],
            "Structure": data["structure"]["trend"],
            "BOS": data["structure"]["bos"],
            "Liquidity": data["liquidity"]["sweep"],
        })

    st.dataframe(
        pd.DataFrame(tf_rows),
        use_container_width=True,
        hide_index=True,
    )

    # --------------------------------------------------------
    # CHART
    # --------------------------------------------------------

    st.subheader(
        "📈 MARKET CHART"
    )

    chart_tf = st.selectbox(
        "Chart timeframe",
        TIMEFRAMES,
        index=3,
    )

    chart_df = calculate_indicators(
        fetch_ohlcv(
            symbol,
            chart_tf
        )
    )

    st.plotly_chart(
        create_chart(
            chart_df,
            symbol,
        ),
        use_container_width=True,
    )

    # --------------------------------------------------------
    # VOLUME PROFILE
    # --------------------------------------------------------

    st.subheader(
        "📦 VOLUME PROFILE"
    )

    vp = result["volume_profile"]

    if vp:

        a, b, c = st.columns(3)

        a.metric(
            "POC",
            f"{vp['POC']:,.4f}",
        )

        b.metric(
            "VAH",
            f"{vp['VAH']:,.4f}",
        )

        c.metric(
            "VAL",
            f"{vp['VAL']:,.4f}",
        )

    # --------------------------------------------------------
    # FUNDING
    # --------------------------------------------------------

    st.subheader(
        "💰 FUNDING RATE"
    )

    if result["funding"]:

        st.metric(
            "Funding",
            f"{result['funding']['rate'] * 100:.5f}%",
        )

        st.write(
            result["funding"]["signal"]
        )

    else:

        st.warning(
            "Funding adat nem érhető el."
        )

    # --------------------------------------------------------
    # MACRO
    # --------------------------------------------------------

    st.subheader(
        "🌍 MACRO INTELLIGENCE"
    )

    if result["macro"]:

        macro_rows = []

        for name, data in result["macro"].items():

            macro_rows.append({
                "Asset": name,
                "Value": data["value"],
                "Daily change (%)": round(
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
            "A makroadatok jelenleg nem érhetők el."
        )

    # --------------------------------------------------------
    # RISK PLAN
    # --------------------------------------------------------

    st.subheader(
        "🛡️ RISK MANAGEMENT"
    )

    atr = (
        result["mtf"]["1m"]["ATR"]
        if "1m" in result["mtf"]
        else result["mtf"]["5m"]["ATR"]
    )

    risk_plan = calculate_risk_plan(
        result["signal"],
        result["price"],
        atr,
        capital,
        risk_percent,
        leverage,
    )

    if risk_plan:

        r1, r2, r3 = st.columns(3)

        r1.metric(
            "STOP LOSS",
            f"{risk_plan['stop_loss']:,.4f}",
        )

        r2.metric(
            "TAKE PROFIT",
            f"{risk_plan['take_profit']:,.4f}",
        )

        r3.metric(
            "POSITION SIZE",
            f"{risk_plan['quantity']:.5f}",
        )

        st.write(
            f"Notional: {risk_plan['notional']:.2f} USDT"
        )

        st.write(
            f"Planned risk: {risk_plan['risk_amount']:.2f} USDT"
        )

        st.write(
            f"Risk/Reward: 1:{risk_plan['risk_reward']:.1f}"
        )

    else:

        st.info(
            "Nincs megfelelő LONG vagy SHORT jelzés. "
            "A rendszer KIVÁRÁS állapotban van, ezért nem készít "
            "automatikus irányú kockázati tervet."
        )

    # --------------------------------------------------------
    # AI ANALYST
    # --------------------------------------------------------

    st.subheader(
        "🤖 SALIGA AI ANALYST"
    )

    if st.button(
        "Generate AI market report",
        use_container_width=True,
    ):

        with st.spinner(
            "AI market interpretation..."
        ):

            explanation = groq_explanation(
                result
            )

        st.markdown(
            explanation
        )

    # --------------------------------------------------------
    # NEWS
    # --------------------------------------------------------

    st.subheader(
        "📰 CRYPTO NEWS"
    )

    news = fetch_news()

    for item in news:

        st.markdown(
            f"- [{item['title']}]({item['link']})"
        )

    # --------------------------------------------------------
    # SAVE SIGNAL
    # --------------------------------------------------------

    st.subheader(
        "💾 SIGNAL JOURNAL"
    )

    if st.button(
        "SAVE CURRENT SIGNAL",
        use_container_width=True,
    ):

        sl = (
            risk_plan["stop_loss"]
            if risk_plan
            else None
        )

        tp = (
            risk_plan["take_profit"]
            if risk_plan
            else None
        )

        save_signal(
            symbol,
            result["signal"],
            result["price"],
            result["confidence"],
            result["score"],
            sl,
            tp,
        )

        st.success(
            "Signal successfully saved."
        )

    journal = get_journal()

    if not journal.empty:

        st.dataframe(
            journal,
            use_container_width=True,
            hide_index=True,
        )

        csv = journal.to_csv(
            index=False
        ).encode("utf-8")

        st.download_button(
            "DOWNLOAD JOURNAL CSV",
            data=csv,
            file_name="saliga_ai_journal.csv",
            mime="text/csv",
        )

        closed = journal[
            journal["status"] == "CLOSED"
        ]

        if not closed.empty:

            wins = (
                closed["result_percent"] > 0
            ).sum()

            losses = (
                closed["result_percent"] <= 0
            ).sum()

            total = wins + losses

            winrate = (
                wins / total * 100
                if total > 0
                else 0
            )

            st.metric(
                "JOURNAL WIN RATE",
                f"{winrate:.1f}%",
            )

    # --------------------------------------------------------
    # ERRORS
    # --------------------------------------------------------

    if result["errors"]:

        with st.expander(
            "Data warnings"
        ):

            for error in result["errors"]:

                st.warning(error)

except Exception as e:

    st.error(
        f"MARKET ANALYSIS ERROR: {str(e)}"
    )

    st.info(
        "Ellenőrizd az OKX elérhetőségét, "
        "a piac nevét és az API-kapcsolatot."
    )


# ============================================================
# FOOTER
# ============================================================

st.divider()

st.caption(
    f"{APP_NAME} | Version {APP_VERSION} | "
    "OKX DATA SOURCE ONLY | "
    "Analytical software – not financial advice."
)
