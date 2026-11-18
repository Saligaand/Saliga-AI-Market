# SALIGA AI PRO 21.0
# OKX + Groq AI + news/macro fusion | No EMA / No SMA
# Run: pip install streamlit pandas numpy requests plotly
#      streamlit run saliga_ai_pro_21.py

from __future__ import annotations
import os, json, time, math, sqlite3, hashlib, re
from datetime import datetime, timezone
from typing import Any, List, Optional, Tuple

import requests
import numpy as np
import pandas as pd
import streamlit as st
import plotly.graph_objects as go

APP_VERSION = "21.0"
OKX_BASE = "https://www.okx.com"
DB_PATH = os.getenv("SALIGA_DB", "saliga_ai_pro.db")
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
DEFAULT_MODEL = "openai/gpt-oss-120b"

SYMBOLS = {
    "BTC": "BTC-USDT-SWAP",
    "ETH": "ETH-USDT-SWAP",
    "SOL": "SOL-USDT-SWAP"
}

TF_MAP = {
    "1m": "1m",
    "5m": "5m",
    "15m": "15m",
    "1H": "1H",
    "4H": "4H"
}

NEWS_FEEDS = [
    "https://cointelegraph.com/rss",
    "https://www.coindesk.com/arc/outboundfeeds/rss/"
]

HEADERS = {
    "User-Agent": "SALIGA-AI/21.0 market research dashboard"
}

st.set_page_config(
    page_title="SALIGA AI PRO 21.0",
    page_icon="⚡",
    layout="wide"
)


# ----------------------------- UTILITIES -----------------------------

def utc_now():
    return datetime.now(timezone.utc)


def iso_now():
    return utc_now().isoformat(timespec="seconds")


def safe_float(x, default=0.0):
    try:
        v = float(x)
        return v if math.isfinite(v) else default
    except Exception:
        return default


def pct(a, b):
    return ((a / b) - 1.0) * 100.0 if b else 0.0


def clamp(x, lo, hi):
    return max(lo, min(hi, x))


def fmt_num(x, digits=2):
    v = safe_float(x, float("nan"))
    return "—" if not math.isfinite(v) else f"{v:,.{digits}f}"


def request_json(path, params=None, timeout=12):
    r = requests.get(
        OKX_BASE + path,
        params=params or {},
        headers=HEADERS,
        timeout=timeout
    )

    r.raise_for_status()
    data = r.json()

    if str(data.get("code", "0")) != "0":
        raise RuntimeError(
            f"OKX API: {data.get('msg', 'unknown error')}"
        )

    return data


# ----------------------------- DATABASE -----------------------------

def init_db():

    with sqlite3.connect(DB_PATH) as con:

        con.execute("""
            CREATE TABLE IF NOT EXISTS oi_history(
                ts INTEGER NOT NULL,
                symbol TEXT NOT NULL,
                oi REAL,
                price REAL,
                PRIMARY KEY(ts,symbol)
            )
        """)

        con.execute("""
            CREATE TABLE IF NOT EXISTS book_history(
                ts INTEGER NOT NULL,
                symbol TEXT NOT NULL,
                side TEXT NOT NULL,
                price REAL NOT NULL,
                size REAL NOT NULL
            )
        """)

        con.execute("""
            CREATE TABLE IF NOT EXISTS events(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL,
                symbol TEXT NOT NULL,
                event_type TEXT NOT NULL,
                direction TEXT,
                price REAL,
                score REAL,
                details TEXT
            )
        """)

        con.execute("""
            CREATE TABLE IF NOT EXISTS trades(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                direction TEXT NOT NULL,
                entry_ts TEXT NOT NULL,
                entry_price REAL NOT NULL,
                status TEXT NOT NULL DEFAULT 'OPEN',
                exit_ts TEXT,
                exit_price REAL,
                exit_reason TEXT,
                pnl_pct REAL,
                fees_pct REAL DEFAULT 0,
                max_favourable REAL DEFAULT 0,
                max_adverse REAL DEFAULT 0,
                stop_price REAL,
                target_price REAL
            )
        """)

        con.execute("""
            CREATE INDEX IF NOT EXISTS idx_oi_symbol_ts
            ON oi_history(symbol,ts)
        """)

        con.execute("""
            CREATE INDEX IF NOT EXISTS idx_book_symbol_ts
            ON book_history(symbol,ts)
        """)


def db_event(
    symbol,
    event_type,
    direction="",
    price=0,
    score=0,
    details=""
):

    with sqlite3.connect(DB_PATH) as con:

        con.execute("""
            INSERT INTO events(
                ts,symbol,event_type,direction,
                price,score,details
            )
            VALUES(?,?,?,?,?,?,?)
        """, (
            iso_now(),
            symbol,
            event_type,
            direction,
            price,
            score,
            details[:4000]
        ))


def store_oi(symbol, oi, price):

    ts = int(time.time())

    with sqlite3.connect(DB_PATH) as con:

        con.execute("""
            INSERT OR REPLACE INTO oi_history(
                ts,symbol,oi,price
            )
            VALUES(?,?,?,?)
        """, (ts, symbol, oi, price))

        con.execute("""
            DELETE FROM oi_history
            WHERE ts < ?
        """, (ts - 14 * 86400,))


def oi_change(symbol, minutes=5):

    cutoff = int(time.time()) - minutes * 60

    with sqlite3.connect(DB_PATH) as con:

        row = con.execute("""
            SELECT oi
            FROM oi_history
            WHERE symbol=? AND ts<=?
            ORDER BY ts DESC
            LIMIT 1
        """, (symbol, cutoff)).fetchone()

        latest = con.execute("""
            SELECT oi
            FROM oi_history
            WHERE symbol=?
            ORDER BY ts DESC
            LIMIT 1
        """, (symbol,)).fetchone()

    if not row or not latest or not row[0]:
        return 0.0

    return pct(float(latest[0]), float(row[0]))


def store_book(symbol, bids, asks):

    ts = int(time.time())

    rows = [
        (ts, symbol, "BID", safe_float(x[0]), safe_float(x[1]))
        for x in bids[:20]
    ]

    rows += [
        (ts, symbol, "ASK", safe_float(x[0]), safe_float(x[1]))
        for x in asks[:20]
    ]

    with sqlite3.connect(DB_PATH) as con:

        con.executemany("""
            INSERT INTO book_history(
                ts,symbol,side,price,size
            )
            VALUES(?,?,?,?,?)
        """, rows)

        con.execute("""
            DELETE FROM book_history
            WHERE ts < ?
        """, (ts - 24 * 3600,))


def get_events(limit=100):

    with sqlite3.connect(DB_PATH) as con:

        return pd.read_sql_query("""
            SELECT ts,symbol,event_type,direction,
                   price,score,details
            FROM events
            ORDER BY id DESC
            LIMIT ?
        """, con, params=(limit,))


def get_trades():

    with sqlite3.connect(DB_PATH) as con:

        return pd.read_sql_query("""
            SELECT *
            FROM trades
            ORDER BY id DESC
            LIMIT 300
        """, con)


# ----------------------------- OKX DATA -----------------------------

@st.cache_data(ttl=45, show_spinner=False)
def fetch_candles(inst, bar, limit=160):

    raw = request_json(
        "/api/v5/market/candles",
        {
            "instId": inst,
            "bar": bar,
            "limit": str(limit)
        }
    )["data"]

    rows = []
    now_ms = int(time.time() * 1000)

    durations = {
        "1m": 60_000,
        "5m": 300_000,
        "15m": 900_000,
        "1H": 3_600_000,
        "4H": 14_400_000
    }

    for r in raw:

        ts = int(r[0])

        if ts + durations.get(bar, 60_000) > now_ms:
            continue

        rows.append([
            ts,
            *[safe_float(v) for v in r[1:6]],
            int(r[8]) if len(r) > 8 else 1
        ])

    df = pd.DataFrame(
        rows,
        columns=[
            "ts",
            "open",
            "high",
            "low",
            "close",
            "volume",
            "confirmed"
        ]
    )

    if not df.empty:

        df = (
            df.sort_values("ts")
            .drop_duplicates("ts")
            .reset_index(drop=True)
        )

        df["time"] = pd.to_datetime(
            df["ts"],
            unit="ms",
            utc=True
        )

    return df


def fetch_ticker(inst):

    d = request_json(
        "/api/v5/market/ticker",
        {"instId": inst}
    )["data"][0]

    return {
        "last": safe_float(d.get("last")),
        "bid": safe_float(d.get("bidPx")),
        "ask": safe_float(d.get("askPx")),
        "vol24h": safe_float(d.get("vol24h"))
    }


def fetch_funding(inst):

    d = request_json(
        "/api/v5/public/funding-rate",
        {"instId": inst}
    )["data"][0]

    return safe_float(d.get("fundingRate")) * 100.0


def fetch_oi(inst):

    d = request_json(
        "/api/v5/public/open-interest",
        {"instId": inst}
    )["data"][0]

    return safe_float(d.get("oi"))


def fetch_book(inst):

    d = request_json(
        "/api/v5/market/books",
        {
            "instId": inst,
            "sz": "50"
        }
    )["data"][0]

    bids = d.get("bids", [])
    asks = d.get("asks", [])

    store_book(inst, bids, asks)

    bid_usd = sum(
        safe_float(x[0]) * safe_float(x[1])
        for x in bids[:20]
    )

    ask_usd = sum(
        safe_float(x[0]) * safe_float(x[1])
        for x in asks[:20]
    )

    total = bid_usd + ask_usd

    imbalance = (
        (bid_usd - ask_usd) / total * 100
        if total else 0.0
    )

    return {
        "bids": bids,
        "asks": asks,
        "bid_usd": bid_usd,
        "ask_usd": ask_usd,
        "imbalance": imbalance
    }


def fetch_trade_delta(inst):

    rows = request_json(
        "/api/v5/market/trades",
        {
            "instId": inst,
            "limit": "500"
        }
    )["data"]

    buy_usd = 0.0
    sell_usd = 0.0

    for r in rows:

        value = (
            safe_float(r.get("px"))
            * safe_float(r.get("sz"))
        )

        if r.get("side") == "buy":
            buy_usd += value
        else:
            sell_usd += value

    total = buy_usd + sell_usd

    delta_pct = (
        (buy_usd - sell_usd) / total * 100
        if total else 0.0
    )

    return {
        "buy_usd": buy_usd,
        "sell_usd": sell_usd,
        "delta_pct": delta_pct
    }


# ----------------------------- TECHNICAL ANALYSIS -----------------------------

def wilder_smooth(series, period):

    values = series.astype(float).to_numpy()

    out = np.full(
        len(values),
        np.nan,
        dtype=float
    )

    if len(values) == 0:
        return pd.Series(out, index=series.index)

    seed_idx = min(period - 1, len(values) - 1)

    seed = np.nanmean(
        values[:seed_idx + 1]
    )

    out[seed_idx] = seed
    prev = seed

    for i in range(seed_idx + 1, len(values)):

        val = values[i]

        if np.isnan(val):
            out[i] = prev
        else:
            prev = (
                prev * (period - 1) + val
            ) / period

            out[i] = prev

    return (
        pd.Series(out, index=series.index)
        .ffill()
        .fillna(0.0)
    )


def rsi(series, period=14):

    delta = series.diff().fillna(0.0)

    gain = wilder_smooth(
        delta.clip(lower=0),
        period
    )

    loss = wilder_smooth(
        (-delta).clip(lower=0),
        period
    )

    rs = gain / loss.replace(0, np.nan)

    result = (
        100 - 100 / (1 + rs)
    ).replace(
        [np.inf, -np.inf],
        np.nan
    ).fillna(50)

    result[(loss == 0) & (gain > 0)] = 100
    result[(gain == 0) & (loss > 0)] = 0

    return result


def atr(df, period=14):

    prev = df["close"].shift(1)

    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - prev).abs(),
        (df["low"] - prev).abs()
    ], axis=1).max(axis=1)

    return wilder_smooth(
        tr.fillna(0.0),
        period
    )


def analyze_tf(df):

    if df is None or len(df) < 35:

        return {
            "bias": "INSUFFICIENT DATA",
            "score": 0,
            "rsi": 50,
            "roc": 0,
            "atr_pct": 0,
            "structure": "UNKNOWN",
            "close": 0,
            "details": []
        }

    x = df.copy()

    x["rsi"] = rsi(x["close"])
    x["atr"] = atr(x)

    last = x.iloc[-1]
    prev = x.iloc[-2]

    close = safe_float(last.close)

    roc5 = pct(
        close,
        safe_float(x.close.iloc[-6])
    )

    roc12 = pct(
        close,
        safe_float(x.close.iloc[-13])
    )

    recent_high = safe_float(
        x.high.iloc[-21:-1].max()
    )

    recent_low = safe_float(
        x.low.iloc[-21:-1].min()
    )

    if close > recent_high:
        structure = "BULLISH BREAKOUT"

    elif close < recent_low:
        structure = "BEARISH BREAKDOWN"

    elif close > safe_float(x.close.iloc[-10]):
        structure = "HIGHER SHORT-TERM"

    elif close < safe_float(x.close.iloc[-10]):
        structure = "LOWER SHORT-TERM"

    else:
        structure = "RANGE"

    score = 0.0

    score += clamp(roc5 * 8, -20, 20)
    score += clamp(roc12 * 4, -15, 15)

    score += clamp(
        (safe_float(last.rsi) - 50) * 0.45,
        -18,
        18
    )

    if structure == "BULLISH BREAKOUT":
        score += 22

    elif structure == "BEARISH BREAKDOWN":
        score -= 22

    elif structure == "HIGHER SHORT-TERM":
        score += 7

    elif structure == "LOWER SHORT-TERM":
        score -= 7

    if close > safe_float(prev.close):
        score += 2

    elif close < safe_float(prev.close):
        score -= 2

    score = clamp(score, -100, 100)

    bias = (
        "BULLISH" if score >= 18
        else "BEARISH" if score <= -18
        else "NEUTRAL"
    )

    return {
        "bias": bias,
        "score": score,
        "rsi": safe_float(last.rsi),
        "roc": roc5,
        "roc12": roc12,
        "atr": safe_float(last.atr),
        "atr_pct": (
            safe_float(last.atr) / close * 100
            if close else 0
        ),
        "structure": structure,
        "close": close,
        "details": [
            f"RSI {safe_float(last.rsi):.1f}",
            f"ROC5 {roc5:+.3f}%",
            structure
        ]
    }


# ----------------------------- POSITION FLOW -----------------------------

def derive_position_flow(
    price_change,
    oi_delta,
    delta_pct
):

    if (
        price_change > 0.03
        and oi_delta > 0.10
        and delta_pct > 3
    ):
        return "LONG BUILDING", "LONG"

    if (
        price_change < -0.03
        and oi_delta > 0.10
        and delta_pct < -3
    ):
        return "SHORT BUILDING", "SHORT"

    if (
        price_change > 0.03
        and oi_delta < -0.10
    ):
        return "POSSIBLE SHORT CLOSING", "LONG"

    if (
        price_change < -0.03
        and oi_delta < -0.10
    ):
        return "POSSIBLE LONG CLOSING", "SHORT"

    if (
        price_change > 0.05
        and delta_pct < -5
    ):
        return (
            "SELLING INTO RISE / BEARISH DIVERGENCE",
            "SHORT"
        )

    if (
        price_change < -0.05
        and delta_pct > 5
    ):
        return (
            "BUYING INTO DROP / BULLISH DIVERGENCE",
            "LONG"
        )

    return "NO CLEAR POSITION FLOW", ""


# ----------------------------- TECHNICAL FUSION -----------------------------

def technical_fusion(tf, deriv, price_change):

    weights = {
        "4H": 0.30,
        "1H": 0.28,
        "15m": 0.22,
        "5m": 0.13,
        "1m": 0.07
    }

    mtf = sum(
        tf[k]["score"] * w
        for k, w in weights.items()
        if k in tf
    )

    flow_event, flow_dir = derive_position_flow(
        price_change,
        deriv["oi_change_5m"],
        deriv["delta_pct"]
    )

    deriv_score = clamp(
        deriv["delta_pct"] * 0.55
        + deriv["book_imbalance"] * 0.35
        + deriv["oi_change_5m"] * 2.0,
        -25,
        25
    )

    total = clamp(
        mtf * 0.78 + deriv_score,
        -100,
        100
    )

    htf = [
        tf[k]["score"]
        for k in ("4H", "1H")
        if k in tf
    ]

    ltf = [
        tf[k]["score"]
        for k in ("5m", "1m")
        if k in tf
    ]

    aligned_long = (
        len(htf) == 2
        and min(htf) > 5
        and (not ltf or max(ltf) > 0)
    )

    aligned_short = (
        len(htf) == 2
        and max(htf) < -5
        and (not ltf or min(ltf) < 0)
    )

    reversal = "NONE"

    if (
        total > 15
        and any(
            tf[k]["score"] < -10
            for k in ("1m", "5m")
            if k in tf
        )
    ):
        reversal = "BULLISH REVERSAL WATCH"

    if (
        total < -15
        and any(
            tf[k]["score"] > 10
            for k in ("1m", "5m")
            if k in tf
        )
    ):
        reversal = "BEARISH REVERSAL WATCH"

    direction = (
        "LONG" if total >= 28
        else "SHORT" if total <= -28
        else "WAIT"
    )

    if direction == "LONG":

        if not (
            aligned_long
            or (
                tf.get("15m", {}).get("score", 0) > 20
                and deriv["delta_pct"] > 5
            )
        ):
            direction = "WAIT"

    if direction == "SHORT":

        if not (
            aligned_short
            or (
                tf.get("15m", {}).get("score", 0) < -20
                and deriv["delta_pct"] < -5
            )
        ):
            direction = "WAIT"

    entry_stage = (
        "WATCH" if direction == "WAIT"
        else "EARLY ENTRY"
    )

    if (
        direction == "LONG"
        and tf.get("5m", {}).get("score", 0) > 10
        and deriv["delta_pct"] > 2
    ):
        entry_stage = "CONFIRMED ENTRY"

    elif (
        direction == "SHORT"
        and tf.get("5m", {}).get("score", 0) < -10
        and deriv["delta_pct"] < -2
    ):
        entry_stage = "CONFIRMED ENTRY"

    return {
        "mtf_score": mtf,
        "deriv_score": deriv_score,
        "score": total,
        "flow_event": flow_event,
        "flow_direction": flow_dir,
        "reversal": reversal,
        "direction": direction,
        "entry_stage": entry_stage
    }


# ----------------------------- NEWS INTELLIGENCE -----------------------------

def strip_html(s):

    return (
        re.sub(r"<[^>]+>", " ", s or "")
        .replace("&amp;", "&")
        .strip()
    )


def fetch_news():

    items = []
    seen = set()

    for url in NEWS_FEEDS:

        try:

            resp = requests.get(
                url,
                headers=HEADERS,
                timeout=10
            )

            resp.raise_for_status()

            import xml.etree.ElementTree as ET

            root = ET.fromstring(resp.content)

            nodes = root.findall(".//item")

            if not nodes:

                nodes = root.findall(
                    ".//{http://www.w3.org/2005/Atom}entry"
                )

            for n in nodes[:20]:

                title = strip_html(
                    n.findtext("title") or ""
                )

                desc = strip_html(
                    n.findtext("description")
                    or n.findtext("summary")
                    or ""
                )

                link = n.findtext("link") or ""

                if not link:

                    lnode = n.find(
                        "{http://www.w3.org/2005/Atom}link"
                    )

                    if lnode is not None:
                        link = lnode.attrib.get("href", "")

                pub = (
                    n.findtext("pubDate")
                    or n.findtext("published")
                    or n.findtext("updated")
                    or ""
                )

                key = hashlib.sha1(
                    (title + link).encode(
                        "utf-8",
                        "ignore"
                    )
                ).hexdigest()

                if title and key not in seen:

                    seen.add(key)

                    items.append({
                        "title": title,
                        "description": desc[:600],
                        "link": link,
                        "published": pub,
                        "source": url
                    })

        except Exception:
            continue

    return items[:25]


def news_digest(news):

    return "\n".join(
        f"- {x['title']} | {x['description']} | {x['link']}"
        for x in news[:15]
    ) or "No fresh RSS headlines retrieved."


# ----------------------------- GROQ AI FUSION -----------------------------

def groq_analyze(api_key, model, payload, news):

    system = """
You are SALIGA AI, a cautious crypto market intelligence analyst.

Combine technical, derivatives, order-book, news and macro context.

Do not invent facts, prices, headlines, DXY values or economic releases.

Distinguish observed data from interpretation.

News sentiment is -2 to +2.

Explain whether news confirms, conflicts with, or is irrelevant to
the technical setup.

Never claim certainty or provide guaranteed predictions.

Return ONLY valid JSON with keys:

market_regime,
bias,
news_sentiment,
news_impact,
technical_news_alignment,
reversal_assessment,
long_case,
short_case,
risks,
action,
confidence,
summary.

action must be LONG / SHORT / WAIT.

confidence is an uncalibrated 0-100 analytical score,
not a probability.

If evidence conflicts or is weak, choose WAIT.
"""

    user = {
        "market_snapshot": payload,
        "recent_news": news[:15],
        "instruction": (
            "Assess current setup and potential reversal. "
            "Give greater weight to live exchange data than "
            "speculative headlines. State if no meaningful "
            "news is present."
        )
    }

    body = {
        "model": model,
        "temperature": 0.15,
        "max_tokens": 1100,
        "response_format": {
            "type": "json_object"
        },
        "messages": [
            {
                "role": "system",
                "content": system
            },
            {
                "role": "user",
                "content": json.dumps(
                    user,
                    ensure_ascii=False
                )
            }
        ]
    }

    r = requests.post(
        GROQ_URL,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        },
        json=body,
        timeout=45
    )

    r.raise_for_status()

    content = (
        r.json()["choices"][0]["message"]["content"]
    )

    return json.loads(content)


# ----------------------------- EVENT-DRIVEN AI SCHEDULER -----------------------------

def should_trigger_event(
    tech,
    deriv,
    news,
    previous_hash
):

    headlines_hash = hashlib.sha1(
        "|".join(
            x["title"] for x in news[:8]
        ).encode()
    ).hexdigest()

    reasons = []

    if tech["reversal"] != "NONE":
        reasons.append(tech["reversal"])

    if abs(deriv["oi_change_5m"]) >= 0.45:
        reasons.append("OI SHOCK")

    if abs(deriv["delta_pct"]) >= 22:
        reasons.append("EXTREME TRADE DELTA")

    if abs(deriv["book_imbalance"]) >= 35:
        reasons.append("ORDER BOOK IMBALANCE")

    if headlines_hash != previous_hash and news:
        reasons.append("NEW HEADLINES")

    return (
        bool(reasons),
        ", ".join(reasons),
        headlines_hash
    )


def ai_due(symbol, event_trigger):

    now = time.time()

    last = st.session_state.get(
        f"ai_last_{symbol}",
        0.0
    )

    last_event = st.session_state.get(
        f"ai_event_last_{symbol}",
        0.0
    )

    regular_due = now - last >= 15 * 60

    event_due = (
        event_trigger
        and now - last_event >= 180
    )

    if regular_due or event_due:

        if event_due and not regular_due:

            st.session_state[
                f"ai_event_last_{symbol}"
            ] = now

        st.session_state[
            f"ai_last_{symbol}"
        ] = now

        return True

    return False


# ----------------------------- TRADE JOURNAL -----------------------------

def update_trade_journal(
    symbol,
    signal,
    price,
    atr_value
):

    direction = signal["direction"]

    if direction not in ("LONG", "SHORT"):
        return

    stop_dist = max(
        atr_value * 1.5,
        price * 0.002
    )

    target_dist = max(
        atr_value * 2.5,
        price * 0.004
    )

    stop = (
        price - stop_dist
        if direction == "LONG"
        else price + stop_dist
    )

    target = (
        price + target_dist
        if direction == "LONG"
        else price - target_dist
    )

    with sqlite3.connect(DB_PATH) as con:

        row = con.execute("""
            SELECT id,direction,entry_price
            FROM trades
            WHERE symbol=? AND status='OPEN'
            ORDER BY id DESC
            LIMIT 1
        """, (symbol,)).fetchone()

        if row and row[1] == direction:
            return

        if row and row[1] != direction:

            old_pnl = (
                pct(price, row[2])
                if row[1] == "LONG"
                else pct(row[2], price)
            )

            con.execute("""
                UPDATE trades
                SET status='CLOSED',
                    exit_ts=?,
                    exit_price=?,
                    exit_reason='OPPOSITE SIGNAL',
                    pnl_pct=?
                WHERE id=?
            """, (
                iso_now(),
                price,
                old_pnl,
                row[0]
            ))

        con.execute("""
            INSERT INTO trades(
                symbol,direction,entry_ts,entry_price,
                status,stop_price,target_price
            )
            VALUES(?,?,?,?,?,?,?)
        """, (
            symbol,
            direction,
            iso_now(),
            price,
            "OPEN",
            stop,
            target
        ))

    db_event(
        symbol,
        f"{signal['entry_stage']} {direction}",
        direction,
        price,
        signal["score"],
        "Paper journal entry; not an executed order."
    )


def manage_open_trade(symbol, price):

    with sqlite3.connect(DB_PATH) as con:

        row = con.execute("""
            SELECT id,direction,entry_price,
                   stop_price,target_price
            FROM trades
            WHERE symbol=? AND status='OPEN'
            ORDER BY id DESC
            LIMIT 1
        """, (symbol,)).fetchone()

        if not row:
            return

        tid, direction, entry, stop, target = row

        exit_reason = None

        if direction == "LONG" and price <= stop:
            exit_reason = "STOP LOSS"

        elif direction == "LONG" and price >= target:
            exit_reason = "TAKE PROFIT"

        elif direction == "SHORT" and price >= stop:
            exit_reason = "STOP LOSS"

        elif direction == "SHORT" and price <= target:
            exit_reason = "TAKE PROFIT"

        if exit_reason:

            pnl = (
                pct(price, entry)
                if direction == "LONG"
                else pct(entry, price)
            )

            con.execute("""
                UPDATE trades
                SET status='CLOSED',
                    exit_ts=?,
                    exit_price=?,
                    exit_reason=?,
                    pnl_pct=?
                WHERE id=?
            """, (
                iso_now(),
                price,
                exit_reason,
                pnl,
                tid
            ))

            db_event(
                symbol,
                exit_reason,
                direction,
                price,
                pnl,
                "Paper journal exit; estimated before fees/slippage."
            )


# ----------------------------- USER INTERFACE -----------------------------

def render_signal_card(
    symbol,
    ticker,
    tf,
    deriv,
    fusion,
    ai,
    error=""
):

    st.subheader(
        f"{symbol}  ·  {SYMBOLS[symbol]}"
    )

    c1, c2, c3, c4 = st.columns(4)

    c1.metric(
        "Last price",
        fmt_num(ticker.get("last"), 4)
    )

    c2.metric(
        "Technical score",
        f"{fusion['score']:+.1f}/100"
    )

    c3.metric(
        "Entry stage",
        fusion["entry_stage"]
    )

    c4.metric(
        "Direction",
        fusion["direction"]
    )

    st.write(
        f"**Position flow:** {fusion['flow_event']}  "
        f"· **Reversal:** {fusion['reversal']}"
    )

    st.write(
        f"**MTF:** "
        f"4H {tf['4H']['bias']} ({tf['4H']['score']:+.0f}) · "
        f"1H {tf['1H']['bias']} ({tf['1H']['score']:+.0f}) · "
        f"15m {tf['15m']['bias']} ({tf['15m']['score']:+.0f}) · "
        f"5m {tf['5m']['bias']} ({tf['5m']['score']:+.0f}) · "
        f"1m {tf['1m']['bias']} ({tf['1m']['score']:+.0f})"
    )

    st.write(
        f"**OI 5m:** {deriv['oi_change_5m']:+.3f}% · "
        f"**Funding:** {deriv['funding_pct']:+.4f}% · "
        f"**Trade delta:** {deriv['delta_pct']:+.1f}% · "
        f"**Book imbalance:** {deriv['book_imbalance']:+.1f}%"
    )

    if error:
        st.warning(error)

    if ai:

        st.markdown("#### Groq AI + News Fusion")

        st.write(
            f"**AI bias:** {ai.get('bias','—')} · "
            f"**AI action:** {ai.get('action','—')} · "
            f"**Analytical confidence:** "
            f"{ai.get('confidence','—')}/100 "
            f"(nem kalibrált valószínűség)"
        )

        st.write(
            f"**News sentiment:** "
            f"{ai.get('news_sentiment','—')} · "
            f"**News impact:** "
            f"{ai.get('news_impact','—')}"
        )

        st.write(
            f"**Technical/news alignment:** "
            f"{ai.get('technical_news_alignment','—')}"
        )

        st.write(
            ai.get("summary", "No AI summary yet.")
        )

        with st.expander("Részletes AI-értékelés"):

            for key in [
                "market_regime",
                "reversal_assessment",
                "long_case",
                "short_case",
                "risks"
            ]:

                st.markdown(
                    f"**{key.replace('_',' ').title()}**"
                )

                st.write(ai.get(key, "—"))

    else:

        st.info(
            "A Groq AI-értékelés még nem futott le ebben a ciklusban. "
            "A technikai motor ettől függetlenül működik."
        )


# ----------------------------- MAIN APPLICATION -----------------------------

def main():

    init_db()

    st.title("⚡ SALIGA AI PRO 21.0")

    st.caption(
        "Multi-asset market intelligence · OKX live data · "
        "Groq + news fusion · No EMA / No SMA · Paper journal only"
    )

    with st.sidebar:

        st.header("Beállítások")

        groq_key = st.text_input(
            "Groq API key",
            value=os.getenv("GROQ_API_KEY", ""),
            type="password"
        )

        model = st.text_input(
            "Groq model",
            value=os.getenv("GROQ_MODEL", DEFAULT_MODEL)
        )

        selected = st.multiselect(
            "Eszközök",
            list(SYMBOLS.keys()),
            default=["BTC", "ETH", "SOL"]
        )

        refresh = st.slider(
            "Dashboard frissítés (perc)",
            1,
            5,
            1
        )

        enable_ai = st.toggle(
            "Groq AI + hírelemzés",
            value=True
        )

        enable_journal = st.toggle(
            "Paper trade napló",
            value=True
        )

        st.caption(
            "Az AI normál esetben 15 percenként fut. "
            "Rendkívüli esemény esetén legalább 3 percenként "
            "újraindítható."
        )

    if not selected:

        st.warning("Válassz legalább egy eszközt.")
        return

    news = fetch_news()

    st.markdown("### Friss kriptóhírek")

    if news:

        for item in news[:8]:

            title = item["title"]

            if item["link"]:

                st.markdown(
                    f"- [{title}]({item['link']})"
                )

            else:
                st.write(f"- {title}")

    else:

        st.info(
            "Az RSS-hírcsatornák jelenleg nem érhetők el. "
            "A technikai elemzés ettől még működik."
        )

    all_snapshots = {}

    for symbol in selected:

        inst = SYMBOLS[symbol]
        errors = []

        try:

            ticker = fetch_ticker(inst)
            oi = fetch_oi(inst)
            funding = fetch_funding(inst)
            book = fetch_book(inst)
            delta = fetch_trade_delta(inst)

            store_oi(
                inst,
                oi,
                ticker["last"]
            )

            oi5 = oi_change(inst, 5)

            frames = {}
            tf_results = {}

            for tf_name, bar in TF_MAP.items():

                try:

                    frames[tf_name] = fetch_candles(
                        inst,
                        bar,
                        160
                    )

                    tf_results[tf_name] = analyze_tf(
                        frames[tf_name]
                    )

                except Exception as e:

                    errors.append(
                        f"{tf_name}: {e}"
                    )

                    tf_results[tf_name] = analyze_tf(
                        pd.DataFrame()
                    )

            one = frames.get(
                "1m",
                pd.DataFrame()
            )

            price_change = (
                pct(
                    one.close.iloc[-1],
                    one.close.iloc[-6]
                )
                if len(one) >= 6
                else 0.0
            )

            deriv = {
                "oi": oi,
                "oi_change_5m": oi5,
                "funding_pct": funding,
                "delta_pct": delta["delta_pct"],
                "buy_usd": delta["buy_usd"],
                "sell_usd": delta["sell_usd"],
                "book_imbalance": book["imbalance"],
                "bid_usd": book["bid_usd"],
                "ask_usd": book["ask_usd"]
            }

            fusion = technical_fusion(
                tf_results,
                deriv,
                price_change
            )

            snap = {
                "symbol": symbol,
                "instrument": inst,
                "price": ticker["last"],
                "price_change_5m_pct": price_change,
                "timeframes": tf_results,
                "derivatives": deriv,
                "technical_fusion": fusion,
                "book_top_bids": book["bids"][:8],
                "book_top_asks": book["asks"][:8]
            }

            # Event-driven AI analysis

            prev_hash = st.session_state.get(
                f"news_hash_{symbol}",
                ""
            )

            event_trigger, event_reason, new_hash = (
                should_trigger_event(
                    fusion,
                    deriv,
                    news,
                    prev_hash
                )
            )

            st.session_state[
                f"news_hash_{symbol}"
            ] = new_hash

            ai_result = st.session_state.get(
                f"ai_result_{symbol}"
            )

            if (
                enable_ai
                and groq_key
                and ai_due(symbol, event_trigger)
            ):

                try:

                    with st.spinner(
                        f"{symbol}: Groq AI + hírek elemzése…"
                    ):

                        ai_result = groq_analyze(
                            groq_key,
                            model,
                            snap,
                            news
                        )

                    st.session_state[
                        f"ai_result_{symbol}"
                    ] = ai_result

                    db_event(
                        inst,
                        "AI ANALYSIS",
                        ai_result.get("action", ""),
                        ticker["last"],
                        safe_float(
                            ai_result.get("confidence")
                        ),
                        (
                            f"Trigger: {event_reason or '15-minute scheduled analysis'}; "
                            f"{ai_result.get('summary','')}"
                        )
                    )

                except Exception as e:

                    errors.append(
                        f"Groq: {e}"
                    )

            elif enable_ai and not groq_key:

                errors.append(
                    "Add meg a Groq API kulcsot az oldalsávban "
                    "vagy a GROQ_API_KEY környezeti változóban."
                )

            # AI contextual validation

            if ai_result:

                ai_action = str(
                    ai_result.get("action", "WAIT")
                ).upper()

                alignment = str(
                    ai_result.get(
                        "technical_news_alignment",
                        ""
                    )
                ).lower()

                if (
                    fusion["direction"] in ("LONG", "SHORT")
                    and ai_action not in (
                        fusion["direction"],
                        "WAIT"
                    )
                ):

                    fusion["entry_stage"] = (
                        "WATCH — AI CONFLICT"
                    )

                elif (
                    "conflict" in alignment
                    and fusion["direction"] in ("LONG", "SHORT")
                ):

                    fusion["entry_stage"] = (
                        "WATCH — NEWS CONFLICT"
                    )

                elif (
                    ai_action == fusion["direction"]
                    and fusion["direction"] in ("LONG", "SHORT")
                    and fusion["entry_stage"] == "EARLY ENTRY"
                ):

                    fusion["entry_stage"] = (
                        "AI-ALIGNED EARLY ENTRY"
                    )

            # Paper trade journal

            if enable_journal:

                manage_open_trade(
                    inst,
                    ticker["last"]
                )

                update_trade_journal(
                    inst,
                    fusion,
                    ticker["last"],
                    tf_results.get("15m", {}).get("atr", 0)
                )

            all_snapshots[symbol] = snap

            with st.container(border=True):

                render_signal_card(
                    symbol,
                    ticker,
                    tf_results,
                    deriv,
                    fusion,
                    ai_result,
                    " · ".join(errors)
                )

                if len(one) > 5:

                    fig = go.Figure(
                        data=[
                            go.Candlestick(
                                x=one.time,
                                open=one.open,
                                high=one.high,
                                low=one.low,
                                close=one.close,
                                name="1m"
                            )
                        ]
                    )

                    fig.update_layout(
                        height=260,
                        margin=dict(
                            l=10,
                            r=10,
                            t=20,
                            b=10
                        ),
                        xaxis_rangeslider_visible=False
                    )

                    st.plotly_chart(
                        fig,
                        use_container_width=True,
                        key=f"chart_{symbol}"
                    )

                with st.expander(
                    "Order book / likviditás pillanatkép"
                ):

                    b1, b2 = st.columns(2)

                    b1.write("**Top bids**")

                    b1.dataframe(
                        pd.DataFrame(
                            book["bids"][:10],
                            columns=[
                                "Price",
                                "Size",
                                "Liquidation",
                                "Orders"
                            ]
                        ).iloc[:, :2],
                        use_container_width=True,
                        hide_index=True
                    )

                    b2.write("**Top asks**")

                    b2.dataframe(
                        pd.DataFrame(
                            book["asks"][:10],
                            columns=[
                                "Price",
                                "Size",
                                "Liquidation",
                                "Orders"
                            ]
                        ).iloc[:, :2],
                        use_container_width=True,
                        hide_index=True
                    )

                with st.expander(
                    "Idősíkok részletes adatai"
                ):

                    st.dataframe(
                        pd.DataFrame([
                            {
                                "TF": k,
                                **{
                                    kk: vv
                                    for kk, vv in v.items()
                                    if kk != "details"
                                }
                            }
                            for k, v in tf_results.items()
                        ]),
                        use_container_width=True,
                        hide_index=True
                    )

        except Exception as e:

            st.error(
                f"{symbol}: adatlekérési hiba: {e}"
            )

    # ----------------------------- EVENT LOG -----------------------------

    st.divider()

    left, right = st.columns(2)

    with left:

        st.subheader(
            "Position flow / eseménynapló"
        )

        ev = get_events(100)

        if not ev.empty:

            st.dataframe(
                ev,
                use_container_width=True,
                hide_index=True
            )

        else:

            st.info(
                "Még nincs rögzített esemény."
            )

    with right:

        st.subheader(
            "Paper trade napló"
        )

        trades = get_trades()

        if not trades.empty:

            st.dataframe(
                trades,
                use_container_width=True,
                hide_index=True
            )

            csv_data = trades.to_csv(
                index=False
            ).encode("utf-8-sig")

            st.download_button(
                "Trade napló letöltése CSV-ben",
                csv_data,
                "saliga_trade_journal.csv",
                "text/csv"
            )

        else:

            st.info(
                "Még nincs naplózott paper trade."
            )

    st.caption(
        f"SALIGA AI PRO {APP_VERSION} · "
        f"Last refresh: {iso_now()} · "
        "A jelzések elemzési/paper-trading célúak, "
        "nem végrehajtott megbízások."
    )

    st.markdown(
        f"<meta http-equiv='refresh' content='{int(refresh)*60}'>",
        unsafe_allow_html=True
    )


if __name__ == "__main__":
    main()
