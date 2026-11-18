# ============================================================
# SALIGA AI PRO 19.0
# INSTITUTIONAL MARKET INTELLIGENCE
# BTC DERIVATIVES + PATTERN AI + NEWS + GROQ
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
# 1. CONFIGURATION
# ============================================================

st.set_page_config(
    page_title="SALIGA AI PRO 19.0",
    page_icon="⚡",
    layout="wide"
)

APP_VERSION = "SALIGA AI PRO 19.0"

SYMBOL = "BTC/USDT:USDT"

DB_PATH = "saliga_ai.db"

GROQ_MODEL = "openai/gpt-oss-120b"

OI_URL = "https://www.okx.com/api/v5/public/open-interest"

RSS_FEEDS = [
    "https://cointelegraph.com/rss",
    "https://www.coindesk.com/arc/outboundfeeds/rss/"
]

TIMEFRAMES = {
    "M15": "15m",
    "H1": "1h",
    "H4": "4h",
    "D1": "1d"
}

HTTP_TIMEOUT = 15

DEFAULT_REFRESH = 300

MAX_CANDLES = 5000

PATTERN_WINDOW = 15

PATTERN_HORIZON = 5

TOP_MATCHES = 30


# ============================================================
# 2. SESSION STATE
# ============================================================

DEFAULT_STATE = {
    "market": {},
    "timeframes": {},
    "news": [],
    "ai_result": None,
    "ai_error": None,
    "last_refresh": None,
    "lookback": 2000,
    "refresh_interval": DEFAULT_REFRESH
}

for key, value in DEFAULT_STATE.items():

    if key not in st.session_state:
        st.session_state[key] = value


# ============================================================
# 3. GENERAL FUNCTIONS
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
                value REAL,
                created_at TEXT
            )
        """)

        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_oi_time
            ON oi_history(created_at)
        """)

        conn.commit()


def save_oi(value):

    value = safe_float(value)

    if value is None:
        return

    with db_connection() as conn:

        conn.execute(
            "INSERT INTO oi_history(value, created_at) VALUES (?, ?)",
            (value, timestamp())
        )

        conn.commit()


def get_oi_change(current):

    if current is None:
        return None

    cutoff = now_utc() - timedelta(minutes=5)

    with db_connection() as conn:

        rows = conn.execute("""
            SELECT value, created_at
            FROM oi_history
            ORDER BY id DESC
            LIMIT 500
        """).fetchall()

    candidates = []

    for value, created_at in rows:

        try:

            dt = datetime.fromisoformat(created_at)

            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)

            if dt <= cutoff:
                candidates.append((value, dt))

        except Exception:
            continue

    if not candidates:
        return None

    old_value, old_time = min(
        candidates,
        key=lambda x: abs((cutoff - x[1]).total_seconds())
    )

    return pct_change(current, old_value)


# ============================================================
# 5. OKX CONNECTION
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


# ============================================================
# 6. HISTORICAL CANDLE DOWNLOAD
# ============================================================

def fetch_candles(exchange, timeframe, requested=2000):

    requested = min(int(requested), MAX_CANDLES)

    tf_ms = exchange.parse_timeframe(timeframe) * 1000

    since = exchange.milliseconds() - requested * tf_ms

    all_rows = {}

    for _ in range(15):

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

        newest = max(int(row[0]) for row in batch)

        if newest <= since:
            break

        since = newest + tf_ms

    if not all_rows:
        return None

    rows = sorted(all_rows.values(), key=lambda x: x[0])

    df = pd.DataFrame(
        rows[-requested:],
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

    # A még formálódó gyertya nem kerül az elemzésbe.

    if len(df) > 1:
        df = df.iloc[:-1]

    return df.reset_index(drop=True)


# ============================================================
# 7. BTC MARKET DATA
# ============================================================

def fetch_market_data(exchange):

    result = {
        "source": "OKX",
        "timestamp": timestamp(),
        "errors": []
    }

    # PRICE

    try:

        ticker = exchange.fetch_ticker(SYMBOL)

        result["price"] = safe_float(
            ticker.get("last")
        )

        result["change_24h"] = safe_float(
            ticker.get("percentage")
        )

        result["high_24h"] = safe_float(
            ticker.get("high")
        )

        result["low_24h"] = safe_float(
            ticker.get("low")
        )

        result["volume_24h"] = safe_float(
            ticker.get("quoteVolume")
        )

    except Exception as e:

        result["errors"].append(
            "PRICE: " + str(e)
        )

    # OPEN INTEREST

    try:

        market = exchange.market(SYMBOL)

        response = requests.get(
            OI_URL,
            params={
                "instType": "SWAP",
                "instId": market["id"]
            },
            timeout=HTTP_TIMEOUT
        )

        response.raise_for_status()

        payload = response.json()

        if str(payload.get("code")) == "0":

            if payload.get("data"):

                row = payload["data"][0]

                result["oi_usd"] = safe_float(
                    row.get("oiUsd")
                )

                result["oi_contracts"] = safe_float(
                    row.get("oi")
                )

        if result.get("oi_usd") is not None:

            result["oi_change"] = get_oi_change(
                result["oi_usd"]
            )

            save_oi(result["oi_usd"])

    except Exception as e:

        result["errors"].append(
            "OPEN INTEREST: " + str(e)
        )

    # FUNDING

    try:

        funding = exchange.fetch_funding_rate(
            SYMBOL
        )

        result["funding_rate"] = safe_float(
            funding.get("fundingRate")
        )

        result["next_funding"] = funding.get(
            "fundingDatetime"
        )

    except Exception as e:

        result["errors"].append(
            "FUNDING: " + str(e)
        )

    # ORDER BOOK

    try:

        book = exchange.fetch_order_book(
            SYMBOL,
            limit=50
        )

        bids = sum(
            float(p) * float(q)
            for p, q in book["bids"]
        )

        asks = sum(
            float(p) * float(q)
            for p, q in book["asks"]
        )

        total = bids + asks

        result["bid_volume"] = bids

        result["ask_volume"] = asks

        result["orderbook_imbalance"] = (
            (bids - asks) / total * 100
            if total > 0 else None
        )

    except Exception as e:

        result["errors"].append(
            "ORDERBOOK: " + str(e)
        )

    # TRADE DELTA

    try:

        trades = exchange.fetch_trades(
            SYMBOL,
            limit=500
        )

        buy_volume = 0.0
        sell_volume = 0.0

        buy_notional = 0.0
        sell_notional = 0.0

        for trade in trades:

            amount = safe_float(
                trade.get("amount")
            ) or 0

            price = safe_float(
                trade.get("price")
            ) or 0

            if trade.get("side") == "buy":

                buy_volume += amount

                buy_notional += amount * price

            elif trade.get("side") == "sell":

                sell_volume += amount

                sell_notional += amount * price

        total = buy_volume + sell_volume

        result["delta_percent"] = (
            (buy_volume - sell_volume) / total * 100
            if total > 0 else None
        )

        result["trade_delta_usd"] = (
            buy_notional - sell_notional
        )

        result["trade_count"] = len(trades)

    except Exception as e:

        result["errors"].append(
            "DELTA: " + str(e)
        )

    return result


# ============================================================
# 8. DERIVATIVES INTELLIGENCE
# ============================================================

def derivatives_analysis(market):

    score = 0

    notes = []

    oi = market.get("oi_change")

    price_change = market.get("change_24h")

    funding = market.get("funding_rate")

    delta = market.get("delta_percent")

    imbalance = market.get("orderbook_imbalance")

    # OI + PRICE

    if oi is not None and price_change is not None:

        if oi > 0.5 and price_change > 0:

            score += 10

            notes.append(
                "Árfolyam emelkedik és OI növekszik: új pozíciók épülhetnek."
            )

        elif oi > 0.5 and price_change < 0:

            score -= 10

            notes.append(
                "Árfolyam esik és OI növekszik: új short pozíciók épülhetnek."
            )

        elif oi < -0.5 and price_change > 0:

            score += 4

            notes.append(
                "Árfolyam emelkedik, OI csökken: shortzárások is hajthatják."
            )

        elif oi < -0.5 and price_change < 0:

            score -= 4

            notes.append(
                "Árfolyam esik, OI csökken: longzárások is történhetnek."
            )

    # FUNDING

    if funding is not None:

        if funding > 0.0008:

            score -= 4

            notes.append(
                "Magas pozitív funding: zsúfolt long pozicionáltság lehet."
            )

        elif funding < -0.0003:

            score += 4

            notes.append(
                "Negatív funding: short pozicionáltság lehet."
            )

    # DELTA

    if delta is not None:

        if delta > 8:

            score += 8

            notes.append(
                "A végrehajtott ügyletek deltaértéke vételi nyomást mutat."
            )

        elif delta < -8:

            score -= 8

            notes.append(
                "A végrehajtott ügyletek deltaértéke eladási nyomást mutat."
            )

    # ORDER BOOK

    if imbalance is not None:

        if imbalance > 15:

            score += 4

            notes.append(
                "Az ajánlati könyv pillanatnyilag vételi túlsúlyt mutat."
            )

        elif imbalance < -15:

            score -= 4

            notes.append(
                "Az ajánlati könyv pillanatnyilag eladói túlsúlyt mutat."
            )

    return {
        "score": score,
        "notes": notes
    }


# ============================================================
# 9. MARKET STRUCTURE
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

    close = float(last["close"])

    score = 0

    if close > resistance:

        score = 18

        status = "BULLISH BREAKOUT"

    elif close < support:

        score = -18

        status = "BEARISH BREAKDOWN"

    else:

        status = "RANGE / CONSOLIDATION"

    return {
        "score": score,
        "status": status,
        "support": support,
        "resistance": resistance
    }


# ============================================================
# 10. MOMENTUM ENGINE
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
# 11. HISTORICAL PATTERN INTELLIGENCE
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

    max_start = len(closes) - window - horizon - window

    for i in range(max_start + 1):

        historical = closes[i:i + window]

        if len(historical) != window:
            continue

        if historical[0] == 0:
            continue

        normalized = historical / historical[0] - 1

        normalized = normalized - np.mean(normalized)

        hist_std = np.std(normalized)

        if hist_std == 0:
            continue

        normalized = normalized / hist_std

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

        if future_move is None:
            continue

        candidates.append(
            (correlation, future_move)
        )

    if not candidates:
        return empty

    candidates.sort(
        key=lambda x: x[0],
        reverse=True
    )

    selected = candidates[:TOP_MATCHES]

    returns = np.array([
        x[1] for x in selected
        if x[0] >= 0.60
    ])

    similarities = [
        x[0] for x in selected
    ]

    if len(returns) < 3:

        returns = np.array([
            x[1] for x in selected[:5]
        ])

    if len(returns) == 0:
        return empty

    up = np.mean(returns > 0.15) * 100

    down = np.mean(returns < -0.15) * 100

    flat = 100 - up - down

    average = np.mean(returns)

    similarity = similarities[0]

    score = 0

    if len(returns) >= 5 and similarity >= 0.65:

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
            "WEAK HISTORICAL MATCH"
        )

    return {

        "score": score,

        "similarity": float(similarity),

        "matches": len(returns),

        "up_rate": float(up),

        "down_rate": float(down),

        "flat_rate": float(flat),

        "average_move": float(average),

        "description": description
    }


# ============================================================
# 12. ATR / VOLATILITY
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

    return float(atr / close * 100)


# ============================================================
# 13. POSITION FLOW INTERPRETATION
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
# 14. TIMEFRAME ANALYSIS
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

    direction = (
        "LONG" if score >= 20
        else "SHORT" if score <= -20
        else "WAIT"
    )

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
# 15. AUTOMATIC NEWS ENGINE
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

                    "summary": (
                        entry.get("summary", "")[:500]
                    )
                })

        except Exception:
            continue

    unique = {}

    for item in headlines:

        key = item["title"].lower().strip()

        unique[key] = item

    return list(unique.values())[:20]


# ============================================================
# 16. GROQ AI INTELLIGENCE
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

You are SALIGA AI PRO.

You are an advanced Bitcoin market intelligence analyst.

Analyze:

1. Open Interest
2. Funding Rate
3. Trade Delta
4. Order Book
5. Position Flow
6. Historical Patterns
7. Support and Resistance
8. Momentum
9. Volatility
10. Multi-Timeframe Structure
11. Crypto News

IMPORTANT:

Only use supplied data.

Never invent market data.

Never invent liquidation levels.

Never claim that an order book snapshot is a liquidation heatmap.

Never invent macroeconomic releases.

Distinguish facts from interpretations.

Do not treat historical correlations as guaranteed predictions.

If data conflicts, explain the conflict.

If evidence is weak, recommend WAIT.

Return valid JSON only.

Required format:

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

        content = response.choices[0].message.content

        return json.loads(content), None

    except Exception as e:

        return None, str(e)


# ============================================================
# 17. COMPLETE ANALYSIS CYCLE
# ============================================================

def run_full_cycle(lookback):

    exchange = get_exchange()

    market = fetch_market_data(exchange)

    derivatives = derivatives_analysis(market)

    timeframe_data = {}

    for name, timeframe in TIMEFRAMES.items():

        timeframe_data[name] = analyze_timeframe(

            exchange,

            name,

            timeframe,

            lookback,

            derivatives
        )

    news = fetch_news()

    ai_result, ai_error = run_groq_analysis(

        market,

        timeframe_data,

        news
    )

    st.session_state["market"] = market

    st.session_state["timeframes"] = timeframe_data

    st.session_state["news"] = news

    st.session_state["ai_result"] = ai_result

    st.session_state["ai_error"] = ai_error

    st.session_state["last_refresh"] = timestamp()


# ============================================================
# 18. APPLICATION START
# ============================================================

init_db()

st.title(
    "⚡ SALIGA AI PRO 19.0"
)

st.caption(
    "INSTITUTIONAL MARKET INTELLIGENCE | "
    "DERIVATIVES | POSITION FLOW | "
    "HISTORICAL PATTERNS | NEWS AI"
)


# ============================================================
# 19. SIDEBAR
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

    st.session_state["refresh_interval"] = (
        refresh_minutes * 60
    )

    auto_refresh = st.checkbox(
        "Automatic Refresh",
        value=True
    )

    if st.button(
        "🔄 REFRESH NOW",
        use_container_width=True
    ):

        with st.spinner("Analyzing BTC market..."):

            run_full_cycle(lookback)


# ============================================================
# 20. INITIAL DATA
# ============================================================

if st.session_state["last_refresh"] is None:

    with st.spinner("Loading market intelligence..."):

        try:

            run_full_cycle(lookback)

        except Exception as e:

            st.error(str(e))


market = st.session_state["market"]

timeframes = st.session_state["timeframes"]

news = st.session_state["news"]

ai = st.session_state["ai_result"]


# ============================================================
# 21. MARKET DASHBOARD
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

    st.metric(
        "24H CHANGE",
        fmt(market.get("change_24h")) + "%"
    )

with c3:

    st.metric(
        "OPEN INTEREST",
        "$" + fmt(market.get("oi_usd"), 0)
    )

with c4:

    st.metric(
        "OI CHANGE",
        fmt(market.get("oi_change"), 3) + "%"
    )

with c5:

    funding = market.get("funding_rate")

    st.metric(
        "FUNDING RATE",
        fmt(funding * 100, 4) + "%"
        if funding is not None
        else "N/A"
    )


c6, c7, c8 = st.columns(3)

with c6:

    st.metric(
        "TRADE DELTA",
        fmt(market.get("delta_percent")) + "%"
    )

with c7:

    st.metric(
        "ORDER BOOK",
        fmt(market.get("orderbook_imbalance")) + "%"
    )

with c8:

    st.metric(
        "24H VOLUME",
        fmt(market.get("volume_24h"), 0)
    )


if market.get("errors"):

    with st.expander("DATA WARNINGS"):

        for error in market["errors"]:

            st.warning(error)


# ============================================================
# 22. POSITION FLOW
# ============================================================

st.markdown("---")

st.header("🏦 POSITION FLOW INTELLIGENCE")

position_flow = analyze_position_flow(market)

st.info(
    "CURRENT POSITION FLOW: "
    + position_flow
)

deriv = derivatives_analysis(market)

st.metric(
    "DERIVATIVES SCORE",
    deriv["score"]
)

for note in deriv["notes"]:

    st.write("• " + note)


# ============================================================
# 23. MAIN BTC RADAR
# ============================================================

st.markdown("---")

st.header("🎯 BTC INTELLIGENCE RADAR")

h1 = timeframes.get("H1", {})

direction = h1.get("direction", "WAIT")

score = h1.get("score", 0)

if direction == "LONG":

    st.success(
        "🟢 BTC LONG BIAS"
    )

elif direction == "SHORT":

    st.error(
        "🔴 BTC SHORT BIAS"
    )

else:

    st.warning(
        "🟡 BTC WAIT / NEUTRAL"
    )


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
        "H1 VOLATILITY (ATR)",
        fmt(h1.get("atr_percent")) + "%"
    )


with right:

    structure = h1.get("structure", {})

    st.write(
        "**MARKET STRUCTURE:** "
        + str(structure.get("status", "N/A"))
    )

    st.write(
        "**SUPPORT:** "
        + fmt(structure.get("support"))
    )

    st.write(
        "**RESISTANCE:** "
        + fmt(structure.get("resistance"))
    )


# ============================================================
# 24. HISTORICAL PATTERN STATISTICS
# ============================================================

st.markdown("---")

st.header("🧩 HISTORICAL PATTERN INTELLIGENCE")

pattern = h1.get("pattern", {})

p1, p2, p3, p4 = st.columns(4)

with p1:

    st.metric(
        "BULLISH OUTCOMES",
        fmt(pattern.get("up_rate")) + "%"
    )

with p2:

    st.metric(
        "BEARISH OUTCOMES",
        fmt(pattern.get("down_rate")) + "%"
    )

with p3:

    st.metric(
        "SIDEWAYS OUTCOMES",
        fmt(pattern.get("flat_rate")) + "%"
    )

with p4:

    st.metric(
        "AVERAGE MOVE",
        fmt(pattern.get("average_move"), 3) + "%"
    )

st.write(
    pattern.get("description", "N/A")
)

st.write(
    "Similar historical cases: "
    + str(pattern.get("matches", 0))
)

st.write(
    "Pattern similarity: "
    + fmt(
        (pattern.get("similarity") or 0) * 100,
        2
    )
    + "%"
)


# ============================================================
# 25. MULTI-TIMEFRAME ANALYSIS
# ============================================================

st.markdown("---")

st.header("🧭 MULTI-TIMEFRAME INTELLIGENCE")

rows = []

for name in TIMEFRAMES:

    data = timeframes.get(name, {})

    structure = data.get("structure", {})

    momentum = data.get("momentum", {})

    pattern_data = data.get("pattern", {})

    rows.append({

        "TIMEFRAME": name,

        "DIRECTION": data.get("direction", "WAIT"),

        "SCORE": data.get("score", 0),

        "CANDLES": data.get("candles_loaded", 0),

        "STRUCTURE": structure.get("status", "N/A"),

        "MOMENTUM 5": momentum.get("return_5"),

        "MOMENTUM 20": momentum.get("return_20"),

        "PATTERN SCORE": pattern_data.get("score", 0),

        "ATR %": data.get("atr_percent")

    })

st.dataframe(

    pd.DataFrame(rows),

    use_container_width=True,

    hide_index=True
)


# ============================================================
# 26. GROQ AI REPORT
# ============================================================

st.markdown("---")

st.header("🤖 GROQ AI INTELLIGENCE REPORT")

if st.session_state["ai_error"]:

    st.error(
        st.session_state["ai_error"]
    )

if ai:

    st.subheader(

        "MARKET BIAS: "
        + str(ai.get("bias", "N/A"))

        + " | CONFIDENCE: "

        + str(ai.get("confidence_score", "N/A"))

        + "%"
    )

    st.write("### EXECUTIVE SUMMARY")

    st.write(
        ai.get("executive_summary", "")
    )

    st.write("### DERIVATIVES ANALYSIS")

    st.info(
        ai.get("derivatives_analysis", "")
    )

    st.write("### POSITION FLOW")

    st.write(
        ai.get("position_flow_analysis", "")
    )

    st.write("### HISTORICAL PATTERNS")

    st.write(
        ai.get("historical_pattern_analysis", "")
    )

    st.write("### NEWS IMPACT")

    st.write(
        ai.get("news_impact", "")
    )

    st.write("### MULTI-TIMEFRAME ANALYSIS")

    st.write(
        ai.get("timeframe_analysis", "")
    )

    st.success(
        ai.get("actionable_insight", "")
    )

    with st.expander("RISKS"):

        for risk in ai.get("risks", []):

            st.write("• " + str(risk))


# ============================================================
# 27. AUTOMATIC NEWS
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

        if item.get("link"):

            st.markdown(
                "[OPEN NEWS](" + item["link"] + ")"
            )

else:

    st.info(
        "No news available."
    )


# ============================================================
# 28. DATA QUALITY
# ============================================================

st.markdown("---")

st.header("🛡️ DATA QUALITY MONITOR")

st.write(
    "**Primary exchange:** OKX"
)

st.write(
    "**Symbol:** " + SYMBOL
)

st.write(
    "**Last successful refresh:** "
    + str(st.session_state["last_refresh"])
)

st.write(
    "**Market data errors:** "
    + str(len(market.get("errors", [])))
)

st.write(
    "**Historical pattern engine:** ACTIVE"
)

st.write(
    "**Derivatives engine:** ACTIVE"
)

st.write(
    "**News engine:** "
    + ("ACTIVE" if news else "NO DATA")
)

st.write(
    "**Groq AI:** "
    + ("ACTIVE" if ai else "NOT AVAILABLE")
)


# ============================================================
# 29. AUTOMATIC REFRESH
# ============================================================

if auto_refresh:

    try:

        from streamlit_autorefresh import st_autorefresh

        st_autorefresh(

            interval=(
                st.session_state["refresh_interval"] * 1000
            ),

            key="saliga_refresh"
        )

        if (
            st.session_state["last_refresh"]
            and time.time() - st.session_state.get(
                "_last_refresh_epoch", 0
            ) >= st.session_state["refresh_interval"]
        ):

            st.session_state["_last_refresh_epoch"] = time.time()

            run_full_cycle(lookback)

            st.rerun()

    except ImportError:

        st.sidebar.warning(
            "Install streamlit-autorefresh: "
            "pip install streamlit-autorefresh"
        )


# ============================================================
# 30. FOOTER
# ============================================================

st.markdown("---")

st.caption(
    "⚡ SALIGA AI PRO 19.0 | "
    "INSTITUTIONAL MARKET INTELLIGENCE | "
    "DATA-DRIVEN ANALYSIS | "
    "NOT FINANCIAL ADVICE"
)
