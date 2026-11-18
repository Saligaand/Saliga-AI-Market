from __future__ import annotations

import os
import json
import time
import sqlite3
import logging
from datetime import datetime, timezone

import ccxt
import numpy as np
import pandas as pd
import streamlit as st
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
# SALIGA AI PRO 11.0
# CENTRAL INTELLIGENCE + SIGNAL JOURNAL
# OKX ONLY
# ============================================================

APP_NAME = "SALIGA AI PRO 11.0"

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

TIMEFRAME_WEIGHTS = {
    "5 perc": 1,
    "15 perc": 2,
    "1 óra": 3,
    "4 óra": 4,
    "1 nap": 5,
}

DB_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "saliga_market.db",
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("SALIGA")


# ============================================================
# PAGE CONFIG
# ============================================================

st.set_page_config(
    page_title=APP_NAME,
    page_icon="📊",
    layout="wide",
)

st.markdown("""
<style>
.stApp {
    background: #f5f7fa;
    color: #172033;
}
[data-testid="stSidebar"] {
    background: #ffffff;
}
[data-testid="stMetric"] {
    background: #ffffff;
    border: 1px solid #e1e6ef;
    border-radius: 10px;
    padding: 12px;
}
.long-text {
    color: #079455;
    font-weight: 800;
}
.short-text {
    color: #d92d20;
    font-weight: 800;
}
.neutral-text {
    color: #b7791f;
    font-weight: 800;
}
.thought-box {
    background: #ffffff;
    border: 1px solid #dce3ed;
    border-left: 5px solid #475467;
    border-radius: 8px;
    padding: 18px;
    margin: 10px 0;
}
</style>
""", unsafe_allow_html=True)


# ============================================================
# DATABASE
# ============================================================

def db_connection():
    conn = sqlite3.connect(
        DB_PATH,
        timeout=30,
        check_same_thread=False,
    )

    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")

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

    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_market_history_symbol_time
        ON market_history(symbol, timestamp)
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS signals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            signal_key TEXT UNIQUE,
            symbol TEXT NOT NULL,
            timeframe TEXT NOT NULL,
            direction TEXT NOT NULL,
            created_at TEXT NOT NULL,
            candle_timestamp INTEGER,
            entry REAL NOT NULL,
            stop_loss REAL NOT NULL,
            tp1 REAL NOT NULL,
            tp2 REAL NOT NULL,
            tp3 REAL NOT NULL,
            status TEXT DEFAULT 'OPEN',
            hit_tp1 INTEGER DEFAULT 0,
            hit_tp2 INTEGER DEFAULT 0,
            hit_tp3 INTEGER DEFAULT 0,
            last_price REAL,
            exit_price REAL,
            closed_at TEXT
        )
    """)

    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_signals_status
        ON signals(status)
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS signal_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            signal_id INTEGER NOT NULL,
            event_type TEXT NOT NULL,
            price REAL NOT NULL,
            event_time TEXT NOT NULL,
            FOREIGN KEY(signal_id) REFERENCES signals(id)
        )
    """)

    conn.commit()
    return conn


def save_snapshot(symbol, price, oi, delta):
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


def get_previous_oi_snapshot(symbol):
    conn = db_connection()
    now = int(time.time())

    row = conn.execute("""
        SELECT timestamp, price, open_interest, delta
        FROM market_history
        WHERE symbol = ?
          AND open_interest IS NOT NULL
          AND timestamp <= ?
          AND timestamp >= ?
        ORDER BY timestamp DESC
        LIMIT 1
    """, (
        symbol,
        now - 300,
        now - 86400,
    )).fetchone()

    conn.close()
    return row


# ============================================================
# EXCHANGE
# ============================================================

@st.cache_resource
def get_exchange():
    exchange = ccxt.okx({
        "enableRateLimit": True,
        "timeout": 25000,
        "options": {
            "defaultType": "swap",
        },
    })

    exchange.load_markets()
    return exchange


def safe_api_call(function, default=None):
    try:
        return function()
    except Exception as exc:
        logger.warning("API error: %s", exc)
        return default


# ============================================================
# MARKET DATA
# ============================================================

def fetch_ohlcv(symbol, timeframe, limit=200):
    exchange = get_exchange()

    candles = exchange.fetch_ohlcv(
        symbol,
        timeframe=timeframe,
        limit=limit,
    )

    if not candles or len(candles) < 3:
        return None

    df = pd.DataFrame(
        candles,
        columns=[
            "timestamp",
            "open",
            "high",
            "low",
            "close",
            "volume",
        ],
    )

    for column in [
        "open",
        "high",
        "low",
        "close",
        "volume",
    ]:
        df[column] = pd.to_numeric(
            df[column],
            errors="coerce",
        )

    df = df.dropna().reset_index(drop=True)

    # Az utolsó gyertya még formálódhat.
    # Az elemzés kizárólag lezárt gyertyákat használ.
    if len(df) > 2:
        df = df.iloc[:-1].reset_index(drop=True)

    return df


def fetch_ticker(symbol):
    return get_exchange().fetch_ticker(symbol)


def fetch_orderbook(symbol):
    return get_exchange().fetch_order_book(
        symbol,
        limit=50,
    )


def fetch_trades(symbol):
    return get_exchange().fetch_trades(
        symbol,
        limit=300,
    )


def fetch_open_interest(symbol):
    try:
        result = get_exchange().fetch_open_interest(symbol)

        if not result:
            return None

        info = result.get("info") or {}

        candidates = [
            result.get("openInterestAmount"),
            result.get("openInterestValue"),
            result.get("openInterest"),
            info.get("oi"),
            info.get("oiCcy"),
        ]

        for value in candidates:
            if value is None:
                continue

            number = float(value)

            if np.isfinite(number) and number > 0:
                return number

        return None

    except Exception as exc:
        logger.warning("OI error: %s", exc)
        return None


def fetch_funding(symbol):
    try:
        result = get_exchange().fetch_funding_rate(symbol)
        value = result.get("fundingRate")

        if value is None:
            return None

        return float(value)

    except Exception as exc:
        logger.warning("Funding error: %s", exc)
        return None


# ============================================================
# INDICATORS
# ============================================================

def calculate_rsi(series, period=14):
    change = series.diff()

    gains = change.clip(lower=0)
    losses = -change.clip(upper=0)

    avg_gain = gains.rolling(period).mean()
    avg_loss = losses.rolling(period).mean()

    rs = avg_gain / avg_loss.replace(0, np.nan)

    result = 100 - (100 / (1 + rs))

    return result.fillna(50)


def calculate_atr(df, period=14):
    previous_close = df["close"].shift(1)

    true_range = pd.concat(
        [
            df["high"] - df["low"],
            (df["high"] - previous_close).abs(),
            (df["low"] - previous_close).abs(),
        ],
        axis=1,
    ).max(axis=1)

    return true_range.rolling(period).mean()


def calculate_vwap(df, period=48):
    typical_price = (
        df["high"] + df["low"] + df["close"]
    ) / 3

    weighted_price = (
        typical_price * df["volume"]
    ).rolling(period).sum()

    total_volume = df["volume"].rolling(period).sum()

    return weighted_price / total_volume.replace(0, np.nan)


def calculate_momentum(df, lookback=5):
    if len(df) <= lookback:
        return 0.0

    old_price = float(df["close"].iloc[-lookback - 1])
    current_price = float(df["close"].iloc[-1])

    if old_price == 0:
        return 0.0

    return (current_price - old_price) / old_price * 100


# ============================================================
# MARKET STRUCTURE
# ============================================================

def detect_structure(df):
    if df is None or len(df) < 30:
        return "NINCS ELEGENDŐ ADAT", 0

    current = df.iloc[-1]
    previous = df.iloc[-2]

    reference = df.iloc[-26:-1]

    recent_high = float(reference["high"].max())
    recent_low = float(reference["low"].min())

    close = float(current["close"])

    if close > recent_high:
        return "BULLISH KITÖRÉS", 3

    if close < recent_low:
        return "BEARISH KITÖRÉS", -3

    if (
        float(current["high"]) > recent_high
        and close < recent_high
    ):
        return "FELSŐ LIKVIDITÁS VISSZAUTASÍTVA", -2

    if (
        float(current["low"]) < recent_low
        and close > recent_low
    ):
        return "ALSÓ LIKVIDITÁS VISSZAUTASÍTVA", 2

    if close > float(previous["close"]):
        return "ENYHÉN EMELKEDŐ STRUKTÚRA", 1

    if close < float(previous["close"]):
        return "ENYHÉN CSÖKKENŐ STRUKTÚRA", -1

    return "KONSZOLIDÁCIÓ", 0


def detect_liquidity_sweep(df):
    if df is None or len(df) < 30:
        return "NINCS ADAT"

    current = df.iloc[-1]
    reference = df.iloc[-26:-1]

    high = float(reference["high"].max())
    low = float(reference["low"].min())

    if (
        float(current["high"]) > high
        and float(current["close"]) < high
    ):
        return "FELSŐ LIKVIDITÁS KISÖPRÉSE"

    if (
        float(current["low"]) < low
        and float(current["close"]) > low
    ):
        return "ALSÓ LIKVIDITÁS KISÖPRÉSE"

    return "NINCS FRISS LIKVIDITÁSI SÖPRÉS"


# ============================================================
# ORDER FLOW
# ============================================================

def calculate_trade_delta(trades):
    buy_volume = 0.0
    sell_volume = 0.0

    for trade in trades or []:
        side = trade.get("side")
        amount = trade.get("amount")

        if amount is None:
            continue

        amount = float(amount)

        if side == "buy":
            buy_volume += amount

        elif side == "sell":
            sell_volume += amount

    total = buy_volume + sell_volume
    delta = buy_volume - sell_volume

    delta_percent = (
        delta / total * 100
        if total > 0
        else 0
    )

    if delta_percent > 5:
        pressure = "VEVŐI FÖLÉNY"
    elif delta_percent < -5:
        pressure = "ELADÓI FÖLÉNY"
    else:
        pressure = "KIEGYENSÚLYOZOTT"

    return {
        "buy_volume": buy_volume,
        "sell_volume": sell_volume,
        "delta": delta,
        "delta_percent": delta_percent,
        "pressure": pressure,
        "trade_count": len(trades or []),
    }


def calculate_orderbook_imbalance(orderbook):
    if not orderbook:
        return {
            "imbalance": None,
            "state": "NINCS ORDER BOOK ADAT",
            "bid_volume": 0,
            "ask_volume": 0,
        }

    bids = orderbook.get("bids", [])[:20]
    asks = orderbook.get("asks", [])[:20]

    bid_volume = sum(
        float(level[1])
        for level in bids
    )

    ask_volume = sum(
        float(level[1])
        for level in asks
    )

    total = bid_volume + ask_volume

    if total <= 0:
        return {
            "imbalance": None,
            "state": "NINCS HASZNÁLHATÓ ADAT",
            "bid_volume": 0,
            "ask_volume": 0,
        }

    imbalance = (
        (bid_volume - ask_volume) / total
    ) * 100

    if imbalance > 10:
        state = "VÉTELI OLDALI FÖLÉNY"
    elif imbalance < -10:
        state = "ELADÁSI OLDALI FÖLÉNY"
    else:
        state = "KIEGYENSÚLYOZOTT ORDER BOOK"

    return {
        "imbalance": imbalance,
        "state": state,
        "bid_volume": bid_volume,
        "ask_volume": ask_volume,
    }


# ============================================================
# OPEN INTEREST ANALYSIS
# ============================================================

def analyze_open_interest(name, current_oi, current_price):
    if current_oi is None:
        return {
            "change": None,
            "state": "OI ADAT NEM ELÉRHETŐ",
            "direction": 0,
            "confidence": "NINCS ADAT",
        }

    previous = get_previous_oi_snapshot(name)

    if not previous:
        return {
            "change": None,
            "state": "OI ELŐZMÉNY GYŰJTÉSE",
            "direction": 0,
            "confidence": "NINCS ELŐZMÉNY",
        }

    old_timestamp, old_price, old_oi, _ = previous

    if not old_oi or not old_price:
        return {
            "change": None,
            "state": "NEM ÉRTÉKELHETŐ",
            "direction": 0,
            "confidence": "HIÁNYOS ADAT",
        }

    oi_change = (
        (current_oi - old_oi) / abs(old_oi)
    ) * 100

    price_change = (
        (current_price - old_price) / abs(old_price)
    ) * 100

    if oi_change > 0.15 and price_change > 0.05:
        state = "LONG ÉPÜLÉS BECSLÉSE"
        direction = 1

    elif oi_change > 0.15 and price_change < -0.05:
        state = "SHORT ÉPÜLÉS BECSLÉSE"
        direction = -1

    elif oi_change < -0.15 and price_change > 0.05:
        state = "SHORT ZÁRÁS LEHETŐSÉGE"
        direction = 1

    elif oi_change < -0.15 and price_change < -0.05:
        state = "LONG ZÁRÁS LEHETŐSÉGE"
        direction = -1

    else:
        state = "NINCS EGYÉRTELMŰ POZÍCIÓÉPÜLÉS"
        direction = 0

    return {
        "change": oi_change,
        "price_change": price_change,
        "state": state,
        "direction": direction,
        "confidence": "BECSLÉS",
        "elapsed_seconds": int(time.time() - old_timestamp),
    }


# ============================================================
# TIMEFRAME ANALYSIS
# ============================================================

def analyze_timeframe(df, label):
    if df is None or len(df) < 50:
        return {
            "timeframe": label,
            "direction": "NINCS ADAT",
            "score": 0,
            "strength": "NEM ÉRTÉKELHETŐ",
            "structure": "NINCS ADAT",
            "rsi": None,
            "momentum": None,
            "volume_ratio": None,
            "volume_state": "NINCS ADAT",
            "sweep": "NINCS ADAT",
            "price": None,
            "atr": None,
            "reasons": [],
        }

    current = df.iloc[-1]
    price = float(current["close"])

    rsi = float(
        calculate_rsi(df["close"]).iloc[-1]
    )

    momentum = calculate_momentum(df)

    atr_value = calculate_atr(df).iloc[-1]

    atr = (
        float(atr_value)
        if pd.notna(atr_value)
        else None
    )

    vwap_value = calculate_vwap(df).iloc[-1]

    vwap = (
        float(vwap_value)
        if pd.notna(vwap_value)
        else None
    )

    structure, structure_score = detect_structure(df)

    average_volume = float(
        df["volume"].iloc[-21:-1].mean()
    )

    current_volume = float(current["volume"])

    volume_ratio = (
        current_volume / average_volume
        if average_volume > 0
        else 1
    )

    score = 0
    reasons = []

    score += structure_score * 2

    if structure_score > 0:
        reasons.append("Emelkedő struktúra")
    elif structure_score < 0:
        reasons.append("Csökkenő struktúra")

    if rsi > 55:
        score += 2
        reasons.append("RSI vevői erőt mutat")
    elif rsi < 45:
        score -= 2
        reasons.append("RSI eladói erőt mutat")

    if momentum > 0.15:
        score += 2
        reasons.append("Pozitív momentum")
    elif momentum < -0.15:
        score -= 2
        reasons.append("Negatív momentum")

    if vwap is not None:
        if price > vwap:
            score += 1
            reasons.append("Ár a VWAP felett")
        elif price < vwap:
            score -= 1
            reasons.append("Ár a VWAP alatt")

    if volume_ratio >= 1.3:
        if momentum > 0:
            score += 1
            reasons.append("Emelkedés magasabb forgalommal")
        elif momentum < 0:
            score -= 1
            reasons.append("Esés magasabb forgalommal")

    if score >= 5:
        direction = "LONG"
    elif score <= -5:
        direction = "SHORT"
    else:
        direction = "SEMLEGES"

    abs_score = abs(score)

    if abs_score >= 10:
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

    return {
        "timeframe": label,
        "direction": direction,
        "score": score,
        "strength": strength,
        "structure": structure,
        "rsi": rsi,
        "momentum": momentum,
        "volume_ratio": volume_ratio,
        "volume_state": volume_state,
        "sweep": detect_liquidity_sweep(df),
        "price": price,
        "atr": atr,
        "vwap": vwap,
        "reasons": reasons,
    }


# ============================================================
# MULTI-TIMEFRAME SUMMARY
# ============================================================

def summarize_timeframes(timeframes):
    weighted_score = 0.0
    total_weight = 0.0

    long_count = 0
    short_count = 0
    neutral_count = 0

    for label, result in timeframes.items():
        if result["direction"] == "NINCS ADAT":
            continue

        weight = TIMEFRAME_WEIGHTS[label]

        weighted_score += result["score"] * weight
        total_weight += weight

        if result["direction"] == "LONG":
            long_count += 1
        elif result["direction"] == "SHORT":
            short_count += 1
        else:
            neutral_count += 1

    if total_weight == 0:
        return {
            "direction": "NINCS ADAT",
            "score": 0,
            "agreement": "NEM ÉRTÉKELHETŐ",
            "long_count": 0,
            "short_count": 0,
            "neutral_count": 0,
            "valid_count": 0,
        }

    final_score = weighted_score / total_weight

    if final_score >= 3:
        direction = "LONG"
    elif final_score <= -3:
        direction = "SHORT"
    else:
        direction = "SEMLEGES"

    if long_count >= 4 and short_count == 0:
        agreement = "ERŐS LONG EGYBEHANGZÁS"
    elif short_count >= 4 and long_count == 0:
        agreement = "ERŐS SHORT EGYBEHANGZÁS"
    elif long_count > 0 and short_count > 0:
        agreement = "IDŐSÍKOK KÖZÖTTI ELLENTMONDÁS"
    else:
        agreement = "VEGYES / BIZONYTALAN PIAC"

    return {
        "direction": direction,
        "score": round(final_score, 2),
        "agreement": agreement,
        "long_count": long_count,
        "short_count": short_count,
        "neutral_count": neutral_count,
        "valid_count": long_count + short_count + neutral_count,
    }


# ============================================================
# CENTRAL INTELLIGENCE
# ============================================================

def central_intelligence(data):
    tf = data["timeframes"]
    mtf = data["mtf"]

    long_reasons = []
    short_reasons = []
    conflicts = []
    watch_items = []

    for label, result in tf.items():
        direction = result["direction"]

        if direction == "LONG":
            long_reasons.append(
                f"{label}: LONG jelzés ({result['strength'].lower()})"
            )

        elif direction == "SHORT":
            short_reasons.append(
                f"{label}: SHORT jelzés ({result['strength'].lower()})"
            )

        elif direction == "NINCS ADAT":
            watch_items.append(
                f"{label}: nincs elegendő adat"
            )

    if mtf["long_count"] > 0 and mtf["short_count"] > 0:
        conflicts.append(
            "A rövidebb és hosszabb idősíkok eltérő irányt mutatnak."
        )

    delta = data["delta"]

    if delta["delta_percent"] > 5:
        long_reasons.append(
            "A lekért kötésekben vevői fölény látható."
        )
    elif delta["delta_percent"] < -5:
        short_reasons.append(
            "A lekért kötésekben eladói fölény látható."
        )
    else:
        watch_items.append(
            "A delta nem mutat jelentős fölényt."
        )

    imbalance = data["orderbook"]["imbalance"]

    if imbalance is not None:
        if imbalance > 10:
            long_reasons.append(
                "Az order book pillanatképe vételi fölényt mutat."
            )
        elif imbalance < -10:
            short_reasons.append(
                "Az order book pillanatképe eladási fölényt mutat."
            )
        else:
            watch_items.append(
                "Az order book jelenleg kiegyensúlyozott."
            )
    else:
        watch_items.append(
            "Az order book adata nem érhető el."
        )

    oi = data["oi_analysis"]

    if oi["direction"] > 0:
        long_reasons.append(oi["state"])
    elif oi["direction"] < 0:
        short_reasons.append(oi["state"])
    else:
        watch_items.append(oi["state"])

    funding = data["funding"]

    if funding is not None:
        if funding > 0.0005:
            watch_items.append(
                "A pozitív funding miatt a long pozíciók költsége magasabb lehet."
            )
        elif funding < -0.0005:
            watch_items.append(
                "A negatív funding miatt a short pozíciók költsége magasabb lehet."
            )

    sweeps = [
        result["sweep"]
        for result in tf.values()
        if result["sweep"] not in (
            "NINCS FRISS LIKVIDITÁSI SÖPRÉS",
            "NINCS ADAT",
        )
    ]

    if sweeps:
        watch_items.extend(sweeps)

    overall = mtf["direction"]

    if (
        len(long_reasons) >= 2
        and len(short_reasons) >= 2
    ):
        confidence = "VEGYES JELZÉSEK"
    elif overall == "LONG" and len(long_reasons) >= 3:
        confidence = "TÖBB TÉNYEZŐ ÁLTAL TÁMOGATOTT LONG"
    elif overall == "SHORT" and len(short_reasons) >= 3:
        confidence = "TÖBB TÉNYEZŐ ÁLTAL TÁMOGATOTT SHORT"
    else:
        confidence = "KORLÁTOZOTT MEGERŐSÍTÉS"

    if overall == "LONG":
        conclusion = (
            "Az összesített idősíkos értékelés LONG irányba mutat. "
            "A vételi struktúrát a delta, az OI és az order book "
            "adataival együtt kell ellenőrizni."
        )

        waiting_for = (
            "A vételi nyomás fennmaradására, a rövidebb idősíkok "
            "megerősítésére és az ellenállások áttörésére."
        )

        invalidation = (
            "A vételi delta eltűnése, az árstruktúra letörése "
            "és a tartós eladói fölény gyengítené az értékelést."
        )

    elif overall == "SHORT":
        conclusion = (
            "Az összesített idősíkos értékelés SHORT irányba mutat. "
            "Az eladói struktúrát a delta, az OI és az order book "
            "adataival együtt kell ellenőrizni."
        )

        waiting_for = (
            "Az eladói nyomás fennmaradására és a fontos támaszok letörésére."
        )

        invalidation = (
            "A tartós vevői fölény és a fontos ellenállások visszavétele "
            "gyengítené az értékelést."
        )

    else:
        conclusion = (
            "A jelenlegi összesített kép nem ad kellően egyértelmű irányt. "
            "A rendszer kivárást jelez."
        )

        waiting_for = (
            "Egyértelmű kitörésre, több idősíkon megerősített irányra "
            "és az order flow támogatására."
        )

        invalidation = (
            "Jelenleg nincs érvényesített LONG vagy SHORT terv."
        )

    return {
        "direction": overall,
        "confidence": confidence,
        "conclusion": conclusion,
        "long_reasons": long_reasons,
        "short_reasons": short_reasons,
        "conflicts": conflicts,
        "watch_items": watch_items,
        "waiting_for": waiting_for,
        "invalidation": invalidation,
        "agreement": mtf["agreement"],
    }


# ============================================================
# SUPPORT / RESISTANCE
# ============================================================

def calculate_levels(df):
    if df is None or len(df) < 30:
        return None, None

    reference = df.iloc[-30:-1]

    return (
        float(reference["low"].min()),
        float(reference["high"].max()),
    )


# ============================================================
# TRADE PLAN
# ============================================================

def calculate_trade_plan(price, atr, direction, support, resistance):
    if atr is None or atr <= 0:
        return None

    if direction == "LONG":
        stop = price - atr * 1.5

        if support is not None and support < price:
            candidate = support - atr * 0.1

            if price - candidate < atr * 2.5:
                stop = min(stop, candidate)

        risk = price - stop

        if risk <= 0:
            return None

        return {
            "direction": "LONG",
            "entry": price,
            "stop": stop,
            "tp1": price + risk,
            "tp2": price + risk * 2,
            "tp3": price + risk * 3,
            "risk": risk,
        }

    if direction == "SHORT":
        stop = price + atr * 1.5

        if resistance is not None and resistance > price:
            candidate = resistance + atr * 0.1

            if candidate - price < atr * 2.5:
                stop = max(stop, candidate)

        risk = stop - price

        if risk <= 0:
            return None

        return {
            "direction": "SHORT",
            "entry": price,
            "stop": stop,
            "tp1": price - risk,
            "tp2": price - risk * 2,
            "tp3": price - risk * 3,
            "risk": risk,
        }

    return None


# ============================================================
# COMPLETE COIN ANALYSIS
# ============================================================

def analyze_coin(name, symbol):
    result = {
        "name": name,
        "symbol": symbol,
        "error": None,
        "timeframes": {},
    }

    try:
        ticker = fetch_ticker(symbol)

        price = float(ticker["last"])

        frames = {}

        for label, timeframe in TIMEFRAMES.items():
            df = fetch_ohlcv(
                symbol,
                timeframe,
                200,
            )

            frames[label] = df

            result["timeframes"][label] = analyze_timeframe(
                df,
                label,
            )

        df15 = frames.get("15 perc")

        if df15 is None or len(df15) < 50:
            raise RuntimeError(
                "A 15 perces adatok nem érhetők el megfelelő mennyiségben."
            )

        # A jelzésnapló az utolsó lezárt gyertya időbélyegét használja.
        result["df15_timestamp"] = int(
            df15.iloc[-1]["timestamp"]
        )

        trades = safe_api_call(
            lambda: fetch_trades(symbol),
            [],
        )

        orderbook_raw = safe_api_call(
            lambda: fetch_orderbook(symbol),
            None,
        )

        oi = safe_api_call(
            lambda: fetch_open_interest(symbol),
            None,
        )

        funding = safe_api_call(
            lambda: fetch_funding(symbol),
            None,
        )

        delta = calculate_trade_delta(trades)

        orderbook = calculate_orderbook_imbalance(
            orderbook_raw
        )

        oi_analysis = analyze_open_interest(
            name,
            oi,
            price,
        )

        mtf = summarize_timeframes(
            result["timeframes"]
        )

        support, resistance = calculate_levels(df15)

        atr_value = calculate_atr(df15).iloc[-1]

        atr = (
            float(atr_value)
            if pd.notna(atr_value)
            else None
        )

        result.update({
            "price": price,
            "delta": delta,
            "orderbook": orderbook,
            "oi": oi,
            "oi_analysis": oi_analysis,
            "funding": funding,
            "mtf": mtf,
            "support": support,
            "resistance": resistance,
            "atr": atr,
            "last_update": datetime.now().strftime(
                "%Y-%m-%d %H:%M:%S"
            ),
        })

        result["intelligence"] = central_intelligence(result)

        result["plan"] = calculate_trade_plan(
            price,
            atr,
            result["intelligence"]["direction"],
            support,
            resistance,
        )

        save_snapshot(
            name,
            price,
            oi,
            delta["delta"],
        )

        return result

    except Exception as exc:
        logger.exception("Coin analysis failed: %s", name)
        result["error"] = str(exc)
        return result


# ============================================================
# MARKET CACHE
# ============================================================

@st.cache_data(ttl=30, show_spinner=False)
def load_market_data():
    output = {}

    for name, symbol in SYMBOLS.items():
        output[name] = analyze_coin(
            name,
            symbol,
        )

    return output


# ============================================================
# SIGNAL JOURNAL
# ============================================================

def create_signal(name, data):
    plan = data.get("plan")

    if not plan:
        return

    direction = plan["direction"]

    if direction not in ("LONG", "SHORT"):
        return

    candle_timestamp = data.get("df15_timestamp")

    if not candle_timestamp:
        return

    signal_key = (
        f"{name}_15m_{direction}_{candle_timestamp}"
    )

    conn = db_connection()

    try:
        conn.execute("""
            INSERT OR IGNORE INTO signals (
                signal_key,
                symbol,
                timeframe,
                direction,
                created_at,
                candle_timestamp,
                entry,
                stop_loss,
                tp1,
                tp2,
                tp3,
                last_price
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            signal_key,
            name,
            "15m",
            direction,
            datetime.now(timezone.utc).isoformat(),
            candle_timestamp,
            float(plan["entry"]),
            float(plan["stop"]),
            float(plan["tp1"]),
            float(plan["tp2"]),
            float(plan["tp3"]),
            float(data["price"]),
        ))

        conn.commit()

    finally:
        conn.close()


def record_signal_event(conn, signal_id, event_type, price):
    conn.execute("""
        INSERT INTO signal_events (
            signal_id,
            event_type,
            price,
            event_time
        )
        VALUES (?, ?, ?, ?)
    """, (
        signal_id,
        event_type,
        float(price),
        datetime.now(timezone.utc).isoformat(),
    ))


def update_signal_journal():
    conn = db_connection()

    rows = conn.execute("""
        SELECT
            id,
            symbol,
            direction,
            entry,
            stop_loss,
            tp1,
            tp2,
            tp3,
            status,
            hit_tp1,
            hit_tp2,
            hit_tp3
        FROM signals
        WHERE status NOT IN (
            'STOP_LOSS',
            'STOP_AFTER_TP1',
            'STOP_AFTER_TP2',
            'TP3'
        )
    """).fetchall()

    conn.close()

    exchange = get_exchange()

    for row in rows:
        (
            signal_id,
            name,
            direction,
            entry,
            stop,
            tp1,
            tp2,
            tp3,
            status,
            hit_tp1,
            hit_tp2,
            hit_tp3,
        ) = row

        symbol = SYMBOLS.get(name)

        if not symbol:
            continue

        ticker = safe_api_call(
            lambda: exchange.fetch_ticker(symbol),
            None,
        )

        if not ticker or ticker.get("last") is None:
            continue

        price = float(ticker["last"])

        conn = db_connection()

        try:
            signal = conn.execute("""
                SELECT status, hit_tp1, hit_tp2, hit_tp3
                FROM signals
                WHERE id = ?
            """, (signal_id,)).fetchone()

            if not signal:
                continue

            status, hit_tp1, hit_tp2, hit_tp3 = signal

            if status in (
                "STOP_LOSS",
                "STOP_AFTER_TP1",
                "STOP_AFTER_TP2",
                "TP3",
            ):
                continue

            # LONG
            if direction == "LONG":

                # A stopot először vizsgáljuk.
                if price <= stop:

                    if hit_tp2:
                        final_status = "STOP_AFTER_TP2"
                    elif hit_tp1:
                        final_status = "STOP_AFTER_TP1"
                    else:
                        final_status = "STOP_LOSS"

                    conn.execute("""
                        UPDATE signals
                        SET status = ?,
                            last_price = ?,
                            exit_price = ?,
                            closed_at = ?
                        WHERE id = ?
                    """, (
                        final_status,
                        price,
                        price,
                        datetime.now(timezone.utc).isoformat(),
                        signal_id,
                    ))

                    record_signal_event(
                        conn,
                        signal_id,
                        final_status,
                        price,
                    )

                else:
                    targets = [
                        ("TP1", tp1, hit_tp1),
                        ("TP2", tp2, hit_tp2),
                        ("TP3", tp3, hit_tp3),
                    ]

                    for label, target, already_hit in targets:

                        if price >= target and not already_hit:

                            if label == "TP1":
                                conn.execute("""
                                    UPDATE signals
                                    SET hit_tp1 = 1,
                                        status = 'TP1'
                                    WHERE id = ?
                                """, (signal_id,))

                            elif label == "TP2":
                                conn.execute("""
                                    UPDATE signals
                                    SET hit_tp2 = 1,
                                        status = 'TP2'
                                    WHERE id = ?
                                """, (signal_id,))

                            elif label == "TP3":
                                conn.execute("""
                                    UPDATE signals
                                    SET hit_tp3 = 1,
                                        status = 'TP3',
                                        exit_price = ?,
                                        closed_at = ?
                                    WHERE id = ?
                                """, (
                                    price,
                                    datetime.now(timezone.utc).isoformat(),
                                    signal_id,
                                ))

                            record_signal_event(
                                conn,
                                signal_id,
                                label,
                                price,
                            )

            # SHORT
            elif direction == "SHORT":

                if price >= stop:

                    if hit_tp2:
                        final_status = "STOP_AFTER_TP2"
                    elif hit_tp1:
                        final_status = "STOP_AFTER_TP1"
                    else:
                        final_status = "STOP_LOSS"

                    conn.execute("""
                        UPDATE signals
                        SET status = ?,
                            last_price = ?,
                            exit_price = ?,
                            closed_at = ?
                        WHERE id = ?
                    """, (
                        final_status,
                        price,
                        price,
                        datetime.now(timezone.utc).isoformat(),
                        signal_id,
                    ))

                    record_signal_event(
                        conn,
                        signal_id,
                        final_status,
                        price,
                    )

                else:
                    targets = [
                        ("TP1", tp1, hit_tp1),
                        ("TP2", tp2, hit_tp2),
                        ("TP3", tp3, hit_tp3),
                    ]

                    for label, target, already_hit in targets:

                        if price <= target and not already_hit:

                            if label == "TP1":
                                conn.execute("""
                                    UPDATE signals
                                    SET hit_tp1 = 1,
                                        status = 'TP1'
                                    WHERE id = ?
                                """, (signal_id,))

                            elif label == "TP2":
                                conn.execute("""
                                    UPDATE signals
                                    SET hit_tp2 = 1,
                                        status = 'TP2'
                                    WHERE id = ?
                                """, (signal_id,))

                            elif label == "TP3":
                                conn.execute("""
                                    UPDATE signals
                                    SET hit_tp3 = 1,
                                        status = 'TP3',
                                        exit_price = ?,
                                        closed_at = ?
                                    WHERE id = ?
                                """, (
                                    price,
                                    datetime.now(timezone.utc).isoformat(),
                                    signal_id,
                                ))

                            record_signal_event(
                                conn,
                                signal_id,
                                label,
                                price,
                            )

            conn.execute("""
                UPDATE signals
                SET last_price = ?
                WHERE id = ?
            """, (price, signal_id))

            conn.commit()

        finally:
            conn.close()


def get_signal_journal():
    conn = db_connection()

    signals = pd.read_sql_query("""
        SELECT *
        FROM signals
        ORDER BY id DESC
        LIMIT 500
    """, conn)

    events = pd.read_sql_query("""
        SELECT
            signal_events.*,
            signals.symbol,
            signals.direction
        FROM signal_events
        JOIN signals
        ON signals.id = signal_events.signal_id
        ORDER BY signal_events.id DESC
        LIMIT 500
    """, conn)

    conn.close()

    return signals, events


# ============================================================
# NEWS
# ============================================================

def fetch_news():
    feeds = [
        "https://www.coindesk.com/arc/outboundfeeds/rss/",
        "https://cointelegraph.com/rss",
    ]

    items = []

    for url in feeds:
        try:
            feed = feedparser.parse(url)

            for entry in feed.entries[:8]:
                items.append({
                    "title": entry.get("title", ""),
                    "link": entry.get("link", ""),
                    "published": entry.get("published", ""),
                })

        except Exception as exc:
            logger.warning("News error: %s", exc)

    return items[:15]


# ============================================================
# GROQ
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


def market_context_text(market_data):
    context = {}

    for name, data in market_data.items():

        if data.get("error"):
            context[name] = {
                "error": data["error"],
            }
            continue

        context[name] = {
            "price": data.get("price"),
            "timeframes": data.get("timeframes"),
            "mtf": data.get("mtf"),
            "intelligence": data.get("intelligence"),
            "delta": data.get("delta"),
            "orderbook": data.get("orderbook"),
            "oi_analysis": data.get("oi_analysis"),
            "funding": data.get("funding"),
            "support": data.get("support"),
            "resistance": data.get("resistance"),
            "plan": data.get("plan"),
        }

    return json.dumps(
        context,
        ensure_ascii=False,
        default=str,
    )


def local_market_answer(question, market_data):
    """
    Groq nélkül is használható egyszerű belső válasz.
    A válasz kizárólag a betöltött piaci adatokból dolgozik.
    """

    lines = [
        "SALIGA AI – BELSŐ PIACI ADATOK",
        "",
        f"Kérdés: {question}",
        "",
    ]

    for name, data in market_data.items():

        if data.get("error"):
            lines.append(
                f"{name}: az adatok nem érhetők el. "
                f"Hiba: {data['error']}"
            )
            continue

        intelligence = data["intelligence"]
        plan = data.get("plan")

        lines.append(
            f"{name}: {intelligence['direction']} – "
            f"{intelligence['confidence']}"
        )

        lines.append(
            f"Ár: {data['price']:,.4f} USDT"
        )

        lines.append(
            f"Idősíkok: {data['mtf']['agreement']}"
        )

        lines.append(
            f"Delta: {data['delta']['delta_percent']:.2f}%"
        )

        lines.append(
            f"OI: {data['oi_analysis']['state']}"
        )

        lines.append(
            f"Order book: {data['orderbook']['state']}"
        )

        if plan:
            lines.append(
                f"Referencia Entry: {plan['entry']:.4f}"
            )
            lines.append(
                f"Stop Loss: {plan['stop']:.4f}"
            )
            lines.append(
                f"TP1: {plan['tp1']:.4f}"
            )
            lines.append(
                f"TP2: {plan['tp2']:.4f}"
            )
            lines.append(
                f"TP3: {plan['tp3']:.4f}"
            )
        else:
            lines.append(
                "Nincs érvényesített kereskedési terv."
            )

        lines.append("")

    lines.append(
        "Megjegyzés: az OI-alapú pozícióáramlás becslés, "
        "a delta a lekért kötésekből származik, "
        "az order book pedig pillanatfelvétel."
    )

    return "\n".join(lines)


def ask_groq(question, market_data):
    client = get_groq_client()

    if client is None:
        return local_market_answer(
            question,
            market_data,
        )

    context = market_context_text(market_data)

    try:
        response = client.chat.completions.create(
            model="openai/gpt-oss-120b",
            messages=[
                {
                    "role": "system",
                    "content": """
Te a SALIGA AI piaci asszisztense vagy.

A kapott adatok a belső elemzőmotor tényleges eredményei.

Szabályok:
- Ne találj ki piaci adatokat.
- Ne állítsd, hogy biztosan tudod, mit csinálnak a szereplők.
- Az OI-alapú pozícióépülés csak becslés.
- A delta csak a lekért kötések becslése.
- Az order book pillanatfelvétel.
- Vedd figyelembe mind az öt idősíkot.
- Jelezd az ellentmondásokat.
- Magyarázd el, mely adatok támogatják és gyengítik az irányt.
- Ha nincs elég adat, mondd ki.
- Ne ígérj nyereséget.
- Ne használj EMA- vagy SMA-alapú jelzéseket.
- Magyarul, világosan és konkrétan válaszolj.
"""
                },
                {
                    "role": "user",
                    "content": (
                        "AKTUÁLIS PIACI ADATOK:\n"
                        + context
                        + "\n\nKÉRDÉS:\n"
                        + question
                    ),
                },
            ],
            temperature=0.2,
        )

        return response.choices[0].message.content

    except Exception as exc:
        logger.warning("Groq error: %s", exc)

        return (
            "A külső AI jelenleg nem válaszol. "
            "Az alábbi válasz a belső SALIGA adatokból készült:\n\n"
            + local_market_answer(question, market_data)
        )


# ============================================================
# SIDEBAR
# ============================================================

st.sidebar.title("⚡ SALIGA AI PRO")

refresh_seconds = st.sidebar.selectbox(
    "Automatikus frissítés",
    [30, 60, 120, 300],
    index=1,
)

selected_coin = st.sidebar.selectbox(
    "Kiemelt instrumentum",
    ["BTC", "ETH", "SOL"],
)

account_balance = st.sidebar.number_input(
    "Számlaegyenleg (USDT)",
    min_value=0.0,
    value=10000.0,
    step=500.0,
)

risk_percent = st.sidebar.slider(
    "Tervezett kockázat (%)",
    0.25,
    3.0,
    1.0,
    0.25,
)

if st_autorefresh is not None:
    st_autorefresh(
        interval=refresh_seconds * 1000,
        key="saliga_auto_refresh",
    )

if st.sidebar.button("🔄 Adatok frissítése"):
    load_market_data.clear()
    st.rerun()


# ============================================================
# HEADER
# ============================================================

st.title("⚡ SALIGA AI PRO 11.0")

st.caption(
    "CENTRAL INTELLIGENCE | OKX ONLY | "
    "MULTI-TIMEFRAME | ORDER FLOW | SIGNAL JOURNAL"
)

st.info(
    "A belső elemzőmotor önállóan működik. "
    "A külső AI csak kiegészítő értelmezést ad."
)


# ============================================================
# LOAD MARKET DATA
# ============================================================

with st.spinner(
    "SALIGA AI: BTC, ETH és SOL elemzése..."
):
    market_data = load_market_data()


# ============================================================
# INITIALIZE AND UPDATE JOURNAL
# ============================================================

init_signal_journal = db_connection
_init_conn = init_signal_journal()
_init_conn.close()

for coin_name, coin_data in market_data.items():
    if not coin_data.get("error"):
        create_signal(coin_name, coin_data)

try:
    update_signal_journal()
except Exception as exc:
    logger.warning("Journal update error: %s", exc)


# ============================================================
# TOP SUMMARY
# ============================================================

st.header("Piaci összkép")

top_columns = st.columns(3)

for index, name in enumerate(["BTC", "ETH", "SOL"]):

    data = market_data[name]

    with top_columns[index]:

        st.subheader(name)

        if data.get("error"):
            st.error(data["error"])
            continue

        direction = data["intelligence"]["direction"]

        st.metric(
            "Aktuális ár",
            f"{data['price']:,.4f} USDT",
        )

        if direction == "LONG":
            st.success("KÖZPONTI IRÁNY: LONG")
        elif direction == "SHORT":
            st.error("KÖZPONTI IRÁNY: SHORT")
        else:
            st.warning("KÖZPONTI IRÁNY: SEMLEGES")

        st.write(
            data["intelligence"]["confidence"]
        )

        st.caption(
            data["mtf"]["agreement"]
        )


# ============================================================
# TABS
# ============================================================

tabs = st.tabs([
    "🧠 SALIGA gondolatai",
    "⏱️ Idősíkok",
    "📊 Piaci elemzés",
    "🎯 Entry / SL / TP",
    "📈 Order Flow",
    "📰 Hírek és AI",
    "💬 Chat",
    "📒 Jelzésnapló",
])


# ============================================================
# TAB 1 – CENTRAL INTELLIGENCE
# ============================================================

with tabs[0]:

    st.header("🧠 SALIGA AI – Központi intelligencia")

    st.write(
        "A rendszer az összes elérhető idősíkot, "
        "order flow adatot, OI-t és order book információt "
        "együttesen értékeli."
    )

    for name in ["BTC", "ETH", "SOL"]:

        data = market_data[name]

        st.subheader(
            f"{name} – aktuális piaci értékelés"
        )

        if data.get("error"):
            st.error(data["error"])
            continue

        intelligence = data["intelligence"]

        if intelligence["direction"] == "LONG":
            st.success("KÖZPONTI KÖVETKEZTETÉS: LONG")
        elif intelligence["direction"] == "SHORT":
            st.error("KÖZPONTI KÖVETKEZTETÉS: SHORT")
        else:
            st.warning("KÖZPONTI KÖVETKEZTETÉS: SEMLEGES")

        st.markdown(
            f"**Értékelés:** {intelligence['confidence']}"
        )

        st.write(intelligence["conclusion"])

        st.markdown("**Mi támogatja az emelkedést?**")

        if intelligence["long_reasons"]:
            for reason in intelligence["long_reasons"]:
                st.write(f"🟢 {reason}")
        else:
            st.write("Nincs egyértelmű vételi megerősítés.")

        st.markdown("**Mi támogatja az esést?**")

        if intelligence["short_reasons"]:
            for reason in intelligence["short_reasons"]:
                st.write(f"🔴 {reason}")
        else:
            st.write("Nincs egyértelmű eladási megerősítés.")

        st.markdown("**Ellentmondások**")

        if intelligence["conflicts"]:
            for item in intelligence["conflicts"]:
                st.warning(item)
        else:
            st.write(
                "Nem észlelhető egyértelmű idősík-ellentmondás."
            )

        st.markdown("**Mit figyel most a rendszer?**")

        for item in intelligence["watch_items"]:
            st.write(f"• {item}")

        st.markdown("**Mire vár a rendszer?**")
        st.write(intelligence["waiting_for"])

        st.markdown("**Mi érvénytelenítené az értékelést?**")
        st.write(intelligence["invalidation"])

        st.caption(
            f"Frissítve: {data['last_update']}"
        )

        st.divider()


# ============================================================
# TAB 2 – TIMEFRAMES
# ============================================================

with tabs[1]:

    st.header("⏱️ Multi-Timeframe Intelligence")

    for name in ["BTC", "ETH", "SOL"]:

        data = market_data[name]

        st.subheader(f"{name} – idősíkos elemzés")

        if data.get("error"):
            st.warning(data["error"])
            continue

        rows = []

        for label, result in data["timeframes"].items():

            rows.append({
                "Idősík": label,
                "Irány": result["direction"],
                "Erősség": result["strength"],
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

        def direction_color(value):
            if value == "LONG":
                return "color: #079455; font-weight: bold"
            if value == "SHORT":
                return "color: #d92d20; font-weight: bold"
            if value == "SEMLEGES":
                return "color: #b7791f; font-weight: bold"
            return ""

        st.dataframe(
            table.style.map(
                direction_color,
                subset=["Irány"],
            ),
            use_container_width=True,
            hide_index=True,
        )

        mtf = data["mtf"]

        st.write(
            f"**Összesített irány:** {mtf['direction']}"
        )

        st.write(
            f"**Idősíkok egyezése:** {mtf['agreement']}"
        )

        st.write(
            f"LONG: {mtf['long_count']} | "
            f"SHORT: {mtf['short_count']} | "
            f"SEMLEGES: {mtf['neutral_count']}"
        )

        st.divider()


# ============================================================
# TAB 3 – MARKET ANALYSIS
# ============================================================

with tabs[2]:

    st.header("📊 Részletes piaci elemzés")

    for name in ["BTC", "ETH", "SOL"]:

        data = market_data[name]

        st.subheader(name)

        if data.get("error"):
            st.error(data["error"])
            continue

        a, b, c, d = st.columns(4)

        a.metric(
            "Ár",
            f"{data['price']:,.4f}",
        )

        b.metric(
            "Delta",
            f"{data['delta']['delta_percent']:.2f}%",
        )

        c.metric(
            "Order book",
            (
                "N/A"
                if data["orderbook"]["imbalance"] is None
                else f"{data['orderbook']['imbalance']:.2f}%"
            ),
        )

        d.metric(
            "Funding",
            (
                "N/A"
                if data["funding"] is None
                else f"{data['funding'] * 100:.4f}%"
            ),
        )

        st.write(
            f"**OI állapot:** {data['oi_analysis']['state']}"
        )

        if data["oi_analysis"]["change"] is not None:
            st.write(
                f"OI változás: "
                f"{data['oi_analysis']['change']:.4f}%"
            )

        if data["support"] is not None:
            st.write(
                f"Támasz: {data['support']:,.4f}"
            )

        if data["resistance"] is not None:
            st.write(
                f"Ellenállás: {data['resistance']:,.4f}"
            )

        st.divider()


# ============================================================
# TAB 4 – TRADE PLAN
# ============================================================

with tabs[3]:

    st.header("🎯 Belépés / Stop Loss / Take Profit")

    data = market_data[selected_coin]

    if data.get("error"):

        st.error(data["error"])

    elif data["plan"] is None:

        st.warning(
            "A központi motor nem lát kellően egyértelmű irányt. "
            "Nem készít mesterséges kereskedési tervet."
        )

    else:

        plan = data["plan"]

        st.subheader(
            f"{selected_coin} – {plan['direction']}"
        )

        a, b, c = st.columns(3)

        a.metric(
            "Belépési referencia",
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

        theoretical_quantity = (
            risk_amount / plan["risk"]
            if plan["risk"] > 0
            else 0
        )

        st.info(
            f"Tervezett kockázati összeg: "
            f"{risk_amount:.2f} USDT"
        )

        st.write(
            f"Elméleti alapmennyiség: "
            f"{theoretical_quantity:.6f}"
        )

        st.caption(
            "Ez referenciaáras terv, nem végrehajtott megbízás. "
            "A mennyiség nem OKX-kontraktusméretre korrigált. "
            "Díjakat, csúszást és tőkeáttételt nem tartalmaz."
        )


# ============================================================
# TAB 5 – ORDER FLOW
# ============================================================

with tabs[4]:

    st.header("📈 Order Flow Intelligence")

    data = market_data[selected_coin]

    if data.get("error"):

        st.error(data["error"])

    else:

        delta = data["delta"]
        book = data["orderbook"]

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
            "Delta",
            f"{delta['delta_percent']:.2f}%",
        )

        st.write(
            f"**Kötések alapján:** {delta['pressure']}"
        )

        st.write(
            f"**Order book:** {book['state']}"
        )

        if book["imbalance"] is not None:
            st.write(
                f"Order book eltérés: "
                f"{book['imbalance']:.2f}%"
            )

        st.write(
            f"**Open Interest:** {data['oi_analysis']['state']}"
        )

        if data["funding"] is not None:
            st.write(
                f"Funding rate: {data['funding'] * 100:.5f}%"
            )

        st.caption(
            "A delta a legutóbb lekért kötésekből számított "
            "taker buy/sell becslés. Nem teljes historikus CVD. "
            "Az order book pillanatfelvétel."
        )


# ============================================================
# TAB 6 – NEWS AND AI
# ============================================================

with tabs[5]:

    st.header("📰 Piaci hírek és AI")

    if st.button("Hírek frissítése"):
        st.session_state["saliga_news"] = fetch_news()

    if "saliga_news" not in st.session_state:
        st.session_state["saliga_news"] = fetch_news()

    for item in st.session_state["saliga_news"]:

        st.markdown(
            f"**{item['title']}**  \n"
            f"[Hír megnyitása]({item['link']})"
        )

        if item["published"]:
            st.caption(item["published"])

        st.divider()

    st.subheader("AI piaci összefoglaló")

    if st.button("SALIGA AI – Teljes piaci összefoglaló"):

        with st.spinner("AI értelmezi a központi elemzést..."):

            answer = ask_groq(
                (
                    "Készíts részletes, érthető magyar piaci "
                    "összefoglalót BTC, ETH és SOL instrumentumokra. "
                    "Mutasd meg az irányokat, ellentmondásokat, "
                    "kockázatokat és a megerősítési feltételeket."
                ),
                market_data,
            )

        st.markdown(answer)


# ============================================================
# TAB 7 – CHAT
# ============================================================

with tabs[6]:

    st.header("💬 Kommunikáció a SALIGA AI-val")

    st.write(
        "Kérdezz az idősíkokról, OI-ról, deltáról, "
        "pozícióáramlásról vagy a központi következtetésről."
    )

    if "saliga_chat" not in st.session_state:
        st.session_state["saliga_chat"] = []

    for message in st.session_state["saliga_chat"]:

        with st.chat_message(message["role"]):
            st.markdown(message["content"])

    question = st.chat_input(
        "Kérdezz a piacról..."
    )

    if question:

        st.session_state["saliga_chat"].append({
            "role": "user",
            "content": question,
        })

        with st.chat_message("user"):
            st.markdown(question)

        with st.chat_message("assistant"):

            with st.spinner("SALIGA AI válaszol..."):

                answer = ask_groq(
                    question,
                    market_data,
                )

            st.markdown(answer)

        st.session_state["saliga_chat"].append({
            "role": "assistant",
            "content": answer,
        })


# ============================================================
# TAB 8 – SIGNAL JOURNAL
# ============================================================

with tabs[7]:

    st.header("📒 SALIGA AI – Kereskedési jelzésnapló")

    signals, events = get_signal_journal()

    if signals.empty:

        st.info(
            "Még nincs rögzített kereskedési jelzés. "
            "A napló akkor rögzít új sort, amikor a belső motor "
            "LONG vagy SHORT tervet készít."
        )

    else:

        total = len(signals)

        open_count = int(
            (signals["status"] == "OPEN").sum()
        )

        tp1_count = int(
            signals["hit_tp1"].sum()
        )

        tp2_count = int(
            signals["hit_tp2"].sum()
        )

        tp3_count = int(
            signals["hit_tp3"].sum()
        )

        stop_count = int(
            signals["status"].isin([
                "STOP_LOSS",
                "STOP_AFTER_TP1",
                "STOP_AFTER_TP2",
            ]).sum()
        )

        a, b, c, d, e = st.columns(5)

        a.metric("Összes jelzés", total)
        b.metric("Nyitott", open_count)
        c.metric("TP1 elérve", tp1_count)
        d.metric("TP2 elérve", tp2_count)
        e.metric("TP3 elérve", tp3_count)

        st.divider()

        st.subheader("Jelzések")

        display = signals[[
            "created_at",
            "symbol",
            "timeframe",
            "direction",
            "entry",
            "stop_loss",
            "tp1",
            "tp2",
            "tp3",
            "last_price",
            "status",
        ]].copy()

        display["created_at"] = (
            pd.to_datetime(
                display["created_at"],
                errors="coerce",
                utc=True,
            )
            .dt.strftime("%Y-%m-%d %H:%M")
        )

        display.columns = [
            "Létrehozva",
            "Coin",
            "Idősík",
            "Irány",
            "Entry",
            "Stop Loss",
            "TP1",
            "TP2",
            "TP3",
            "Utolsó ár",
            "Állapot",
        ]

        st.dataframe(
            display,
            use_container_width=True,
            hide_index=True,
        )

        st.divider()

        st.subheader("Eseménynapló")

        if events.empty:

            st.info(
                "Még nincs célár- vagy stopesemény."
            )

        else:

            event_display = events[[
                "event_time",
                "symbol",
                "direction",
                "event_type",
                "price",
            ]].copy()

            event_display.columns = [
                "Időpont",
                "Coin",
                "Irány",
                "Esemény",
                "Ár",
            ]

            st.dataframe(
                event_display,
                use_container_width=True,
                hide_index=True,
            )

        st.divider()

        st.subheader("Eredménykimutatás")

        closed_count = tp3_count + stop_count

        if closed_count > 0:

            success_rate = (
                tp3_count / closed_count
            ) * 100

            st.metric(
                "TP3 / lezárt jelzések aránya",
                f"{success_rate:.2f}%",
            )

        else:

            st.info(
                "Még nincs elegendő lezárt jelzés "
                "az eredményarány kiszámításához."
            )

        st.caption(
            "A TP1 és TP2 részleges célár-elérést jelent. "
            "A TP3 teljes célár-elérést jelent. "
            "A stopesemények külön kerülnek rögzítésre. "
            "A napló nem valódi tőzsdei megbízásokat kezel."
        )


# ============================================================
# FOOTER
# ============================================================

st.divider()

st.caption(
    f"{APP_NAME} | OKX ONLY | "
    "CENTRAL INTELLIGENCE ENGINE | SIGNAL JOURNAL"
)

st.caption(
    "Az elemzés becsléseket és technikai feltételeket használ. "
    "Nem garantálja a piaci irányt, és nem pénzügyi tanács."
)
