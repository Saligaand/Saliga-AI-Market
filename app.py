import os
import time
import json
import sqlite3
import logging
from datetime import datetime, timezone

import ccxt
import numpy as np
import pandas as pd
import streamlit as st

try:
    from streamlit_autorefresh import st_autorefresh
except ImportError:
    st_autorefresh = None

try:
    from openai import OpenAI
except ImportError:
    OpenAI = None


# =========================================================
# 1. APP CONFIG
# =========================================================

st.set_page_config(
    page_title="SALIGA AI PRO",
    page_icon="⚡",
    layout="wide"
)

APP_NAME = "SALIGA AI PRO – MARKET INTELLIGENCE"
DB_PATH = "saliga_market.db"

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

# Külön küszöb minden idősíkhoz.
THRESHOLDS = {
    "M5": 2.5,
    "M15": 3.0,
    "H1": 3.5,
    "H4": 4.0,
    "D1": 4.5
}

logging.basicConfig(level=logging.ERROR)


# =========================================================
# 2. ADATBÁZIS
# Új táblanevek: a régi hibás táblák nem zavarják.
# =========================================================

def db():
    conn = sqlite3.connect(
        DB_PATH,
        timeout=30
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def init_db():

    conn = db()

    conn.execute("""
    CREATE TABLE IF NOT EXISTS signals_v2 (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        signal_key TEXT UNIQUE,
        symbol TEXT,
        timeframe TEXT,
        side TEXT,
        created_at TEXT,
        candle_timestamp INTEGER,
        entry REAL,
        stop_loss REAL,
        tp1 REAL,
        tp2 REAL,
        tp3 REAL,
        status TEXT DEFAULT 'OPEN',
        hit_tp1 INTEGER DEFAULT 0,
        hit_tp2 INTEGER DEFAULT 0,
        hit_tp3 INTEGER DEFAULT 0,
        last_price REAL,
        closed_at TEXT,
        exit_price REAL
    )
    """)

    conn.execute("""
    CREATE TABLE IF NOT EXISTS signal_events_v2 (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        signal_id INTEGER,
        event TEXT,
        price REAL,
        created_at TEXT
    )
    """)

    conn.execute("""
    CREATE TABLE IF NOT EXISTS market_history_v2 (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        symbol TEXT,
        timestamp INTEGER,
        price REAL,
        open_interest REAL,
        delta REAL
    )
    """)

    conn.execute("""
    CREATE TABLE IF NOT EXISTS adaptive_stats_v2 (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        symbol TEXT,
        timeframe TEXT,
        total INTEGER DEFAULT 0,
        tp3_count INTEGER DEFAULT 0,
        stop_count INTEGER DEFAULT 0,
        tp3_rate REAL DEFAULT 0,
        updated_at TEXT,
        UNIQUE(symbol, timeframe)
    )
    """)

    conn.execute("""
    CREATE INDEX IF NOT EXISTS idx_signals_v2_status
    ON signals_v2(status)
    """)

    conn.execute("""
    CREATE INDEX IF NOT EXISTS idx_events_v2_signal
    ON signal_events_v2(signal_id)
    """)

    conn.commit()
    conn.close()


init_db()


# =========================================================
# 3. SEGÉDFÜGGVÉNYEK
# =========================================================

def now_text():

    return datetime.now(
        timezone.utc
    ).strftime("%Y-%m-%d %H:%M:%S UTC")


def number(value, default=None):

    try:
        result = float(value)

        if np.isfinite(result):
            return result

    except (TypeError, ValueError):
        pass

    return default


def safe_secrets(key, default=None):

    try:
        return st.secrets.get(key, default)
    except Exception:
        return os.getenv(key, default)


# =========================================================
# 4. OKX
# =========================================================

@st.cache_resource
def get_exchange():

    exchange = ccxt.okx({
        "enableRateLimit": True,
        "timeout": 25000,
        "options": {
            "defaultType": "swap"
        }
    })

    exchange.load_markets()

    return exchange


# =========================================================
# 5. TECHNIKAI SZÁMÍTÁSOK
# =========================================================

def calculate_rsi(series, period=14):

    change = series.diff()

    gain = change.clip(lower=0)
    loss = -change.clip(upper=0)

    avg_gain = gain.rolling(period).mean()
    avg_loss = loss.rolling(period).mean()

    rs = avg_gain / avg_loss.replace(0, np.nan)

    rsi = 100 - (100 / (1 + rs))

    rsi = rsi.mask(
        (avg_loss == 0) & (avg_gain > 0),
        100
    )

    rsi = rsi.mask(
        (avg_gain == 0) & (avg_loss > 0),
        0
    )

    rsi = rsi.mask(
        (avg_gain == 0) & (avg_loss == 0),
        50
    )

    return rsi


def calculate_atr(df, period=14):

    previous = df["close"].shift(1)

    tr = pd.concat(
        [
            df["high"] - df["low"],
            (df["high"] - previous).abs(),
            (df["low"] - previous).abs()
        ],
        axis=1
    ).max(axis=1)

    return tr.rolling(period).mean()


def calculate_vwap(df, period=48):

    typical = (
        df["high"] +
        df["low"] +
        df["close"]
    ) / 3

    volume = df["volume"].replace(0, np.nan)

    numerator = (
        typical * volume
    ).rolling(period).sum()

    denominator = volume.rolling(period).sum()

    return numerator / denominator


# =========================================================
# 6. OHLCV ADATOK
# =========================================================

@st.cache_data(ttl=40, show_spinner=False)
def fetch_ohlcv(symbol, timeframe):

    exchange = get_exchange()

    raw = exchange.fetch_ohlcv(
        symbol,
        timeframe=timeframe,
        limit=220
    )

    if not raw or len(raw) < 60:
        raise ValueError("Nem érkezett elegendő gyertyaadat.")

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

    # Csak lezárt gyertyákat vizsgálunk.
    df = df.iloc[:-1].copy()

    df["rsi"] = calculate_rsi(df["close"])
    df["atr"] = calculate_atr(df)
    df["vwap"] = calculate_vwap(df)

    df["momentum"] = (
        df["close"] - df["close"].shift(5)
    )

    df["volume_avg"] = (
        df["volume"].rolling(20).mean()
    )

    df["volume_ratio"] = (
        df["volume"] /
        df["volume_avg"].replace(0, np.nan)
    )

    df = df.replace(
        [np.inf, -np.inf],
        np.nan
    )

    df = df.dropna().reset_index(drop=True)

    if len(df) < 30:
        raise ValueError("Nem elegendő érvényes adat.")

    return df


# =========================================================
# 7. PIACI ADATOK
# =========================================================

@st.cache_data(ttl=20, show_spinner=False)
def fetch_market_data(symbol):

    exchange = get_exchange()

    result = {
        "price": None,
        "oi": None,
        "funding": None,
        "delta": None,
        "delta_trades": 0,
        "orderbook": None,
        "buy_volume": None,
        "sell_volume": None,
        "errors": []
    }

    # PRICE

    try:
        ticker = exchange.fetch_ticker(symbol)
        result["price"] = number(ticker.get("last"))
    except Exception as e:
        result["errors"].append("Ticker: " + str(e))

    # OPEN INTEREST

    try:

        oi = exchange.fetch_open_interest(symbol)

        info = oi.get("info", {})

        candidates = [
            oi.get("openInterestValue"),
            oi.get("openInterestAmount"),
            info.get("oi"),
            oi.get("openInterest")
        ]

        for candidate in candidates:

            parsed = number(candidate)

            if parsed is not None:
                result["oi"] = parsed
                break

    except Exception as e:
        result["errors"].append("OI: " + str(e))

    # FUNDING

    try:

        funding = exchange.fetch_funding_rate(symbol)

        result["funding"] = number(
            funding.get("fundingRate")
        )

    except Exception as e:
        result["errors"].append("Funding: " + str(e))

    # ORDER BOOK

    try:

        book = exchange.fetch_order_book(
            symbol,
            limit=20
        )

        bids = sum(
            item[1] for item in book.get("bids", [])
        )

        asks = sum(
            item[1] for item in book.get("asks", [])
        )

        total = bids + asks

        if total > 0:

            result["orderbook"] = (
                (bids - asks) / total
            )

    except Exception as e:
        result["errors"].append("Order book: " + str(e))

    # ESTIMATED DELTA

    try:

        trades = exchange.fetch_trades(
            symbol,
            limit=300
        )

        buy = 0.0
        sell = 0.0

        for trade in trades:

            amount = number(
                trade.get("amount"),
                0
            )

            if trade.get("side") == "buy":
                buy += amount

            elif trade.get("side") == "sell":
                sell += amount

        total = buy + sell

        if total > 0:

            result["delta"] = (
                (buy - sell) / total
            )

        result["buy_volume"] = buy
        result["sell_volume"] = sell
        result["delta_trades"] = len(trades)

    except Exception as e:
        result["errors"].append("Delta: " + str(e))

    return result


# =========================================================
# 8. OI VÁLTOZÁS
# =========================================================

def save_market_snapshot(symbol, data):

    if data["price"] is None:
        return

    conn = db()

    conn.execute("""
    INSERT INTO market_history_v2
    (
        symbol,
        timestamp,
        price,
        open_interest,
        delta
    )
    VALUES (?, ?, ?, ?, ?)
    """, (
        symbol,
        int(time.time()),
        data["price"],
        data["oi"],
        data["delta"]
    ))

    conn.commit()
    conn.close()


def get_oi_change(symbol, current_oi):

    if current_oi is None:
        return None

    conn = db()

    previous = conn.execute("""
    SELECT open_interest
    FROM market_history_v2
    WHERE symbol = ?
    AND open_interest IS NOT NULL
    AND timestamp <= ?
    ORDER BY timestamp DESC
    LIMIT 1
    """, (
        symbol,
        int(time.time()) - 300
    )).fetchone()

    conn.close()

    if not previous:
        return None

    old = number(previous["open_interest"])

    if old is None or old == 0:
        return None

    return (
        (current_oi - old) / abs(old)
    ) * 100


# =========================================================
# 9. PIACI STRUKTÚRA
# =========================================================

def market_structure(df):

    recent = df.tail(25)
    previous = recent.iloc[:-1]
    last = recent.iloc[-1]

    previous_high = previous["high"].max()
    previous_low = previous["low"].min()

    if last["close"] > previous_high:
        return "BULLISH BREAKOUT"

    if last["close"] < previous_low:
        return "BEARISH BREAKDOWN"

    highs = df["high"].tail(6).to_numpy()
    lows = df["low"].tail(6).to_numpy()

    if (
        highs[-1] > highs[-2]
        and lows[-1] > lows[-2]
    ):
        return "BULLISH STRUCTURE"

    if (
        highs[-1] < highs[-2]
        and lows[-1] < lows[-2]
    ):
        return "BEARISH STRUCTURE"

    return "RANGE"


def liquidity_sweep(df):

    recent = df.tail(21)

    previous = recent.iloc[:-1]
    last = recent.iloc[-1]

    high = previous["high"].max()
    low = previous["low"].min()

    if (
        last["high"] > high
        and last["close"] < high
    ):
        return "BEARISH SWEEP"

    if (
        last["low"] < low
        and last["close"] > low
    ):
        return "BULLISH SWEEP"

    return "NONE"


# =========================================================
# 10. IDŐSÍKONKÉNTI JELZÉSMOTOR
# =========================================================

def analyze_timeframe(
    symbol,
    timeframe,
    df,
    market,
    oi_change
):

    last = df.iloc[-1]

    price = float(last["close"])
    atr = float(last["atr"])
    rsi = float(last["rsi"])
    momentum = float(last["momentum"])
    volume_ratio = float(last["volume_ratio"])

    structure = market_structure(df)
    sweep = liquidity_sweep(df)

    score = 0.0
    long_reasons = []
    short_reasons = []

    # 1. PIACI STRUKTÚRA

    if structure == "BULLISH BREAKOUT":

        score += 2.0
        long_reasons.append("Bullish kitörés")

    elif structure == "BEARISH BREAKDOWN":

        score -= 2.0
        short_reasons.append("Bearish letörés")

    elif structure == "BULLISH STRUCTURE":

        score += 1.0
        long_reasons.append("Emelkedő struktúra")

    elif structure == "BEARISH STRUCTURE":

        score -= 1.0
        short_reasons.append("Csökkenő struktúra")

    # 2. RSI

    if rsi >= 55:

        score += 1.0
        long_reasons.append("Pozitív RSI")

    elif rsi <= 45:

        score -= 1.0
        short_reasons.append("Negatív RSI")

    # 3. MOMENTUM

    if momentum > 0:

        score += 1.0
        long_reasons.append("Pozitív momentum")

    elif momentum < 0:

        score -= 1.0
        short_reasons.append("Negatív momentum")

    # 4. ROLLING VWAP

    vwap = number(last["vwap"])

    if vwap is not None:

        if price > vwap:

            score += 0.75
            long_reasons.append("Ár VWAP felett")

        elif price < vwap:

            score -= 0.75
            short_reasons.append("Ár VWAP alatt")

    # 5. FORGALOM

    if volume_ratio >= 1.5:

        if momentum > 0:

            score += 0.75
            long_reasons.append("Emelkedő forgalom")

        elif momentum < 0:

            score -= 0.75
            short_reasons.append("Eladási forgalom")

    # 6. LIQUIDITY SWEEP

    if sweep == "BULLISH SWEEP":

        score += 1.25
        long_reasons.append("Bullish likviditási sweep")

    elif sweep == "BEARISH SWEEP":

        score -= 1.25
        short_reasons.append("Bearish likviditási sweep")

    # 7. ORDER BOOK

    orderbook = market["orderbook"]

    if orderbook is not None:

        if orderbook > 0.15:

            score += 0.75
            long_reasons.append("Vételi könyvfölény")

        elif orderbook < -0.15:

            score -= 0.75
            short_reasons.append("Eladási könyvfölény")

    # 8. ESTIMATED DELTA

    delta = market["delta"]

    if delta is not None:

        if delta > 0.15:

            score += 1.0
            long_reasons.append("Pozitív agresszív delta")

        elif delta < -0.15:

            score -= 1.0
            short_reasons.append("Negatív agresszív delta")

    # 9. OPEN INTEREST ÉS DELTA

    if oi_change is not None and delta is not None:

        if oi_change > 0.5 and delta > 0.10:

            score += 0.75
            long_reasons.append("OI növekedés + vételi delta")

        elif oi_change > 0.5 and delta < -0.10:

            score -= 0.75
            short_reasons.append("OI növekedés + eladási delta")

    # KÜLÖN IDŐSÍK-KÜSZÖB

    threshold = THRESHOLDS[timeframe]

    # Jelzési irány

    if score >= threshold:

        side = "LONG"

    elif score <= -threshold:

        side = "SHORT"

    else:

        side = "WAIT"

    # WAIT diagnosztika

    if side == "WAIT":

        if score > 0:

            missing = (
                f"LONG irányhoz még "
                f"{threshold - score:.2f} pont hiányzik."
            )

        elif score < 0:

            missing = (
                f"SHORT irányhoz még "
                f"{threshold - abs(score):.2f} pont hiányzik."
            )

        else:

            missing = (
                "Nincs egyértelmű irányú piaci megerősítés."
            )

    else:

        missing = "A jelzési küszöb teljesült."

    confidence = min(
        99,
        int(abs(score) / 8 * 100)
    )

    return {
        "symbol": symbol,
        "timeframe": timeframe,
        "price": price,
        "score": round(score, 2),
        "threshold": threshold,
        "side": side,
        "confidence": confidence,
        "rsi": round(rsi, 2),
        "atr": atr,
        "vwap": vwap,
        "structure": structure,
        "sweep": sweep,
        "volume_ratio": volume_ratio,
        "delta": delta,
        "oi_change": oi_change,
        "orderbook": orderbook,
        "funding": market["funding"],
        "long_reasons": long_reasons,
        "short_reasons": short_reasons,
        "missing": missing,
        "candle_timestamp": int(
            df["timestamp"].iloc[-1]
        )
    }


# =========================================================
# 11. POZÍCIÓTERV
# =========================================================

def create_trade_plan(result):

    if result["side"] == "WAIT":
        return None

    entry = result["price"]
    atr = result["atr"]

    if atr <= 0:
        return None

    distance = atr * 1.5

    if result["side"] == "LONG":

        sl = entry - distance

        tp1 = entry + distance
        tp2 = entry + distance * 2
        tp3 = entry + distance * 3

    else:

        sl = entry + distance

        tp1 = entry - distance
        tp2 = entry - distance * 2
        tp3 = entry - distance * 3

    return {
        "entry": entry,
        "sl": sl,
        "tp1": tp1,
        "tp2": tp2,
        "tp3": tp3
    }


# =========================================================
# 12. JELZÉSEK NAPLÓZÁSA
# =========================================================

def save_signal(result, plan):

    if plan is None:
        return

    key = (
        f"{result['symbol']}|"
        f"{result['timeframe']}|"
        f"{result['side']}|"
        f"{result['candle_timestamp']}"
    )

    conn = db()

    conn.execute("""
    INSERT OR IGNORE INTO signals_v2
    (
        signal_key,
        symbol,
        timeframe,
        side,
        created_at,
        candle_timestamp,
        entry,
        stop_loss,
        tp1,
        tp2,
        tp3,
        status,
        last_price
    )
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'OPEN', ?)
    """, (
        key,
        result["symbol"],
        result["timeframe"],
        result["side"],
        now_text(),
        result["candle_timestamp"],
        plan["entry"],
        plan["sl"],
        plan["tp1"],
        plan["tp2"],
        plan["tp3"],
        result["price"]
    ))

    conn.commit()
    conn.close()


# =========================================================
# 13. JELZÉSEK KÖVETÉSE
# =========================================================

def track_signals():

    conn = db()

    rows = conn.execute("""
    SELECT *
    FROM signals_v2
    WHERE status IN ('OPEN', 'TP1', 'TP2')
    """).fetchall()

    for row in rows:

        market_symbol = SYMBOLS.get(row["symbol"])

        if not market_symbol:
            continue

        try:

            ticker = get_exchange().fetch_ticker(
                market_symbol
            )

            price = number(ticker.get("last"))

        except Exception:
            continue

        if price is None:
            continue

        side = row["side"]
        signal_id = row["id"]

        sl = row["stop_loss"]
        tp1 = row["tp1"]
        tp2 = row["tp2"]
        tp3 = row["tp3"]

        hit1 = bool(row["hit_tp1"])
        hit2 = bool(row["hit_tp2"])

        def event(name):

            conn.execute("""
            INSERT INTO signal_events_v2
            (
                signal_id,
                event,
                price,
                created_at
            )
            VALUES (?, ?, ?, ?)
            """, (
                signal_id,
                name,
                price,
                now_text()
            ))

        conn.execute("""
        UPDATE signals_v2
        SET last_price = ?
        WHERE id = ?
        """, (price, signal_id))

        stop_hit = (
            price <= sl
            if side == "LONG"
            else price >= sl
        )

        # Stop elsőbbség: ha két frissítés között
        # mindkét szintet átlépte, konzervatívan stop.

        if stop_hit:

            if hit2:
                status = "STOP_AFTER_TP2"

            elif hit1:
                status = "STOP_AFTER_TP1"

            else:
                status = "STOP_LOSS"

            event(status)

            conn.execute("""
            UPDATE signals_v2
            SET status = ?,
                closed_at = ?,
                exit_price = ?
            WHERE id = ?
            """, (
                status,
                now_text(),
                price,
                signal_id
            ))

            continue

        tp1_hit = (
            price >= tp1
            if side == "LONG"
            else price <= tp1
        )

        if tp1_hit and not hit1:

            event("TP1")

            conn.execute("""
            UPDATE signals_v2
            SET hit_tp1 = 1,
                status = 'TP1'
            WHERE id = ?
            """, (signal_id,))

            hit1 = True

        tp2_hit = (
            price >= tp2
            if side == "LONG"
            else price <= tp2
        )

        if tp2_hit and not hit2:

            event("TP2")

            conn.execute("""
            UPDATE signals_v2
            SET hit_tp2 = 1,
                status = 'TP2'
            WHERE id = ?
            """, (signal_id,))

            hit2 = True

        tp3_hit = (
            price >= tp3
            if side == "LONG"
            else price <= tp3
        )

        if tp3_hit:

            event("TP3")

            conn.execute("""
            UPDATE signals_v2
            SET hit_tp3 = 1,
                status = 'TP3',
                closed_at = ?,
                exit_price = ?
            WHERE id = ?
            """, (
                now_text(),
                price,
                signal_id
            ))

    conn.commit()
    conn.close()

    update_stats()


# =========================================================
# 14. STATISZTIKÁK
# =========================================================

def update_stats():

    conn = db()

    for symbol in SYMBOLS:

        for timeframe in TIMEFRAMES:

            rows = conn.execute("""
            SELECT status
            FROM signals_v2
            WHERE symbol = ?
            AND timeframe = ?
            AND status IN (
                'TP3',
                'STOP_LOSS',
                'STOP_AFTER_TP1',
                'STOP_AFTER_TP2'
            )
            """, (
                symbol,
                timeframe
            )).fetchall()

            total = len(rows)

            if total == 0:
                continue

            tp3_count = sum(
                1 for r in rows
                if r["status"] == "TP3"
            )

            stop_count = total - tp3_count

            rate = tp3_count / total

            conn.execute("""
            INSERT INTO adaptive_stats_v2
            (
                symbol,
                timeframe,
                total,
                tp3_count,
                stop_count,
                tp3_rate,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)

            ON CONFLICT(symbol, timeframe)
            DO UPDATE SET
                total = excluded.total,
                tp3_count = excluded.tp3_count,
                stop_count = excluded.stop_count,
                tp3_rate = excluded.tp3_rate,
                updated_at = excluded.updated_at
            """, (
                symbol,
                timeframe,
                total,
                tp3_count,
                stop_count,
                rate,
                now_text()
            ))

    conn.commit()
    conn.close()


# =========================================================
# 15. TELJES ELEMZÉS
# =========================================================

def load_all_analysis():

    all_data = {}

    for coin, symbol in SYMBOLS.items():

        try:

            market = fetch_market_data(symbol)

            oi_change = get_oi_change(
                coin,
                market["oi"]
            )

            save_market_snapshot(
                coin,
                market
            )

            timeframe_results = {}

            for label, timeframe in TIMEFRAMES.items():

                try:

                    df = fetch_ohlcv(
                        symbol,
                        timeframe
                    )

                    result = analyze_timeframe(
                        coin,
                        label,
                        df,
                        market,
                        oi_change
                    )

                    plan = create_trade_plan(result)

                    result["plan"] = plan

                    save_signal(result, plan)

                    timeframe_results[label] = result

                except Exception as e:

                    timeframe_results[label] = {
                        "error": str(e)
                    }

            all_data[coin] = {
                "market": market,
                "oi_change": oi_change,
                "timeframes": timeframe_results
            }

        except Exception as e:

            all_data[coin] = {
                "error": str(e)
            }

    return all_data


# =========================================================
# 16. GROQ AI
# =========================================================

def ask_ai(question, data):

    api_key = safe_secrets("GROQ_API_KEY")
    model = safe_secrets(
        "GROQ_MODEL",
        "llama-3.3-70b-versatile"
    )

    if not api_key or OpenAI is None:

        return (
            "A Groq AI nem érhető el. "
            "Az OKX-alapú elemzőmotor ettől függetlenül működik."
        )

    try:

        client = OpenAI(
            api_key=api_key,
            base_url="https://api.groq.com/openai/v1"
        )

        response = client.chat.completions.create(
            model=model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Te a SALIGA AI kriptopiaci elemzője vagy. "
                        "Csak a kapott adatokat használd. "
                        "Ne találj ki piaci adatot. "
                        "Különböztesd meg a tényt és a becslést. "
                        "Ne ígérj biztos nyereséget."
                    )
                },
                {
                    "role": "user",
                    "content": (
                        "Piaci adatok:\n"
                        + json.dumps(data, default=str)
                        + "\nKérdés:\n"
                        + question
                    )
                }
            ],
            temperature=0.2
        )

        return response.choices[0].message.content

    except Exception as e:

        return "Groq hiba: " + str(e)


# =========================================================
# 17. FELÜLET
# =========================================================

st.markdown("""
<style>
.stApp {
    background: #f4f6fa;
    color: #182230;
}

[data-testid="stMetric"] {
    background: white;
    border: 1px solid #e1e7ef;
    border-radius: 12px;
    padding: 12px;
}
</style>
""", unsafe_allow_html=True)


st.title("⚡ SALIGA AI PRO")
st.caption(
    "MULTI-ASSET INTELLIGENCE | "
    "MARKET STRUCTURE | POSITION FLOW"
)


# =========================================================
# 18. OLDALSÁV
# =========================================================

with st.sidebar:

    st.header("Vezérlőpult")

    refresh = st.selectbox(
        "Automatikus frissítés",
        [30, 45, 60, 120, 300],
        index=2
    )

    if st.button("🔄 Frissítés most"):

        fetch_market_data.clear()
        fetch_ohlcv.clear()

        st.rerun()

    if st_autorefresh is not None:

        st_autorefresh(
            interval=refresh * 1000,
            key="saliga_refresh"
        )

    st.caption("Adatforrás: OKX perpetual swaps")
    st.caption("Nincs EMA / SMA.")


# =========================================================
# 19. ADATOK BETÖLTÉSE
# =========================================================

with st.spinner("Piaci adatok lekérése és elemzése..."):

    market_data = load_all_analysis()


try:

    track_signals()

except Exception as e:

    st.warning(
        "Jelzéskövetési probléma: " + str(e)
    )


# =========================================================
# 20. PIACI ÁTTEKINTÉS
# =========================================================

st.subheader("🌐 Piaci áttekintés")

cols = st.columns(3)

for index, coin in enumerate(SYMBOLS):

    item = market_data.get(coin, {})

    with cols[index]:

        if "error" in item:

            st.error(item["error"])
            continue

        market = item["market"]

        price = market["price"]

        st.metric(
            coin,
            f"{price:,.4f}" if price is not None else "N/A"
        )

        oi = item["oi_change"]
        delta = market["delta"]

        st.write(
            "OI változás:",
            f"{oi:+.2f}%" if oi is not None else "N/A"
        )

        st.write(
            "Estimated Delta:",
            f"{delta:+.3f}" if delta is not None else "N/A"
        )


# =========================================================
# 21. IDŐSÍKONKÉNTI ELEMZÉS
# =========================================================

st.divider()
st.subheader("📊 Multi-Timeframe Intelligence")

tabs = st.tabs(list(TIMEFRAMES.keys()))

for tab, timeframe in zip(tabs, TIMEFRAMES.keys()):

    with tab:

        st.caption(
            f"Jelzési küszöb: {THRESHOLDS[timeframe]} pont"
        )

        for coin in SYMBOLS:

            item = market_data.get(coin, {})

            st.markdown(f"### {coin}")

            if "error" in item:

                st.error(item["error"])
                continue

            result = item["timeframes"].get(timeframe)

            if not result:

                st.warning("Nincs elérhető elemzés.")
                continue

            if "error" in result:

                st.error(result["error"])
                continue

            if result["side"] == "LONG":

                st.success("🟢 LONG – vételi irány")

            elif result["side"] == "SHORT":

                st.error("🔴 SHORT – eladási irány")

            else:

                st.info(
                    "⚪ WAIT – "
                    + result["missing"]
                )

            c1, c2, c3, c4 = st.columns(4)

            c1.metric(
                "Score",
                result["score"]
            )

            c2.metric(
                "RSI",
                result["rsi"]
            )

            c3.metric(
                "Confidence",
                f"{result['confidence']}%"
            )

            c4.metric(
                "Forgalom",
                f"{result['volume_ratio']:.2f}x"
            )

            st.write(
                "**Struktúra:**",
                result["structure"]
            )

            st.write(
                "**Likviditási sweep:**",
                result["sweep"]
            )

            st.write(
                "**LONG tényezők:**",
                ", ".join(result["long_reasons"])
                if result["long_reasons"]
                else "Nincs"
            )

            st.write(
                "**SHORT tényezők:**",
                ", ".join(result["short_reasons"])
                if result["short_reasons"]
                else "Nincs"
            )

            plan = result["plan"]

            if plan:

                p1, p2, p3, p4, p5 = st.columns(5)

                p1.metric(
                    "Entry",
                    f"{plan['entry']:.5f}"
                )

                p2.metric(
                    "Stop Loss",
                    f"{plan['sl']:.5f}"
                )

                p3.metric(
                    "TP1",
                    f"{plan['tp1']:.5f}"
                )

                p4.metric(
                    "TP2",
                    f"{plan['tp2']:.5f}"
                )

                p5.metric(
                    "TP3",
                    f"{plan['tp3']:.5f}"
                )

            st.divider()


# =========================================================
# 22. POZÍCIÓÁRAMLÁS
# =========================================================

st.subheader("🌊 Position Flow Intelligence")

flow_cols = st.columns(3)

for index, coin in enumerate(SYMBOLS):

    item = market_data.get(coin, {})

    with flow_cols[index]:

        st.markdown(f"### {coin}")

        if "error" in item:
            continue

        market = item["market"]

        oi = item["oi_change"]
        delta = market["delta"]
        book = market["orderbook"]

        st.write(
            "Open Interest:",
            f"{oi:+.2f}%" if oi is not None else "N/A"
        )

        st.write(
            "Delta:",
            f"{delta:+.3f}" if delta is not None else "N/A"
        )

        st.write(
            "Order Book:",
            f"{book:+.3f}" if book is not None else "N/A"
        )

        if oi is None or delta is None:

            st.info(
                "Nincs elegendő adat a pozícióáramlás becsléséhez."
            )

        elif oi > 0.5 and delta > 0.1:

            st.success(
                "Lehetséges LONG pozícióépülés"
            )

        elif oi > 0.5 and delta < -0.1:

            st.error(
                "Lehetséges SHORT pozícióépülés"
            )

        elif oi < -0.5:

            st.warning(
                "Lehetséges pozíciózárások"
            )

        else:

            st.info(
                "Nincs egyértelmű pozícióépülés."
            )


# =========================================================
# 23. JELZÉSNAPLÓ
# =========================================================

st.divider()
st.subheader("📒 Jelzéstörténet")

conn = db()

signals = pd.read_sql_query("""
SELECT
    id,
    symbol,
    timeframe,
    side,
    created_at,
    entry,
    stop_loss,
    tp1,
    tp2,
    tp3,
    last_price,
    status,
    hit_tp1,
    hit_tp2,
    hit_tp3
FROM signals_v2
ORDER BY id DESC
LIMIT 200
""", conn)

conn.close()

if not signals.empty:

    st.dataframe(
        signals,
        use_container_width=True,
        hide_index=True
    )

else:

    st.info(
        "Még nem keletkezett LONG vagy SHORT jelzés."
    )


# =========================================================
# 24. ESEMÉNYNAPLÓ
# =========================================================

st.subheader("📌 Jelzési események")

conn = db()

events = pd.read_sql_query("""
SELECT
    e.id,
    e.created_at,
    s.symbol,
    s.timeframe,
    s.side,
    e.event,
    e.price
FROM signal_events_v2 e
LEFT JOIN signals_v2 s
ON e.signal_id = s.id
ORDER BY e.id DESC
LIMIT 100
""", conn)

conn.close()

if not events.empty:

    st.dataframe(
        events,
        use_container_width=True,
        hide_index=True
    )

else:

    st.info(
        "Még nincs TP- vagy stopesemény."
    )


# =========================================================
# 25. STATISZTIKÁK
# =========================================================

st.subheader("🧠 Adaptive Performance")

conn = db()

stats = pd.read_sql_query("""
SELECT
    symbol,
    timeframe,
    total,
    tp3_count,
    stop_count,
    tp3_rate,
    updated_at
FROM adaptive_stats_v2
ORDER BY symbol, timeframe
""", conn)

conn.close()

if not stats.empty:

    stats["tp3_rate"] = (
        stats["tp3_rate"] * 100
    ).round(2)

    stats = stats.rename(
        columns={
            "symbol": "Coin",
            "timeframe": "Idősík",
            "total": "Lezárt jelzés",
            "tp3_count": "TP3",
            "stop_count": "Stop",
            "tp3_rate": "TP3 arány %",
            "updated_at": "Frissítés"
        }
    )

    st.dataframe(
        stats,
        use_container_width=True,
        hide_index=True
    )

else:

    st.info(
        "A statisztikához lezárt jelzések szükségesek."
    )


# =========================================================
# 26. AI CHAT
# =========================================================

st.divider()
st.subheader("🤖 SALIGA AI Assistant")

question = st.text_input(
    "Kérdés a jelenlegi piacról:"
)

if st.button("AI elemzés"):

    if question.strip():

        with st.spinner("AI elemzés..."):

            answer = ask_ai(
                question,
                market_data
            )

        st.markdown(answer)


# =========================================================
# 27. LÁBLÉC
# =========================================================

st.divider()

st.caption("""
SALIGA AI PRO

Adatforrás: OKX perpetual swap piac.

Az OI, a becsült delta és az order book különböző
piaci információkat mutatnak, de önmagukban nem
bizonyítják a kereskedők szándékát.

A jelzések elemzési jelzések, nem végrehajtott megbízások.
A TP/SL értékek elméleti referenciaértékek.

A jelzéskövetés az alkalmazás frissítésekor történik.
A frissítések közötti ármozgás pontos sorrendje
nem minden esetben rekonstruálható.

A múltbeli eredmények nem garantálják a jövőbeli teljesítményt.
""")

st.caption(
    APP_NAME + " | OKX ONLY | " + now_text()
)
