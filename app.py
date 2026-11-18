# ============================================================
# SALIGA AI PRO 17.1
# OKX MULTI-ASSET MARKET INTELLIGENCE
# BTC / ETH / SOL
# M5 / M15 / H1 / H4 / D1
# ============================================================

import streamlit as st
import ccxt
import requests
import pandas as pd
import numpy as np
import sqlite3
import json
import os
import time

from datetime import datetime, timezone
from openai import OpenAI


# ============================================================
# 1. CONFIGURATION
# ============================================================

st.set_page_config(
    page_title="SALIGA AI PRO 17.1",
    page_icon="⚡",
    layout="wide"
)

APP_VERSION = "SALIGA AI PRO 17.1"
DB_PATH = "saliga_ai.db"

SYMBOLS = {
    "BTC": "BTC/USDT:USDT",
    "ETH": "ETH/USDT:USDT",
    "SOL": "SOL/USDT:USDT"
}

TIMEFRAMES = {
    "M5": "5m",
    "M15": "15m",
    "H1": "1h",
    "H4": "4h",
    "D1": "1d"
}

OI_URL = "https://www.okx.com/api/v5/public/open-interest"

GROQ_MODEL = "openai/gpt-oss-120b"

MARKET_REFRESH = 60
OI_HISTORY_INTERVAL = 300
MIN_SIGNAL_SCORE = 65

HTTP_TIMEOUT = 15


# ============================================================
# 2. SESSION STATE
# ============================================================

DEFAULT_STATE = {
    "last_refresh": 0,
    "analysis_data": {},
    "ai_result": None,
    "ai_error": None,
    "chat_answer": None
}

for key, value in DEFAULT_STATE.items():
    if key not in st.session_state:
        st.session_state[key] = value


# ============================================================
# 3. GENERAL HELPERS
# ============================================================

def now_utc():
    return datetime.now(timezone.utc)


def timestamp():
    return now_utc().isoformat()


def safe_float(value):

    try:
        result = float(value)

        if np.isfinite(result):
            return result

        return None

    except (TypeError, ValueError):
        return None


def fmt(value, decimals=2):

    if value is None:
        return "N/A"

    try:
        return f"{float(value):,.{decimals}f}"

    except (TypeError, ValueError):
        return "N/A"


def pct_change(current, previous):

    if current is None or previous is None:
        return None

    if previous == 0:
        return None

    return ((current - previous) / previous) * 100


def get_secret(name):

    try:
        value = st.secrets.get(name)

        if value:
            return str(value)

    except Exception:
        pass

    return os.getenv(name)


def safe_close(conn):

    try:
        conn.close()
    except Exception:
        pass


# ============================================================
# 4. DATABASE
# ============================================================

def db_connection():

    return sqlite3.connect(
        DB_PATH,
        check_same_thread=False,
        timeout=20
    )


def init_db():

    conn = db_connection()

    try:

        cur = conn.cursor()

        cur.execute("""
            CREATE TABLE IF NOT EXISTS oi_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                asset TEXT,
                value REAL,
                unit TEXT,
                created_at TEXT
            )
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS signals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                asset TEXT,
                timeframe TEXT,
                direction TEXT,
                score REAL,
                entry REAL,
                stop_loss REAL,
                tp1 REAL,
                tp2 REAL,
                tp3 REAL,
                status TEXT,
                created_at TEXT
            )
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS signal_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                signal_id INTEGER,
                event TEXT,
                price REAL,
                created_at TEXT
            )
        """)

        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_oi_asset
            ON oi_history(asset, id)
        """)

        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_signal_status
            ON signals(status)
        """)

        conn.commit()

    finally:
        safe_close(conn)


init_db()


# ============================================================
# 5. OKX CONNECTION
# ============================================================

@st.cache_resource
def get_exchange():

    client = ccxt.okx({
        "enableRateLimit": True,
        "timeout": 20000,
        "options": {
            "defaultType": "swap"
        }
    })

    client.load_markets()

    return client


exchange = None
exchange_error = None

try:

    exchange = get_exchange()

except Exception as e:

    exchange_error = str(e)


# ============================================================
# 6. OPEN INTEREST PARSER
# ============================================================

def extract_oi_record(record):

    if not isinstance(record, dict):
        return None

    fields = [
        ("oiUsd", "USD"),
        ("oiCcy", "BASE"),
        ("oi", "CONTRACTS"),
        ("openInterestValue", "USD"),
        ("openInterestAmount", "BASE")
    ]

    for field, unit in fields:

        value = safe_float(record.get(field))

        if value is not None and value > 0:

            return {
                "value": value,
                "unit": unit,
                "raw": record
            }

    return None


def parse_oi_payload(payload):

    if not isinstance(payload, dict):
        return None

    if str(payload.get("code")) != "0":
        return None

    records = payload.get("data", [])

    if isinstance(records, dict):
        records = [records]

    if not isinstance(records, list):
        return None

    for record in records:

        extracted = extract_oi_record(record)

        if extracted:
            return extracted

    return None


# ============================================================
# 7. OKX OPEN INTEREST
# ============================================================

def fetch_okx_oi(asset):

    if exchange is None:

        return {
            "value": None,
            "unit": "N/A",
            "source": "FAILED",
            "error": exchange_error or "OKX unavailable"
        }

    symbol = SYMBOLS[asset]
    errors = []

    try:

        market = exchange.market(symbol)
        inst_id = market["id"]

    except Exception as e:

        return {
            "value": None,
            "unit": "N/A",
            "source": "FAILED",
            "error": "Market lookup: " + str(e)
        }

    params = {
        "instType": "SWAP",
        "instId": inst_id
    }

    # METHOD 1: CCXT NATIVE

    try:

        method = getattr(
            exchange,
            "public_get_public_open_interest",
            None
        )

        if method is None:

            method = getattr(
                exchange,
                "publicGetPublicOpenInterest",
                None
            )

        if method is not None:

            payload = method(params)

            result = parse_oi_payload(payload)

            if result:

                return {
                    "value": result["value"],
                    "unit": result["unit"],
                    "source": "OKX CCXT NATIVE",
                    "error": None
                }

            errors.append(
                "CCXT native: no usable OI"
            )

        else:

            errors.append(
                "CCXT native method unavailable"
            )

    except Exception as e:

        errors.append(
            "CCXT native: " + str(e)
        )

    # METHOD 2: CCXT UNIFIED

    try:

        result = exchange.fetch_open_interest(symbol)

        if isinstance(result, dict):

            extracted = extract_oi_record(result)

            if extracted:

                return {
                    "value": extracted["value"],
                    "unit": extracted["unit"],
                    "source": "OKX CCXT UNIFIED",
                    "error": None
                }

            info = result.get("info", {})

            extracted = extract_oi_record(info)

            if extracted:

                return {
                    "value": extracted["value"],
                    "unit": extracted["unit"],
                    "source": "OKX CCXT UNIFIED",
                    "error": None
                }

            if isinstance(info, dict):

                for record in info.get("data", []):

                    extracted = extract_oi_record(record)

                    if extracted:

                        return {
                            "value": extracted["value"],
                            "unit": extracted["unit"],
                            "source": "OKX CCXT UNIFIED",
                            "error": None
                        }

        errors.append(
            "CCXT unified: no usable OI"
        )

    except Exception as e:

        errors.append(
            "CCXT unified: " + str(e)
        )

    # METHOD 3: DIRECT OKX HTTP

    try:

        response = requests.get(
            OI_URL,
            params=params,
            headers={
                "Accept": "application/json",
                "User-Agent": "SALIGA-AI-PRO"
            },
            timeout=HTTP_TIMEOUT
        )

        response.raise_for_status()

        payload = response.json()

        result = parse_oi_payload(payload)

        if result:

            return {
                "value": result["value"],
                "unit": result["unit"],
                "source": "OKX DIRECT HTTP",
                "error": None
            }

        errors.append(
            "Direct HTTP: "
            + str(payload.get("code"))
            + " "
            + str(payload.get("msg"))
        )

    except Exception as e:

        errors.append(
            "Direct HTTP: " + str(e)
        )

    return {
        "value": None,
        "unit": "N/A",
        "source": "FAILED",
        "error": " | ".join(errors)
    }


# ============================================================
# 8. OPEN INTEREST HISTORY
# ============================================================

def save_oi_history(asset, value, unit):

    if value is None:
        return

    conn = db_connection()

    try:

        cur = conn.cursor()

        cur.execute("""
            INSERT INTO oi_history
            (asset, value, unit, created_at)
            VALUES (?, ?, ?, ?)
        """, (
            asset,
            value,
            unit,
            timestamp()
        ))

        conn.commit()

    finally:

        safe_close(conn)


def calculate_oi_change(asset, current_value):

    if current_value is None:
        return None

    conn = db_connection()

    try:

        cur = conn.cursor()

        cur.execute("""
            SELECT value, created_at
            FROM oi_history
            WHERE asset = ?
            ORDER BY id DESC
            LIMIT 100
        """, (asset,))

        rows = cur.fetchall()

    finally:

        safe_close(conn)

    if not rows:
        return None

    current_time = now_utc()

    for value, created_at in rows:

        try:

            old_time = datetime.fromisoformat(
                created_at
            )

            if old_time.tzinfo is None:

                old_time = old_time.replace(
                    tzinfo=timezone.utc
                )

            elapsed = (
                current_time - old_time
            ).total_seconds()

            if elapsed >= OI_HISTORY_INTERVAL:

                return pct_change(
                    current_value,
                    float(value)
                )

        except Exception:

            continue

    return None


# ============================================================
# 9. MARKET DATA
# ============================================================

def fetch_market_data(asset):

    if exchange is None:

        return {
            "asset": asset,
            "error": exchange_error or "OKX unavailable",
            "price": None,
            "oi": None,
            "oi_unit": "N/A",
            "oi_change": None,
            "funding": None,
            "orderbook_imbalance": None
        }

    symbol = SYMBOLS[asset]

    result = {
        "asset": asset,
        "symbol": symbol,
        "error": None
    }

    # TICKER

    try:

        ticker = exchange.fetch_ticker(symbol)

        result["price"] = safe_float(
            ticker.get("last")
        )

        result["bid"] = safe_float(
            ticker.get("bid")
        )

        result["ask"] = safe_float(
            ticker.get("ask")
        )

        result["high"] = safe_float(
            ticker.get("high")
        )

        result["low"] = safe_float(
            ticker.get("low")
        )

        result["volume"] = safe_float(
            ticker.get("quoteVolume")
        )

        result["percentage"] = safe_float(
            ticker.get("percentage")
        )

    except Exception as e:

        result["error"] = "Ticker: " + str(e)

        result["price"] = None

    # ORDER BOOK

    try:

        orderbook = exchange.fetch_order_book(
            symbol,
            limit=20
        )

        bids = orderbook.get("bids", [])
        asks = orderbook.get("asks", [])

        bid_volume = sum(
            float(item[0]) * float(item[1])
            for item in bids
        )

        ask_volume = sum(
            float(item[0]) * float(item[1])
            for item in asks
        )

        total = bid_volume + ask_volume

        result["bid_volume"] = bid_volume
        result["ask_volume"] = ask_volume

        if total > 0:

            result["orderbook_imbalance"] = (
                (bid_volume - ask_volume) / total
            ) * 100

        else:

            result["orderbook_imbalance"] = None

    except Exception as e:

        result["orderbook_imbalance"] = None
        result["orderbook_error"] = str(e)

    # FUNDING

    try:

        funding = exchange.fetch_funding_rate(symbol)

        result["funding"] = safe_float(
            funding.get("fundingRate")
        )

    except Exception as e:

        result["funding"] = None
        result["funding_error"] = str(e)

    # OPEN INTEREST

    oi = fetch_okx_oi(asset)

    result["oi"] = oi["value"]
    result["oi_unit"] = oi["unit"]
    result["oi_source"] = oi["source"]
    result["oi_error"] = oi["error"]

    # Calculate change before saving new sample.

    result["oi_change"] = calculate_oi_change(
        asset,
        result["oi"]
    )

    if result["oi"] is not None:

        save_oi_history(
            asset,
            result["oi"],
            result["oi_unit"]
        )

    return result


# ============================================================
# 10. CANDLE DATA
# ============================================================

def fetch_candles(asset, timeframe):

    if exchange is None:
        return None

    try:

        data = exchange.fetch_ohlcv(
            SYMBOLS[asset],
            timeframe=TIMEFRAMES[timeframe],
            limit=250
        )

        if not data or len(data) < 50:
            return None

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

        numeric_columns = [
            "open",
            "high",
            "low",
            "close",
            "volume"
        ]

        for col in numeric_columns:

            df[col] = pd.to_numeric(
                df[col],
                errors="coerce"
            )

        # Exclude currently forming candle.

        if len(df) > 2:
            df = df.iloc[:-1].copy()

        return (
            df.dropna()
            .reset_index(drop=True)
        )

    except Exception:

        return None


# ============================================================
# 11. TECHNICAL INDICATORS
# ============================================================

def calculate_rsi(series, period=14):

    delta = series.diff()

    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)

    avg_gain = gain.ewm(
        alpha=1 / period,
        adjust=False
    ).mean()

    avg_loss = loss.ewm(
        alpha=1 / period,
        adjust=False
    ).mean()

    rs = avg_gain / avg_loss.replace(
        0,
        np.nan
    )

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
        adjust=False
    ).mean()


def calculate_vwap(df):

    typical = (
        df["high"]
        + df["low"]
        + df["close"]
    ) / 3

    volume = df["volume"]

    total_volume = volume.rolling(50).sum()

    vwap = (
        (typical * volume).rolling(50).sum()
        / total_volume.replace(0, np.nan)
    )

    return vwap


# ============================================================
# 12. MARKET STRUCTURE
# ============================================================

def analyze_structure(df):

    if df is None or len(df) < 30:

        return {
            "trend": "UNKNOWN",
            "breakout": "NONE",
            "sweep": "NONE",
            "support": None,
            "resistance": None
        }

    last = df.iloc[-1]

    previous = df.iloc[-21:-1]

    resistance = float(
        previous["high"].max()
    )

    support = float(
        previous["low"].min()
    )

    trend = "NEUTRAL"

    if last["close"] > previous["close"].mean():

        trend = "BULLISH"

    elif last["close"] < previous["close"].mean():

        trend = "BEARISH"

    breakout = "NONE"

    if last["close"] > resistance:

        breakout = "BULLISH BREAKOUT"

    elif last["close"] < support:

        breakout = "BEARISH BREAKDOWN"

    sweep = "NONE"

    if (
        last["low"] < support
        and last["close"] > support
    ):

        sweep = "SELL-SIDE LIQUIDITY SWEEP"

    elif (
        last["high"] > resistance
        and last["close"] < resistance
    ):

        sweep = "BUY-SIDE LIQUIDITY SWEEP"

    return {
        "trend": trend,
        "breakout": breakout,
        "sweep": sweep,
        "support": support,
        "resistance": resistance
    }


# ============================================================
# 13. ESTIMATED DELTA
# ============================================================

def calculate_estimated_delta(asset):

    if exchange is None:
        return None

    symbol = SYMBOLS[asset]

    try:

        trades = exchange.fetch_trades(
            symbol,
            limit=300
        )

        buy_volume = 0.0
        sell_volume = 0.0

        for trade in trades:

            amount = safe_float(
                trade.get("amount")
            )

            side = trade.get("side")

            if amount is None:
                continue

            if side == "buy":

                buy_volume += amount

            elif side == "sell":

                sell_volume += amount

        total = buy_volume + sell_volume

        if total <= 0:
            return None

        delta = buy_volume - sell_volume

        return {
            "buy": buy_volume,
            "sell": sell_volume,
            "delta": delta,
            "delta_percent": (
                delta / total
            ) * 100,
            "total": total
        }

    except Exception:

        return None


# ============================================================
# 14. TIMEFRAME ANALYSIS
# ============================================================

def analyze_timeframe(asset, timeframe):

    df = fetch_candles(
        asset,
        timeframe
    )

    if df is None or len(df) < 50:

        return {
            "asset": asset,
            "timeframe": timeframe,
            "direction": "WAIT",
            "score": 0,
            "error": "Insufficient candle data"
        }

    df["rsi"] = calculate_rsi(
        df["close"]
    )

    df["atr"] = calculate_atr(df)

    df["vwap"] = calculate_vwap(df)

    df["volume_avg"] = (
        df["volume"].rolling(20).mean()
    )

    last = df.iloc[-1]

    structure = analyze_structure(df)

    score = 0
    reasons = []

    # MOMENTUM

    momentum_5 = (
        (last["close"] / df.iloc[-6]["close"]) - 1
    ) * 100

    momentum_20 = (
        (last["close"] / df.iloc[-21]["close"]) - 1
    ) * 100

    if momentum_5 > 0:

        score += 10
        reasons.append("Positive short momentum")

    elif momentum_5 < 0:

        score -= 10
        reasons.append("Negative short momentum")

    if momentum_20 > 0:

        score += 10
        reasons.append("Positive medium momentum")

    elif momentum_20 < 0:

        score -= 10
        reasons.append("Negative medium momentum")

    # RSI

    rsi = float(last["rsi"])

    if 50 < rsi < 70:

        score += 10
        reasons.append("Bullish RSI")

    elif 30 < rsi < 50:

        score -= 10
        reasons.append("Bearish RSI")

    elif rsi >= 70:

        score -= 5
        reasons.append("RSI overbought")

    elif rsi <= 30:

        score += 5
        reasons.append("RSI oversold")

    # VWAP

    vwap = safe_float(last["vwap"])

    if vwap is not None:

        if last["close"] > vwap:

            score += 10
            reasons.append("Price above VWAP")

        elif last["close"] < vwap:

            score -= 10
            reasons.append("Price below VWAP")

    # VOLUME

    volume_avg = safe_float(
        last["volume_avg"]
    )

    volume_ratio = None

    if volume_avg is not None and volume_avg > 0:

        volume_ratio = (
            float(last["volume"]) / volume_avg
        )

        if volume_ratio > 1.5:

            if last["close"] > last["open"]:

                score += 10
                reasons.append("Strong bullish volume")

            elif last["close"] < last["open"]:

                score -= 10
                reasons.append("Strong bearish volume")

    # MARKET STRUCTURE

    if structure["breakout"] == "BULLISH BREAKOUT":

        score += 15
        reasons.append("Bullish structure breakout")

    elif structure["breakout"] == "BEARISH BREAKDOWN":

        score -= 15
        reasons.append("Bearish structure breakdown")

    # LIQUIDITY SWEEP

    if structure["sweep"] == "SELL-SIDE LIQUIDITY SWEEP":

        score += 10
        reasons.append("Sell-side liquidity sweep")

    elif structure["sweep"] == "BUY-SIDE LIQUIDITY SWEEP":

        score -= 10
        reasons.append("Buy-side liquidity sweep")

    score = max(
        -100,
        min(100, score)
    )

    if score >= MIN_SIGNAL_SCORE:

        direction = "LONG"

    elif score <= -MIN_SIGNAL_SCORE:

        direction = "SHORT"

    else:

        direction = "WAIT"

    return {
        "asset": asset,
        "timeframe": timeframe,
        "direction": direction,
        "score": score,
        "price": float(last["close"]),
        "rsi": rsi,
        "atr": safe_float(last["atr"]),
        "vwap": vwap,
        "momentum_5": momentum_5,
        "momentum_20": momentum_20,
        "volume_ratio": volume_ratio,
        "structure": structure,
        "reasons": reasons,
        "error": None
    }


# ============================================================
# 15. POSITION FLOW
# ============================================================

def determine_position_flow(market, delta):

    oi_change = market.get("oi_change")

    delta_percent = None

    if delta:

        delta_percent = delta.get(
            "delta_percent"
        )

    if oi_change is None or delta_percent is None:

        return {
            "state": "INSUFFICIENT DATA",
            "description": (
                "Az OI változás és a becsült delta "
                "együttes értelmezéséhez nincs elegendő adat."
            )
        }

    if oi_change > 0 and delta_percent > 5:

        return {
            "state": "LONG BUILDUP",
            "description": (
                "Növekvő Open Interest és pozitív becsült delta. "
                "Long irányú aktivitásra utalhat."
            )
        }

    if oi_change > 0 and delta_percent < -5:

        return {
            "state": "SHORT BUILDUP",
            "description": (
                "Növekvő Open Interest és negatív becsült delta. "
                "Short irányú aktivitásra utalhat."
            )
        }

    if oi_change < 0 and delta_percent > 5:

        return {
            "state": "SHORT CLOSING / BUYING",
            "description": (
                "Csökkenő Open Interest és pozitív delta. "
                "Short zárások is történhetnek."
            )
        }

    if oi_change < 0 and delta_percent < -5:

        return {
            "state": "LONG CLOSING / SELLING",
            "description": (
                "Csökkenő Open Interest és negatív delta. "
                "Long zárások is történhetnek."
            )
        }

    return {
        "state": "MIXED FLOW",
        "description": (
            "Az OI és a delta nem mutat egyértelmű irányt."
        )
    }


# ============================================================
# 16. MARKET SCORE
# ============================================================

def calculate_market_score(market, delta):

    score = 0
    factors = []

    oi_change = market.get("oi_change")

    if oi_change is not None:

        if oi_change > 1:

            factors.append("Open Interest rising")

        elif oi_change < -1:

            factors.append("Open Interest falling")

    if delta:

        d = delta.get("delta_percent")

        if d is not None:

            if d > 5:

                score += 15
                factors.append("Positive estimated delta")

            elif d < -5:

                score -= 15
                factors.append("Negative estimated delta")

    imbalance = market.get(
        "orderbook_imbalance"
    )

    if imbalance is not None:

        if imbalance > 10:

            score += 10
            factors.append("Bid-side orderbook dominance")

        elif imbalance < -10:

            score -= 10
            factors.append("Ask-side orderbook dominance")

    funding = market.get("funding")

    if funding is not None:

        if funding > 0.0005:

            factors.append("Elevated positive funding")

        elif funding < -0.0005:

            factors.append("Negative funding")

    return {
        "score": score,
        "factors": factors
    }


# ============================================================
# 17. TRADE PLAN
# ============================================================

def create_trade_plan(analysis):

    price = analysis.get("price")
    atr = analysis.get("atr")
    direction = analysis.get("direction")

    if (
        price is None
        or atr is None
        or atr <= 0
        or direction not in ["LONG", "SHORT"]
    ):

        return None

    risk = atr * 1.5

    if direction == "LONG":

        return {
            "entry": price,
            "stop_loss": price - risk,
            "tp1": price + risk,
            "tp2": price + risk * 2,
            "tp3": price + risk * 3
        }

    return {
        "entry": price,
        "stop_loss": price + risk,
        "tp1": price - risk,
        "tp2": price - risk * 2,
        "tp3": price - risk * 3
    }


# ============================================================
# 18. GROQ AI
# ============================================================

def get_groq_client():

    key = get_secret("GROQ_API_KEY")

    if not key:
        return None

    return OpenAI(
        api_key=key,
        base_url="https://api.groq.com/openai/v1"
    )


def run_groq_analysis(data):

    client = get_groq_client()

    if client is None:

        return None, "GROQ_API_KEY is missing"

    system_prompt = """
You are SALIGA AI PRO, a cryptocurrency market
intelligence analyst.

Analyze only the supplied OKX market data.

Do not invent missing data.
Do not treat rising Open Interest alone as proof
of long or short buildup.

Use estimated delta with appropriate caution.
Order book imbalance is a snapshot, not persistent flow.
Funding is contextual information, not a standalone signal.

Return valid JSON only.

Required structure:

{
  "overall_bias": "BULLISH/BEARISH/NEUTRAL",
  "confidence": 0,
  "summary": "",
  "position_flow": "",
  "bullish_factors": [],
  "bearish_factors": [],
  "risks": [],
  "assets": {
    "BTC": "",
    "ETH": "",
    "SOL": ""
  },
  "timeframe_outlook": {
    "M5": "",
    "M15": "",
    "H1": "",
    "H4": "",
    "D1": ""
  }
}

Never promise profits.
Never invent prices.
Never claim an order was executed.
"""

    try:

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
                        data,
                        ensure_ascii=False,
                        default=str
                    )
                }
            ],
            temperature=0.2,
            max_tokens=2500,
            response_format={
                "type": "json_object"
            }
        )

        content = response.choices[0].message.content

        if not content:

            return None, "Groq returned an empty response"

        result = json.loads(content)

        return result, None

    except Exception as e:

        return None, str(e)


# ============================================================
# 19. COMPLETE ANALYSIS CYCLE
# ============================================================

def run_analysis_cycle():

    market_results = {}
    timeframe_results = {}
    delta_results = {}
    flow_results = {}
    market_scores = {}

    for asset in SYMBOLS:

        market = fetch_market_data(asset)

        market_results[asset] = market

        delta = calculate_estimated_delta(asset)

        delta_results[asset] = delta

        flow_results[asset] = determine_position_flow(
            market,
            delta
        )

        market_scores[asset] = calculate_market_score(
            market,
            delta
        )

        timeframe_results[asset] = {}

        for timeframe in TIMEFRAMES:

            timeframe_results[asset][timeframe] = (
                analyze_timeframe(
                    asset,
                    timeframe
                )
            )

    return {
        "timestamp": timestamp(),
        "market": market_results,
        "delta": delta_results,
        "flow": flow_results,
        "market_scores": market_scores,
        "timeframes": timeframe_results
    }


# ============================================================
# 20. SIGNAL DATABASE
# ============================================================

def save_signal(asset, timeframe, analysis):

    if analysis.get("direction") not in [
        "LONG",
        "SHORT"
    ]:

        return False

    plan = create_trade_plan(analysis)

    if plan is None:
        return False

    conn = db_connection()

    try:

        cur = conn.cursor()

        cur.execute("""
            INSERT INTO signals (
                asset,
                timeframe,
                direction,
                score,
                entry,
                stop_loss,
                tp1,
                tp2,
                tp3,
                status,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            asset,
            timeframe,
            analysis["direction"],
            analysis["score"],
            plan["entry"],
            plan["stop_loss"],
            plan["tp1"],
            plan["tp2"],
            plan["tp3"],
            "OPEN",
            timestamp()
        ))

        conn.commit()

        return True

    finally:

        safe_close(conn)


def get_signals():

    conn = db_connection()

    try:

        return pd.read_sql_query("""
            SELECT *
            FROM signals
            ORDER BY id DESC
            LIMIT 100
        """, conn)

    finally:

        safe_close(conn)


# ============================================================
# 21. SIGNAL TRACKING
# ============================================================

def update_signal_status():

    if exchange is None:
        return

    conn = db_connection()

    try:

        cur = conn.cursor()

        cur.execute("""
            SELECT
                id,
                asset,
                direction,
                stop_loss,
                tp1,
                tp2,
                tp3,
                status
            FROM signals
            WHERE status IN (
                'OPEN',
                'TP1 HIT',
                'TP2 HIT'
            )
        """)

        signals = cur.fetchall()

        for row in signals:

            (
                signal_id,
                asset,
                direction,
                stop,
                tp1,
                tp2,
                tp3,
                status
            ) = row

            try:

                ticker = exchange.fetch_ticker(
                    SYMBOLS[asset]
                )

                price = safe_float(
                    ticker.get("last")
                )

                if price is None:
                    continue

                event = None
                new_status = status

                if direction == "LONG":

                    if price <= stop:

                        event = "STOP LOSS"
                        new_status = "STOP LOSS"

                    elif price >= tp3:

                        event = "TP3"
                        new_status = "TP3"

                    elif price >= tp2 and status != "TP2 HIT":

                        event = "TP2"
                        new_status = "TP2 HIT"

                    elif price >= tp1 and status == "OPEN":

                        event = "TP1"
                        new_status = "TP1 HIT"

                else:

                    if price >= stop:

                        event = "STOP LOSS"
                        new_status = "STOP LOSS"

                    elif price <= tp3:

                        event = "TP3"
                        new_status = "TP3"

                    elif price <= tp2 and status != "TP2 HIT":

                        event = "TP2"
                        new_status = "TP2 HIT"

                    elif price <= tp1 and status == "OPEN":

                        event = "TP1"
                        new_status = "TP1 HIT"

                if event:

                    cur.execute("""
                        INSERT INTO signal_events (
                            signal_id,
                            event,
                            price,
                            created_at
                        )
                        VALUES (?, ?, ?, ?)
                    """, (
                        signal_id,
                        event,
                        price,
                        timestamp()
                    ))

                    cur.execute("""
                        UPDATE signals
                        SET status = ?
                        WHERE id = ?
                    """, (
                        new_status,
                        signal_id
                    ))

            except Exception:
                continue

        conn.commit()

    finally:

        safe_close(conn)


# ============================================================
# 22. SIGNAL EVENTS
# ============================================================

def get_signal_events():

    conn = db_connection()

    try:

        return pd.read_sql_query("""
            SELECT *
            FROM signal_events
            ORDER BY id DESC
            LIMIT 100
        """, conn)

    finally:

        safe_close(conn)


# ============================================================
# 23. DASHBOARD HEADER
# ============================================================

st.title("⚡ SALIGA AI PRO")

st.caption(
    "MULTI-ASSET MARKET INTELLIGENCE | "
    "OKX DATA | GROQ AI | POSITION FLOW"
)

st.caption(
    f"Version: {APP_VERSION} | "
    f"UTC: {timestamp()}"
)

if exchange_error:

    st.error(
        "OKX connection error: "
        + exchange_error
    )


# ============================================================
# 24. DASHBOARD CONTROLS
# ============================================================

col1, col2, col3 = st.columns(3)

with col1:

    refresh_button = st.button(
        "🔄 REFRESH MARKET DATA",
        use_container_width=True
    )

with col2:

    ai_button = st.button(
        "🧠 RUN GROQ AI ANALYSIS",
        use_container_width=True
    )

with col3:

    st.metric(
        "Tracked assets",
        len(SYMBOLS)
    )


# ============================================================
# 25. MARKET ANALYSIS REFRESH
# ============================================================

should_refresh = (
    refresh_button
    or not st.session_state["analysis_data"]
    or (
        time.time()
        - st.session_state["last_refresh"]
        > MARKET_REFRESH
    )
)

if should_refresh:

    with st.spinner(
        "Downloading OKX data and analyzing BTC, ETH, SOL..."
    ):

        try:

            st.session_state["analysis_data"] = (
                run_analysis_cycle()
            )

            st.session_state["last_refresh"] = time.time()

            update_signal_status()

        except Exception as e:

            st.error(
                "Analysis cycle error: " + str(e)
            )


data = st.session_state["analysis_data"]


# ============================================================
# 26. MARKET OVERVIEW
# ============================================================

st.header("📊 MARKET OVERVIEW")

if data:

    market_data = data.get("market", {})

    cols = st.columns(3)

    for index, asset in enumerate(SYMBOLS):

        market = market_data.get(asset, {})

        with cols[index]:

            st.subheader(asset)

            st.metric(
                "Price",
                fmt(market.get("price"), 4)
            )

            percentage = market.get("percentage")

            st.metric(
                "24h change",
                (
                    fmt(percentage, 2) + "%"
                    if percentage is not None
                    else "N/A"
                )
            )

            st.metric(
                "Open Interest "
                + str(market.get("oi_unit", "N/A")),
                fmt(market.get("oi"), 2)
            )

            oi_change = market.get("oi_change")

            st.metric(
                "OI change",
                (
                    f"{oi_change:+.3f}%"
                    if oi_change is not None
                    else "Waiting for history"
                )
            )

            st.caption(
                "OI source: "
                + str(market.get("oi_source", "N/A"))
            )

            if market.get("oi_error"):

                with st.expander(
                    "Open Interest diagnostics"
                ):

                    st.error(
                        market["oi_error"]
                    )

            if market.get("error"):

                st.warning(
                    market["error"]
                )


# ============================================================
# 27. POSITION FLOW
# ============================================================

st.header("🌊 POSITION FLOW")

if data:

    cols = st.columns(3)

    for index, asset in enumerate(SYMBOLS):

        market = data["market"].get(
            asset,
            {}
        )

        delta = data["delta"].get(
            asset
        )

        flow = data["flow"].get(
            asset,
            {}
        )

        with cols[index]:

            st.subheader(asset)

            st.markdown(
                f"**{flow.get('state', 'N/A')}**"
            )

            st.write(
                flow.get("description", "")
            )

            if delta:

                st.metric(
                    "Estimated delta",
                    fmt(delta.get("delta"), 4)
                )

                delta_percent = delta.get(
                    "delta_percent"
                )

                st.metric(
                    "Delta %",
                    (
                        fmt(delta_percent, 2) + "%"
                        if delta_percent is not None
                        else "N/A"
                    )
                )

                st.write(
                    "Buy volume:",
                    fmt(delta.get("buy"), 4)
                )

                st.write(
                    "Sell volume:",
                    fmt(delta.get("sell"), 4)
                )

            else:

                st.warning(
                    "Estimated delta unavailable"
                )

            imbalance = market.get(
                "orderbook_imbalance"
            )

            st.metric(
                "Order book imbalance",
                (
                    fmt(imbalance, 2) + "%"
                    if imbalance is not None
                    else "N/A"
                )
            )

            funding = market.get("funding")

            st.metric(
                "Funding rate",
                (
                    fmt(funding * 100, 5) + "%"
                    if funding is not None
                    else "N/A"
                )
            )


# ============================================================
# 28. MARKET SCORE
# ============================================================

st.header("📈 MARKET INTELLIGENCE SCORE")

if data:

    cols = st.columns(3)

    for index, asset in enumerate(SYMBOLS):

        market_score = data["market_scores"].get(
            asset,
            {}
        )

        with cols[index]:

            st.subheader(asset)

            st.metric(
                "Market score",
                fmt(
                    market_score.get("score", 0),
                    0
                )
            )

            for factor in market_score.get(
                "factors",
                []
            ):

                st.write("• " + factor)


# ============================================================
# 29. MULTI-TIMEFRAME ANALYSIS
# ============================================================

st.header("🧭 MULTI-TIMEFRAME ANALYSIS")

if data:

    for asset in SYMBOLS:

        st.subheader(asset)

        rows = []

        for timeframe in TIMEFRAMES:

            analysis = data["timeframes"][
                asset
            ][timeframe]

            structure = analysis.get(
                "structure",
                {}
            )

            rows.append({
                "Timeframe": timeframe,
                "Direction": analysis.get(
                    "direction",
                    "WAIT"
                ),
                "Score": analysis.get(
                    "score",
                    0
                ),
                "Price": analysis.get(
                    "price"
                ),
                "RSI": analysis.get(
                    "rsi"
                ),
                "Momentum 5": analysis.get(
                    "momentum_5"
                ),
                "Momentum 20": analysis.get(
                    "momentum_20"
                ),
                "Structure": structure.get(
                    "trend",
                    "N/A"
                )
            })

        st.dataframe(
            pd.DataFrame(rows),
            use_container_width=True,
            hide_index=True
        )


# ============================================================
# 30. DETAILED ANALYSIS
# ============================================================

st.header("🔍 DETAILED ANALYSIS")

selected_asset = st.selectbox(
    "Select asset",
    list(SYMBOLS.keys())
)

selected_tf = st.selectbox(
    "Select timeframe",
    list(TIMEFRAMES.keys())
)

if data:

    detail = data["timeframes"][
        selected_asset
    ][selected_tf]

    if detail.get("error"):

        st.error(
            detail["error"]
        )

    else:

        c1, c2, c3 = st.columns(3)

        with c1:

            st.metric(
                "Direction",
                detail["direction"]
            )

        with c2:

            st.metric(
                "Score",
                fmt(detail["score"], 0)
            )

        with c3:

            st.metric(
                "RSI",
                fmt(detail["rsi"])
            )

        st.write(
            "Market structure:",
            detail.get("structure", {})
        )

        st.write("Analysis factors:")

        for reason in detail.get("reasons", []):

            st.write("• " + reason)

        plan = create_trade_plan(detail)

        if plan:

            st.subheader(
                "Reference trade plan"
            )

            st.json(plan)

            save_key = (
                "save_"
                + selected_asset
                + "_"
                + selected_tf
            )

            if st.button(
                "SAVE SIGNAL",
                key=save_key
            ):

                saved = save_signal(
                    selected_asset,
                    selected_tf,
                    detail
                )

                if saved:

                    st.success(
                        "Signal saved to journal."
                    )

                else:

                    st.warning(
                        "Signal could not be saved."
                    )


# ============================================================
# 31. GROQ AI ANALYSIS
# ============================================================

st.header("🧠 GROQ AI MARKET INTELLIGENCE")

if ai_button and data:

    with st.spinner(
        "Groq AI is analyzing the market..."
    ):

        result, error = run_groq_analysis(data)

        st.session_state["ai_result"] = result
        st.session_state["ai_error"] = error


if st.session_state["ai_error"]:

    st.error(
        "Groq AI error: "
        + str(st.session_state["ai_error"])
    )


if st.session_state["ai_result"]:

    ai = st.session_state["ai_result"]

    st.subheader(
        "Overall market bias: "
        + str(ai.get("overall_bias", "N/A"))
    )

    st.metric(
        "AI confidence",
        str(ai.get("confidence", "N/A"))
    )

    st.write(
        ai.get("summary", "")
    )

    st.subheader("Position flow")

    st.write(
        ai.get("position_flow", "")
    )

    col1, col2 = st.columns(2)

    with col1:

        st.markdown("### Bullish factors")

        for item in ai.get(
            "bullish_factors",
            []
        ):

            st.write("🟢 " + str(item))

    with col2:

        st.markdown("### Bearish factors")

        for item in ai.get(
            "bearish_factors",
            []
        ):

            st.write("🔴 " + str(item))

    st.subheader("Risks")

    for item in ai.get("risks", []):

        st.write("⚠️ " + str(item))

    st.subheader("Asset outlook")

    st.json(
        ai.get("assets", {})
    )

    st.subheader("Timeframe outlook")

    st.json(
        ai.get("timeframe_outlook", {})
    )

else:

    st.info(
        "Press RUN GROQ AI ANALYSIS to generate "
        "the market intelligence report."
    )


# ============================================================
# 32. SIGNAL JOURNAL
# ============================================================

st.header("📒 SIGNAL JOURNAL")

signals = get_signals()

if not signals.empty:

    st.dataframe(
        signals,
        use_container_width=True,
        hide_index=True
    )

else:

    st.info(
        "No saved signals yet."
    )


# ============================================================
# 33. SIGNAL EVENTS
# ============================================================

st.header("📌 SIGNAL EVENTS")

events = get_signal_events()

if not events.empty:

    st.dataframe(
        events,
        use_container_width=True,
        hide_index=True
    )

else:

    st.info(
        "No TP/SL events recorded yet."
    )


# ============================================================
# 34. SYSTEM STATUS
# ============================================================

st.header("⚙️ SYSTEM STATUS")

c1, c2, c3 = st.columns(3)

with c1:

    st.metric(
        "OKX connection",
        "ONLINE" if exchange else "OFFLINE"
    )

with c2:

    st.metric(
        "Groq API key",
        (
            "CONFIGURED"
            if get_secret("GROQ_API_KEY")
            else "MISSING"
        )
    )

with c3:

    st.metric(
        "Tracked assets",
        "BTC / ETH / SOL"
    )

st.caption(
    "Data provider: OKX only. "
    "AI provider: Groq. "
    "Estimated delta uses recent trades. "
    "Order book imbalance is a snapshot. "
    "Trade plans are analytical references, "
    "not executed orders."
)

st.caption(
    "SALIGA AI PRO 17.1 | "
    "Market intelligence, not financial advice."
)
