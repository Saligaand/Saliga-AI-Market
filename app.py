import streamlit as st
import ccxt
import requests
import pandas as pd
import numpy as np
import sqlite3
import json
import time
import os
from datetime import datetime, timezone
from openai import OpenAI

# ============================================================
# SALIGA AI PRO 16.1
# OKX ONLY | MULTI-METHOD OPEN INTEREST | GROQ AI
# ============================================================

st.set_page_config(
    page_title="SALIGA AI PRO",
    page_icon="⚡",
    layout="wide"
)

APP_VERSION = "SALIGA AI PRO 16.1"
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
AI_REFRESH_SECONDS = 900
OI_SAMPLE_SECONDS = 300
GROQ_COOLDOWN_SECONDS = 900

OKX_OI_URL = "https://www.okx.com/api/v5/public/open-interest"


# ============================================================
# SESSION STATE
# ============================================================

DEFAULTS = {
    "ai_cache": {},
    "last_ai_attempt": {},
    "groq_cooldown_until": 0,
    "last_data": None,
    "last_refresh": 0,
    "chat_answer": None,
    "force_ai": False
}

for key, value in DEFAULTS.items():
    if key not in st.session_state:
        st.session_state[key] = value


# ============================================================
# HELPERS
# ============================================================

def now_utc():
    return datetime.now(timezone.utc).isoformat()


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


def get_secret(name):
    try:
        return st.secrets.get(name, "")
    except Exception:
        return os.getenv(name, "")


GROQ_API_KEY = get_secret("GROQ_API_KEY")


# ============================================================
# DATABASE
# AUTOMATIKUS RÉGI ADATBÁZIS JAVÍTÁS
# ============================================================

def get_db():
    return sqlite3.connect(
        DB_PATH,
        check_same_thread=False,
        timeout=30
    )


def ensure_columns(cur, table, required_columns):

    cur.execute(f"PRAGMA table_info({table})")

    existing = {
        row[1] for row in cur.fetchall()
    }

    for column, definition in required_columns.items():

        if column not in existing:

            cur.execute(
                f"ALTER TABLE {table} "
                f"ADD COLUMN {column} {definition}"
            )


def init_db():

    con = get_db()
    cur = con.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS signals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            signal_key TEXT UNIQUE,
            asset TEXT,
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
        CREATE TABLE IF NOT EXISTS signal_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            signal_key TEXT,
            event TEXT,
            price REAL,
            created_at TEXT
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS oi_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            asset TEXT,
            oi_value REAL,
            oi_unit TEXT,
            price REAL,
            created_at REAL
        )
    """)

    ensure_columns(
        cur,
        "oi_history",
        {
            "asset": "TEXT",
            "oi_value": "REAL",
            "oi_unit": "TEXT DEFAULT 'N/A'",
            "price": "REAL",
            "created_at": "REAL"
        }
    )

    ensure_columns(
        cur,
        "signals",
        {
            "signal_key": "TEXT",
            "asset": "TEXT",
            "timeframe": "TEXT",
            "direction": "TEXT",
            "entry": "REAL",
            "sl": "REAL",
            "tp1": "REAL",
            "tp2": "REAL",
            "tp3": "REAL",
            "score": "REAL",
            "ai_direction": "TEXT",
            "status": "TEXT DEFAULT 'OPEN'",
            "created_at": "TEXT"
        }
    )

    ensure_columns(
        cur,
        "signal_events",
        {
            "signal_key": "TEXT",
            "event": "TEXT",
            "price": "REAL",
            "created_at": "TEXT"
        }
    )

    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_oi_asset_time
        ON oi_history(asset, created_at)
    """)

    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_signals_status
        ON signals(status)
    """)

    con.commit()
    con.close()


init_db()


# ============================================================
# OKX CONNECTION
# ============================================================

@st.cache_resource
def get_exchange():

    exchange = ccxt.okx({
        "enableRateLimit": True,
        "timeout": 20000,
        "options": {
            "defaultType": "swap"
        }
    })

    exchange.load_markets()

    return exchange


exchange = get_exchange()


# ============================================================
# OPEN INTEREST HELPERS
# ============================================================

def extract_oi_record(record):
    """
    OKX OI-adatok felismerése többféle formátumból.
    """

    if not isinstance(record, dict):
        return None

    fields = [
        ("oiUsd", "USD"),
        ("oiCcy", "BASE"),
        ("oi", "CONTRACTS")
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


def extract_unified_oi(result):
    """
    CCXT egységesített OI-válasz feldolgozása.
    """

    if not isinstance(result, dict):
        return None

    # Először a nyers OKX információkat vizsgáljuk.
    info = result.get("info", {})

    if isinstance(info, dict):

        direct = extract_oi_record(info)

        if direct:
            return direct

        records = info.get("data", [])

        if isinstance(records, list):

            for record in records:

                extracted = extract_oi_record(record)

                if extracted:
                    return extracted

    # CCXT egységesített mezők.
    unified_fields = [
        ("openInterestValue", "USD"),
        ("openInterestAmount", "BASE")
    ]

    for field, unit in unified_fields:

        value = safe_float(result.get(field))

        if value is not None and value > 0:

            return {
                "value": value,
                "unit": unit,
                "raw": result
            }

    return None


# ============================================================
# OKX OPEN INTEREST
# ELSŐDLEGES: CCXT NATÍV OKX API
# MÁSODLAGOS: CCXT UNIFIED API
# HARMADLAGOS: KÖZVETLEN HTTP
# ============================================================

def fetch_okx_oi(asset):

    symbol = SYMBOLS[asset]

    errors = []

    try:

        market = exchange.market(symbol)
        inst_id = market["id"]

        params = {
            "instType": "SWAP",
            "instId": inst_id
        }

        # ----------------------------------------------------
        # 1. CCXT NATÍV OKX API
        # ----------------------------------------------------

        try:

            method = getattr(
                exchange,
                "publicGetPublicOpenInterest",
                None
            )

            if method is None:
                raise Exception(
                    "A CCXT verzióban nem található "
                    "a publicGetPublicOpenInterest metódus."
                )

            payload = method(params)

            if not isinstance(payload, dict):
                raise Exception("Érvénytelen CCXT API-válasz.")

            if str(payload.get("code")) != "0":

                raise Exception(
                    f"OKX API {payload.get('code')}: "
                    f"{payload.get('msg')}"
                )

            records = payload.get("data", [])

            if isinstance(records, list):

                for record in records:

                    extracted = extract_oi_record(record)

                    if extracted:

                        return {
                            "value": extracted["value"],
                            "unit": extracted["unit"],
                            "error": None,
                            "source": "CCXT NATIVE OKX",
                            "raw": extracted["raw"]
                        }

            raise Exception(
                "A natív CCXT válaszban nincs használható OI-adat."
            )

        except Exception as e:

            errors.append(
                "CCXT natív API: " + str(e)
            )

        # ----------------------------------------------------
        # 2. CCXT UNIFIED API
        # ----------------------------------------------------

        try:

            result = exchange.fetch_open_interest(symbol)

            extracted = extract_unified_oi(result)

            if extracted:

                return {
                    "value": extracted["value"],
                    "unit": extracted["unit"],
                    "error": None,
                    "source": "CCXT UNIFIED",
                    "raw": extracted["raw"]
                }

            raise Exception(
                "A CCXT unified válaszban nincs használható OI-adat."
            )

        except Exception as e:

            errors.append(
                "CCXT unified API: " + str(e)
            )

        # ----------------------------------------------------
        # 3. KÖZVETLEN OKX HTTP API
        # ----------------------------------------------------

        try:

            response = requests.get(
                OKX_OI_URL,
                params=params,
                headers={
                    "Accept": "application/json",
                    "User-Agent": "SALIGA-AI-PRO"
                },
                timeout=15
            )

            response.raise_for_status()

            payload = response.json()

            if str(payload.get("code")) != "0":

                raise Exception(
                    f"OKX HTTP {payload.get('code')}: "
                    f"{payload.get('msg')}"
                )

            records = payload.get("data", [])

            if isinstance(records, list):

                for record in records:

                    extracted = extract_oi_record(record)

                    if extracted:

                        return {
                            "value": extracted["value"],
                            "unit": extracted["unit"],
                            "error": None,
                            "source": "DIRECT OKX HTTP",
                            "raw": extracted["raw"]
                        }

            raise Exception(
                "A közvetlen HTTP-válaszban nincs használható OI-adat."
            )

        except Exception as e:

            errors.append(
                "Közvetlen HTTP: " + str(e)
            )

        raise Exception(
            "Mindhárom OI-lekérési módszer sikertelen:\n"
            + "\n".join(errors)
        )

    except Exception as e:

        return {
            "value": None,
            "unit": "N/A",
            "error": str(e),
            "source": "FAILED",
            "raw": None
        }


# ============================================================
# OI HISTORY
# ============================================================

def save_oi_history(asset, value, unit, price):

    if value is None:
        return

    con = get_db()

    try:

        cur = con.cursor()
        now = time.time()

        cur.execute("""
            SELECT created_at
            FROM oi_history
            WHERE asset = ?
            AND oi_unit = ?
            ORDER BY created_at DESC
            LIMIT 1
        """, (asset, unit))

        row = cur.fetchone()

        if row and row[0] is not None:

            if now - float(row[0]) < OI_SAMPLE_SECONDS:
                return

        cur.execute("""
            INSERT INTO oi_history (
                asset,
                oi_value,
                oi_unit,
                price,
                created_at
            )
            VALUES (?, ?, ?, ?, ?)
        """, (
            asset,
            value,
            unit,
            price,
            now
        ))

        con.commit()

    finally:
        con.close()


def calculate_oi_change(asset, current_value, unit):

    if current_value is None:
        return None

    con = get_db()

    try:

        cur = con.cursor()

        target = time.time() - OI_SAMPLE_SECONDS

        cur.execute("""
            SELECT oi_value
            FROM oi_history
            WHERE asset = ?
            AND oi_unit = ?
            AND created_at <= ?
            AND oi_value > 0
            ORDER BY created_at DESC
            LIMIT 1
        """, (
            asset,
            unit,
            target
        ))

        row = cur.fetchone()

    finally:
        con.close()

    if not row:
        return None

    previous = safe_float(row[0])

    if previous is None or previous <= 0:
        return None

    return (
        (current_value / previous) - 1
    ) * 100


# ============================================================
# MARKET DATA
# ============================================================

@st.cache_data(ttl=45, show_spinner=False)
def fetch_market_data(asset):

    symbol = SYMBOLS[asset]

    result = {
        "ticker": None,
        "orderbook": None,
        "funding": None,
        "trades": None,
        "oi": None,
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

    try:
        result["trades"] = exchange.fetch_trades(
            symbol,
            limit=300
        )
    except Exception as e:
        result["errors"].append("Trades: " + str(e))

    result["oi"] = fetch_okx_oi(asset)

    if result["oi"]["error"]:
        result["errors"].append(
            "OKX OI: " + result["oi"]["error"]
        )

    return result


@st.cache_data(ttl=30, show_spinner=False)
def fetch_candles(symbol, timeframe):

    return exchange.fetch_ohlcv(
        symbol,
        timeframe=timeframe,
        limit=250
    )


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

    imbalance = None

    if total > 0:

        imbalance = (
            (bid_volume - ask_volume) / total
        ) * 100

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

def analyze_delta(trades):

    if not trades:
        return {
            "delta": None,
            "buy": None,
            "sell": None
        }

    buy = 0.0
    sell = 0.0

    for trade in trades:

        amount = safe_float(
            trade.get("amount"),
            0
        )

        if trade.get("side") == "buy":
            buy += amount

        elif trade.get("side") == "sell":
            sell += amount

    total = buy + sell

    delta = None

    if total > 0:

        delta = (
            (buy - sell) / total
        ) * 100

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
        raise ValueError("Kevés gyertyaadat.")

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

    df = df.iloc[:-1].copy()

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

    df = df.dropna().reset_index(drop=True)

    if len(df) < 50:
        raise ValueError("Kevés lezárt gyertya.")

    close = df["close"]
    high = df["high"]
    low = df["low"]
    volume = df["volume"]

    # RSI
    price_change = close.diff()

    gain = price_change.clip(lower=0)
    loss = -price_change.clip(upper=0)

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

    volume_sum = volume.rolling(30).sum()

    vwap = (
        (typical * volume).rolling(30).sum()
        /
        volume_sum.replace(0, np.nan)
    )

    volume_reference = volume.iloc[-21:-1].mean()

    volume_ratio = None

    if pd.notna(volume_reference) and volume_reference > 0:

        volume_ratio = (
            volume.iloc[-1] / volume_reference
        )

    momentum_5 = (
        close.iloc[-1] / close.iloc[-6] - 1
    ) * 100

    momentum_20 = (
        close.iloc[-1] / close.iloc[-21] - 1
    ) * 100

    recent_high = high.iloc[-21:-1].max()
    recent_low = low.iloc[-21:-1].min()

    previous_high = high.iloc[-11:-1].max()
    previous_low = low.iloc[-11:-1].min()

    current_close = close.iloc[-1]

    structure = "RANGE"

    if current_close > previous_high:
        structure = "BULLISH BREAK"

    elif current_close < previous_low:
        structure = "BEARISH BREAK"

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
# SCORE ENGINE
# ============================================================

def calculate_score(candle, market, threshold):

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
            reasons.append("Positive momentum")

        elif momentum < -0.15:
            short_score += 1
            reasons.append("Negative momentum")

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
# TRADE PLAN
# ============================================================

def create_trade_plan(direction, candle):

    price = candle["price"]
    atr = candle["atr"]

    if atr is None or atr <= 0:
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
# BUILD ANALYSIS
# ============================================================

def build_analysis():

    all_data = {}
    errors = []

    for asset, symbol in SYMBOLS.items():

        raw = fetch_market_data(asset)

        ticker = raw["ticker"]

        if not ticker:
            errors.append(asset + ": ticker nem érhető el.")
            continue

        price = safe_float(ticker.get("last"))

        if price is None:
            errors.append(asset + ": nincs árfolyam.")
            continue

        oi = raw["oi"]

        oi_value = oi["value"]
        oi_unit = oi["unit"]

        oi_change = calculate_oi_change(
            asset,
            oi_value,
            oi_unit
        )

        save_oi_history(
            asset,
            oi_value,
            oi_unit,
            price
        )

        delta_data = analyze_delta(raw["trades"])
        book_data = analyze_orderbook(raw["orderbook"])

        funding = None

        if raw["funding"]:

            funding = safe_float(
                raw["funding"].get("fundingRate")
            )

            if funding is not None:
                funding *= 100

        market = {
            "price": price,
            "oi": oi_value,
            "oi_unit": oi_unit,
            "oi_change": oi_change,
            "oi_source": oi.get("source", "N/A"),
            "oi_error": oi.get("error"),
            "delta": delta_data["delta"],
            "buy_volume": delta_data["buy"],
            "sell_volume": delta_data["sell"],
            "imbalance": book_data["imbalance"],
            "book_bias": book_data["bias"],
            "funding": funding
        }

        timeframe_data = {}

        for tf, exchange_tf in TIMEFRAMES.items():

            try:

                candles = fetch_candles(
                    symbol,
                    exchange_tf
                )

                candle = analyze_candles(candles)

                score = calculate_score(
                    candle,
                    market,
                    THRESHOLDS[tf]
                )

                plan = create_trade_plan(
                    score["direction"],
                    candle
                )

                timeframe_data[tf] = {
                    "candle": candle,
                    "score": score,
                    "plan": plan,
                    "ai": None,
                    "ai_direction": "N/A",
                    "final_direction": "WAIT"
                }

            except Exception as e:

                timeframe_data[tf] = {
                    "error": str(e),
                    "candle": None,
                    "score": None,
                    "plan": None,
                    "ai": None,
                    "ai_direction": "N/A",
                    "final_direction": "WAIT"
                }

        all_data[asset] = {
            "symbol": symbol,
            "market": market,
            "timeframes": timeframe_data,
            "errors": raw["errors"]
        }

    return all_data, errors


# ============================================================
# GROQ
# ============================================================

def get_groq_client():

    if not GROQ_API_KEY:
        return None

    return OpenAI(
        api_key=GROQ_API_KEY,
        base_url="https://api.groq.com/openai/v1",
        timeout=45
    )


def groq_analyze(asset, data):

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
Te vagy a SALIGA AI PRO kriptopiaci elemzője.

Elemezd:
- Több idősík irányát.
- Open Interest változását.
- Becsült deltát.
- Order book pillanatképet.
- Funding értékét.
- Piaci struktúrát és likviditási sweepet.

Fontos:
Az OI emelkedése önmagában nem bizonyítja,
hogy long vagy short pozíciók épülnek.
A delta becsült kötésalapú adat.
Az order book pillanatfelvétel.
Hiányzó adatot ne találj ki.
Ellentmondó adatok esetén WAIT.

Magyarul válaszolj.
Kizárólag érvényes JSON-t adj vissza.

Formátum:
{
 "overall_bias":"LONG vagy SHORT vagy WAIT",
 "confidence":0,
 "market_summary":"rövid elemzés",
 "position_flow":"pozícióáramlás elemzése",
 "long_factors":["tényező"],
 "short_factors":["tényező"],
 "risks":["kockázat"],
 "timeframes":{
  "M5":{"bias":"LONG vagy SHORT vagy WAIT","analysis":"elemzés"},
  "M15":{"bias":"LONG vagy SHORT vagy WAIT","analysis":"elemzés"},
  "H1":{"bias":"LONG vagy SHORT vagy WAIT","analysis":"elemzés"},
  "H4":{"bias":"LONG vagy SHORT vagy WAIT","analysis":"elemzés"},
  "D1":{"bias":"LONG vagy SHORT vagy WAIT","analysis":"elemzés"}
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
            response_format={
                "type": "json_object"
            },
            max_completion_tokens=450
        )

        content = response.choices[0].message.content

        if not content:
            raise Exception("A Groq üres választ adott.")

        return json.loads(content), None

    except Exception as e:

        error = str(e)

        if (
            "429" in error
            or "rate_limit" in error.lower()
            or "tokens per day" in error.lower()
        ):

            st.session_state.groq_cooldown_until = (
                time.time() + GROQ_COOLDOWN_SECONDS
            )

        return None, error


# ============================================================
# AI REFRESH CONTROL
# ============================================================

def get_ai_result(asset, data, force=False):

    now = time.time()

    cache = st.session_state.ai_cache
    attempts = st.session_state.last_ai_attempt

    previous = cache.get(asset)

    if (
        not force
        and now < st.session_state.groq_cooldown_until
    ):

        return (
            previous.get("result") if previous else None,
            "Groq korlátozás: várakozás."
        )

    last_attempt = attempts.get(asset, 0)

    if (
        not force
        and previous
        and now - last_attempt < AI_REFRESH_SECONDS
    ):

        return (
            previous.get("result"),
            previous.get("error")
        )

    attempts[asset] = now
    st.session_state.last_ai_attempt = attempts

    result, error = groq_analyze(
        asset,
        data
    )

    cache[asset] = {
        "result": result,
        "error": error,
        "updated_at": now_utc()
    }

    st.session_state.ai_cache = cache

    return result, error


# ============================================================
# AI VALIDATION
# ============================================================

def apply_ai_validation(data, ai_result):

    for tf, item in data["timeframes"].items():

        if not item.get("score"):
            item["final_direction"] = "WAIT"
            continue

        engine_direction = item["score"]["direction"]

        ai_tf = {}

        if ai_result:

            ai_tf = ai_result.get(
                "timeframes", {}
            ).get(tf, {})

        ai_direction = ai_tf.get("bias", "WAIT")

        if ai_direction not in ["LONG", "SHORT", "WAIT"]:
            ai_direction = "WAIT"

        item["ai"] = ai_tf
        item["ai_direction"] = ai_direction

        if (
            engine_direction == ai_direction
            and engine_direction in ["LONG", "SHORT"]
            and item.get("plan")
        ):

            item["final_direction"] = engine_direction

        else:

            item["final_direction"] = "WAIT"


# ============================================================
# SIGNAL STORAGE
# ============================================================

def save_signal(asset, tf, item):

    direction = item.get("final_direction")

    if direction not in ["LONG", "SHORT"]:
        return

    candle = item["candle"]
    plan = item["plan"]
    score = item["score"]

    signal_key = (
        f"{asset}_{tf}_{direction}_"
        f"{candle['candle_time']}"
    )

    con = get_db()

    try:

        cur = con.cursor()

        cur.execute("""
            INSERT OR IGNORE INTO signals (
                signal_key,
                asset,
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
            signal_key,
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
            now_utc()
        ))

        con.commit()

    finally:
        con.close()


# ============================================================
# TP / SL TRACKING
# ============================================================

def track_signals(prices):

    con = get_db()

    try:

        cur = con.cursor()

        cur.execute("""
            SELECT
                signal_key,
                asset,
                direction,
                sl,
                tp1,
                tp2,
                tp3,
                status
            FROM signals
            WHERE status IN ('OPEN', 'TP1', 'TP2')
        """)

        rows = cur.fetchall()

        for row in rows:

            (
                key,
                asset,
                direction,
                sl,
                tp1,
                tp2,
                tp3,
                status
            ) = row

            price = prices.get(asset)

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
                    INSERT INTO signal_events (
                        signal_key,
                        event,
                        price,
                        created_at
                    )
                    VALUES (?, ?, ?, ?)
                """, (
                    key,
                    event,
                    price,
                    now_utc()
                ))

                cur.execute("""
                    UPDATE signals
                    SET status = ?
                    WHERE signal_key = ?
                """, (
                    new_status,
                    key
                ))

        con.commit()

    finally:
        con.close()


# ============================================================
# FULL CYCLE
# ============================================================

def run_cycle(force_ai=False):

    all_data, errors = build_analysis()

    ai_results = {}
    prices = {}

    for asset, data in all_data.items():

        prices[asset] = data["market"]["price"]

        ai_result, ai_error = get_ai_result(
            asset,
            data,
            force=force_ai
        )

        ai_results[asset] = {
            "result": ai_result,
            "error": ai_error,
            "updated_at": (
                st.session_state.ai_cache
                .get(asset, {})
                .get("updated_at")
            )
        }

        apply_ai_validation(
            data,
            ai_result
        )

        for tf, item in data["timeframes"].items():
            save_signal(asset, tf, item)

    track_signals(prices)

    return {
        "all_data": all_data,
        "ai_results": ai_results,
        "errors": errors,
        "updated_at": now_utc()
    }


# ============================================================
# DASHBOARD
# ============================================================

@st.fragment(run_every=MARKET_REFRESH_SECONDS)
def dashboard():

    if st.session_state.last_data is None:

        with st.spinner("SALIGA AI PRO indul..."):

            try:
                st.session_state.last_data = run_cycle()
                st.session_state.last_refresh = time.time()

            except Exception as e:
                st.error("Indítási hiba: " + str(e))
                return

    elif (
        time.time() - st.session_state.last_refresh
        >= MARKET_REFRESH_SECONDS
    ):

        try:

            fetch_market_data.clear()
            fetch_candles.clear()

            with st.spinner("OKX piaci adatok frissítése..."):

                st.session_state.last_data = run_cycle()

            st.session_state.last_refresh = time.time()

        except Exception as e:

            st.error("Frissítési hiba: " + str(e))

    data_pack = st.session_state.last_data

    if not data_pack:
        st.error("Nincs elérhető piaci adat.")
        return

    all_data = data_pack["all_data"]
    ai_results = data_pack["ai_results"]

    st.title("⚡ SALIGA AI PRO")

    st.caption(
        APP_VERSION
        + " | OKX MULTI-METHOD OI | GROQ AI"
    )

    st.info(
        "Piaci adatok: 60 másodperc | "
        "TP/SL követés: 60 másodperc | "
        "Groq AI: 15 perc"
    )

    # MANUAL AI REFRESH

    if st.button(
        "🧠 Teljes AI-elemzés most",
        type="primary"
    ):

        try:

            fetch_market_data.clear()
            fetch_candles.clear()

            with st.spinner("Teljes AI-elemzés..."):

                st.session_state.last_data = run_cycle(
                    force_ai=True
                )

            st.session_state.last_refresh = time.time()

            st.rerun()

        except Exception as e:

            st.error("AI frissítési hiba: " + str(e))

    # MARKET OVERVIEW

    st.header("🌐 PIACI ÁTTEKINTÉS")

    cols = st.columns(3)

    for i, (asset, data) in enumerate(all_data.items()):

        market = data["market"]

        with cols[i]:

            st.subheader(asset)

            st.metric(
                "Árfolyam",
                fmt(market["price"], 4)
            )

            st.metric(
                "Open Interest " + market["oi_unit"],
                fmt(market["oi"], 2)
            )

            st.caption(
                "OI forrás: "
                + str(market.get("oi_source", "N/A"))
            )

            st.metric(
                "OI változás",
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

            if market.get("oi_error"):

                with st.expander(asset + " OI részletes hiba"):

                    st.error(market["oi_error"])

    # AI SUMMARY

    st.header("🧠 GROQ AI – PIACI INTELLIGENCIA")

    for asset, ai_data in ai_results.items():

        with st.expander(
            asset + " AI-elemzés",
            expanded=True
        ):

            result = ai_data["result"]

            st.caption(
                "Utolsó AI-frissítés: "
                + str(ai_data.get("updated_at") or "N/A")
            )

            if result:

                st.subheader(
                    "Irány: "
                    + str(result.get("overall_bias", "WAIT"))
                )

                st.metric(
                    "Bizalmi érték",
                    str(result.get("confidence", "N/A"))
                )

                st.markdown("**Piaci összefoglaló**")
                st.write(result.get("market_summary", "N/A"))

                st.markdown("**Pozícióáramlás**")
                st.write(result.get("position_flow", "N/A"))

                st.markdown("**LONG tényezők**")

                for factor in result.get("long_factors", []):
                    st.write("🟢 " + str(factor))

                st.markdown("**SHORT tényezők**")

                for factor in result.get("short_factors", []):
                    st.write("🔴 " + str(factor))

                st.markdown("**Kockázatok**")

                for risk in result.get("risks", []):
                    st.write("⚠️ " + str(risk))

            else:

                st.warning(
                    "AI-adat nem érhető el: "
                    + str(ai_data["error"])
                )

    # MULTI TIMEFRAME

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
                    "Struktúra: " + candle["structure"]
                )

                st.write(
                    "Likviditás: " + candle["sweep"]
                )

                st.write(
                    f"LONG pont: {score['long_score']} | "
                    f"SHORT pont: {score['short_score']} | "
                    f"Nettó score: {score['score']}"
                )

                st.write(
                    "Matematikai irány: "
                    + score["direction"]
                )

                st.write(
                    "AI irány: "
                    + str(item["ai_direction"])
                )

                final_direction = item["final_direction"]

                if final_direction == "LONG":
                    st.success("HYBRID DÖNTÉS: LONG")

                elif final_direction == "SHORT":
                    st.error("HYBRID DÖNTÉS: SHORT")

                else:
                    st.warning("HYBRID DÖNTÉS: WAIT")

                if item.get("ai"):
                    st.write(
                        item["ai"].get("analysis", "")
                    )

                if (
                    plan
                    and final_direction in ["LONG", "SHORT"]
                ):

                    st.markdown(
                        "**Kereskedési terv – referencia**"
                    )

                    p1, p2, p3, p4, p5 = st.columns(5)

                    p1.metric("ENTRY", fmt(plan["entry"], 4))
                    p2.metric("SL", fmt(plan["sl"], 4))
                    p3.metric("TP1", fmt(plan["tp1"], 4))
                    p4.metric("TP2", fmt(plan["tp2"], 4))
                    p5.metric("TP3", fmt(plan["tp3"], 4))

                for reason in score["reasons"]:
                    st.write("• " + reason)

                st.divider()

    # LIVE SIGNALS

    st.header("🚨 ÉLŐ JELZÉSEK")

    signal_rows = []

    for asset, data in all_data.items():

        for tf, item in data["timeframes"].items():

            if not item.get("score"):
                continue

            candle = item["candle"]
            score = item["score"]
            plan = item["plan"]

            signal_rows.append({
                "Eszköz": asset,
                "Idősík": tf,
                "Jelzés": item["final_direction"],
                "Matematikai irány": score["direction"],
                "AI irány": item["ai_direction"],
                "LONG pont": score["long_score"],
                "SHORT pont": score["short_score"],
                "Nettó score": score["score"],
                "Ár": candle["price"],
                "Entry": plan["entry"] if plan else None,
                "SL": plan["sl"] if plan else None,
                "TP1": plan["tp1"] if plan else None,
                "TP2": plan["tp2"] if plan else None,
                "TP3": plan["tp3"] if plan else None
            })

    if signal_rows:

        df = pd.DataFrame(signal_rows)

        priority = {
            "LONG": 0,
            "SHORT": 1,
            "WAIT": 2
        }

        df["_sort"] = df["Jelzés"].map(priority)

        df = df.sort_values(
            ["_sort", "Nettó score"],
            ascending=[True, False]
        )

        df = df.drop(columns=["_sort"])

        st.dataframe(
            df,
            use_container_width=True,
            hide_index=True
        )

    else:

        st.warning(
            "Nincs jelenleg értékelhető jelzés."
        )

    # SIGNAL JOURNAL

    st.header("📚 JELZÉSNAPLÓ")

    con = get_db()

    try:

        history = pd.read_sql_query("""
            SELECT
                asset,
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
            FROM signals
            ORDER BY id DESC
            LIMIT 100
        """, con)

    finally:
        con.close()

    if not history.empty:

        st.dataframe(
            history,
            use_container_width=True,
            hide_index=True
        )

    else:

        st.info("Még nincs rögzített jelzés.")

    # TP / SL EVENTS

    st.header("📍 TP / SL ESEMÉNYEK")

    con = get_db()

    try:

        events = pd.read_sql_query("""
            SELECT
                signal_key,
                event,
                price,
                created_at
            FROM signal_events
            ORDER BY id DESC
            LIMIT 100
        """, con)

    finally:
        con.close()

    if not events.empty:

        st.dataframe(
            events,
            use_container_width=True,
            hide_index=True
        )

    else:

        st.info("Még nincs TP vagy SL esemény.")

    # GROQ CHAT

    st.header("💬 SALIGA AI – KÉRDEZZ A PIACRÓL")

    question = st.text_area(
        "Kérdésed:",
        placeholder=(
            "Például: Miért emelkedik a BTC, "
            "miközben az OI csökken?"
        ),
        key="chat_question"
    )

    if st.button("AI válasz kérése"):

        if not question.strip():

            st.warning("Írj be egy kérdést.")

        elif not GROQ_API_KEY:

            st.error("Nincs beállítva a GROQ_API_KEY.")

        elif time.time() < st.session_state.groq_cooldown_until:

            st.warning("A Groq jelenleg korlátozást jelzett.")

        else:

            client = get_groq_client()

            context = {}

            for asset, data in all_data.items():

                context[asset] = {
                    "market": data["market"],
                    "timeframes": {}
                }

                for tf, item in data["timeframes"].items():

                    if item.get("score"):

                        context[asset]["timeframes"][tf] = {
                            "direction": item["final_direction"],
                            "ai_direction": item["ai_direction"],
                            "score": item["score"],
                            "price": item["candle"]["price"],
                            "structure": item["candle"]["structure"]
                        }

            try:

                with st.spinner("SALIGA AI válaszol..."):

                    response = client.chat.completions.create(
                        model=GROQ_MODEL,
                        messages=[
                            {
                                "role": "system",
                                "content": (
                                    "Te a SALIGA AI PRO magyar nyelvű "
                                    "kriptopiaci elemzője vagy. "
                                    "A megadott adatokból dolgozz. "
                                    "Ne találj ki hiányzó adatokat. "
                                    "Ne ígérj garantált eredményt."
                                )
                            },
                            {
                                "role": "user",
                                "content": (
                                    "Piaci adatok:\n"
                                    + json.dumps(
                                        context,
                                        ensure_ascii=False,
                                        default=str
                                    )
                                    + "\nKérdés: "
                                    + question
                                )
                            }
                        ],
                        max_completion_tokens=450
                    )

                    answer = (
                        response.choices[0]
                        .message.content
                    )

                    st.markdown("### SALIGA AI válasza")
                    st.write(answer)

            except Exception as e:

                error = str(e)

                if (
                    "429" in error
                    or "rate_limit" in error.lower()
                    or "tokens per day" in error.lower()
                ):

                    st.session_state.groq_cooldown_until = (
                        time.time() + GROQ_COOLDOWN_SECONDS
                    )

                st.error("Groq hiba: " + error)

    # SYSTEM STATUS

    st.header("⚙️ RENDSZERÁLLAPOT")

    c1, c2, c3 = st.columns(3)

    c1.metric(
        "OKX",
        "KAPCSOLVA" if all_data else "HIBA"
    )

    c2.metric(
        "GROQ",
        "KAPCSOLVA" if GROQ_API_KEY else "API-KULCS HIÁNYZIK"
    )

    c3.metric(
        "AI modell",
        GROQ_MODEL
    )

    st.caption(
        "SALIGA AI PRO | HYBRID MARKET INTELLIGENCE | "
        "Az elemzés tájékoztató jellegű. "
        "A kereskedési tervek nem végrehajtott megbízások."
    )


# ============================================================
# APP START
# ============================================================

dashboard()
