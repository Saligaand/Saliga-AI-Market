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
    AUTOREFRESH_AVAILABLE = True
except ImportError:
    AUTOREFRESH_AVAILABLE = False

try:
    import feedparser
    FEEDPARSER_AVAILABLE = True
except ImportError:
    FEEDPARSER_AVAILABLE = False

try:
    from openai import OpenAI
    OPENAI_AVAILABLE = True
except ImportError:
    OPENAI_AVAILABLE = False


# =========================================================
# 1. ALAPBEÁLLÍTÁSOK
# =========================================================

APP_NAME = "SALIGA AI PRO – ADAPTIVE INTELLIGENCE"
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

BASE_THRESHOLD = 5.0

logging.basicConfig(level=logging.ERROR)


# =========================================================
# 2. ADATBÁZIS
# =========================================================

def db():
    conn = sqlite3.connect(DB_PATH, timeout=20)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=20000")
    return conn


def init_db():

    conn = db()

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
    CREATE TABLE IF NOT EXISTS signals (
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
        exit_price REAL,
        note TEXT
    )
    """)

    conn.execute("""
    CREATE TABLE IF NOT EXISTS signal_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        signal_id INTEGER,
        event TEXT,
        price REAL,
        created_at TEXT
    )
    """)

    conn.execute("""
    CREATE TABLE IF NOT EXISTS adaptive_stats (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        symbol TEXT,
        timeframe TEXT,
        total INTEGER,
        wins INTEGER,
        losses INTEGER,
        win_rate REAL,
        updated_at TEXT,
        UNIQUE(symbol, timeframe)
    )
    """)

    # Régebbi adatbázisok kompatibilitása

    columns = {
        row["name"]
        for row in conn.execute(
            "PRAGMA table_info(market_history)"
        ).fetchall()
    }

    required = {
        "symbol": "TEXT",
        "timestamp": "INTEGER",
        "price": "REAL",
        "open_interest": "REAL",
        "delta": "REAL"
    }

    for name, dtype in required.items():
        if name not in columns:
            conn.execute(
                f"ALTER TABLE market_history ADD COLUMN {name} {dtype}"
            )

    conn.commit()
    conn.close()


init_db()


# =========================================================
# 3. OKX KAPCSOLAT
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


exchange = get_exchange()


# =========================================================
# 4. SEGÉDFÜGGVÉNYEK
# =========================================================

def now_text():
    return datetime.now(timezone.utc).strftime(
        "%Y-%m-%d %H:%M:%S UTC"
    )


def safe_float(value, default=None):
    try:
        result = float(value)
        if np.isfinite(result):
            return result
    except (TypeError, ValueError):
        pass

    return default


def rsi(series, period=14):

    delta = series.diff()

    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)

    avg_gain = gain.rolling(period).mean()
    avg_loss = loss.rolling(period).mean()

    rs = avg_gain / avg_loss.replace(0, np.nan)

    result = 100 - (100 / (1 + rs))

    result = result.mask(
        (avg_loss == 0) & (avg_gain > 0),
        100
    )

    result = result.mask(
        (avg_gain == 0) & (avg_loss > 0),
        0
    )

    result = result.mask(
        (avg_gain == 0) & (avg_loss == 0),
        50
    )

    return result


def calculate_atr(df, period=14):

    high = df["high"]
    low = df["low"]
    close = df["close"]

    previous = close.shift(1)

    tr = pd.concat([
        high - low,
        (high - previous).abs(),
        (low - previous).abs()
    ], axis=1).max(axis=1)

    return tr.rolling(period).mean()


def rolling_vwap(df, period=48):

    typical = (
        df["high"] + df["low"] + df["close"]
    ) / 3

    volume = df["volume"].replace(0, np.nan)

    numerator = (typical * volume).rolling(period).sum()
    denominator = volume.rolling(period).sum()

    return numerator / denominator


def candle_time(timestamp):

    return int(timestamp)


# =========================================================
# 5. GYERTYAADATOK
# =========================================================

@st.cache_data(ttl=45)
def fetch_ohlcv(symbol, timeframe):

    raw = exchange.fetch_ohlcv(
        symbol,
        timeframe=timeframe,
        limit=220
    )

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

    # Az aktuális, még nem lezárt gyertyát kihagyjuk.
    df = df.iloc[:-1].copy()

    if len(df) < 40:
        raise ValueError("Kevés lezárt gyertya.")

    df["rsi"] = rsi(df["close"])
    df["atr"] = calculate_atr(df)
    df["vwap"] = rolling_vwap(df)

    df["momentum"] = (
        df["close"] - df["close"].shift(5)
    )

    df["volume_avg"] = (
        df["volume"].rolling(20).mean()
    )

    df["volume_ratio"] = (
        df["volume"] / df["volume_avg"].replace(0, np.nan)
    )

    return df.dropna().reset_index(drop=True)


# =========================================================
# 6. PIACI ADATOK – OKX
# =========================================================

def fetch_ticker(symbol):

    try:
        ticker = exchange.fetch_ticker(symbol)

        return safe_float(ticker.get("last"))

    except Exception:
        return None


def fetch_orderbook(symbol):

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

        if total == 0:
            return None

        return (bids - asks) / total

    except Exception:
        return None


def fetch_delta(symbol):

    try:
        trades = exchange.fetch_trades(
            symbol,
            limit=300
        )

        buy = 0.0
        sell = 0.0

        for trade in trades:

            amount = safe_float(trade.get("amount"), 0)

            if trade.get("side") == "buy":
                buy += amount

            elif trade.get("side") == "sell":
                sell += amount

        total = buy + sell

        if total == 0:
            return None, 0

        return (buy - sell) / total, len(trades)

    except Exception:
        return None, 0


def fetch_open_interest(symbol):

    try:
        result = exchange.fetch_open_interest(symbol)

        info = result.get("info", {})

        candidates = [
            result.get("openInterestValue"),
            result.get("openInterestAmount"),
            info.get("oi"),
            result.get("openInterest")
        ]

        for value in candidates:

            parsed = safe_float(value)

            if parsed is not None:
                return parsed

    except Exception:
        pass

    return None


def fetch_funding(symbol):

    try:
        result = exchange.fetch_funding_rate(symbol)

        return safe_float(
            result.get("fundingRate")
        )

    except Exception:
        return None


# =========================================================
# 7. OPEN INTEREST TÖRTÉNET
# =========================================================

def save_market_snapshot(symbol, price, oi, delta):

    if price is None:
        return

    conn = db()

    conn.execute("""
    INSERT INTO market_history
    (symbol, timestamp, price, open_interest, delta)
    VALUES (?, ?, ?, ?, ?)
    """, (
        symbol,
        int(time.time()),
        price,
        oi,
        delta
    ))

    conn.commit()
    conn.close()


def get_oi_change(symbol, current_oi):

    if current_oi is None:
        return None

    conn = db()

    previous = conn.execute("""
    SELECT open_interest
    FROM market_history
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

    old = safe_float(previous["open_interest"])

    if old is None or old == 0:
        return None

    return (current_oi - old) / abs(old) * 100


# =========================================================
# 8. PIACI STRUKTÚRA
# =========================================================

def market_structure(df):

    recent = df.tail(25)

    high = recent["high"].iloc[:-1].max()
    low = recent["low"].iloc[:-1].min()

    price = df["close"].iloc[-1]

    previous_high = df["high"].iloc[-3]
    previous_low = df["low"].iloc[-3]

    structure = "RANGE"

    if price > high:
        structure = "BULLISH BREAKOUT"

    elif price < low:
        structure = "BEARISH BREAKDOWN"

    elif (
        df["high"].iloc[-1] > previous_high
        and df["low"].iloc[-1] > previous_low
    ):
        structure = "BULLISH STRUCTURE"

    elif (
        df["high"].iloc[-1] < previous_high
        and df["low"].iloc[-1] < previous_low
    ):
        structure = "BEARISH STRUCTURE"

    return structure


def liquidity_sweep(df):

    recent = df.tail(21)

    previous = recent.iloc[:-1]
    last = recent.iloc[-1]

    previous_high = previous["high"].max()
    previous_low = previous["low"].min()

    if (
        last["high"] > previous_high
        and last["close"] < previous_high
    ):
        return "BEARISH SWEEP"

    if (
        last["low"] < previous_low
        and last["close"] > previous_low
    ):
        return "BULLISH SWEEP"

    return "NONE"


# =========================================================
# 9. ADAPTÍV KÜSZÖB
# =========================================================

def adaptive_threshold(symbol, timeframe):

    conn = db()

    row = conn.execute("""
    SELECT total, win_rate
    FROM adaptive_stats
    WHERE symbol = ? AND timeframe = ?
    """, (
        symbol,
        timeframe
    )).fetchone()

    conn.close()

    # Kevés adat esetén nincs automatikus módosítás.
    if not row or row["total"] < 20:
        return BASE_THRESHOLD

    win_rate = safe_float(row["win_rate"], 0.5)

    # Óvatos, korlátozott küszöbmódosítás.
    adjustment = (0.50 - win_rate) * 2

    return float(
        np.clip(
            BASE_THRESHOLD + adjustment,
            4.5,
            6.5
        )
    )


# =========================================================
# 10. ADAPTÍV STATISZTIKA FRISSÍTÉSE
# =========================================================

def update_adaptive_stats():

    conn = db()

    for symbol in SYMBOLS:

        for timeframe in TIMEFRAMES:

            rows = conn.execute("""
            SELECT status
            FROM signals
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

            wins = sum(
                1 for row in rows
                if row["status"] == "TP3"
            )

            losses = total - wins

            win_rate = wins / total

            conn.execute("""
            INSERT INTO adaptive_stats
            (symbol, timeframe, total, wins, losses,
             win_rate, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)

            ON CONFLICT(symbol, timeframe)
            DO UPDATE SET
                total=excluded.total,
                wins=excluded.wins,
                losses=excluded.losses,
                win_rate=excluded.win_rate,
                updated_at=excluded.updated_at
            """, (
                symbol,
                timeframe,
                total,
                wins,
                losses,
                win_rate,
                now_text()
            ))

    conn.commit()
    conn.close()


# =========================================================
# 11. EGY IDŐSÍK ELEMZÉSE
# =========================================================

def analyze_timeframe(
    symbol,
    timeframe,
    df,
    orderbook,
    delta,
    oi_change,
    funding
):

    last = df.iloc[-1]

    price = float(last["close"])
    atr = float(last["atr"])
    current_rsi = float(last["rsi"])
    momentum = float(last["momentum"])

    score = 0.0
    reasons = []

    structure = market_structure(df)
    sweep = liquidity_sweep(df)

    # Piaci struktúra

    if structure in [
        "BULLISH BREAKOUT",
        "BULLISH STRUCTURE"
    ]:
        score += 2
        reasons.append("Bullish piaci struktúra")

    elif structure in [
        "BEARISH BREAKDOWN",
        "BEARISH STRUCTURE"
    ]:
        score -= 2
        reasons.append("Bearish piaci struktúra")

    # RSI

    if current_rsi > 55:
        score += 1
        reasons.append("Pozitív RSI")

    elif current_rsi < 45:
        score -= 1
        reasons.append("Negatív RSI")

    # Momentum

    if momentum > 0:
        score += 1

    elif momentum < 0:
        score -= 1

    # VWAP

    vwap = safe_float(last["vwap"])

    if vwap is not None:

        if price > vwap:
            score += 1
            reasons.append("Ár a rolling VWAP felett")

        elif price < vwap:
            score -= 1
            reasons.append("Ár a rolling VWAP alatt")

    # Forgalom

    volume_ratio = safe_float(
        last["volume_ratio"],
        1
    )

    if volume_ratio > 1.5:

        if momentum > 0:
            score += 0.5

        elif momentum < 0:
            score -= 0.5

    # Liquidity sweep

    if sweep == "BULLISH SWEEP":
        score += 1.5
        reasons.append("Bullish likviditási kisöprés")

    elif sweep == "BEARISH SWEEP":
        score -= 1.5
        reasons.append("Bearish likviditási kisöprés")

    # Order book

    if orderbook is not None:

        if orderbook > 0.15:
            score += 1
            reasons.append("Vételi könyvfölény")

        elif orderbook < -0.15:
            score -= 1
            reasons.append("Eladási könyvfölény")

    # Delta

    if delta is not None:

        if delta > 0.15:
            score += 1
            reasons.append("Pozitív becsült delta")

        elif delta < -0.15:
            score -= 1
            reasons.append("Negatív becsült delta")

    # Open Interest

    if oi_change is not None:

        if oi_change > 0.5 and delta is not None:

            if delta > 0.10:
                score += 1
                reasons.append("OI emelkedik, vételi agresszió")

            elif delta < -0.10:
                score -= 1
                reasons.append("OI emelkedik, eladási agresszió")

        elif oi_change < -0.5:
            reasons.append("OI csökken – pozíciózárás lehetséges")

    # Funding

    if funding is not None:

        if funding > 0.001:
            reasons.append("Magasabb pozitív funding")

        elif funding < -0.001:
            reasons.append("Negatív funding")

    threshold = adaptive_threshold(
        symbol,
        timeframe
    )

    if score >= threshold:
        side = "LONG"

    elif score <= -threshold:
        side = "SHORT"

    else:
        side = "WAIT"

    confidence = min(
        99,
        int(abs(score) / 9 * 100)
    )

    return {
        "symbol": symbol,
        "timeframe": timeframe,
        "price": price,
        "score": round(score, 2),
        "side": side,
        "confidence": confidence,
        "rsi": round(current_rsi, 2),
        "atr": atr,
        "structure": structure,
        "sweep": sweep,
        "delta": delta,
        "oi_change": oi_change,
        "funding": funding,
        "orderbook": orderbook,
        "volume_ratio": volume_ratio,
        "reasons": reasons,
        "candle_timestamp": candle_time(
            df["timestamp"].iloc[-1]
        )
    }


# =========================================================
# 12. POZÍCIÓTERV
# =========================================================

def create_trade_plan(result):

    if result["side"] == "WAIT":
        return None

    entry = result["price"]
    atr = result["atr"]

    if atr <= 0:
        return None

    side = result["side"]

    risk_distance = atr * 1.5

    if side == "LONG":

        sl = entry - risk_distance

        tp1 = entry + risk_distance
        tp2 = entry + risk_distance * 2
        tp3 = entry + risk_distance * 3

    else:

        sl = entry + risk_distance

        tp1 = entry - risk_distance
        tp2 = entry - risk_distance * 2
        tp3 = entry - risk_distance * 3

    return {
        "entry": entry,
        "sl": sl,
        "tp1": tp1,
        "tp2": tp2,
        "tp3": tp3
    }


# =========================================================
# 13. JELZÉSNAPLÓ
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
    INSERT OR IGNORE INTO signals
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
# 14. JELZÉSEK KÖVETÉSE
# =========================================================

def track_signals():

    conn = db()

    rows = conn.execute("""
    SELECT *
    FROM signals
    WHERE status IN ('OPEN', 'TP1', 'TP2')
    """).fetchall()

    for row in rows:

        symbol = row["symbol"]

        price = fetch_ticker(
            SYMBOLS.get(symbol)
        )

        if price is None:
            continue

        side = row["side"]

        sl = row["stop_loss"]
        tp1 = row["tp1"]
        tp2 = row["tp2"]
        tp3 = row["tp3"]

        hit1 = bool(row["hit_tp1"])
        hit2 = bool(row["hit_tp2"])
        hit3 = bool(row["hit_tp3"])

        signal_id = row["id"]

        conn.execute("""
        UPDATE signals
        SET last_price = ?
        WHERE id = ?
        """, (price, signal_id))

        def event(name):

            conn.execute("""
            INSERT INTO signal_events
            (signal_id, event, price, created_at)
            VALUES (?, ?, ?, ?)
            """, (
                signal_id,
                name,
                price,
                now_text()
            ))

        # Konzervatív szabály:
        # Ha két ellenkező irányú szint is átlépődött
        # két frissítés között, a stopot vesszük előre.

        stop_hit = (
            price <= sl if side == "LONG"
            else price >= sl
        )

        if stop_hit:

            if hit2:
                status = "STOP_AFTER_TP2"

            elif hit1:
                status = "STOP_AFTER_TP1"

            else:
                status = "STOP_LOSS"

            event(status)

            conn.execute("""
            UPDATE signals
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

        # TP1

        tp1_hit = (
            price >= tp1 if side == "LONG"
            else price <= tp1
        )

        if tp1_hit and not hit1:

            hit1 = True
            event("TP1")

            conn.execute("""
            UPDATE signals
            SET hit_tp1 = 1,
                status = 'TP1'
            WHERE id = ?
            """, (signal_id,))

        # TP2

        tp2_hit = (
            price >= tp2 if side == "LONG"
            else price <= tp2
        )

        if tp2_hit and not hit2:

            hit2 = True
            event("TP2")

            conn.execute("""
            UPDATE signals
            SET hit_tp2 = 1,
                status = 'TP2'
            WHERE id = ?
            """, (signal_id,))

        # TP3

        tp3_hit = (
            price >= tp3 if side == "LONG"
            else price <= tp3
        )

        if tp3_hit and not hit3:

            event("TP3")

            conn.execute("""
            UPDATE signals
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

    update_adaptive_stats()


# =========================================================
# 15. TELJES PIACI ELEMZÉS
# =========================================================

@st.cache_data(ttl=45, show_spinner=False)
def load_analysis():

    results = {}

    for coin, symbol in SYMBOLS.items():

        coin_results = {}

        try:

            orderbook = fetch_orderbook(symbol)
            delta, trade_count = fetch_delta(symbol)
            oi = fetch_open_interest(symbol)
            funding = fetch_funding(symbol)
            price = fetch_ticker(symbol)

            oi_change = get_oi_change(
                coin,
                oi
            )

            save_market_snapshot(
                coin,
                price,
                oi,
                delta
            )

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
                        orderbook,
                        delta,
                        oi_change,
                        funding
                    )

                    result["trade_count"] = trade_count

                    plan = create_trade_plan(result)

                    result["plan"] = plan

                    save_signal(
                        result,
                        plan
                    )

                    coin_results[label] = result

                except Exception as error:

                    coin_results[label] = {
                        "error": str(error)
                    }

            results[coin] = {
                "timeframes": coin_results,
                "price": price,
                "oi": oi,
                "oi_change": oi_change,
                "delta": delta,
                "trade_count": trade_count,
                "funding": funding,
                "orderbook": orderbook
            }

        except Exception as error:

            results[coin] = {
                "error": str(error)
            }

    return results


# =========================================================
# 16. BIZTONSÁGOS HELYI AI VÁLASZ
# =========================================================

def local_answer(question, data):

    lines = [
        "SALIGA AI – belső elemzőmotor",
        "",
        "A rendelkezésre álló piaci adatok:"
    ]

    for coin, item in data.items():

        if "timeframes" not in item:
            continue

        lines.append(f"\n{coin}:")

        for tf, result in item["timeframes"].items():

            if "error" in result:
                continue

            lines.append(
                f"{tf}: {result['side']} | "
                f"Score: {result['score']} | "
                f"RSI: {result['rsi']} | "
                f"Struktúra: {result['structure']}"
            )

    lines.extend([
        "",
        "Ez szabályalapú elemzés, nem garantált előrejelzés.",
        "A kérdésed: " + question
    ])

    return "\n".join(lines)


def groq_answer(question, data):

    try:

        api_key = st.secrets.get(
            "GROQ_API_KEY",
            os.getenv("GROQ_API_KEY")
        )

        model = st.secrets.get(
            "GROQ_MODEL",
            "llama-3.3-70b-versatile"
        )

        if not api_key or not OPENAI_AVAILABLE:
            return local_answer(question, data)

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
                        "Csak a megadott adatokat használd. "
                        "Ne találj ki adatokat. "
                        "Ne állíts biztos profitot. "
                        "Különböztesd meg a tényt és a becslést."
                    )
                },
                {
                    "role": "user",
                    "content": (
                        "Piaci adatok:\n"
                        + json.dumps(data, default=str)
                        + "\n\nKérdés:\n"
                        + question
                    )
                }
            ],
            temperature=0.2
        )

        return response.choices[0].message.content

    except Exception as error:

        return (
            local_answer(question, data)
            + "\n\nGroq nem elérhető: "
            + str(error)
        )


# =========================================================
# 17. FELÜLET
# =========================================================

st.set_page_config(
    page_title=APP_NAME,
    page_icon="⚡",
    layout="wide"
)

st.markdown("""
<style>

.stApp {
    background-color: #f5f7fa;
    color: #182230;
}

[data-testid="stMetric"] {
    background: white;
    border: 1px solid #e2e8f0;
    padding: 14px;
    border-radius: 12px;
}

.long {
    color: #079455;
    font-weight: bold;
}

.short {
    color: #d92d20;
    font-weight: bold;
}

.wait {
    color: #667085;
    font-weight: bold;
}

</style>
""", unsafe_allow_html=True)


st.title("⚡ SALIGA AI PRO")
st.caption(
    "MULTI-ASSET INTELLIGENCE | "
    "POSITION FLOW | ADAPTIVE ANALYSIS"
)


# =========================================================
# 18. OLDALSÁV
# =========================================================

with st.sidebar:

    st.header("Beállítások")

    refresh_seconds = st.selectbox(
        "Automatikus frissítés",
        [30, 45, 60, 120, 300],
        index=2
    )

    selected_coin = st.selectbox(
        "Kiemelt instrumentum",
        list(SYMBOLS.keys())
    )

    balance = st.number_input(
        "Számlaegyenleg USDT",
        min_value=0.0,
        value=10000.0,
        step=100.0
    )

    risk_percent = st.number_input(
        "Kockázat (%)",
        min_value=0.1,
        max_value=5.0,
        value=1.0,
        step=0.1
    )

    if st.button("🔄 Adatok frissítése"):

        load_analysis.clear()
        st.rerun()

    if AUTOREFRESH_AVAILABLE:

        st_autorefresh(
            interval=refresh_seconds * 1000,
            key="saliga_refresh"
        )

    else:

        st.info(
            "Az automatikus frissítéshez "
            "telepítsd a streamlit-autorefresh csomagot."
        )


# =========================================================
# 19. ADATOK BETÖLTÉSE
# =========================================================

with st.spinner("SALIGA AI – piaci adatok elemzése..."):

    market_data = load_analysis()


# A jelzéskövetés nem a cache-elt elemzés része.
try:
    track_signals()
except Exception as error:
    st.warning(f"Jelzéskövetési hiba: {error}")


# =========================================================
# 20. FŐ PIACI ÁTTEKINTÉS
# =========================================================

st.subheader("🌐 Piaci áttekintés")

columns = st.columns(3)

for index, coin in enumerate(SYMBOLS):

    item = market_data.get(coin, {})

    with columns[index]:

        if item.get("price"):

            st.metric(
                coin + " / USDT",
                f"{item['price']:,.4f}"
            )

        else:

            st.metric(
                coin,
                "Nincs adat"
            )

        if item.get("oi_change") is not None:

            st.write(
                f"OI változás: "
                f"{item['oi_change']:+.2f}%"
            )

        else:

            st.write("OI változás: nincs elegendő adat")

        if item.get("delta") is not None:

            st.write(
                f"Estimated Delta: "
                f"{item['delta']:+.3f}"
            )

        else:

            st.write("Delta: nincs adat")


# =========================================================
# 21. IDŐSÍKOS ELEMZÉS
# =========================================================

st.divider()

st.subheader("📊 Többidősíkú pozícióelemzés")

tabs = st.tabs([
    "M5",
    "M15",
    "H1",
    "H4",
    "D1"
])

for tab, timeframe in zip(
    tabs,
    TIMEFRAMES.keys()
):

    with tab:

        for coin in SYMBOLS:

            item = market_data.get(coin, {})

            result = item.get(
                "timeframes",
                {}
            ).get(timeframe)

            st.markdown(f"### {coin}")

            if not result or "error" in result:

                st.warning(
                    result.get("error", "Nincs adat")
                    if result else "Nincs adat"
                )

                continue

            side = result["side"]

            if side == "LONG":

                st.success("🟢 LONG")

            elif side == "SHORT":

                st.error("🔴 SHORT")

            else:

                st.info("⚪ WAIT – nincs megerősített irány")

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
                "Forgalom arány",
                f"{result['volume_ratio']:.2f}"
            )

            st.write(
                f"**Piaci struktúra:** {result['structure']}"
            )

            st.write(
                f"**Likviditás:** {result['sweep']}"
            )

            st.write(
                "**Elemzési tényezők:** "
                + (
                    ", ".join(result["reasons"])
                    if result["reasons"]
                    else "Nincs erős megerősítés"
                )
            )

            plan = result.get("plan")

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

                risk_amount = (
                    balance * risk_percent / 100
                )

                stop_distance = abs(
                    plan["entry"] - plan["sl"]
                )

                if stop_distance > 0:

                    quantity = (
                        risk_amount / stop_distance
                    )

                    st.caption(
                        f"Elméleti pozícióméret: "
                        f"{quantity:.5f} egység. "
                        "Ez nem veszi figyelembe a kontraktusméretet, "
                        "a díjakat, a csúszást és a tőkeáttételt."
                    )

            else:

                st.info(
                    "Nincs megfelelően megerősített pozícióterv."
                )

            st.divider()


# =========================================================
# 22. POZÍCIÓÁRAMLÁS
# =========================================================

st.subheader("🌊 Pozícióáramlás")

flow_columns = st.columns(3)

for index, coin in enumerate(SYMBOLS):

    item = market_data.get(coin, {})

    with flow_columns[index]:

        st.markdown(f"### {coin}")

        oi = item.get("oi_change")
        delta = item.get("delta")
        funding = item.get("funding")
        book = item.get("orderbook")

        st.write(
            "OI változás:",
            f"{oi:+.2f}%" if oi is not None else "N/A"
        )

        st.write(
            "Becsült delta:",
            f"{delta:+.3f}" if delta is not None else "N/A"
        )

        st.write(
            "Funding:",
            f"{funding:.6f}" if funding is not None else "N/A"
        )

        st.write(
            "Order Book:",
            f"{book:+.3f}" if book is not None else "N/A"
        )

        if oi is not None and delta is not None:

            if oi > 0.5 and delta > 0.1:

                st.success(
                    "Lehetséges long pozícióépülés"
                )

            elif oi > 0.5 and delta < -0.1:

                st.error(
                    "Lehetséges short pozícióépülés"
                )

            elif oi < -0.5:

                st.warning(
                    "Lehetséges pozíciózárások"
                )

            else:

                st.info(
                    "Nincs egyértelmű pozícióáramlás"
                )

        else:

            st.info(
                "Nincs elegendő adat a pozícióáramlás becsléséhez."
            )


# =========================================================
# 23. BTC – ALT KAPCSOLAT
# =========================================================

st.divider()

st.subheader("₿ BTC – ALT piaci kapcsolat")

btc = market_data.get("BTC", {})

for coin in ["ETH", "SOL"]:

    alt = market_data.get(coin, {})

    btc_tf = btc.get("timeframes", {}).get("H1", {})
    alt_tf = alt.get("timeframes", {}).get("H1", {})

    if (
        not btc_tf
        or not alt_tf
        or "error" in btc_tf
        or "error" in alt_tf
    ):
        continue

    st.write(f"**BTC és {coin} H1 összehasonlítás**")

    st.write(
        f"BTC: {btc_tf['side']} "
        f"({btc_tf['score']})"
    )

    st.write(
        f"{coin}: {alt_tf['side']} "
        f"({alt_tf['score']})"
    )

    if (
        btc_tf["side"] == alt_tf["side"]
        and btc_tf["side"] != "WAIT"
    ):

        st.success(
            "Az irányok összhangban vannak."
        )

    elif (
        btc_tf["side"] != "WAIT"
        and alt_tf["side"] != "WAIT"
    ):

        st.warning(
            "A BTC és az altcoin iránya eltér."
        )

    else:

        st.info(
            "Nincs egyértelmű közös irány."
        )


# =========================================================
# 24. JELZÉSNAPLÓ
# =========================================================

st.divider()

st.subheader("📒 SALIGA AI – Jelzéstörténet")

conn = db()

signals = pd.read_sql_query("""
SELECT
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
FROM signals
ORDER BY id DESC
LIMIT 200
""", conn)

conn.close()

if not signals.empty:

    total = len(signals)

    open_count = int(
        signals["status"].isin(
            ["OPEN", "TP1", "TP2"]
        ).sum()
    )

    tp3_count = int(
        (signals["status"] == "TP3").sum()
    )

    stopped_count = int(
        signals["status"].isin([
            "STOP_LOSS",
            "STOP_AFTER_TP1",
            "STOP_AFTER_TP2"
        ]).sum()
    )

    m1, m2, m3, m4 = st.columns(4)

    m1.metric("Jelzések", total)
    m2.metric("Nyitott", open_count)
    m3.metric("TP3", tp3_count)
    m4.metric("Stop", stopped_count)

    st.dataframe(
        signals,
        use_container_width=True,
        hide_index=True
    )

else:

    st.info(
        "Még nincs naplózott LONG vagy SHORT jelzés."
    )


# =========================================================
# 25. ADAPTIVE INTELLIGENCE
# =========================================================

st.divider()

st.subheader("🧠 Adaptive Intelligence")

conn = db()

adaptive = pd.read_sql_query("""
SELECT *
FROM adaptive_stats
ORDER BY symbol, timeframe
""", conn)

conn.close()

if not adaptive.empty:

    adaptive["win_rate"] = (
        adaptive["win_rate"] * 100
    ).round(2)

    st.dataframe(
        adaptive,
        use_container_width=True,
        hide_index=True
    )

else:

    st.info(
        "Az adaptív statisztikához legalább 20 lezárt "
        "jelzés szükséges instrumentumonként és idősíkonként."
    )


# =========================================================
# 26. AI CHAT
# =========================================================

st.divider()

st.subheader("🤖 SALIGA AI – Piaci asszisztens")

question = st.text_input(
    "Kérdezz a jelenlegi piaci helyzetről:"
)

if st.button("Elemzés indítása"):

    if question.strip():

        with st.spinner("Elemzés..."):

            answer = groq_answer(
                question,
                market_data
            )

        st.markdown(answer)


# =========================================================
# 27. FIGYELMEZTETÉS
# =========================================================

st.divider()

st.caption("""
SALIGA AI PRO – Kísérleti kriptopiaci elemzőrendszer.

Az adatok az OKX nyilvános piacáról származnak.

Az Open Interest, a delta és az order book elemzése
becslés, nem teljes piaci pozíciókimutatás.

A jelzések nem végrehajtott megbízások.
A pozíciótervek elméleti referenciaértékek.

A napló a frissítések között észlelt árszinteket rögzíti,
nem folyamatos tick- vagy gyertyaalapú visszatesztet.

A korábbi eredmények nem garantálják a jövőbeli teljesítményt.
""")

st.caption(
    f"{APP_NAME} | OKX ONLY | "
    f"Last update: {now_text()}"
)
