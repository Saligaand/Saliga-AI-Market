import streamlit as st
import ccxt
import pandas as pd
import numpy as np
import sqlite3
import json
import time
import os
from datetime import datetime, timezone
from openai import OpenAI

# ============================================================
# SALIGA AI PRO - HYBRID INTELLIGENCE 14.1
# OKX ONLY | GROQ AI | OI FIX
# MARKET + TP/SL: 60 SEC
# GROQ AI: 15 MINUTES
# NO EMA / NO SMA
# ============================================================

st.set_page_config(
    page_title="SALIGA AI PRO",
    page_icon="⚡",
    layout="wide"
)

APP_VERSION = "HYBRID PRO 14.1"
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

THRESHOLDS = {
    "M5": 2.5,
    "M15": 3.0,
    "H1": 3.5,
    "H4": 4.0,
    "D1": 4.5
}

GROQ_MODEL = "openai/gpt-oss-120b"

MARKET_REFRESH_SECONDS = 60
SIGNAL_REFRESH_SECONDS = 60
GROQ_REFRESH_SECONDS = 900
OI_SAMPLE_SECONDS = 300
GROQ_RETRY_SECONDS = 900
GROQ_MAX_TOKENS = 450


# ============================================================
# DATABASE
# ============================================================

def db():
    return sqlite3.connect(
        DB_PATH,
        check_same_thread=False,
        timeout=20
    )


def init_db():
    con = db()
    cur = con.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS signals_v2 (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            signal_key TEXT UNIQUE,
            symbol TEXT,
            timeframe TEXT,
            direction TEXT,
            entry REAL,
            sl REAL,
            tp1 REAL,
            tp2 REAL,
            tp3 REAL,
            score REAL,
            ai_direction TEXT,
            status TEXT,
            created_at TEXT
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS signal_events_v2 (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            signal_key TEXT,
            event TEXT,
            price REAL,
            created_at TEXT
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS market_history_v2 (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT,
            oi_value REAL,
            price REAL,
            created_at REAL,
            oi_unit TEXT DEFAULT 'UNKNOWN'
        )
    """)

    # Régebbi adatbázis kompatibilitás
    columns = [
        row[1] for row in cur.execute(
            "PRAGMA table_info(market_history_v2)"
        ).fetchall()
    ]

    if "oi_unit" not in columns:
        cur.execute("""
            ALTER TABLE market_history_v2
            ADD COLUMN oi_unit TEXT DEFAULT 'UNKNOWN'
        """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS app_state (
            name TEXT PRIMARY KEY,
            value TEXT
        )
    """)

    con.commit()
    con.close()


init_db()


# ============================================================
# SESSION STATE
# ============================================================

defaults = {
    "saliga_ai_cache": {},
    "saliga_data": None,
    "signal_refresh_count": 0,
    "last_signal_refresh": None,
    "groq_cooldown_until": 0,
    "force_ai": False,
    "last_market_refresh": 0
}

for key, value in defaults.items():
    if key not in st.session_state:
        st.session_state[key] = value


# ============================================================
# SECRETS
# ============================================================

def get_secret(name):
    try:
        return st.secrets.get(name, "")
    except Exception:
        return os.getenv(name, "")


GROQ_API_KEY = get_secret("GROQ_API_KEY")


# ============================================================
# OKX CONNECTION
# ============================================================

@st.cache_resource
def get_exchange():
    ex = ccxt.okx({
        "enableRateLimit": True,
        "options": {
            "defaultType": "swap"
        },
        "timeout": 20000
    })
    ex.load_markets()
    return ex


exchange = get_exchange()


# ============================================================
# SAFE HELPERS
# ============================================================

def safe_float(value, default=None):
    try:
        if value is None:
            return default
        result = float(value)
        if not np.isfinite(result):
            return default
        return result
    except Exception:
        return default


def fmt(value, digits=2):
    if value is None:
        return "N/A"
    try:
        return f"{float(value):,.{digits}f}"
    except Exception:
        return "N/A"


def utc_now():
    return datetime.now(timezone.utc).isoformat()


# ============================================================
# OPEN INTEREST NORMALIZATION - OKX FIX
# ============================================================

def extract_oi(oi_data):
    """
    OKX Open Interest válasz feldolgozása.
    Elsődlegesen USD, utána BASE, végül kontraktus.
    """

    if not oi_data:
        return {
            "value": None,
            "unit": "N/A",
            "raw": None
        }

    data = oi_data

    if isinstance(data, dict):

        if isinstance(data.get("data"), list):
            if data["data"]:
                data = data["data"][0]

        elif isinstance(data.get("info"), dict):
            info = data["info"]

            if isinstance(info.get("data"), list):
                if info["data"]:
                    data = info["data"][0]
            else:
                data = info

    if isinstance(data, list):
        data = data[0] if data else None

    if not isinstance(data, dict):
        return {
            "value": None,
            "unit": "N/A",
            "raw": oi_data
        }

    # OKX mezők
    fields = [
        ("oiUsd", "USD"),
        ("oiCcy", "BASE"),
        ("oi", "CONTRACTS"),
        ("openInterestValue", "USD"),
        ("openInterestAmount", "BASE"),
        ("openInterest", "UNKNOWN")
    ]

    for field, unit in fields:
        value = safe_float(data.get(field))

        if value is not None and value > 0:
            return {
                "value": value,
                "unit": unit,
                "raw": data
            }

    # Egyes CCXT válaszoknál az info mezőben található
    info = data.get("info")

    if isinstance(info, dict):
        for field, unit in fields:
            value = safe_float(info.get(field))

            if value is not None and value > 0:
                return {
                    "value": value,
                    "unit": unit,
                    "raw": info
                }

    return {
        "value": None,
        "unit": "N/A",
        "raw": data
    }


# ============================================================
# OKX OPEN INTEREST REQUEST
# ============================================================

def fetch_okx_oi(symbol):
    """
    Közvetlen OKX SWAP OI lekérés.
    Ha a közvetlen lekérés sikertelen, CCXT fallback.
    """

    errors = []
    result = None

    try:
        market = exchange.market(symbol)
        inst_id = market["id"]

        method = getattr(
            exchange,
            "publicGetPublicOpenInterest",
            None
        )

        if method is None:
            method = getattr(
                exchange,
                "public_get_public_open_interest",
                None
            )

        if method is not None:
            result = method({
                "instType": "SWAP",
                "instId": inst_id
            })

            if isinstance(result, dict):
                if str(result.get("code", "0")) != "0":
                    raise Exception(
                        str(result.get("msg", "OKX OI API hiba"))
                    )

            parsed = extract_oi(result)

            if parsed["value"] is not None:
                return result, errors

            errors.append(
                "OKX közvetlen OI válaszában nem található érték."
            )
        else:
            errors.append(
                "OKX közvetlen OI metódus nem található."
            )

    except Exception as e:
        errors.append("Közvetlen OI: " + str(e))

    try:
        fallback = exchange.fetch_open_interest(symbol)

        parsed = extract_oi(fallback)

        if parsed["value"] is not None:
            return fallback, errors

        errors.append(
            "CCXT OI válaszában sem található érték."
        )

    except Exception as e:
        errors.append("CCXT OI fallback: " + str(e))

    return None, errors


# ============================================================
# MARKET DATA
# ============================================================

@st.cache_data(ttl=15, show_spinner=False)
def fetch_market_data(symbol):

    result = {
        "ticker": None,
        "orderbook": None,
        "funding": None,
        "oi": None,
        "trades": None,
        "errors": []
    }

    try:
        result["ticker"] = exchange.fetch_ticker(symbol)
    except Exception as e:
        result["errors"].append("Ticker: " + str(e))

    try:
        result["orderbook"] = exchange.fetch_order_book(
            symbol,
            limit=50
        )
    except Exception as e:
        result["errors"].append("Order book: " + str(e))

    try:
        result["funding"] = exchange.fetch_funding_rate(symbol)
    except Exception as e:
        result["errors"].append("Funding: " + str(e))

    # JAVÍTOTT OKX OI
    oi_result, oi_errors = fetch_okx_oi(symbol)
    result["oi"] = oi_result
    result["errors"].extend(oi_errors)

    try:
        result["trades"] = exchange.fetch_trades(
            symbol,
            limit=300
        )
    except Exception as e:
        result["errors"].append("Trades: " + str(e))

    return result


@st.cache_data(ttl=30, show_spinner=False)
def fetch_candles(symbol, timeframe):
    return exchange.fetch_ohlcv(
        symbol,
        timeframe=timeframe,
        limit=250
    )


# ============================================================
# OI HISTORY
# ============================================================

def save_oi_snapshot(symbol, oi_value, price, unit):

    if oi_value is None:
        return

    con = db()
    cur = con.cursor()

    cur.execute("""
        SELECT created_at
        FROM market_history_v2
        WHERE symbol = ?
        ORDER BY created_at DESC
        LIMIT 1
    """, (symbol,))

    row = cur.fetchone()
    now = time.time()

    if row and now - float(row[0]) < OI_SAMPLE_SECONDS:
        con.close()
        return

    cur.execute("""
        INSERT INTO market_history_v2
        (symbol, oi_value, price, created_at, oi_unit)
        VALUES (?, ?, ?, ?, ?)
    """, (
        symbol,
        oi_value,
        price,
        now,
        unit
    ))

    con.commit()
    con.close()


def get_oi_change(symbol, current_oi, unit):

    if current_oi is None:
        return None

    con = db()
    cur = con.cursor()

    target_time = time.time() - OI_SAMPLE_SECONDS

    cur.execute("""
        SELECT oi_value
        FROM market_history_v2
        WHERE symbol = ?
        AND oi_unit = ?
        AND created_at <= ?
        ORDER BY created_at DESC
        LIMIT 1
    """, (
        symbol,
        unit,
        target_time
    ))

    row = cur.fetchone()
    con.close()

    if not row:
        return None

    previous = safe_float(row[0])

    if previous is None or previous <= 0:
        return None

    return ((current_oi / previous) - 1) * 100


# ============================================================
# ORDER BOOK
# ============================================================

def analyze_orderbook(orderbook):

    if not orderbook:
        return {
            "imbalance": None,
            "bid_volume": None,
            "ask_volume": None,
            "bias": "N/A"
        }

    bids = orderbook.get("bids", [])[:20]
    asks = orderbook.get("asks", [])[:20]

    bid_volume = sum(
        safe_float(x[1], 0) for x in bids
    )

    ask_volume = sum(
        safe_float(x[1], 0) for x in asks
    )

    total = bid_volume + ask_volume

    imbalance = (
        ((bid_volume - ask_volume) / total) * 100
        if total > 0 else None
    )

    if imbalance is None:
        bias = "N/A"
    elif imbalance > 10:
        bias = "BUY PRESSURE"
    elif imbalance < -10:
        bias = "SELL PRESSURE"
    else:
        bias = "BALANCED"

    return {
        "imbalance": imbalance,
        "bid_volume": bid_volume,
        "ask_volume": ask_volume,
        "bias": bias
    }


# ============================================================
# ESTIMATED DELTA
# ============================================================

def calculate_delta(trades):

    if not trades:
        return {
            "delta": None,
            "buy": None,
            "sell": None
        }

    buy = 0.0
    sell = 0.0

    for trade in trades:
        amount = safe_float(trade.get("amount"), 0)

        if trade.get("side") == "buy":
            buy += amount
        elif trade.get("side") == "sell":
            sell += amount

    total = buy + sell

    delta = (
        ((buy - sell) / total) * 100
        if total > 0 else None
    )

    return {
        "delta": delta,
        "buy": buy,
        "sell": sell
    }


# ============================================================
# CANDLE ANALYSIS
# ============================================================

def analyze_candles(raw):

    if not raw or len(raw) < 60:
        raise ValueError("Nincs elegendő gyertyaadat.")

    df = pd.DataFrame(
        raw,
        columns=[
            "timestamp",
            "open",
            "high",
            "low",
            "close",
            "volume"
        ]
    )

    # Csak lezárt gyertyák
    df = df.iloc[:-1].copy()

    if len(df) < 50:
        raise ValueError("Kevés lezárt gyertya.")

    for col in [
        "open", "high", "low", "close", "volume"
    ]:
        df[col] = pd.to_numeric(
            df[col],
            errors="coerce"
        )

    df = df.dropna().reset_index(drop=True)

    close = df["close"]
    high = df["high"]
    low = df["low"]
    volume = df["volume"]

    # RSI
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)

    avg_gain = gain.rolling(14).mean()
    avg_loss = loss.rolling(14).mean()

    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))

    # ATR
    previous_close = close.shift(1)

    tr = pd.concat([
        high - low,
        (high - previous_close).abs(),
        (low - previous_close).abs()
    ], axis=1).max(axis=1)

    atr = tr.rolling(14).mean()

    # VWAP
    typical = (high + low + close) / 3

    vwap = (
        (typical * volume).rolling(30).sum()
        /
        volume.rolling(30).sum().replace(0, np.nan)
    )

    # Volume ratio
    volume_reference = volume.iloc[-21:-1].mean()

    volume_ratio = (
        volume.iloc[-1] / volume_reference
        if volume_reference and volume_reference > 0
        else None
    )

    # Momentum
    momentum_5 = (
        (close.iloc[-1] / close.iloc[-6]) - 1
    ) * 100

    momentum_20 = (
        (close.iloc[-1] / close.iloc[-21]) - 1
    ) * 100

    # Market structure
    recent_high = high.iloc[-21:-1].max()
    recent_low = low.iloc[-21:-1].min()

    current_close = close.iloc[-1]

    previous_high = high.iloc[-11:-1].max()
    previous_low = low.iloc[-11:-1].min()

    structure = "RANGE"

    if current_close > previous_high:
        structure = "BULLISH BREAK"
    elif current_close < previous_low:
        structure = "BEARISH BREAK"

    # Liquidity sweep
    last_high = high.iloc[-1]
    last_low = low.iloc[-1]

    sweep = "NONE"

    if last_high > recent_high and current_close < recent_high:
        sweep = "BUY-SIDE SWEEP"

    elif last_low < recent_low and current_close > recent_low:
        sweep = "SELL-SIDE SWEEP"

    return {
        "price": float(current_close),
        "rsi": safe_float(rsi.iloc[-1]),
        "atr": safe_float(atr.iloc[-1]),
        "vwap": safe_float(vwap.iloc[-1]),
        "volume_ratio": safe_float(volume_ratio),
        "momentum_5": safe_float(momentum_5),
        "momentum_20": safe_float(momentum_20),
        "structure": structure,
        "sweep": sweep,
        "recent_high": float(recent_high),
        "recent_low": float(recent_low),
        "candle_time": int(df["timestamp"].iloc[-1])
    }


# ============================================================
# MATHEMATICAL MARKET SCORE
# ============================================================

def calculate_score(candle, market):

    long_score = 0.0
    short_score = 0.0
    reasons = []

    rsi = candle["rsi"]
    momentum = candle["momentum_5"]
    momentum20 = candle["momentum_20"]
    volume_ratio = candle["volume_ratio"]

    if rsi is not None:
        if 52 <= rsi <= 70:
            long_score += 1
            reasons.append("RSI bullish")
        elif 30 <= rsi <= 48:
            short_score += 1
            reasons.append("RSI bearish")

    if momentum is not None:
        if momentum > 0.15:
            long_score += 1
            reasons.append("Positive short momentum")
        elif momentum < -0.15:
            short_score += 1
            reasons.append("Negative short momentum")

    if momentum20 is not None:
        if momentum20 > 0.4:
            long_score += 1
            reasons.append("Positive medium momentum")
        elif momentum20 < -0.4:
            short_score += 1
            reasons.append("Negative medium momentum")

    if candle["structure"] == "BULLISH BREAK":
        long_score += 1.5
        reasons.append("Bullish structure")

    elif candle["structure"] == "BEARISH BREAK":
        short_score += 1.5
        reasons.append("Bearish structure")

    if candle["sweep"] == "SELL-SIDE SWEEP":
        long_score += 1
        reasons.append("Sell-side liquidity sweep")

    elif candle["sweep"] == "BUY-SIDE SWEEP":
        short_score += 1
        reasons.append("Buy-side liquidity sweep")

    if volume_ratio is not None and volume_ratio > 1.3:
        if momentum is not None and momentum > 0:
            long_score += 0.5
        elif momentum is not None and momentum < 0:
            short_score += 0.5

    delta = market.get("delta")

    if delta is not None:
        if delta > 10:
            long_score += 1
            reasons.append("Positive estimated delta")
        elif delta < -10:
            short_score += 1
            reasons.append("Negative estimated delta")

    imbalance = market.get("imbalance")

    if imbalance is not None:
        if imbalance > 10:
            long_score += 0.5
            reasons.append("Order book buy pressure")
        elif imbalance < -10:
            short_score += 0.5
            reasons.append("Order book sell pressure")

    oi_change = market.get("oi_change")

    if oi_change is not None and oi_change > 0.5:
        if momentum is not None and momentum > 0:
            long_score += 0.5
            reasons.append("OI rising with positive momentum")
        elif momentum is not None and momentum < 0:
            short_score += 0.5
            reasons.append("OI rising with negative momentum")

    funding = market.get("funding")

    if funding is not None:
        if funding > 0.05:
            short_score += 0.25
            reasons.append("High positive funding")
        elif funding < -0.05:
            long_score += 0.25
            reasons.append("Negative funding")

    net_score = long_score - short_score

    if net_score >= 0:
        direction = "LONG"
        strength = net_score
    else:
        direction = "SHORT"
        strength = abs(net_score)

    threshold = market.get("threshold", 3)

    if strength < threshold:
        direction = "WAIT"

    return {
        "long_score": round(long_score, 2),
        "short_score": round(short_score, 2),
        "score": round(net_score, 2),
        "strength": round(strength, 2),
        "direction": direction,
        "threshold": threshold,
        "reasons": reasons
    }


# ============================================================
# ENTRY / STOP LOSS / TAKE PROFIT
# ============================================================

def create_trade_plan(direction, candle):

    price = candle["price"]
    atr = candle["atr"]

    if not atr or atr <= 0:
        return None

    if direction == "LONG":
        sl = price - atr * 1.5
        risk = price - sl

        return {
            "entry": price,
            "sl": sl,
            "tp1": price + risk,
            "tp2": price + risk * 2,
            "tp3": price + risk * 3
        }

    if direction == "SHORT":
        sl = price + atr * 1.5
        risk = sl - price

        return {
            "entry": price,
            "sl": sl,
            "tp1": price - risk,
            "tp2": price - risk * 2,
            "tp3": price - risk * 3
        }

    return None


# ============================================================
# FULL MARKET ANALYSIS
# ============================================================

def build_analysis():

    all_data = {}
    global_errors = []

    for asset, symbol in SYMBOLS.items():

        market_raw = fetch_market_data(symbol)

        ticker = market_raw["ticker"]

        if not ticker:
            global_errors.append(
                asset + ": OKX ticker nem érhető el."
            )
            continue

        price = safe_float(ticker.get("last"))

        if price is None:
            global_errors.append(
                asset + ": nincs árfolyamadat."
            )
            continue

        oi_info = extract_oi(market_raw["oi"])

        oi_value = oi_info["value"]
        oi_unit = oi_info["unit"]

        oi_change = get_oi_change(
            asset,
            oi_value,
            oi_unit
        )

        save_oi_snapshot(
            asset,
            oi_value,
            price,
            oi_unit
        )

        delta = calculate_delta(market_raw["trades"])
        book = analyze_orderbook(market_raw["orderbook"])

        funding = None

        if market_raw["funding"]:
            funding = safe_float(
                market_raw["funding"].get("fundingRate")
            )

            if funding is not None:
                funding *= 100

        market_context = {
            "price": price,
            "oi": oi_value,
            "oi_unit": oi_unit,
            "oi_change": oi_change,
            "delta": delta["delta"],
            "buy_volume": delta["buy"],
            "sell_volume": delta["sell"],
            "imbalance": book["imbalance"],
            "book_bias": book["bias"],
            "funding": funding
        }

        timeframe_results = {}

        for tf_name, tf_value in TIMEFRAMES.items():

            try:
                raw = fetch_candles(symbol, tf_value)
                candle = analyze_candles(raw)

                score_market = dict(market_context)
                score_market["threshold"] = THRESHOLDS[tf_name]

                score = calculate_score(
                    candle,
                    score_market
                )

                plan = create_trade_plan(
                    score["direction"],
                    candle
                )

                timeframe_results[tf_name] = {
                    "candle": candle,
                    "score": score,
                    "plan": plan,
                    "ai": None,
                    "final_direction": "WAIT"
                }

            except Exception as e:
                timeframe_results[tf_name] = {
                    "error": str(e),
                    "candle": None,
                    "score": None,
                    "plan": None,
                    "ai": None,
                    "final_direction": "WAIT"
                }

        all_data[asset] = {
            "symbol": symbol,
            "market": market_context,
            "timeframes": timeframe_results,
            "errors": market_raw["errors"]
        }

    return all_data, global_errors


# ============================================================
# GROQ CLIENT
# ============================================================

def get_groq_client():

    if not GROQ_API_KEY:
        return None

    return OpenAI(
        api_key=GROQ_API_KEY,
        base_url="https://api.groq.com/openai/v1",
        timeout=45
    )


# ============================================================
# GROQ AI ANALYSIS
# ============================================================

def groq_analyze_asset(asset, data):

    client = get_groq_client()

    if client is None:
        return None, "GROQ_API_KEY nincs beállítva."

    compact = {
        "asset": asset,
        "market": data["market"],
        "timeframes": {}
    }

    for tf, item in data["timeframes"].items():

        if not item.get("score"):
            continue

        compact["timeframes"][tf] = {
            "price": item["candle"]["price"],
            "rsi": item["candle"]["rsi"],
            "momentum_5": item["candle"]["momentum_5"],
            "momentum_20": item["candle"]["momentum_20"],
            "volume_ratio": item["candle"]["volume_ratio"],
            "structure": item["candle"]["structure"],
            "sweep": item["candle"]["sweep"],
            "long_score": item["score"]["long_score"],
            "short_score": item["score"]["short_score"],
            "engine_direction": item["score"]["direction"]
        }

    system_prompt = """
Te a SALIGA AI PRO OKX kriptopiaci elemzőmotorja vagy.
Kizárólag a kapott adatokból dolgozz, ne találj ki értékeket.
Vizsgáld a több idősíkot, OI-t, becsült deltát, fundingot,
order bookot, struktúrát és likviditási sweepet.
Az OI önmagában nem bizonyítja, hogy long vagy short pozíciók épülnek.
Az order book pillanatfelvétel, a delta becsült kötésalapú adat.
Ellentmondó jelek esetén WAIT.
Magyarul válaszolj.
Kizárólag érvényes JSON-t adj vissza ebben a formában:

{
 "overall_bias":"LONG vagy SHORT vagy WAIT",
 "confidence":0,
 "market_summary":"rövid magyar elemzés",
 "position_flow":"rövid magyar elemzés",
 "long_factors":["..."],
 "short_factors":["..."],
 "risks":["..."],
 "timeframes":{
  "M5":{"bias":"LONG vagy SHORT vagy WAIT","analysis":"..."},
  "M15":{"bias":"LONG vagy SHORT vagy WAIT","analysis":"..."},
  "H1":{"bias":"LONG vagy SHORT vagy WAIT","analysis":"..."},
  "H4":{"bias":"LONG vagy SHORT vagy WAIT","analysis":"..."},
  "D1":{"bias":"LONG vagy SHORT vagy WAIT","analysis":"..."}
 }
}
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
                        compact,
                        ensure_ascii=False,
                        default=str
                    )
                }
            ],
            response_format={"type": "json_object"},
            max_completion_tokens=GROQ_MAX_TOKENS
        )

        content = response.choices[0].message.content
        parsed = json.loads(content)

        return parsed, None

    except Exception as e:

        error = str(e)

        # Globális Groq cooldown 429 esetén
        if (
            "429" in error
            or "rate_limit" in error.lower()
            or "tokens per day" in error.lower()
        ):
            st.session_state.groq_cooldown_until = (
                time.time() + GROQ_RETRY_SECONDS
            )

        return None, error


# ============================================================
# GROQ 15 MINUTE CONTROL
# ============================================================

def get_asset_ai(asset, data, force_ai=False):

    cache = st.session_state.saliga_ai_cache
    previous = cache.get(asset)
    now = time.time()

    # Központi Groq korlátozás
    cooldown = st.session_state.groq_cooldown_until

    if not force_ai and now < cooldown:
        return (
            previous.get("result") if previous else None,
            "Groq korlátozás: várakozás a következő próbálkozásig."
        )

    # 15 percenként egy AI-hívás eszközönként
    if (
        not force_ai
        and previous
        and previous.get("attempted_at")
        and now - previous["attempted_at"] < GROQ_REFRESH_SECONDS
    ):
        return (
            previous.get("result"),
            previous.get("error")
        )

    result, error = groq_analyze_asset(
        asset,
        data
    )

    cache[asset] = {
        "result": result,
        "error": error,
        "attempted_at": now,
        "updated_at": utc_now()
    }

    st.session_state.saliga_ai_cache = cache

    return result, error


# ============================================================
# HYBRID VALIDATION
# ============================================================

def apply_ai_validation(data, ai_result):

    for tf, item in data["timeframes"].items():

        score = item.get("score")

        if not score:
            item["final_direction"] = "WAIT"
            continue

        engine_direction = score["direction"]

        ai_tf = {}

        if ai_result:
            ai_tf = (
                ai_result.get("timeframes", {}).get(tf, {})
            )

        ai_direction = ai_tf.get("bias", "WAIT")

        item["ai"] = ai_tf
        item["ai_direction"] = ai_direction

        if (
            engine_direction in ["LONG", "SHORT"]
            and ai_direction == engine_direction
            and item.get("plan")
        ):
            item["final_direction"] = engine_direction
        else:
            item["final_direction"] = "WAIT"


# ============================================================
# SIGNAL JOURNAL
# ============================================================

def save_signal(asset, tf, item):

    direction = item.get("final_direction")

    if direction not in ["LONG", "SHORT"]:
        return

    candle = item["candle"]
    plan = item["plan"]
    score = item["score"]

    key = (
        f"{asset}_{tf}_{direction}_"
        f"{candle['candle_time']}"
    )

    con = db()
    cur = con.cursor()

    cur.execute("""
        INSERT OR IGNORE INTO signals_v2 (
            signal_key,
            symbol,
            timeframe,
            direction,
            entry,
            sl,
            tp1,
            tp2,
            tp3,
            score,
            ai_direction,
            status,
            created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        key,
        asset,
        tf,
        direction,
        plan["entry"],
        plan["sl"],
        plan["tp1"],
        plan["tp2"],
        plan["tp3"],
        score["score"],
        item.get("ai_direction"),
        "OPEN",
        utc_now()
    ))

    con.commit()
    con.close()


# ============================================================
# TP / SL TRACKING
# ============================================================

def track_signals(prices=None):

    if prices is None:
        prices = {}

    con = db()
    cur = con.cursor()

    cur.execute("""
        SELECT
            signal_key,
            symbol,
            direction,
            entry,
            sl,
            tp1,
            tp2,
            tp3,
            status
        FROM signals_v2
        WHERE status IN ('OPEN', 'TP1', 'TP2')
    """)

    rows = cur.fetchall()

    for row in rows:

        (
            key,
            symbol,
            direction,
            entry,
            sl,
            tp1,
            tp2,
            tp3,
            status
        ) = row

        price = prices.get(symbol)

        if price is None:
            continue

        event = None
        new_status = status

        if direction == "LONG":

            if price <= sl:
                event = "STOP LOSS"
                new_status = "STOP"

            elif price >= tp3:
                event = "TP3"
                new_status = "TP3"

            elif price >= tp2 and status != "TP2":
                event = "TP2"
                new_status = "TP2"

            elif price >= tp1 and status == "OPEN":
                event = "TP1"
                new_status = "TP1"

        elif direction == "SHORT":

            if price >= sl:
                event = "STOP LOSS"
                new_status = "STOP"

            elif price <= tp3:
                event = "TP3"
                new_status = "TP3"

            elif price <= tp2 and status != "TP2":
                event = "TP2"
                new_status = "TP2"

            elif price <= tp1 and status == "OPEN":
                event = "TP1"
                new_status = "TP1"

        if event:

            cur.execute("""
                INSERT INTO signal_events_v2
                (signal_key, event, price, created_at)
                VALUES (?, ?, ?, ?)
            """, (
                key,
                event,
                price,
                utc_now()
            ))

            cur.execute("""
                UPDATE signals_v2
                SET status = ?
                WHERE signal_key = ?
            """, (
                new_status,
                key
            ))

    con.commit()
    con.close()


# ============================================================
# COMPLETE ANALYSIS CYCLE
# ============================================================

def refresh_analysis_cycle(force_ai=False):

    all_data, global_errors = build_analysis()

    ai_results = {}
    prices = {}

    for asset, data in all_data.items():

        prices[asset] = data["market"]["price"]

        ai_result, ai_error = get_asset_ai(
            asset,
            data,
            force_ai=force_ai
        )

        cache_entry = st.session_state.saliga_ai_cache.get(
            asset, {}
        )

        ai_results[asset] = {
            "result": ai_result,
            "error": ai_error,
            "updated_at": cache_entry.get("updated_at")
        }

        apply_ai_validation(
            data,
            ai_result
        )

        for tf, item in data["timeframes"].items():
            save_signal(asset, tf, item)

    # TP / SL követés minden ciklusban, Groq nélkül
    track_signals(prices)

    return {
        "all_data": all_data,
        "ai_results": ai_results,
        "global_errors": global_errors,
        "updated_at": utc_now()
    }


# ============================================================
# INITIAL LOAD
# ============================================================

if st.session_state.saliga_data is None:

    with st.spinner(
        "SALIGA AI PRO indulás: OKX adatok lekérése..."
    ):

        st.session_state.saliga_data = (
            refresh_analysis_cycle(force_ai=False)
        )


# ============================================================
# HEADER
# ============================================================

st.title("⚡ SALIGA AI PRO")

st.caption(
    "HYBRID MARKET INTELLIGENCE | OKX + GROQ AI | "
    + APP_VERSION
)

st.info(
    "Piaci adatok: 60 másodpercenként | "
    "TP/SL követés: 60 másodpercenként | "
    "Groq AI: 15 percenként."
)

if not GROQ_API_KEY:
    st.error(
        "A GROQ_API_KEY nincs beállítva a Streamlit Secrets-ben."
    )


# ============================================================
# MANUAL AI REFRESH
# ============================================================

if st.button(
    "🧠 Teljes AI-elemzés most",
    type="primary"
):

    with st.spinner(
        "OKX adatok frissítése és Groq AI elemzés..."
    ):

        try:
            fetch_market_data.clear()
            fetch_candles.clear()

            st.session_state.saliga_ai_cache = {}
            st.session_state.groq_cooldown_until = 0

            st.session_state.saliga_data = (
                refresh_analysis_cycle(force_ai=True)
            )

            st.success("A kézi AI-elemzés befejeződött.")

        except Exception as e:
            st.error("Elemzési hiba: " + str(e))


# ============================================================
# CURRENT DATA
# ============================================================

current_data = st.session_state.saliga_data
all_data = current_data["all_data"]
ai_results = current_data["ai_results"]
global_errors = current_data["global_errors"]


# ============================================================
# MARKET OVERVIEW
# ============================================================

st.header("🌐 PIACI ÁTTEKINTÉS")

cols = st.columns(3)

for i, (asset, data) in enumerate(all_data.items()):

    with cols[i]:

        market = data["market"]

        st.subheader(asset)

        st.metric(
            "Árfolyam",
            fmt(market["price"], 4)
        )

        st.metric(
            "Open Interest (" + str(market["oi_unit"]) + ")",
            fmt(market["oi"], 2)
        )

        st.metric(
            "OI változás (5 perc)",
            (
                fmt(market["oi_change"], 3) + "%"
                if market["oi_change"] is not None
                else "N/A"
            )
        )

        st.metric(
            "Becsült Delta",
            (
                fmt(market["delta"], 2) + "%"
                if market["delta"] is not None
                else "N/A"
            )
        )

        st.metric(
            "Order Book",
            market["book_bias"]
        )

        st.metric(
            "Funding",
            (
                fmt(market["funding"], 4) + "%"
                if market["funding"] is not None
                else "N/A"
            )
        )

        if data["errors"]:
            with st.expander(asset + " adatlekérési hibák"):
                for error in data["errors"]:
                    st.write("⚠️ " + error)


# ============================================================
# AI SUMMARY
# ============================================================

st.header("🧠 GROQ AI – PIACI INTELLIGENCIA")

for asset, ai_data in ai_results.items():

    with st.expander(
        asset + " – AI elemzés",
        expanded=True
    ):

        result = ai_data["result"]

        st.caption(
            "Utolsó AI-frissítés: "
            + str(ai_data.get("updated_at") or "N/A")
        )

        if result:

            st.subheader(
                "AI irány: "
                + str(result.get("overall_bias", "WAIT"))
            )

            st.metric(
                "AI bizalmi érték",
                str(result.get("confidence", "N/A"))
            )

            st.markdown("### Piaci összefoglaló")
            st.write(result.get("market_summary", "N/A"))

            st.markdown("### Pozícióáramlás")
            st.write(result.get("position_flow", "N/A"))

            st.markdown("### LONG tényezők")
            for x in result.get("long_factors", []):
                st.write("🟢 " + str(x))

            st.markdown("### SHORT tényezők")
            for x in result.get("short_factors", []):
                st.write("🔴 " + str(x))

            st.markdown("### Kockázatok")
            for x in result.get("risks", []):
                st.write("⚠️ " + str(x))

        else:
            st.warning(
                "Groq AI eredmény jelenleg nem érhető el: "
                + str(ai_data["error"])
            )


# ============================================================
# MULTI TIMEFRAME ANALYSIS
# ============================================================

st.header("📊 TÖBBIDŐSÍKÚ ELEMZÉS")

tabs = st.tabs(list(TIMEFRAMES.keys()))

for tab_index, tf in enumerate(TIMEFRAMES.keys()):

    with tabs[tab_index]:

        for asset, data in all_data.items():

            item = data["timeframes"][tf]

            st.subheader(asset + " – " + tf)

            if item.get("error"):
                st.error(item["error"])
                continue

            candle = item["candle"]
            score = item["score"]
            plan = item["plan"]

            c1, c2, c3, c4 = st.columns(4)

            c1.metric("Ár", fmt(candle["price"], 4))
            c2.metric("RSI", fmt(candle["rsi"]))
            c3.metric(
                "Momentum",
                fmt(candle["momentum_5"], 3) + "%"
            )
            c4.metric("ATR", fmt(candle["atr"], 4))

            st.write(
                "Piaci struktúra: " + candle["structure"]
            )

            st.write(
                "Likviditási esemény: " + candle["sweep"]
            )

            st.write(
                f"LONG pont: {score['long_score']} | "
                f"SHORT pont: {score['short_score']} | "
                f"Nettó score: {score['score']}"
            )

            st.write(
                "Matematikai motor: " + score["direction"]
            )

            st.write(
                "AI irány: "
                + str(item.get("ai_direction", "N/A"))
            )

            final_direction = item["final_direction"]

            if final_direction == "LONG":
                st.success("HYBRID DÖNTÉS: LONG")

            elif final_direction == "SHORT":
                st.error("HYBRID DÖNTÉS: SHORT")

            else:
                st.warning("HYBRID DÖNTÉS: WAIT")

            ai_tf = item.get("ai")

            if ai_tf:
                st.markdown("**AI idősík-elemzés:**")
                st.write(ai_tf.get("analysis", "N/A"))

            if plan and final_direction in ["LONG", "SHORT"]:

                st.markdown("### Kereskedési terv – referencia")

                p1, p2, p3, p4, p5 = st.columns(5)

                p1.metric("ENTRY", fmt(plan["entry"], 4))
                p2.metric("STOP LOSS", fmt(plan["sl"], 4))
                p3.metric("TP1", fmt(plan["tp1"], 4))
                p4.metric("TP2", fmt(plan["tp2"], 4))
                p5.metric("TP3", fmt(plan["tp3"], 4))

            st.write("Indokok:")

            for reason in score["reasons"]:
                st.write("• " + reason)

            st.divider()


# ============================================================
# LIVE SIGNAL PANEL
# ============================================================

@st.fragment(run_every=SIGNAL_REFRESH_SECONDS)
def live_signal_panel():

    st.header("🚨 ÉLŐ JELZÉSEK")

    st.caption(
        "60 másodperces piaci és TP/SL frissítés. "
        "A Groq AI csak 15 percenként fut."
    )

    st.session_state.signal_refresh_count += 1

    if st.session_state.signal_refresh_count > 1:

        try:

            with st.spinner(
                "OKX adatok és jelzések frissítése..."
            ):

                new_data = refresh_analysis_cycle(
                    force_ai=False
                )

                st.session_state.saliga_data = new_data
                st.session_state.last_signal_refresh = (
                    new_data["updated_at"]
                )

        except Exception as e:
            st.warning(
                "A frissítés nem sikerült: " + str(e)
            )

    latest = st.session_state.saliga_data

    st.caption(
        "Utolsó frissítés: "
        + str(latest.get("updated_at", "N/A"))
    )

    # Aktuális jelzések
    signal_rows = []

    for asset, data in latest["all_data"].items():

        for tf, item in data["timeframes"].items():

            if item.get("error"):
                continue

            score = item.get("score")

            if not score:
                continue

            candle = item.get("candle")
            plan = item.get("plan")

            signal_rows.append({
                "Eszköz": asset,
                "Idősík": tf,
                "Jelzés": item.get("final_direction", "WAIT"),
                "Matematikai irány": score["direction"],
                "AI irány": item.get("ai_direction", "N/A"),
                "LONG pont": score["long_score"],
                "SHORT pont": score["short_score"],
                "Nettó score": score["score"],
                "Ár": candle["price"] if candle else None,
                "Entry": plan["entry"] if plan else None,
                "Stop": plan["sl"] if plan else None,
                "TP1": plan["tp1"] if plan else None,
                "TP2": plan["tp2"] if plan else None,
                "TP3": plan["tp3"] if plan else None
            })

    if signal_rows:

        signal_df = pd.DataFrame(signal_rows)

        priority = {
            "LONG": 0,
            "SHORT": 1,
            "WAIT": 2
        }

        signal_df["_priority"] = (
            signal_df["Jelzés"].map(priority)
        )

        signal_df = signal_df.sort_values(
            ["_priority", "Nettó score"],
            ascending=[True, False]
        ).drop(columns=["_priority"])

        long_count = int(
            (signal_df["Jelzés"] == "LONG").sum()
        )

        short_count = int(
            (signal_df["Jelzés"] == "SHORT").sum()
        )

        wait_count = int(
            (signal_df["Jelzés"] == "WAIT").sum()
        )

        a, b, c = st.columns(3)

        a.metric("LONG jelzések", long_count)
        b.metric("SHORT jelzések", short_count)
        c.metric("WAIT", wait_count)

        st.dataframe(
            signal_df,
            use_container_width=True,
            hide_index=True
        )

    else:
        st.warning("Jelenleg nincs értékelhető jelzés.")

    # Jelzésnapló
    st.subheader("📚 Megerősített jelzések naplója")

    con = db()

    history = pd.read_sql_query("""
        SELECT
            symbol,
            timeframe,
            direction,
            entry,
            sl,
            tp1,
            tp2,
            tp3,
            score,
            ai_direction,
            status,
            created_at
        FROM signals_v2
        ORDER BY id DESC
        LIMIT 100
    """, con)

    con.close()

    if not history.empty:
        st.dataframe(
            history,
            use_container_width=True,
            hide_index=True
        )
    else:
        st.info("Még nincs rögzített megerősített jelzés.")

    # TP / SL események
    st.subheader("📍 Jelzések eseménynaplója")

    con = db()

    events = pd.read_sql_query("""
        SELECT
            signal_key,
            event,
            price,
            created_at
        FROM signal_events_v2
        ORDER BY id DESC
        LIMIT 100
    """, con)

    con.close()

    if not events.empty:
        st.dataframe(
            events,
            use_container_width=True,
            hide_index=True
        )
    else:
        st.info("Még nincs TP vagy SL esemény.")


live_signal_panel()


# ============================================================
# GROQ CHAT - MANUAL ONLY
# ============================================================

def groq_chat(question, market_context):

    client = get_groq_client()

    if client is None:
        return "A Groq nem érhető el. Ellenőrizd a GROQ_API_KEY beállítását."

    if time.time() < st.session_state.groq_cooldown_until:
        return "A Groq jelenleg korlátozást jelzett. Várd meg a következő próbálkozási időt."

    try:

        response = client.chat.completions.create(
            model=GROQ_MODEL,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Te a SALIGA AI PRO piaci elemző asszisztense vagy. "
                        "A megadott OKX-adatokból dolgozz. "
                        "Ne találj ki adatokat. "
                        "Magyarul válaszolj. "
                        "Ne ígérj garantált kereskedési eredményt."
                    )
                },
                {
                    "role": "user",
                    "content": (
                        "Piaci adatok:\n"
                        + json.dumps(
                            market_context,
                            ensure_ascii=False,
                            default=str
                        )
                        + "\nKérdés: "
                        + question
                    )
                }
            ],
            max_completion_tokens=GROQ_MAX_TOKENS
        )

        return response.choices[0].message.content

    except Exception as e:

        error = str(e)

        if (
            "429" in error
            or "rate_limit" in error.lower()
            or "tokens per day" in error.lower()
        ):
            st.session_state.groq_cooldown_until = (
                time.time() + GROQ_RETRY_SECONDS
            )

        return "Groq hiba: " + error


st.header("💬 SALIGA AI – Kérdezz a piacról")

question = st.text_area(
    "Írd be a kérdésedet:",
    placeholder="Például: Miért emelkedik a BTC, miközben az OI csökken?"
)

if st.button("AI válasz kérése", type="primary"):

    if not question.strip():
        st.warning("Írj be egy kérdést.")

    elif not GROQ_API_KEY:
        st.error("Nincs beállítva a Groq API-kulcs.")

    else:

        chat_context = {}

        for asset, data in all_data.items():

            chat_context[asset] = {
                "market": data["market"],
                "timeframes": {}
            }

            for tf, item in data["timeframes"].items():

                if item.get("score"):

                    chat_context[asset]["timeframes"][tf] = {
                        "direction": item["final_direction"],
                        "ai_direction": item.get("ai_direction"),
                        "score": item["score"],
                        "price": item["candle"]["price"],
                        "structure": item["candle"]["structure"]
                    }

        with st.spinner("Groq AI elemzi a kérdésedet..."):

            answer = groq_chat(
                question,
                chat_context
            )

        st.markdown("### SALIGA AI válasza")
        st.write(answer)


# ============================================================
# SYSTEM STATUS
# ============================================================

st.header("⚙️ RENDSZERÁLLAPOT")

s1, s2, s3 = st.columns(3)

s1.metric(
    "OKX",
    "ADATKAPCSOLAT" if all_data else "HIBA"
)

s2.metric(
    "Groq AI",
    "KAPCSOLVA" if GROQ_API_KEY else "NINCS API-KULCS"
)

s3.metric(
    "Modell",
    GROQ_MODEL
)

if global_errors:

    with st.expander("Rendszerhibák"):
        for error in global_errors:
            st.write("⚠️ " + error)

st.caption(
    "SALIGA AI PRO | HYBRID MARKET INTELLIGENCE | "
    "Az elemzés tájékoztató jellegű. "
    "A kereskedési terv referencia, nem végrehajtott megbízás."
)
