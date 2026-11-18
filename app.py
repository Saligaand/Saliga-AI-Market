# ============================================================
# SALIGA AI PRO 20.0
# MARKET INTELLIGENCE + AUTOMATIC SIGNAL JOURNAL
# OKX | DERIVATIVES | PATTERNS | NEWS | GROQ AI
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
import feedparser

from datetime import datetime, timezone, timedelta
from openai import OpenAI


# ============================================================
# 1. CONFIG
# ============================================================

st.set_page_config(
    page_title="SALIGA AI PRO 20.0",
    page_icon="⚡",
    layout="wide"
)

APP_VERSION = "20.0"

SYMBOL = "BTC/USDT:USDT"
INST_ID = "BTC-USDT-SWAP"

OKX_BASE = "https://www.okx.com"

DB_PATH = "saliga_ai.db"

GROQ_MODEL = os.getenv(
    "GROQ_MODEL",
    "openai/gpt-oss-120b"
)

TIMEFRAMES = {
    "M15": "15m",
    "H1": "1h",
    "H4": "4h",
    "D1": "1d"
}

RSS_FEEDS = [
    "https://cointelegraph.com/rss",
    "https://www.coindesk.com/arc/outboundfeeds/rss/"
]

HTTP_TIMEOUT = 15
MAX_CANDLES = 5000
TOP_MATCHES = 30
PATTERN_WINDOW = 15
PATTERN_HORIZON = 5


# ============================================================
# 2. SESSION STATE
# ============================================================

defaults = {
    "market": {},
    "timeframes": {},
    "news": [],
    "ai": None,
    "ai_error": None,
    "last_refresh": None,
    "last_refresh_epoch": 0,
    "lookback": 2000,
    "refresh_count": 0
}

for key, value in defaults.items():
    if key not in st.session_state:
        st.session_state[key] = value


# ============================================================
# 3. GENERAL HELPERS
# ============================================================

def utc_now():
    return datetime.now(timezone.utc)


def timestamp():
    return utc_now().isoformat()


def safe_float(value):
    try:
        if value is None:
            return None

        result = float(value)

        if np.isfinite(result):
            return result

        return None

    except (ValueError, TypeError):
        return None


def fmt(value, decimals=2):
    value = safe_float(value)

    if value is None:
        return "N/A"

    return f"{value:,.{decimals}f}"


def pct_change(current, previous):
    current = safe_float(current)
    previous = safe_float(previous)

    if current is None or previous in (None, 0):
        return None

    return (current / previous - 1) * 100


def get_secret(name):
    try:
        value = st.secrets.get(name)

        if value:
            return str(value)

    except Exception:
        pass

    return os.getenv(name)


# ============================================================
# 4. DATABASE
# ============================================================

def db_connection():
    return sqlite3.connect(
        DB_PATH,
        timeout=30,
        check_same_thread=False
    )


def init_db():

    with db_connection() as conn:

        conn.execute("""
            CREATE TABLE IF NOT EXISTS oi_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                value REAL NOT NULL,
                created_at TEXT NOT NULL
            )
        """)

        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_oi_time
            ON oi_history(created_at)
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS signal_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                direction TEXT NOT NULL,
                entry_price REAL NOT NULL,
                exit_price REAL,
                entry_time TEXT NOT NULL,
                exit_time TEXT,
                result_pct REAL,
                status TEXT NOT NULL
            )
        """)

        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_signal_status
            ON signal_history(status)
        """)

        conn.commit()


# ============================================================
# 5. OI HISTORY
# ============================================================

def save_oi(value):

    value = safe_float(value)

    if value is None:
        return

    with db_connection() as conn:

        conn.execute(
            """
            INSERT INTO oi_history(value, created_at)
            VALUES (?, ?)
            """,
            (value, timestamp())
        )

        conn.commit()


def get_oi_change(current):

    current = safe_float(current)

    if current is None:
        return None

    target = utc_now() - timedelta(minutes=5)

    with db_connection() as conn:

        rows = conn.execute("""
            SELECT value, created_at
            FROM oi_history
            ORDER BY id DESC
            LIMIT 1000
        """).fetchall()

    candidates = []

    for value, created_at in rows:

        try:

            dt = datetime.fromisoformat(created_at)

            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)

            if dt <= target:
                candidates.append((value, dt))

        except Exception:
            continue

    if not candidates:
        return None

    old_value, _ = min(
        candidates,
        key=lambda x: abs(
            (target - x[1]).total_seconds()
        )
    )

    return pct_change(current, old_value)


# ============================================================
# 6. SIGNAL JOURNAL
# ============================================================

def get_open_signal():

    with db_connection() as conn:

        row = conn.execute("""
            SELECT id, direction, entry_price, entry_time
            FROM signal_history
            WHERE status = 'OPEN'
            ORDER BY id DESC
            LIMIT 1
        """).fetchone()

    return row


def open_signal(direction, price):

    if direction not in ("LONG", "SHORT"):
        return

    price = safe_float(price)

    if price is None or price <= 0:
        return

    with db_connection() as conn:

        conn.execute("""
            INSERT INTO signal_history (
                direction,
                entry_price,
                entry_time,
                status
            )
            VALUES (?, ?, ?, 'OPEN')
        """, (
            direction,
            price,
            timestamp()
        ))

        conn.commit()


def close_signal(
    signal_id,
    direction,
    entry_price,
    exit_price
):

    entry_price = safe_float(entry_price)
    exit_price = safe_float(exit_price)

    if (
        entry_price is None
        or exit_price is None
        or entry_price <= 0
        or exit_price <= 0
    ):
        return

    if direction == "LONG":

        result = (
            exit_price / entry_price - 1
        ) * 100

    else:

        result = (
            entry_price / exit_price - 1
        ) * 100

    with db_connection() as conn:

        conn.execute("""
            UPDATE signal_history

            SET
                exit_price = ?,
                exit_time = ?,
                result_pct = ?,
                status = 'CLOSED'

            WHERE id = ?
              AND status = 'OPEN'
        """, (
            exit_price,
            timestamp(),
            result,
            signal_id
        ))

        conn.commit()


def update_signal_history(direction, price):

    if direction not in ("LONG", "SHORT"):
        return

    price = safe_float(price)

    if price is None or price <= 0:
        return

    active = get_open_signal()

    # Nincs nyitott pozíció.
    if active is None:

        open_signal(direction, price)

        return

    signal_id, old_direction, entry_price, entry_time = active

    # Már ugyanabba az irányba van nyitott pozíció.
    if old_direction == direction:
        return

    # Ellentétes jelzés: régi pozíció lezárása.
    close_signal(
        signal_id,
        old_direction,
        entry_price,
        price
    )

    # Új pozíció nyitása.
    open_signal(
        direction,
        price
    )


def get_signal_statistics():

    with db_connection() as conn:

        rows = conn.execute("""
            SELECT direction, result_pct
            FROM signal_history
            WHERE status = 'CLOSED'
        """).fetchall()

    results = [
        (direction, float(result))
        for direction, result in rows
        if result is not None
    ]

    if not results:

        return {
            "total": 0,
            "wins": 0,
            "losses": 0,
            "win_rate": 0,
            "total_result": 0,
            "long_win_rate": 0,
            "short_win_rate": 0,
            "long_count": 0,
            "short_count": 0
        }

    wins = sum(
        1 for _, result in results
        if result > 0
    )

    losses = sum(
        1 for _, result in results
        if result <= 0
    )

    long_results = [
        result
        for direction, result in results
        if direction == "LONG"
    ]

    short_results = [
        result
        for direction, result in results
        if direction == "SHORT"
    ]

    def calculate_win_rate(values):

        if not values:
            return 0

        return (
            sum(1 for x in values if x > 0)
            / len(values)
        ) * 100

    return {
        "total": len(results),
        "wins": wins,
        "losses": losses,
        "win_rate": wins / len(results) * 100,
        "total_result": sum(
            result for _, result in results
        ),
        "long_win_rate": calculate_win_rate(
            long_results
        ),
        "short_win_rate": calculate_win_rate(
            short_results
        ),
        "long_count": len(long_results),
        "short_count": len(short_results)
    }


def get_signal_history():

    with db_connection() as conn:

        return pd.read_sql_query("""
            SELECT
                id AS ID,
                direction AS IRANY,
                entry_price AS BELEPESI_AR,
                exit_price AS ZARASI_AR,
                entry_time AS NYITAS_IDEJE,
                exit_time AS ZARAS_IDEJE,
                result_pct AS EREDMENY_SZAZALEK,
                status AS ALLAPOT
            FROM signal_history
            ORDER BY id DESC
            LIMIT 1000
        """, conn)


# ============================================================
# 7. OKX CONNECTION
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


def get_market_info(exchange):
    return exchange.market(SYMBOL)


# ============================================================
# 8. OKX DIRECT API
# ============================================================

def okx_request(endpoint, params=None):

    url = OKX_BASE + endpoint

    response = requests.get(
        url,
        params=params,
        timeout=HTTP_TIMEOUT,
        headers={
            "User-Agent": "Mozilla/5.0 SALIGA-AI-PRO"
        }
    )

    response.raise_for_status()

    try:
        payload = response.json()

    except Exception:

        raise RuntimeError(
            "Az OKX nem JSON választ adott: "
            + response.text[:300]
        )

    if str(payload.get("code")) != "0":

        raise RuntimeError(
            "OKX API hiba: "
            + str(payload.get("code"))
            + " - "
            + str(payload.get("msg"))
        )

    return payload.get("data", [])


# ============================================================
# 9. ORDER BOOK
# ============================================================

def parse_book_levels(levels):

    result = []

    for level in levels or []:

        if len(level) < 2:
            continue

        price = safe_float(level[0])
        size = safe_float(level[1])

        if price is None or size is None:
            continue

        if price <= 0 or size <= 0:
            continue

        result.append((price, size))

    return result


def calculate_book_values(
    bids,
    asks,
    contract_size=1.0
):

    bid_value = sum(
        price * amount * contract_size
        for price, amount in bids
    )

    ask_value = sum(
        price * amount * contract_size
        for price, amount in asks
    )

    total = bid_value + ask_value

    if total <= 0:
        return None, bid_value, ask_value

    imbalance = (
        (bid_value - ask_value) / total
    ) * 100

    return imbalance, bid_value, ask_value


def fetch_orderbook_direct(exchange):

    market = get_market_info(exchange)

    contract_size = safe_float(
        market.get("contractSize")
    ) or 1.0

    data = okx_request(
        "/api/v5/market/books",
        {
            "instId": market.get("id", INST_ID),
            "sz": "50"
        }
    )

    if not data:
        raise RuntimeError(
            "Az OKX üres order book választ adott."
        )

    book = data[0]

    bids = parse_book_levels(
        book.get("bids")
    )

    asks = parse_book_levels(
        book.get("asks")
    )

    if not bids:
        raise RuntimeError(
            "Nincs feldolgozható BID adat."
        )

    if not asks:
        raise RuntimeError(
            "Nincs feldolgozható ASK adat."
        )

    imbalance, bid_value, ask_value = (
        calculate_book_values(
            bids,
            asks,
            contract_size
        )
    )

    if imbalance is None:
        raise RuntimeError(
            "Az order book összértéke nulla."
        )

    return {
        "orderbook_imbalance": imbalance,
        "bid_volume": bid_value,
        "ask_volume": ask_value,
        "orderbook_source": "OKX DIRECT REST",
        "orderbook_error": None,
        "orderbook_bid_levels": len(bids),
        "orderbook_ask_levels": len(asks),
        "orderbook_timestamp": book.get("ts")
    }


def fetch_orderbook_ccxt(exchange):

    market = get_market_info(exchange)

    contract_size = safe_float(
        market.get("contractSize")
    ) or 1.0

    book = exchange.fetch_order_book(
        SYMBOL,
        limit=50
    )

    bids = parse_book_levels(
        book.get("bids")
    )

    asks = parse_book_levels(
        book.get("asks")
    )

    if not bids or not asks:
        raise RuntimeError(
            "A CCXT order book üres."
        )

    imbalance, bid_value, ask_value = (
        calculate_book_values(
            bids,
            asks,
            contract_size
        )
    )

    if imbalance is None:
        raise RuntimeError(
            "A CCXT order book értéke nulla."
        )

    return {
        "orderbook_imbalance": imbalance,
        "bid_volume": bid_value,
        "ask_volume": ask_value,
        "orderbook_source": "CCXT OKX",
        "orderbook_error": None,
        "orderbook_bid_levels": len(bids),
        "orderbook_ask_levels": len(asks),
        "orderbook_timestamp": book.get("timestamp")
    }


def fetch_orderbook(exchange):

    errors = []

    try:
        return fetch_orderbook_direct(exchange)

    except Exception as e:
        errors.append(
            "DIRECT REST: " + str(e)
        )

    try:
        return fetch_orderbook_ccxt(exchange)

    except Exception as e:
        errors.append(
            "CCXT FALLBACK: " + str(e)
        )

    return {
        "orderbook_imbalance": None,
        "bid_volume": None,
        "ask_volume": None,
        "orderbook_source": None,
        "orderbook_error": " | ".join(errors),
        "orderbook_bid_levels": 0,
        "orderbook_ask_levels": 0,
        "orderbook_timestamp": None
    }


# ============================================================
# 10. PRICE + VOLUME
# ============================================================

def fetch_price_data(exchange):

    ticker = exchange.fetch_ticker(SYMBOL)

    market = get_market_info(exchange)

    info = ticker.get("info") or {}

    price = safe_float(
        ticker.get("last")
    )

    quote_volume = safe_float(
        info.get("volCcyQuote24h")
    )

    base_volume = safe_float(
        info.get("volCcy24h")
    )

    contract_volume = safe_float(
        info.get("vol24h")
    )

    contract_size = safe_float(
        market.get("contractSize")
    ) or 1.0

    unified_quote = safe_float(
        ticker.get("quoteVolume")
    )

    unified_base = safe_float(
        ticker.get("baseVolume")
    )

    volume_usdt = None
    volume_source = None

    if quote_volume is not None:

        volume_usdt = quote_volume
        volume_source = "OKX volCcyQuote24h"

    elif base_volume is not None and price is not None:

        volume_usdt = base_volume * price
        volume_source = "OKX volCcy24h * price"

    elif unified_quote is not None:

        volume_usdt = unified_quote
        volume_source = "CCXT quoteVolume"

    elif unified_base is not None and price is not None:

        volume_usdt = unified_base * price
        volume_source = "CCXT baseVolume * price"

    elif contract_volume is not None and price is not None:

        volume_usdt = (
            contract_volume * contract_size * price
        )

        volume_source = (
            "OKX vol24h * contractSize * price"
        )

    return {
        "price": price,
        "change_24h": safe_float(
            ticker.get("percentage")
        ),
        "high_24h": safe_float(
            ticker.get("high")
        ),
        "low_24h": safe_float(
            ticker.get("low")
        ),
        "volume_24h": volume_usdt,
        "volume_source": volume_source
    }


# ============================================================
# 11. OPEN INTEREST
# ============================================================

def fetch_open_interest(exchange):

    market = get_market_info(exchange)

    data = okx_request(
        "/api/v5/public/open-interest",
        {
            "instType": "SWAP",
            "instId": market["id"]
        }
    )

    if not data:
        raise RuntimeError(
            "Nincs Open Interest adat."
        )

    row = data[0]

    return {
        "oi_usd": safe_float(
            row.get("oiUsd")
        ),
        "oi_contracts": safe_float(
            row.get("oi")
        )
    }


# ============================================================
# 12. FUNDING
# ============================================================

def fetch_funding(exchange):

    funding = exchange.fetch_funding_rate(
        SYMBOL
    )

    return {
        "funding_rate": safe_float(
            funding.get("fundingRate")
        ),
        "next_funding": funding.get(
            "fundingDatetime"
        )
    }


# ============================================================
# 13. TRADE DELTA
# ============================================================

def fetch_trade_delta(exchange):

    trades = exchange.fetch_trades(
        SYMBOL,
        limit=500
    )

    market = get_market_info(exchange)

    contract_size = safe_float(
        market.get("contractSize")
    ) or 1.0

    buy_amount = 0.0
    sell_amount = 0.0

    buy_usd = 0.0
    sell_usd = 0.0

    for trade in trades:

        amount = safe_float(
            trade.get("amount")
        ) or 0

        price = safe_float(
            trade.get("price")
        ) or 0

        notional = (
            amount * price * contract_size
        )

        if trade.get("side") == "buy":

            buy_amount += amount
            buy_usd += notional

        elif trade.get("side") == "sell":

            sell_amount += amount
            sell_usd += notional

    total = buy_amount + sell_amount

    delta = None

    if total > 0:

        delta = (
            (buy_amount - sell_amount) / total
        ) * 100

    return {
        "delta_percent": delta,
        "trade_delta_usd": buy_usd - sell_usd,
        "buy_volume": buy_amount,
        "sell_volume": sell_amount,
        "trade_count": len(trades)
    }


# ============================================================
# 14. COMPLETE MARKET DATA
# ============================================================

def fetch_market_data(exchange):

    result = {
        "source": "OKX",
        "timestamp": timestamp(),
        "errors": []
    }

    tasks = [
        ("PRICE", fetch_price_data),
        ("OPEN INTEREST", fetch_open_interest),
        ("FUNDING", fetch_funding),
        ("ORDER BOOK", fetch_orderbook),
        ("TRADE DELTA", fetch_trade_delta)
    ]

    for name, function in tasks:

        try:

            data = function(exchange)

            if isinstance(data, dict):
                result.update(data)

        except Exception as e:

            result["errors"].append(
                name + ": " + str(e)
            )

    oi = result.get("oi_usd")

    if oi is not None:

        result["oi_change"] = get_oi_change(oi)

        save_oi(oi)

    else:

        result["oi_change"] = None

    return result


# ============================================================
# 15. DERIVATIVES ANALYSIS
# ============================================================

def derivatives_analysis(market):

    score = 0
    notes = []

    oi = market.get("oi_change")
    price = market.get("change_24h")
    funding = market.get("funding_rate")
    delta = market.get("delta_percent")
    imbalance = market.get("orderbook_imbalance")

    if oi is not None and price is not None:

        if oi > 0.5 and price > 0:

            score += 10

            notes.append(
                "Ár emelkedik + OI nő: új pozíciók épülhetnek."
            )

        elif oi > 0.5 and price < 0:

            score -= 10

            notes.append(
                "Ár esik + OI nő: új short pozíciók épülhetnek."
            )

        elif oi < -0.5 and price > 0:

            score += 4

            notes.append(
                "Emelkedő ár + csökkenő OI: shortzárás lehetséges."
            )

        elif oi < -0.5 and price < 0:

            score -= 4

            notes.append(
                "Eső ár + csökkenő OI: longzárás lehetséges."
            )

    if funding is not None:

        if funding > 0.0008:

            score -= 4

            notes.append(
                "Magas pozitív funding."
            )

        elif funding < -0.0003:

            score += 4

            notes.append(
                "Negatív funding."
            )

    if delta is not None:

        if delta > 8:

            score += 8

            notes.append(
                "Vételi agresszió a legutóbbi ügyletekben."
            )

        elif delta < -8:

            score -= 8

            notes.append(
                "Eladói agresszió a legutóbbi ügyletekben."
            )

    if imbalance is not None:

        if imbalance > 15:

            score += 4

            notes.append(
                "Pillanatnyi vételi túlsúly az order bookban."
            )

        elif imbalance < -15:

            score -= 4

            notes.append(
                "Pillanatnyi eladói túlsúly az order bookban."
            )

    return {
        "score": score,
        "notes": notes
    }


# ============================================================
# 16. MARKET STRUCTURE
# ============================================================

def analyze_structure(df):

    if df is None or len(df) < 30:

        return {
            "score": 0,
            "status": "N/A",
            "support": None,
            "resistance": None
        }

    last = df.iloc[-1]

    previous = df.iloc[-21:-1]

    support = float(
        previous["low"].min()
    )

    resistance = float(
        previous["high"].max()
    )

    close = float(
        last["close"]
    )

    if close > resistance:

        score = 18
        status = "BULLISH BREAKOUT"

    elif close < support:

        score = -18
        status = "BEARISH BREAKDOWN"

    else:

        score = 0
        status = "RANGE / CONSOLIDATION"

    return {
        "score": score,
        "status": status,
        "support": support,
        "resistance": resistance
    }


# ============================================================
# 17. MOMENTUM
# ============================================================

def analyze_momentum(df):

    if df is None or len(df) < 30:

        return {
            "score": 0,
            "return_5": None,
            "return_20": None
        }

    close = df["close"]

    r5 = pct_change(
        close.iloc[-1],
        close.iloc[-6]
    )

    r20 = pct_change(
        close.iloc[-1],
        close.iloc[-21]
    )

    score = 0

    if r5 is not None:

        if r5 > 0.15:
            score += 8

        elif r5 < -0.15:
            score -= 8

    if r20 is not None:

        if r20 > 0.35:
            score += 12

        elif r20 < -0.35:
            score -= 12

    return {
        "score": score,
        "return_5": r5,
        "return_20": r20
    }


# ============================================================
# 18. HISTORICAL PATTERNS
# ============================================================

def find_historical_patterns(
    df,
    window=PATTERN_WINDOW,
    horizon=PATTERN_HORIZON
):

    empty = {
        "score": 0,
        "similarity": None,
        "matches": 0,
        "up_rate": None,
        "down_rate": None,
        "flat_rate": None,
        "average_move": None,
        "description": "Nincs elegendő adat"
    }

    if df is None or len(df) < window * 4 + horizon:
        return empty

    closes = df["close"].values

    recent = closes[-window:]

    recent = recent / recent[0] - 1

    recent = recent - np.mean(recent)

    std = np.std(recent)

    if std == 0:
        return empty

    recent = recent / std

    candidates = []

    max_start = (
        len(closes) - window - horizon - window
    )

    for i in range(max_start + 1):

        historical = closes[i:i + window]

        if len(historical) != window:
            continue

        if historical[0] == 0:
            continue

        normalized = (
            historical / historical[0] - 1
        )

        normalized -= np.mean(normalized)

        hist_std = np.std(normalized)

        if hist_std == 0:
            continue

        normalized /= hist_std

        correlation = np.corrcoef(
            recent,
            normalized
        )[0, 1]

        if not np.isfinite(correlation):
            continue

        end_index = i + window - 1

        future_index = end_index + horizon

        future_move = pct_change(
            closes[future_index],
            closes[end_index]
        )

        if future_move is not None:

            candidates.append(
                (correlation, future_move)
            )

    if not candidates:
        return empty

    candidates.sort(
        key=lambda x: x[0],
        reverse=True
    )

    selected = [
        x for x in candidates
        if x[0] >= 0.60
    ][:TOP_MATCHES]

    if len(selected) < 3:
        selected = candidates[:5]

    returns = np.array([
        x[1] for x in selected
    ])

    if len(returns) == 0:
        return empty

    up = float(
        np.mean(returns > 0.15) * 100
    )

    down = float(
        np.mean(returns < -0.15) * 100
    )

    flat = 100 - up - down

    average = float(
        np.mean(returns)
    )

    similarity = float(
        selected[0][0]
    )

    score = 0

    if len(returns) >= 5:

        if up >= 60 and average > 0:

            score = 12

            description = (
                "BULLISH HISTORICAL PATTERN"
            )

        elif down >= 60 and average < 0:

            score = -12

            description = (
                "BEARISH HISTORICAL PATTERN"
            )

        else:

            description = (
                "MIXED HISTORICAL PATTERN"
            )

    else:

        description = (
            "LIMITED HISTORICAL SAMPLE"
        )

    return {
        "score": score,
        "similarity": similarity,
        "matches": len(returns),
        "up_rate": up,
        "down_rate": down,
        "flat_rate": flat,
        "average_move": average,
        "description": description
    }


# ============================================================
# 19. ATR
# ============================================================

def calculate_atr(df, period=14):

    if df is None or len(df) < period + 1:
        return None

    high = df["high"]
    low = df["low"]

    previous_close = df["close"].shift(1)

    tr = pd.concat([
        high - low,
        abs(high - previous_close),
        abs(low - previous_close)
    ], axis=1).max(axis=1)

    atr = tr.rolling(period).mean().iloc[-1]

    close = df["close"].iloc[-1]

    if pd.isna(atr) or close == 0:
        return None

    return float(
        atr / close * 100
    )


# ============================================================
# 20. POSITION FLOW
# ============================================================

def analyze_position_flow(market):

    oi = market.get("oi_change")
    price = market.get("change_24h")
    delta = market.get("delta_percent")

    if oi is None or price is None:
        return "INSUFFICIENT DATA"

    if oi > 0.5 and price > 0:

        if delta is not None and delta > 5:
            return "LONG POSITION BUILDING"

        return "OPEN INTEREST GROWING WITH PRICE"

    if oi > 0.5 and price < 0:

        if delta is not None and delta < -5:
            return "SHORT POSITION BUILDING"

        return "OPEN INTEREST GROWING DURING DECLINE"

    if oi < -0.5 and price > 0:
        return "POSSIBLE SHORT CLOSING"

    if oi < -0.5 and price < 0:
        return "POSSIBLE LONG CLOSING"

    return "NO CLEAR POSITION FLOW"


# ============================================================
# 21. CANDLE DOWNLOAD
# ============================================================

def fetch_candles(exchange, timeframe, requested):

    requested = min(
        int(requested),
        MAX_CANDLES
    )

    tf_ms = (
        exchange.parse_timeframe(timeframe) * 1000
    )

    since = (
        exchange.milliseconds()
        - requested * tf_ms
    )

    all_rows = {}

    for _ in range(25):

        remaining = requested - len(all_rows)

        if remaining <= 0:
            break

        batch = exchange.fetch_ohlcv(
            SYMBOL,
            timeframe=timeframe,
            since=since,
            limit=min(300, remaining)
        )

        if not batch:
            break

        for row in batch:
            all_rows[int(row[0])] = row

        newest = max(
            int(row[0]) for row in batch
        )

        if newest <= since:
            break

        since = newest + tf_ms

    if not all_rows:
        return None

    rows = sorted(
        all_rows.values(),
        key=lambda x: x[0]
    )

    df = pd.DataFrame(
        rows,
        columns=[
            "timestamp",
            "open",
            "high",
            "low",
            "close",
            "volume"
        ]
    )

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

    df = df.dropna()

    # A még nyitott gyertyát nem használjuk.
    if len(df) > 1:
        df = df.iloc[:-1]

    return df.reset_index(drop=True)


# ============================================================
# 22. TIMEFRAME ANALYSIS
# ============================================================

def analyze_timeframe(
    exchange,
    name,
    timeframe,
    lookback,
    derivatives
):

    df = fetch_candles(
        exchange,
        timeframe,
        lookback
    )

    if df is None:

        return {
            "direction": "WAIT",
            "score": 0,
            "error": "Insufficient data"
        }

    structure = analyze_structure(df)

    momentum = analyze_momentum(df)

    pattern = find_historical_patterns(df)

    deriv_score = (
        derivatives["score"]
        if name == "H1"
        else 0
    )

    score = (
        structure["score"]
        + momentum["score"]
        + pattern["score"]
        + deriv_score
    )

    if score >= 20:

        direction = "LONG"

    elif score <= -20:

        direction = "SHORT"

    else:

        direction = "WAIT"

    return {
        "direction": direction,
        "score": int(score),
        "price": float(df["close"].iloc[-1]),
        "candles_loaded": len(df),
        "structure": structure,
        "momentum": momentum,
        "pattern": pattern,
        "derivatives_score": deriv_score,
        "atr_percent": calculate_atr(df),
        "error": None
    }


# ============================================================
# 23. NEWS
# ============================================================

def fetch_news():

    headlines = []

    for url in RSS_FEEDS:

        try:

            feed = feedparser.parse(url)

            for entry in feed.entries[:15]:

                title = entry.get("title", "")

                if not title:
                    continue

                headlines.append({
                    "title": title,
                    "published": (
                        entry.get("published")
                        or entry.get("updated")
                        or ""
                    ),
                    "source": feed.feed.get(
                        "title",
                        url
                    ),
                    "link": entry.get("link", ""),
                    "summary": entry.get(
                        "summary",
                        ""
                    )[:500]
                })

        except Exception:
            continue

    unique = {}

    for item in headlines:

        unique[
            item["title"].lower().strip()
        ] = item

    return list(unique.values())[:20]


# ============================================================
# 24. GROQ AI
# ============================================================

def run_groq_analysis(
    market,
    timeframe_data,
    news
):

    key = get_secret("GROQ_API_KEY")

    if not key:
        return None, "GROQ_API_KEY MISSING"

    client = OpenAI(
        api_key=key,
        base_url="https://api.groq.com/openai/v1"
    )

    payload = {
        "market_data": market,
        "timeframe_analysis": timeframe_data,
        "news": news,
        "position_flow": analyze_position_flow(market)
    }

    system_prompt = """
You are SALIGA AI PRO, an advanced crypto market analyst.

Analyze:
- Price action
- Open Interest
- Funding Rate
- Trade Delta
- Order Book
- Position Flow
- Historical Patterns
- Support and Resistance
- Momentum
- Volatility
- Multi-Timeframe Structure
- Crypto News

Use only supplied data.
Never invent market data.
Never invent liquidation levels.
Do not call an order book snapshot a heatmap.
Do not invent macroeconomic releases.
Distinguish facts from interpretations.
If evidence conflicts, explain the conflict.
If evidence is weak, recommend WAIT.

The confidence score is an AI assessment,
not a statistically calibrated probability.

Return valid JSON only:

{
 "bias": "BULLISH / BEARISH / NEUTRAL",
 "confidence_score": 0,
 "executive_summary": "",
 "derivatives_analysis": "",
 "position_flow_analysis": "",
 "historical_pattern_analysis": "",
 "news_impact": "",
 "timeframe_analysis": "",
 "actionable_insight": "",
 "risks": []
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
                        payload,
                        ensure_ascii=False,
                        default=str
                    )
                }
            ],
            temperature=0.15,
            max_tokens=1800,
            response_format={
                "type": "json_object"
            }
        )

        content = (
            response.choices[0].message.content
        )

        return json.loads(content), None

    except Exception as e:

        return None, str(e)


# ============================================================
# 25. FULL ANALYSIS CYCLE
# ============================================================

def run_full_cycle(lookback):

    exchange = get_exchange()

    market = fetch_market_data(exchange)

    derivatives = derivatives_analysis(market)

    timeframe_data = {}

    for name, timeframe in TIMEFRAMES.items():

        try:

            timeframe_data[name] = analyze_timeframe(
                exchange,
                name,
                timeframe,
                lookback,
                derivatives
            )

        except Exception as e:

            timeframe_data[name] = {
                "direction": "WAIT",
                "score": 0,
                "error": str(e)
            }

    news = fetch_news()

    ai, ai_error = run_groq_analysis(
        market,
        timeframe_data,
        news
    )

    # --------------------------------------------------------
    # AUTOMATIC SIGNAL JOURNAL
    # H1 signal controls virtual position tracking.
    # --------------------------------------------------------

    h1 = timeframe_data.get("H1", {})

    signal_direction = h1.get(
        "direction",
        "WAIT"
    )

    current_price = market.get("price")

    update_signal_history(
        signal_direction,
        current_price
    )

    # --------------------------------------------------------

    st.session_state["market"] = market

    st.session_state["timeframes"] = timeframe_data

    st.session_state["news"] = news

    st.session_state["ai"] = ai

    st.session_state["ai_error"] = ai_error

    st.session_state["last_refresh"] = timestamp()

    st.session_state["last_refresh_epoch"] = time.time()


# ============================================================
# 26. INITIALIZE
# ============================================================

init_db()

st.title("⚡ SALIGA AI PRO 20.0")

st.caption(
    "MARKET INTELLIGENCE | DERIVATIVES | "
    "POSITION FLOW | PATTERNS | NEWS AI | "
    "AUTOMATIC SIGNAL HISTORY"
)


# ============================================================
# 27. SIDEBAR
# ============================================================

with st.sidebar:

    st.header("SYSTEM CONTROL")

    lookback = st.slider(
        "Historical Candles",
        500,
        5000,
        st.session_state["lookback"],
        500
    )

    st.session_state["lookback"] = lookback

    refresh_minutes = st.selectbox(
        "Refresh Interval",
        [1, 3, 5, 10, 15],
        index=2
    )

    auto_refresh = st.checkbox(
        "Automatic Refresh",
        value=True
    )

    manual_refresh = st.button(
        "🔄 REFRESH NOW",
        use_container_width=True
    )


# ============================================================
# 28. INITIAL LOAD
# ============================================================

if (
    st.session_state["last_refresh"] is None
    or manual_refresh
):

    with st.spinner(
        "SALIGA AI MARKET ANALYSIS..."
    ):

        try:

            run_full_cycle(lookback)

        except Exception as e:

            st.error(
                "Analysis error: " + str(e)
            )


market = st.session_state["market"]

timeframes = st.session_state["timeframes"]

news = st.session_state["news"]

ai = st.session_state["ai"]


# ============================================================
# 29. MARKET DASHBOARD
# ============================================================

st.markdown("---")

st.header("📊 BTC MARKET DATA")

c1, c2, c3, c4, c5 = st.columns(5)

with c1:

    st.metric(
        "BTC PRICE",
        "$" + fmt(market.get("price"))
    )

with c2:

    value = market.get("change_24h")

    st.metric(
        "24H CHANGE",
        fmt(value) + "%"
        if value is not None
        else "N/A"
    )

with c3:

    st.metric(
        "OPEN INTEREST",
        "$" + fmt(
            market.get("oi_usd"),
            0
        )
    )

with c4:

    value = market.get("oi_change")

    st.metric(
        "OI CHANGE",
        fmt(value, 3) + "%"
        if value is not None
        else "N/A"
    )

with c5:

    value = market.get("funding_rate")

    st.metric(
        "FUNDING RATE",
        fmt(value * 100, 4) + "%"
        if value is not None
        else "N/A"
    )


c6, c7, c8 = st.columns(3)

with c6:

    value = market.get("delta_percent")

    st.metric(
        "TRADE DELTA",
        fmt(value) + "%"
        if value is not None
        else "N/A"
    )

with c7:

    value = market.get("orderbook_imbalance")

    st.metric(
        "ORDER BOOK",
        fmt(value) + "%"
        if value is not None
        else "N/A"
    )

with c8:

    value = market.get("volume_24h")

    st.metric(
        "24H VOLUME",
        "$" + fmt(value, 0)
        if value is not None
        else "N/A"
    )


# ============================================================
# 30. ORDER BOOK DIAGNOSTICS
# ============================================================

st.markdown("---")

st.header("📚 ORDER BOOK DIAGNOSTICS")

if market.get("orderbook_imbalance") is not None:

    st.success(
        "ORDER BOOK CONNECTION ACTIVE"
    )

    st.write(
        "Source: "
        + str(market.get("orderbook_source"))
    )

    st.write(
        "Bid levels: "
        + str(market.get("orderbook_bid_levels"))
    )

    st.write(
        "Ask levels: "
        + str(market.get("orderbook_ask_levels"))
    )

    st.write(
        "Bid notional: $"
        + fmt(market.get("bid_volume"), 0)
    )

    st.write(
        "Ask notional: $"
        + fmt(market.get("ask_volume"), 0)
    )

else:

    st.error(
        "ORDER BOOK CONNECTION FAILED"
    )

    st.code(
        market.get(
            "orderbook_error",
            "No diagnostic information."
        )
    )


st.subheader("24H VOLUME SOURCE")

st.write(
    market.get(
        "volume_source",
        "N/A"
    )
)


if market.get("errors"):

    with st.expander("ALL DATA ERRORS"):

        for error in market["errors"]:
            st.warning(error)


# ============================================================
# 31. POSITION FLOW
# ============================================================

st.markdown("---")

st.header("🏦 POSITION FLOW INTELLIGENCE")

st.info(
    analyze_position_flow(market)
)

derivatives = derivatives_analysis(market)

st.metric(
    "DERIVATIVES SCORE",
    derivatives["score"]
)

for note in derivatives["notes"]:

    st.write("• " + note)


# ============================================================
# 32. BTC RADAR
# ============================================================

st.markdown("---")

st.header("🎯 BTC INTELLIGENCE RADAR")

h1 = timeframes.get("H1", {})

direction = h1.get(
    "direction",
    "WAIT"
)

score = h1.get(
    "score",
    0
)

if direction == "LONG":

    st.success("🟢 BTC LONG BIAS")

elif direction == "SHORT":

    st.error("🔴 BTC SHORT BIAS")

else:

    st.warning("🟡 BTC WAIT / NEUTRAL")


left, right = st.columns(2)

with left:

    st.metric(
        "H1 SCORE",
        score
    )

    st.metric(
        "BTC PRICE",
        fmt(market.get("price"))
    )

    st.metric(
        "H1 ATR",
        fmt(h1.get("atr_percent")) + "%"
        if h1.get("atr_percent") is not None
        else "N/A"
    )

with right:

    structure = h1.get(
        "structure",
        {}
    )

    st.write(
        "STRUCTURE: "
        + str(
            structure.get("status", "N/A")
        )
    )

    st.write(
        "SUPPORT: "
        + fmt(structure.get("support"))
    )

    st.write(
        "RESISTANCE: "
        + fmt(structure.get("resistance"))
    )


# ============================================================
# 33. HISTORICAL PATTERNS
# ============================================================

st.markdown("---")

st.header("🧩 HISTORICAL PATTERN INTELLIGENCE")

pattern = h1.get(
    "pattern",
    {}
)

p1, p2, p3, p4 = st.columns(4)

with p1:

    value = pattern.get("up_rate")

    st.metric(
        "BULLISH OUTCOMES",
        fmt(value) + "%"
        if value is not None
        else "N/A"
    )

with p2:

    value = pattern.get("down_rate")

    st.metric(
        "BEARISH OUTCOMES",
        fmt(value) + "%"
        if value is not None
        else "N/A"
    )

with p3:

    value = pattern.get("flat_rate")

    st.metric(
        "SIDEWAYS OUTCOMES",
        fmt(value) + "%"
        if value is not None
        else "N/A"
    )

with p4:

    value = pattern.get("average_move")

    st.metric(
        "AVERAGE MOVE",
        fmt(value, 3) + "%"
        if value is not None
        else "N/A"
    )

st.write(
    pattern.get(
        "description",
        "N/A"
    )
)

st.write(
    "Similar cases: "
    + str(pattern.get("matches", 0))
)

similarity = pattern.get("similarity")

st.write(
    "Similarity: "
    + (
        fmt(similarity * 100) + "%"
        if similarity is not None
        else "N/A"
    )
)


# ============================================================
# 34. MULTI-TIMEFRAME
# ============================================================

st.markdown("---")

st.header("🧭 MULTI-TIMEFRAME INTELLIGENCE")

rows = []

for name in TIMEFRAMES:

    data = timeframes.get(
        name,
        {}
    )

    structure = data.get(
        "structure",
        {}
    )

    momentum = data.get(
        "momentum",
        {}
    )

    pattern_data = data.get(
        "pattern",
        {}
    )

    rows.append({

        "TIMEFRAME": name,

        "DIRECTION": data.get(
            "direction",
            "WAIT"
        ),

        "SCORE": data.get(
            "score",
            0
        ),

        "CANDLES": data.get(
            "candles_loaded",
            0
        ),

        "STRUCTURE": structure.get(
            "status",
            "N/A"
        ),

        "MOMENTUM 5": momentum.get(
            "return_5"
        ),

        "MOMENTUM 20": momentum.get(
            "return_20"
        ),

        "PATTERN SCORE": pattern_data.get(
            "score",
            0
        ),

        "ATR %": data.get(
            "atr_percent"
        )

    })


st.dataframe(
    pd.DataFrame(rows),
    use_container_width=True,
    hide_index=True
)


# ============================================================
# 35. AUTOMATIC SIGNAL PERFORMANCE JOURNAL
# ============================================================

st.markdown("---")

st.header("📒 SALIGA AI – SIGNAL PERFORMANCE HISTORY")

st.caption(
    "Automatikusan naplózott virtuális H1 pozíciók."
)

stats = get_signal_statistics()

a, b, c, d = st.columns(4)

with a:

    st.metric(
        "LEZÁRT POZÍCIÓK",
        stats["total"]
    )

with b:

    st.metric(
        "NYERŐ",
        stats["wins"]
    )

with c:

    st.metric(
        "VESZTES",
        stats["losses"]
    )

with d:

    st.metric(
        "WIN RATE",
        f'{stats["win_rate"]:.2f}%'
    )


e, f, g = st.columns(3)

with e:

    st.metric(
        "LONG WIN RATE",
        f'{stats["long_win_rate"]:.2f}%'
    )

with f:

    st.metric(
        "SHORT WIN RATE",
        f'{stats["short_win_rate"]:.2f}%'
    )

with g:

    st.metric(
        "ÖSSZESÍTETT EREDMÉNY",
        f'{stats["total_result"]:+.3f}%'
    )


# Aktuális nyitott virtuális pozíció

active = get_open_signal()

st.subheader("AKTUÁLIS POZÍCIÓ")

if active:

    signal_id, active_direction, entry_price, entry_time = active

    current_price = safe_float(
        market.get("price")
    )

    if current_price and entry_price > 0:

        if active_direction == "LONG":

            floating_result = (
                current_price / entry_price - 1
            ) * 100

        else:

            floating_result = (
                entry_price / current_price - 1
            ) * 100

    else:

        floating_result = None

    x1, x2, x3, x4 = st.columns(4)

    x1.metric(
        "IRÁNY",
        active_direction
    )

    x2.metric(
        "BELÉPÉSI ÁR",
        fmt(entry_price)
    )

    x3.metric(
        "AKTUÁLIS ÁR",
        fmt(current_price)
    )

    x4.metric(
        "PILLANATNYI EREDMÉNY",
        fmt(floating_result, 3) + "%"
        if floating_result is not None
        else "N/A"
    )

    st.caption(
        "Nyitás: " + str(entry_time)
    )

else:

    st.info(
        "Jelenleg nincs nyitott virtuális pozíció."
    )


# Korábbi pozíciók

st.subheader("KORÁBBI POZÍCIÓK")

history = get_signal_history()

if not history.empty:

    def format_result(value):

        if pd.isna(value):
            return "NYITOTT"

        if value > 0:
            return "PROFIT"

        if value < 0:
            return "LOSS"

        return "BREAK EVEN"

    history["EREDMENY"] = (
        history["EREDMENY_SZAZALEK"]
        .apply(format_result)
    )

    st.dataframe(
        history,
        use_container_width=True,
        hide_index=True
    )

    csv_data = history.to_csv(
        index=False
    ).encode("utf-8-sig")

    st.download_button(
        "⬇️ SIGNAL HISTORY CSV LETÖLTÉSE",
        data=csv_data,
        file_name="saliga_signal_history.csv",
        mime="text/csv"
    )

else:

    st.info(
        "Még nincs naplózott jelzés."
    )


# ============================================================
# 36. GROQ AI REPORT
# ============================================================

st.markdown("---")

st.header("🤖 GROQ AI INTELLIGENCE")

if st.session_state["ai_error"]:

    st.error(
        st.session_state["ai_error"]
    )

if ai:

    st.subheader(
        "MARKET BIAS: "
        + str(
            ai.get("bias", "N/A")
        )
    )

    st.metric(
        "AI CONFIDENCE ASSESSMENT",
        str(
            ai.get("confidence_score", "N/A")
        ) + "%"
    )

    st.caption(
        "Ez AI-értékelés, nem visszatesztelt valószínűség."
    )

    for title, key in [

        ("EXECUTIVE SUMMARY", "executive_summary"),

        ("DERIVATIVES", "derivatives_analysis"),

        ("POSITION FLOW", "position_flow_analysis"),

        ("HISTORICAL PATTERNS", "historical_pattern_analysis"),

        ("NEWS IMPACT", "news_impact"),

        ("TIMEFRAME ANALYSIS", "timeframe_analysis"),

        ("ACTIONABLE INSIGHT", "actionable_insight")

    ]:

        st.subheader(title)

        st.write(
            ai.get(key, "")
        )

    with st.expander("RISKS"):

        for risk in ai.get("risks", []):

            st.write(
                "• " + str(risk)
            )


# ============================================================
# 37. AUTOMATIC CRYPTO NEWS
# ============================================================

st.markdown("---")

st.header("📰 AUTOMATIC CRYPTO NEWS")

if news:

    for item in news:

        st.write(
            "### " + item["title"]
        )

        st.caption(
            item["source"]
            + " | "
            + item["published"]
        )

        if item.get("summary"):

            st.write(
                item["summary"]
            )

        if item.get("link"):

            st.markdown(
                "[OPEN NEWS](" + item["link"] + ")"
            )

else:

    st.info(
        "No news available."
    )


# ============================================================
# 38. SYSTEM STATUS
# ============================================================

st.markdown("---")

st.header("🛡️ SYSTEM STATUS")

st.write(
    "VERSION: " + APP_VERSION
)

st.write(
    "EXCHANGE: OKX"
)

st.write(
    "INSTRUMENT: " + INST_ID
)

st.write(
    "ORDER BOOK: "
    + (
        "ACTIVE"
        if market.get("orderbook_imbalance") is not None
        else "UNAVAILABLE"
    )
)

st.write(
    "24H VOLUME: "
    + (
        "ACTIVE"
        if market.get("volume_24h") is not None
        else "UNAVAILABLE"
    )
)

st.write(
    "OPEN INTEREST: "
    + (
        "ACTIVE"
        if market.get("oi_usd") is not None
        else "UNAVAILABLE"
    )
)

st.write(
    "FUNDING: "
    + (
        "ACTIVE"
        if market.get("funding_rate") is not None
        else "UNAVAILABLE"
    )
)

st.write(
    "NEWS: "
    + (
        "ACTIVE"
        if news
        else "UNAVAILABLE"
    )
)

st.write(
    "GROQ AI: "
    + (
        "ACTIVE"
        if ai
        else "UNAVAILABLE"
    )
)

st.write(
    "SIGNAL JOURNAL: ACTIVE"
)

st.write(
    "LAST REFRESH: "
    + str(
        st.session_state["last_refresh"]
    )
)


# ============================================================
# 39. AUTO REFRESH
# ============================================================

if auto_refresh:

    try:

        from streamlit_autorefresh import st_autorefresh

        refresh_count = st_autorefresh(
            interval=refresh_minutes * 60 * 1000,
            key="saliga_auto_refresh"
        )

        if (
            refresh_count
            != st.session_state["refresh_count"]
        ):

            st.session_state["refresh_count"] = (
                refresh_count
            )

            if (
                st.session_state["last_refresh"]
                is not None
            ):

                if (
                    time.time()
                    - st.session_state["last_refresh_epoch"]
                    >= refresh_minutes * 60
                ):

                    try:

                        run_full_cycle(lookback)

                    except Exception as e:

                        st.error(
                            "Refresh error: " + str(e)
                        )

                    st.rerun()

    except ImportError:

        st.sidebar.warning(
            "Install streamlit-autorefresh."
        )


# ============================================================
# 40. FOOTER
# ============================================================

st.markdown("---")

st.caption(
    "⚡ SALIGA AI PRO 20.0 | "
    "MARKET INTELLIGENCE | "
    "AUTOMATIC VIRTUAL SIGNAL JOURNAL | "
    "NOT FINANCIAL ADVICE"
)
