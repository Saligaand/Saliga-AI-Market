# ================================================================
# AI PRO by Sali
# ULTIMATE BTC INTELLIGENCE ENGINE 31.2
# BTC ONLY | OKX | GROQ AI | NO EMA | NO SMA
#
# Original 31.0 features retained:
# Secrets, SQLite/migrations, OKX market data, MTF, market structure/regime,
# OI, funding, order book, delta, position flow, liquidity sweep,
# breakout/retest, divergences, historical patterns, RSS news,
# Groq AI, macro calendar/directional macro engine, conflict/macro gate,
# entry quality, risk management, paper trading, fees/slippage,
# signal/event journals, CSV exports, API health and dashboard.
#
# Enhanced in 31.1 S/R + 31.2 live macro calendar:
# Multi-timeframe Support/Resistance zones, clustered pivot levels,
# independent touch counting, rejection/volume/recency scoring,
# role reversal, false-breakout detection, breakout/retest states,
# S/R confluence score, AI payload and chart zone visualization.
#
# Analysis and paper trading only. No guarantee of profitability.
# ================================================================

import streamlit as st
import pandas as pd
import numpy as np
import requests
import sqlite3
import json
import time
import hashlib
import xml.etree.ElementTree as ET
import os
import math
import calendar

from datetime import datetime, timezone, timedelta, date
from pathlib import Path

APP_NAME = "AI PRO by Sali"
INSTRUMENT = "BTC-USDT-SWAP"
BASE_URL = "https://www.okx.com"
GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_DEFAULT_MODEL = "openai/gpt-oss-120b"
DATABASE_FILE = "saliga_ai_31.db"
TIMEFRAMES = {"1m": "1m", "5m": "5m", "15m": "15m", "1H": "1H", "4H": "4H"}
NEWS_FEEDS = {
    "CoinDesk": "https://www.coindesk.com/arc/outboundfeeds/rss/",
    "Cointelegraph": "https://cointelegraph.com/rss"
}
REQUEST_TIMEOUT = 15
DEFAULT_TELEGRAM_BOT_TOKEN = "9112981021:AAHXIfGBAzq_oWUgs5Z1vAIoxGMuY-u4-nw"

# ---------------- STREAMLIT CONFIG ----------------
st.set_page_config(page_title=APP_NAME, page_icon="₿", layout="wide")
st.markdown("""
<style>
.stApp {background-color:#080c13;color:#e8edf5;}
[data-testid="stMetric"] {background:#111a27;border:1px solid #26364c;padding:12px;border-radius:10px;}
h1,h2,h3 {color:#f0f4fa;}
</style>
""", unsafe_allow_html=True)

# ---------------- SECRETS ----------------
def load_secret(name, default=""):
    try:
        value = st.secrets.get(name, default)
        if value:
            return str(value)
    except Exception:
        pass
    return os.getenv(name, default)

GROQ_API_KEY = load_secret("GROQ_API_KEY", "")
GROQ_MODEL = load_secret("GROQ_MODEL", GROQ_DEFAULT_MODEL)
TELEGRAM_BOT_TOKEN = load_secret("TELEGRAM_BOT_TOKEN", DEFAULT_TELEGRAM_BOT_TOKEN)
TELEGRAM_CHAT_ID = load_secret("TELEGRAM_CHAT_ID", "")

# ---------------- DATABASE ----------------
def get_connection():
    connection = sqlite3.connect(DATABASE_FILE, check_same_thread=False)
    connection.row_factory = sqlite3.Row
    return connection

def _ensure_column(connection, table_name, column_name, column_def):
    cols = [row["name"] for row in connection.execute(f"PRAGMA table_info({table_name})").fetchall()]
    if column_name not in cols:
        connection.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_def}")

def initialize_database():
    connection = get_connection()
    connection.execute("""
        CREATE TABLE IF NOT EXISTS paper_trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT, opened TEXT, closed TEXT,
            direction TEXT, entry REAL, stop REAL, tp1 REAL, tp2 REAL, tp3 REAL,
            quantity REAL, score REAL, status TEXT, exit_price REAL,
            gross_pnl REAL DEFAULT 0, fees REAL DEFAULT 0, slippage REAL DEFAULT 0,
            funding REAL DEFAULT 0, net_pnl REAL DEFAULT 0, reason TEXT
        )""")
    connection.execute("""
        CREATE TABLE IF NOT EXISTS signal_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT, price REAL,
            direction TEXT, score REAL, stage TEXT, regime TEXT,
            macro_status TEXT, signal_label TEXT, ai_gate INTEGER
        )""")
    _ensure_column(connection, "signal_history", "macro_status", "TEXT DEFAULT 'UNKNOWN'")
    _ensure_column(connection, "signal_history", "signal_label", "TEXT DEFAULT 'WAIT'")
    connection.execute("""
        CREATE TABLE IF NOT EXISTS event_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT,
            category TEXT, message TEXT
        )""")
    connection.execute("""
        CREATE TABLE IF NOT EXISTS market_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp REAL, price REAL,
            oi REAL, delta REAL, funding REAL, book_imbalance REAL
        )""")
    connection.execute("""
        CREATE TABLE IF NOT EXISTS macro_calendar (
            id INTEGER PRIMARY KEY AUTOINCREMENT, event_date TEXT, time_utc TEXT,
            currency TEXT, event_name TEXT, impact TEXT,
            macro_lean TEXT DEFAULT 'NEUTRAL', notes TEXT
        )""")
    _ensure_column(connection, "macro_calendar", "macro_lean", "TEXT DEFAULT 'NEUTRAL'")
    connection.commit()
    connection.close()

initialize_database()

# ---------------- SESSION STATE ----------------
DEFAULT_STATE = {
    "last_ai_time": 0, "last_ai_result": None, "last_ai_error": None,
    "last_news_hash": None, "last_trigger_time": 0, "last_oi": None,
    "last_price": None, "last_refresh": None, "last_news": [],
    "last_data_errors": [], "seen_news_keys": [], "alerted_macro_keys": []
}
for key, value in DEFAULT_STATE.items():
    if key not in st.session_state:
        st.session_state[key] = list(value) if isinstance(value, list) else value

# ---------------- SIDEBAR ----------------
st.sidebar.title("SALIGA AI PRO 31.2")
st.sidebar.caption("ULTIMATE BTC INTELLIGENCE ENGINE")
st.sidebar.success("Groq API: configured" if GROQ_API_KEY else "Groq API: missing")
st.sidebar.caption(f"Model: {GROQ_MODEL}")
ai_enabled = st.sidebar.toggle("Groq AI enabled", value=True)
macro_gate_enabled = st.sidebar.toggle("Macro event gate (<=1h HIGH)", value=True)
macro_directional_weight = st.sidebar.slider("Macro directional influence", 0.0, 15.0, 8.0, 0.5)
auto_refresh = st.sidebar.toggle("Automatic refresh", value=True)
refresh_seconds = st.sidebar.slider("Refresh interval", 15, 120, 30)
ai_interval = st.sidebar.slider("Normal AI interval (minutes)", 5, 60, 15)
ai_cooldown = st.sidebar.slider("Event AI cooldown (minutes)", 1, 15, 3)
paper_enabled = st.sidebar.toggle("Paper Trading", value=True)
starting_balance = st.sidebar.number_input("Starting balance USDT", min_value=100.0, max_value=1000000.0, value=10000.0)
risk_percent = st.sidebar.slider("Risk per trade (%)", 0.1, 3.0, 0.5, 0.1)
fee_percent = st.sidebar.number_input("Fee per side (%)", 0.0, 0.5, 0.05, 0.01)
slippage_percent = st.sidebar.number_input("Slippage per side (%)", 0.0, 0.5, 0.02, 0.01)

# ---------------- GENERAL HELPERS ----------------
def now_utc():
    return datetime.now(timezone.utc)

def now_string():
    return now_utc().strftime("%Y-%m-%d %H:%M:%S UTC")

def safe_float(value, default=0.0):
    try:
        result = float(value)
        return result if np.isfinite(result) else default
    except Exception:
        return default

def clamp(value, low, high):
    return max(low, min(high, value))

def percentage_change(current, previous):
    if previous is None or previous == 0:
        return 0.0
    return ((current / previous) - 1) * 100

def add_event(category, message):
    connection = get_connection()
    connection.execute(
        "INSERT INTO event_log (timestamp,category,message) VALUES (?,?,?)",
        (now_string(), str(category), str(message))
    )
    connection.commit()
    connection.close()

def database_read(query, params=()):
    connection = get_connection()
    result = pd.read_sql_query(query, connection, params=params)
    connection.close()
    return result

def send_telegram_alert(message):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": message},
            timeout=10
        )
    except Exception:
        pass

def process_news_and_macro_alerts(news_analysis, macro_calendar):
    seen_news = set(st.session_state.get("seen_news_keys", []))
    first_news_run = len(seen_news) == 0
    for article in news_analysis.get("articles", []):
        key = hashlib.sha256((article.get("source", "") + "|" + article.get("title", "")).lower().encode()).hexdigest()
        if key not in seen_news:
            seen_news.add(key)
            stance = article.get("stance", "Neutral")
            if not first_news_run and stance in ("Bullish", "Bearish"):
                msg = f"BTC HÍR [{stance}]: {article.get('title')} ({article.get('source')})"
                send_telegram_alert(msg)
                add_event("ALERT NEWS", msg)
    st.session_state.seen_news_keys = list(seen_news)[-200:]

    alerted_macro = set(st.session_state.get("alerted_macro_keys", []))
    first_macro_run = len(alerted_macro) == 0
    for ev in macro_calendar.get("events", []):
        ev_key = f"{ev.get('date')}|{ev.get('time_utc')}|{ev.get('currency')}|{ev.get('event')}"
        hours_until = safe_float(ev.get("hours_until"), 999)
        impact = str(ev.get("impact", "")).upper()
        actual = str(ev.get("actual") or "").strip()
        stance = ev.get("stance", "Neutral")
        has_release = hours_until <= 0 and actual not in ("", "-", "N/A", "NA", "--", "TBD")
        upcoming_high = impact == "HIGH" and 0 <= hours_until <= 0.5
        if has_release or upcoming_high:
            state_key = f"{ev_key}|{'REL' if has_release else 'UP'}"
            if state_key not in alerted_macro:
                alerted_macro.add(state_key)
                if not first_macro_run:
                    if has_release:
                        msg = (
                            f"MAKRO ADAT [{stance}]: {ev.get('currency')} - {ev.get('event')} "
                            f"(Actual: {actual} | Forecast: {ev.get('forecast') or '-'} | Prev: {ev.get('previous') or '-'})"
                        )
                    else:
                        msg = f"KÖZELGŐ HIGH MAKRO ({hours_until:.2f}h): {ev.get('currency')} - {ev.get('event')} [{stance}]"
                    send_telegram_alert(msg)
                    add_event("ALERT MACRO", msg)
    st.session_state.alerted_macro_keys = list(alerted_macro)[-200:]

# ---------------- OKX API ----------------
def okx_request(endpoint, params=None):
    response = requests.get(
        BASE_URL + endpoint, params=params or {}, timeout=REQUEST_TIMEOUT,
        headers={"User-Agent": "SALIGA-AI-PRO-31.1"}
    )
    response.raise_for_status()
    data = response.json()
    if str(data.get("code", "0")) != "0":
        raise RuntimeError(data.get("msg", "OKX API error"))
    return data.get("data", [])

# ---------------- MARKET DATA ----------------
def fetch_ticker():
    data = okx_request("/api/v5/market/ticker", {"instId": INSTRUMENT})
    if not data:
        raise RuntimeError("Ticker data unavailable")
    item = data[0]
    return {
        "price": safe_float(item.get("last")), "bid": safe_float(item.get("bidPx")),
        "ask": safe_float(item.get("askPx")), "high24h": safe_float(item.get("high24h")),
        "low24h": safe_float(item.get("low24h")), "volume24h": safe_float(item.get("vol24h")),
        "timestamp": item.get("ts")
    }

def fetch_candles(bar, limit=300):
    data = okx_request("/api/v5/market/candles", {"instId": INSTRUMENT, "bar": bar, "limit": str(limit)})
    rows = []
    for item in data:
        rows.append({
            "timestamp": int(item[0]), "open": safe_float(item[1]),
            "high": safe_float(item[2]), "low": safe_float(item[3]),
            "close": safe_float(item[4]), "volume": safe_float(item[5]),
            "volume_currency": safe_float(item[6]), "volume_quote": safe_float(item[7]),
            "confirmed": item[8] == "1"
        })
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    return df.sort_values("timestamp").drop_duplicates("timestamp").reset_index(drop=True)

def fetch_open_interest():
    data = okx_request("/api/v5/public/open-interest", {"instType": "SWAP", "instId": INSTRUMENT})
    if not data:
        return None
    item = data[0]
    return {"oi": safe_float(item.get("oi")), "oi_currency": safe_float(item.get("oiCcy")), "timestamp": item.get("ts")}

def fetch_funding():
    data = okx_request("/api/v5/public/funding-rate", {"instId": INSTRUMENT})
    if not data:
        return None
    item = data[0]
    return {"funding": safe_float(item.get("fundingRate")), "next_funding": item.get("nextFundingTime")}

def fetch_order_book():
    data = okx_request("/api/v5/market/books", {"instId": INSTRUMENT, "sz": "50"})
    if not data:
        return None
    item = data[0]
    bids = [(safe_float(x[0]), safe_float(x[1])) for x in item.get("bids", [])]
    asks = [(safe_float(x[0]), safe_float(x[1])) for x in item.get("asks", [])]
    bid_volume, ask_volume = sum(x[1] for x in bids), sum(x[1] for x in asks)
    total = bid_volume + ask_volume
    imbalance = (bid_volume - ask_volume) / total if total > 0 else 0
    return {
        "bids": bids, "asks": asks, "bid_volume": bid_volume, "ask_volume": ask_volume,
        "imbalance": imbalance, "spread": asks[0][0] - bids[0][0] if bids and asks else 0
    }

def fetch_recent_trades():
    data = okx_request("/api/v5/market/trades", {"instId": INSTRUMENT, "limit": "500"})
    return [{
        "price": safe_float(x.get("px")), "size": safe_float(x.get("sz")),
        "side": x.get("side"), "timestamp": safe_float(x.get("ts"))
    } for x in data]

# ---------------- TECHNICAL ENGINE (NO EMA/SMA) ----------------
def calculate_rsi(series, period=14):
    delta = series.diff()
    gain, loss = delta.clip(lower=0), -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return (100 - 100 / (1 + rs)).fillna(50)

def calculate_atr(df, period=14):
    previous_close = df["close"].shift(1)
    ranges = pd.concat([
        df["high"] - df["low"], (df["high"] - previous_close).abs(),
        (df["low"] - previous_close).abs()
    ], axis=1)
    return ranges.max(axis=1).ewm(alpha=1 / period, adjust=False, min_periods=period).mean()

def add_indicators(df):
    if df.empty:
        return df
    df = df.copy()
    df["rsi"] = calculate_rsi(df["close"])
    df["atr"] = calculate_atr(df)
    df["roc_5"] = df["close"].pct_change(5) * 100
    df["roc_10"] = df["close"].pct_change(10) * 100
    df["volume_median"] = df["volume"].rolling(20).median()
    df["volume_ratio"] = df["volume"] / df["volume_median"].replace(0, np.nan)
    df["atr_pct"] = df["atr"] / df["close"].replace(0, np.nan) * 100
    return df.replace([np.inf, -np.inf], np.nan)

# ---------------- MARKET STRUCTURE ----------------
def market_structure(df):
    if df.empty or len(df) < 30:
        return {"direction": "UNKNOWN", "strength": 0, "support": None, "resistance": None}
    recent = df.tail(30)
    highs, lows = recent["high"].to_numpy(), recent["low"].to_numpy()
    swing_highs, swing_lows = [], []
    for i in range(2, len(recent) - 2):
        if highs[i] > highs[i-1] and highs[i] > highs[i-2] and highs[i] > highs[i+1] and highs[i] > highs[i+2]:
            swing_highs.append(highs[i])
        if lows[i] < lows[i-1] and lows[i] < lows[i-2] and lows[i] < lows[i+1] and lows[i] < lows[i+2]:
            swing_lows.append(lows[i])
    higher_high = len(swing_highs) >= 2 and swing_highs[-1] > swing_highs[-2]
    lower_high = len(swing_highs) >= 2 and swing_highs[-1] < swing_highs[-2]
    higher_low = len(swing_lows) >= 2 and swing_lows[-1] > swing_lows[-2]
    lower_low = len(swing_lows) >= 2 and swing_lows[-1] < swing_lows[-2]
    bullish, bearish = int(higher_high) + int(higher_low), int(lower_high) + int(lower_low)
    direction = "BULLISH" if bullish > bearish else "BEARISH" if bearish > bullish else "NEUTRAL"
    current_price = safe_float(recent["close"].iloc[-1])
    support = max((x for x in swing_lows if x < current_price), default=None)
    resistance = min((x for x in swing_highs if x > current_price), default=None)
    return {"direction": direction, "strength": abs(bullish - bearish) * 50, "support": support, "resistance": resistance}

# ---------------- ENHANCED SUPPORT / RESISTANCE ENGINE ----------------
def _pivot_points(df, left=3, right=3):
    """Confirmed swing pivots. The right-side bars prevent using unconfirmed pivots."""
    points = []
    if df.empty or len(df) < left + right + 5:
        return points
    highs, lows = df["high"].to_numpy(), df["low"].to_numpy()
    for i in range(left, len(df) - right):
        hwin = highs[i-left:i+right+1]
        lwin = lows[i-left:i+right+1]
        if highs[i] >= np.max(hwin) and np.sum(hwin == highs[i]) == 1:
            points.append({"price": float(highs[i]), "kind": "RESISTANCE", "index": i})
        if lows[i] <= np.min(lwin) and np.sum(lwin == lows[i]) == 1:
            points.append({"price": float(lows[i]), "kind": "SUPPORT", "index": i})
    return points

def _cluster_sr_points(points, tolerance):
    """Cluster nearby pivot prices; each cluster becomes a price zone."""
    clusters = []
    for point in sorted(points, key=lambda x: x["price"]):
        chosen = None
        for cluster in clusters:
            center = float(np.mean([p["price"] for p in cluster]))
            if abs(point["price"] - center) <= tolerance:
                chosen = cluster
                break
        if chosen is None:
            clusters.append([point])
        else:
            chosen.append(point)
    return clusters

def _score_sr_cluster(cluster, df, timeframe, current_price, atr):
    prices = np.array([p["price"] for p in cluster], dtype=float)
    indices = [int(p["index"]) for p in cluster]
    center = float(np.median(prices))
    spread = float(np.max(prices) - np.min(prices)) if len(prices) > 1 else 0.0
    zone_half = max(atr * 0.12, center * 0.00025, spread * 0.55)
    lower, upper = center - zone_half, center + zone_half
    # Count independent reactions, not adjacent candles repeatedly touching the same zone.
    touch_indices = []
    rejection_strengths = []
    volumes = []
    # Evaluate pivots in chronological order so nearby price-sorted points
    # cannot accidentally suppress or inflate independent reaction counts.
    for point in sorted(cluster, key=lambda item: item["index"]):
        idx = int(point["index"])
        if not touch_indices or idx - touch_indices[-1] >= 3:
            touch_indices.append(idx)
            row = df.iloc[idx]
            candle_range = max(safe_float(row["high"] - row["low"]), 1e-9)
            if point["kind"] == "SUPPORT":
                wick = max(0.0, min(row["open"], row["close"]) - row["low"])
                away = max(0.0, row["close"] - row["low"])
            else:
                wick = max(0.0, row["high"] - max(row["open"], row["close"]))
                away = max(0.0, row["high"] - row["close"])
            rejection_strengths.append(clamp((wick + away * 0.35) / candle_range, 0, 1))
            volumes.append(safe_float(row.get("volume_ratio", 1.0), 1.0))
    touches = len(touch_indices)
    last_idx = max(indices) if indices else 0
    recency = math.exp(-max(0, len(df) - 1 - last_idx) / max(20, len(df) * 0.35))
    rejection = float(np.mean(rejection_strengths)) if rejection_strengths else 0.0
    volume_confirmation = clamp((float(np.nanmean(volumes)) - 0.7) / 1.8, 0, 1) if volumes else 0.0
    touch_score = min(touches / 5.0, 1.0)
    score = 100 * (
        0.38 * touch_score +
        0.24 * rejection +
        0.16 * volume_confirmation +
        0.22 * recency
    )
    kinds = [p["kind"] for p in cluster]
    kind = "SUPPORT" if kinds.count("SUPPORT") >= kinds.count("RESISTANCE") else "RESISTANCE"
    return {
        "type": kind, "center": center, "lower": lower, "upper": upper,
        "touches": touches, "rejection": round(rejection, 3),
        "volume_confirmation": round(volume_confirmation, 3),
        "recency": round(recency, 3), "score": round(score, 1),
        "timeframes": [timeframe], "last_pivot_index": last_idx
    }

def _build_sr_zones_for_timeframe(df, timeframe):
    if df.empty or len(df) < 35:
        return []
    # Use timeframe-adaptive pivots and ATR; pivot history is limited to avoid stale zones.
    work = df.tail(260).reset_index(drop=True)
    atr = safe_float(work["atr"].iloc[-1], 0.0) if "atr" in work else 0.0
    price = safe_float(work["close"].iloc[-1])
    if price <= 0:
        return []
    if atr <= 0:
        atr = price * 0.001
    tolerance = max(atr * 0.30, price * 0.00045)
    if timeframe in ("1H", "4H"):
        left, right = 3, 3
    elif timeframe == "15m":
        left, right = 3, 2
    else:
        left, right = 2, 2
    points = _pivot_points(work, left, right)
    zones = []
    for cluster in _cluster_sr_points(points, tolerance):
        if len(cluster) == 1 and len(work) - cluster[0]["index"] > 100:
            continue
        zone = _score_sr_cluster(cluster, work, timeframe, price, atr)
        zones.append(zone)
    return zones

def analyze_support_resistance(timeframe_data, current_price):
    """Multi-timeframe S/R confluence, zone strength, breakout and role-reversal logic."""
    all_zones = []
    for tf in ("1m", "5m", "15m", "1H", "4H"):
        df = timeframe_data.get(tf, pd.DataFrame())
        for zone in _build_sr_zones_for_timeframe(df, tf):
            all_zones.append(zone)

    if current_price <= 0:
        return {
            "status": "NO PRICE", "nearest_support": None, "nearest_resistance": None,
            "zones": [], "directional_score": 0, "summary": "No valid price."
        }

    # Merge zones across timeframes when their price bands overlap or centers are close.
    merged = []
    for zone in sorted(all_zones, key=lambda z: z["center"]):
        match = None
        for existing in merged:
            tol = max((existing["upper"] - existing["lower"]) * 0.5,
                      (zone["upper"] - zone["lower"]) * 0.5,
                      current_price * 0.00035)
            if abs(existing["center"] - zone["center"]) <= tol:
                match = existing
                break
        if match is None:
            merged.append(dict(zone))
        else:
            old_weight = max(match["score"], 1.0)
            new_weight = max(zone["score"], 1.0)
            match["center"] = (match["center"] * old_weight + zone["center"] * new_weight) / (old_weight + new_weight)
            match["lower"] = min(match["lower"], zone["lower"])
            match["upper"] = max(match["upper"], zone["upper"])
            match["score"] = min(100.0, match["score"] + zone["score"] * 0.22)
            match["touches"] += zone["touches"]
            match["timeframes"] = sorted(set(match["timeframes"] + zone["timeframes"]),
                                         key=lambda x: ("1m", "5m", "15m", "1H", "4H").index(x))
            match["rejection"] = round(max(match["rejection"], zone["rejection"]), 3)
            match["volume_confirmation"] = round(max(match["volume_confirmation"], zone["volume_confirmation"]), 3)

    # Classify zones relative to price and detect breaks / failed breaks.
    for zone in merged:
        if zone["lower"] <= current_price <= zone["upper"]:
            zone["position"] = "PRICE INSIDE ZONE"
        elif zone["upper"] < current_price:
            zone["position"] = "BELOW PRICE"
        else:
            zone["position"] = "ABOVE PRICE"
        zone["distance"] = abs(current_price - zone["center"])
        zone["distance_pct"] = zone["distance"] / current_price * 100
        zone["mtf_confluence"] = len(zone["timeframes"])
        # Reversal of function: old resistance below price may now act as support;
        # old support above price may now act as resistance after a move through it.
        if zone["position"] == "BELOW PRICE" and zone["type"] == "RESISTANCE":
            zone["role"] = "POSSIBLE RESISTANCE-TO-SUPPORT"
        elif zone["position"] == "ABOVE PRICE" and zone["type"] == "SUPPORT":
            zone["role"] = "POSSIBLE SUPPORT-TO-RESISTANCE"
        else:
            zone["role"] = "ORIGINAL ROLE"
        zone["score"] = round(min(100.0, zone["score"] + max(0, zone["mtf_confluence"] - 1) * 7), 1)

    supports = [z for z in merged if z["upper"] < current_price and z["type"] == "SUPPORT"]
    resistances = [z for z in merged if z["lower"] > current_price and z["type"] == "RESISTANCE"]
    # A broken prior resistance/support may be used as a role-reversal candidate.
    supports += [z for z in merged if z["upper"] < current_price and z["role"] == "POSSIBLE RESISTANCE-TO-SUPPORT"]
    resistances += [z for z in merged if z["lower"] > current_price and z["role"] == "POSSIBLE SUPPORT-TO-RESISTANCE"]
    nearest_support = max(supports, key=lambda z: z["center"], default=None)
    nearest_resistance = min(resistances, key=lambda z: z["center"], default=None)
    strongest_support = max(supports, key=lambda z: z["score"], default=None)
    strongest_resistance = max(resistances, key=lambda z: z["score"], default=None)

    # Latest candle across the principal execution timeframe, otherwise first available.
    execution_df = timeframe_data.get("5m", pd.DataFrame())
    if execution_df.empty:
        execution_df = timeframe_data.get("1m", pd.DataFrame())
    if execution_df.empty:
        execution_df = timeframe_data.get("15m", pd.DataFrame())
    breakout_state = "NO S/R BREAKOUT"
    false_breakout = False
    if not execution_df.empty and len(execution_df) >= 2:
        last = execution_df.iloc[-1]
        prev = execution_df.iloc[-2]
        atr = safe_float(last.get("atr"), current_price * 0.001)
        buffer = max(atr * 0.12, current_price * 0.00025)
        if nearest_resistance and safe_float(last["close"]) > nearest_resistance["upper"] + buffer:
            breakout_state = "BULLISH S/R BREAKOUT"
            if safe_float(prev["close"]) <= nearest_resistance["upper"]:
                breakout_state += " - FRESH"
        elif nearest_support and safe_float(last["close"]) < nearest_support["lower"] - buffer:
            breakout_state = "BEARISH S/R BREAKDOWN"
            if safe_float(prev["close"]) >= nearest_support["lower"]:
                breakout_state += " - FRESH"
        elif nearest_resistance and safe_float(last["high"]) > nearest_resistance["upper"] + buffer and safe_float(last["close"]) <= nearest_resistance["upper"]:
            breakout_state = "BEARISH FALSE BREAKOUT / REJECTION"
            false_breakout = True
        elif nearest_support and safe_float(last["low"]) < nearest_support["lower"] - buffer and safe_float(last["close"]) >= nearest_support["lower"]:
            breakout_state = "BULLISH FALSE BREAKDOWN / RECLAIM"
            false_breakout = True
        elif nearest_resistance and safe_float(last["close"]) > nearest_resistance["center"] and safe_float(last["low"]) <= nearest_resistance["upper"] and safe_float(last["close"]) >= nearest_resistance["upper"]:
            breakout_state = "BULLISH RETEST OF BROKEN RESISTANCE"
        elif nearest_support and safe_float(last["close"]) < nearest_support["center"] and safe_float(last["high"]) >= nearest_support["lower"] and safe_float(last["close"]) <= nearest_support["lower"]:
            breakout_state = "BEARISH RETEST OF BROKEN SUPPORT"

    # Bounded directional contribution; proximity to a strong zone is context, not a signal alone.
    directional_score = 0.0
    if nearest_support:
        proximity = clamp(1 - nearest_support["distance_pct"] / 1.5, 0, 1)
        directional_score += nearest_support["score"] / 100 * proximity * 7
    if nearest_resistance:
        proximity = clamp(1 - nearest_resistance["distance_pct"] / 1.5, 0, 1)
        directional_score -= nearest_resistance["score"] / 100 * proximity * 7
    if "BULLISH FALSE" in breakout_state or "BULLISH RETEST" in breakout_state:
        directional_score += 4
    if "BEARISH FALSE" in breakout_state or "BEARISH RETEST" in breakout_state:
        directional_score -= 4
    if breakout_state.startswith("BULLISH S/R BREAKOUT"):
        directional_score += 3
    elif breakout_state.startswith("BEARISH S/R BREAKDOWN"):
        directional_score -= 3

    zones = sorted(merged, key=lambda z: z["score"], reverse=True)[:16]
    def compact(zone):
        if not zone:
            return None
        return {
            "center": round(zone["center"], 2), "lower": round(zone["lower"], 2),
            "upper": round(zone["upper"], 2), "score": zone["score"],
            "touches": zone["touches"], "timeframes": zone["timeframes"],
            "distance_pct": round(zone["distance_pct"], 3), "role": zone["role"],
            "rejection": zone["rejection"], "volume_confirmation": zone["volume_confirmation"]
        }
    status = breakout_state
    summary_parts = [breakout_state]
    if nearest_support:
        summary_parts.append(f"Nearest support {nearest_support['lower']:.2f}-{nearest_support['upper']:.2f}, strength {nearest_support['score']:.0f}/100")
    if nearest_resistance:
        summary_parts.append(f"Nearest resistance {nearest_resistance['lower']:.2f}-{nearest_resistance['upper']:.2f}, strength {nearest_resistance['score']:.0f}/100")
    return {
        "status": status,
        "breakout_state": breakout_state,
        "false_breakout": false_breakout,
        "nearest_support": compact(nearest_support),
        "nearest_resistance": compact(nearest_resistance),
        "strongest_support": compact(strongest_support),
        "strongest_resistance": compact(strongest_resistance),
        "directional_score": round(clamp(directional_score, -12, 12), 2),
        "zones": [{
            "type": z["type"], "center": round(z["center"], 2),
            "lower": round(z["lower"], 2), "upper": round(z["upper"], 2),
            "score": z["score"], "touches": z["touches"],
            "timeframes": z["timeframes"], "position": z["position"],
            "role": z["role"], "distance_pct": round(z["distance_pct"], 3)
        } for z in zones],
        "summary": " | ".join(summary_parts)
    }

# ---------------- MARKET REGIME ----------------
def market_regime(df):
    if df.empty or len(df) < 50:
        return {"regime": "UNKNOWN", "volatility": "UNKNOWN", "efficiency": 0}
    recent = df.tail(30)
    close = recent["close"]
    total_move = abs(close.iloc[-1] - close.iloc[0])
    path_move = close.diff().abs().sum()
    efficiency = total_move / path_move if path_move > 0 else 0
    atr_pct = safe_float(recent["atr_pct"].iloc[-1])
    structure = market_structure(df)
    volatility = "EXTREME" if atr_pct > 1.0 else "HIGH" if atr_pct > 0.45 else "NORMAL" if atr_pct > 0.15 else "LOW"
    if efficiency > 0.55:
        regime = "TRENDING BULLISH" if structure["direction"] == "BULLISH" else "TRENDING BEARISH" if structure["direction"] == "BEARISH" else "TRENDING"
    elif efficiency < 0.20:
        regime = "RANGE"
    else:
        regime = "TRANSITION"
    return {"regime": regime, "volatility": volatility, "efficiency": efficiency}

# ---------------- DELTA ----------------
def calculate_delta(trades):
    if not trades:
        return {"buy_volume": 0, "sell_volume": 0, "delta": 0, "delta_pct": 0, "classification": "NO DATA"}
    buy = sum(x["size"] for x in trades if x["side"] == "buy")
    sell = sum(x["size"] for x in trades if x["side"] == "sell")
    total, delta = buy + sell, buy - sell
    delta_pct = delta / total * 100 if total > 0 else 0
    classification = "STRONG BUYING" if delta_pct > 15 else "BUYING" if delta_pct > 4 else "STRONG SELLING" if delta_pct < -15 else "SELLING" if delta_pct < -4 else "BALANCED"
    return {"buy_volume": buy, "sell_volume": sell, "delta": delta, "delta_pct": delta_pct, "classification": classification}

# ---------------- ORDER BOOK ----------------
def analyze_order_book(book):
    if not book:
        return {"imbalance": 0, "classification": "NO DATA", "bid_wall": None, "ask_wall": None}
    imbalance = book["imbalance"]
    classification = "BUY SIDE DOMINANT" if imbalance > 0.20 else "SELL SIDE DOMINANT" if imbalance < -0.20 else "BALANCED"
    return {
        "imbalance": imbalance, "classification": classification,
        "bid_wall": max(book["bids"], key=lambda x: x[1], default=None),
        "ask_wall": max(book["asks"], key=lambda x: x[1], default=None)
    }

# ---------------- OPEN INTEREST ----------------
def analyze_open_interest(oi):
    if oi is None:
        return {"oi": None, "change_pct": None, "classification": "NO DATA"}
    connection = get_connection()
    previous = connection.execute("SELECT oi,price FROM market_snapshots WHERE oi IS NOT NULL ORDER BY id DESC LIMIT 1").fetchone()
    current_price = st.session_state.get("current_price")
    if previous:
        previous_oi, previous_price = safe_float(previous["oi"]), safe_float(previous["price"])
        oi_change = percentage_change(oi["oi"], previous_oi)
        price_change = percentage_change(current_price, previous_price) if current_price and previous_price else 0
        if oi_change > 0.3 and price_change > 0.05: classification = "LONG BUILDING"
        elif oi_change > 0.3 and price_change < -0.05: classification = "SHORT BUILDING"
        elif oi_change < -0.3 and price_change > 0.05: classification = "SHORT CLOSING"
        elif oi_change < -0.3 and price_change < -0.05: classification = "LONG CLOSING"
        else: classification = "NEUTRAL / MIXED"
    else:
        oi_change, classification = None, "WAITING FOR COMPARISON DATA"
    connection.close()
    return {"oi": oi["oi"], "change_pct": oi_change, "classification": classification}

# ---------------- FUNDING ----------------
def analyze_funding(funding):
    if funding is None:
        return {"rate": None, "classification": "NO DATA"}
    rate = funding["funding"]
    classification = "EXTREME LONG CROWDING" if rate > 0.001 else "LONGS DOMINANT" if rate > 0.0005 else "EXTREME SHORT CROWDING" if rate < -0.001 else "SHORTS DOMINANT" if rate < -0.0005 else "BALANCED"
    return {"rate": rate, "classification": classification}

# ---------------- LIQUIDITY ----------------
def analyze_liquidity(df):
    if df.empty or len(df) < 30:
        return {"sweep": "UNKNOWN", "breakout": "UNKNOWN"}
    latest, previous = df.iloc[-1], df.iloc[-21:-1]
    previous_high, previous_low = previous["high"].max(), previous["low"].min()
    bullish_sweep = latest["low"] < previous_low and latest["close"] > previous_low
    bearish_sweep = latest["high"] > previous_high and latest["close"] < previous_high
    sweep = "BULLISH LIQUIDITY SWEEP" if bullish_sweep else "BEARISH LIQUIDITY SWEEP" if bearish_sweep else "NO CONFIRMED SWEEP"
    breakout = "BULLISH BREAKOUT" if latest["close"] > previous_high else "BEARISH BREAKDOWN" if latest["close"] < previous_low else "NO BREAKOUT"
    return {"sweep": sweep, "breakout": breakout, "previous_high": previous_high, "previous_low": previous_low}

# ---------------- BREAKOUT / RETEST ----------------
def analyze_retest(df):
    if df.empty or len(df) < 25:
        return "INSUFFICIENT DATA"
    recent = df.tail(25)
    previous, prior, latest = recent.iloc[:-2], recent.iloc[-2], recent.iloc[-1]
    high, low = previous["high"].max(), previous["low"].min()
    atr = safe_float(latest["atr"])
    if atr <= 0:
        return "NO VALID ATR"
    if prior["close"] > high and latest["low"] <= high + atr * 0.25 and latest["close"] > high:
        return "BULLISH BREAKOUT RETEST"
    if prior["close"] < low and latest["high"] >= low - atr * 0.25 and latest["close"] < low:
        return "BEARISH BREAKOUT RETEST"
    return "NO CONFIRMED RETEST"

# ---------------- POSITION FLOW ----------------
def analyze_position_flow(oi, delta, structure):
    events = []
    oi_state = oi["classification"]
    if oi_state in ["LONG BUILDING", "SHORT BUILDING", "LONG CLOSING", "SHORT CLOSING"]:
        events.append(oi_state)
    if structure["direction"] == "BULLISH" and delta["classification"] in ["SELLING", "STRONG SELLING"]:
        events.append("BUYING WEAKNESS / SELLING PRESSURE")
    if structure["direction"] == "BEARISH" and delta["classification"] in ["BUYING", "STRONG BUYING"]:
        events.append("SELLING WEAKNESS / BUYING PRESSURE")
    if oi_state == "LONG BUILDING" and delta["delta_pct"] < -10:
        events.append("LONG BUILDING WITH NEGATIVE DELTA")
    if oi_state == "SHORT BUILDING" and delta["delta_pct"] > 10:
        events.append("SHORT BUILDING WITH POSITIVE DELTA")
    return events or ["NO CLEAR POSITION FLOW EVENT"]

# ---------------- DIVERGENCE ----------------
def analyze_divergences(df, delta):
    divergences = []
    if df.empty or len(df) < 20:
        return divergences
    recent = df.tail(20)
    price_change = percentage_change(recent["close"].iloc[-1], recent["close"].iloc[0])
    rsi_change = safe_float(recent["rsi"].iloc[-1]) - safe_float(recent["rsi"].iloc[0])
    if price_change > 0.4 and rsi_change < -5: divergences.append("POSSIBLE BEARISH RSI DIVERGENCE")
    if price_change < -0.4 and rsi_change > 5: divergences.append("POSSIBLE BULLISH RSI DIVERGENCE")
    if price_change > 0.4 and delta["delta_pct"] < -10: divergences.append("PRICE UP / DELTA DOWN")
    if price_change < -0.4 and delta["delta_pct"] > 10: divergences.append("PRICE DOWN / DELTA UP")
    return divergences

# ---------------- MULTI TIMEFRAME ----------------
def analyze_timeframes(data):
    result = {}
    for timeframe, df in data.items():
        if df.empty:
            result[timeframe] = {"direction": "NO DATA", "structure": "UNKNOWN", "rsi": None, "roc": None}
        else:
            latest = df.iloc[-1]
            structure = market_structure(df)
            result[timeframe] = {
                "direction": structure["direction"], "structure": structure["direction"],
                "rsi": safe_float(latest["rsi"]), "roc": safe_float(latest["roc_5"])
            }
    return result

# ---------------- HISTORICAL PATTERNS ----------------
def analyze_historical_patterns(df):
    empty_result = {"matches": 0, "bullish_rate": None, "bearish_rate": None, "average_return": None}
    if df.empty or len(df) < 100:
        return empty_result
    data = df.reset_index(drop=True)
    current = data.iloc[-1]
    current_features = np.array([
        safe_float(current["rsi"]), safe_float(current["roc_5"]),
        safe_float(current["atr_pct"]), safe_float(current["volume_ratio"], 1)
    ])
    scale = np.array([20, 1, 0.3, 1])
    matches = []
    start = max(30, len(data) - 250)
    for i in range(start, len(data) - 6):
        row = data.iloc[i]
        features = np.array([
            safe_float(row["rsi"]), safe_float(row["roc_5"]),
            safe_float(row["atr_pct"]), safe_float(row["volume_ratio"], 1)
        ])
        distance = np.sqrt(np.mean(((features - current_features) / scale) ** 2))
        if distance < 1.0:
            future_return = percentage_change(data.iloc[i + 5]["close"], row["close"])
            matches.append({"distance": distance, "return": future_return})
    matches = sorted(matches, key=lambda x: x["distance"])[:20]
    if not matches:
        return empty_result
    returns = [x["return"] for x in matches]
    return {
        "matches": len(returns),
        "bullish_rate": sum(x > 0 for x in returns) / len(returns) * 100,
        "bearish_rate": sum(x < 0 for x in returns) / len(returns) * 100,
        "average_return": float(np.mean(returns))
    }

# ---------------- LIVE MACRO CALENDAR / DIRECTIONAL ENGINE ----------------
FOREX_FACTORY_CALENDAR_URLS = [
    "https://nfs.faireconomy.media/ff_calendar_thisweek.json",
    "https://nfs.faireconomy.media/ff_calendar_nextweek.json",
]
MACRO_RELEVANT_CURRENCIES = {"USD", "EUR", "GBP", "JPY", "CNY", "CAD", "AUD", "CHF", "NZD"}

@st.cache_data(ttl=600, show_spinner=False)
def _fetch_forexfactory_calendar_cached():
    events, errors, successful_sources = [], [], []
    headers = {"User-Agent": "Mozilla/5.0 (compatible; SALIGA-AI-PRO/31.2; +https://www.forexfactory.com/)"}
    for url in FOREX_FACTORY_CALENDAR_URLS:
        try:
            response = requests.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, list):
                raise ValueError("Calendar response is not a JSON list")
            successful_sources.append(url)
            for raw in payload:
                if not isinstance(raw, dict):
                    continue
                title = str(raw.get("title") or raw.get("event") or "").strip()
                currency = str(raw.get("country") or raw.get("currency") or "").strip().upper()
                raw_date = str(raw.get("date") or raw.get("datetime") or "").strip()
                if not title or not raw_date:
                    continue
                try:
                    parsed_dt = datetime.fromisoformat(raw_date.replace("Z", "+00:00"))
                    if parsed_dt.tzinfo is None:
                        parsed_dt = parsed_dt.replace(tzinfo=timezone.utc)
                    event_dt = parsed_dt.astimezone(timezone.utc)
                except Exception:
                    continue
                impact_raw = str(raw.get("impact") or "LOW").strip().upper()
                impact = "HIGH" if "HIGH" in impact_raw else "MEDIUM" if "MEDIUM" in impact_raw else "LOW"
                if currency not in MACRO_RELEVANT_CURRENCIES:
                    continue
                events.append({
                    "id": None,
                    "date": event_dt.date().isoformat(),
                    "time_utc": event_dt.strftime("%H:%M"),
                    "datetime_utc": event_dt.isoformat(),
                    "currency": currency,
                    "event": title,
                    "impact": impact,
                    "actual": str(raw.get("actual") or "").strip(),
                    "forecast": str(raw.get("forecast") or "").strip(),
                    "previous": str(raw.get("previous") or "").strip(),
                    "macro_lean": "NEUTRAL",
                    "surprise_score": 0.0,
                    "surprise": "NOT RELEASED / NO VERIFIED ACTUAL",
                    "source": "FOREX_FACTORY_LIVE",
                    "notes": "Live public economic-calendar feed; actual/forecast/previous shown as supplied by source."
                })
        except Exception as exc:
            errors.append(f"{url}: {type(exc).__name__}: {exc}")
    unique = {}
    for event in events:
        key = (event["datetime_utc"], event["currency"], event["event"])
        unique[key] = event
    return list(unique.values()), errors, successful_sources, now_utc().isoformat()

def _macro_numeric_value(value):
    if value is None:
        return None
    text_value = str(value).strip().upper().replace(",", "").replace(" ", "")
    if not text_value or text_value in {"-", "N/A", "NA", "--", "TBD"}:
        return None
    multiplier = 1.0
    if text_value.endswith("K"):
        multiplier, text_value = 1_000.0, text_value[:-1]
    elif text_value.endswith("M"):
        multiplier, text_value = 1_000_000.0, text_value[:-1]
    elif text_value.endswith("B"):
        multiplier, text_value = 1_000_000_000.0, text_value[:-1]
    elif text_value.endswith("T"):
        multiplier, text_value = 1_000_000_000_000.0, text_value[:-1]
    text_value = text_value.replace("%", "")
    try:
        return float(text_value) * multiplier
    except (TypeError, ValueError):
        return None

def _score_macro_surprise(event):
    if event.get("currency") != "USD":
        return 0.0, "NEUTRAL", "NO USD SURPRISE SCORE"
    actual = _macro_numeric_value(event.get("actual"))
    forecast = _macro_numeric_value(event.get("forecast"))
    if actual is None or forecast is None:
        return 0.0, "NEUTRAL", "ACTUAL OR FORECAST NOT AVAILABLE"
    if abs(forecast) > 1e-12:
        relative_surprise = (actual - forecast) / max(abs(forecast), 1e-9)
    else:
        relative_surprise = actual - forecast
    if abs(relative_surprise) < 0.0005:
        return 0.0, "NEUTRAL", "IN LINE WITH FORECAST"
    name = str(event.get("event", "")).lower()
    inflation_terms = ("cpi", "pce", "ppi", "inflation", "price index", "import prices")
    labor_strong_terms = ("non-farm", "nonfarm", "payroll", "employment change", "average hourly earnings", "wage")
    claims_terms = ("unemployment claims", "jobless claims", "initial claims", "continuing claims")
    growth_terms = ("gdp", "retail sales", " ism ", "pmi", "industrial production", "capacity utilization")
    if any(term in name for term in inflation_terms):
        btc_sign = -1.0 if relative_surprise > 0 else 1.0
        category = "HOTTER THAN EXPECTED" if relative_surprise > 0 else "COOLER THAN EXPECTED"
    elif any(term in name for term in claims_terms):
        btc_sign = 1.0 if relative_surprise > 0 else -1.0
        category = "WEAKER LABOR (CLAIMS ABOVE FORECAST)" if relative_surprise > 0 else "STRONGER LABOR (CLAIMS BELOW FORECAST)"
    elif any(term in name for term in labor_strong_terms + growth_terms):
        btc_sign = -1.0 if relative_surprise > 0 else 1.0
        category = "STRONGER THAN EXPECTED" if relative_surprise > 0 else "WEAKER THAN EXPECTED"
    else:
        return 0.0, "NEUTRAL", "RELEASED; NO GENERIC DIRECTION ASSIGNED"
    magnitude = min(1.0, max(0.25, abs(relative_surprise) * 10.0))
    score = round(btc_sign * magnitude, 3)
    lean = "BULLISH_BTC" if score > 0 else "BEARISH_BTC"
    return score, lean, category

def _macro_event_stance(event):
    lean = str(event.get("macro_lean") or "NEUTRAL").upper()
    score = safe_float(event.get("surprise_score"), 0.0)
    if lean in ("BULLISH_BTC", "DOVISH_RELIEF") or score > 0:
        return "Bullish"
    if lean in ("BEARISH_BTC", "HAWKISH_RISK") or score < 0:
        return "Bearish"
    return "Neutral"

def add_macro_event(event_date, time_utc, currency, event_name, impact="HIGH", macro_lean="NEUTRAL", notes=""):
    connection = get_connection()
    connection.execute(
        "INSERT INTO macro_calendar (event_date,time_utc,currency,event_name,impact,macro_lean,notes) VALUES (?,?,?,?,?,?,?)",
        (str(event_date), str(time_utc), str(currency).upper(), str(event_name), str(impact).upper(), str(macro_lean).upper(), str(notes))
    )
    connection.commit()
    connection.close()
    add_event("MACRO CALENDAR", f"Added custom event: {event_date} {time_utc} UTC | {currency} {event_name} ({impact}, {macro_lean})")

def delete_macro_event(event_id):
    connection = get_connection()
    connection.execute("DELETE FROM macro_calendar WHERE id=?", (int(event_id),))
    connection.commit()
    connection.close()
    add_event("MACRO CALENDAR", f"Deleted custom event ID #{event_id}")

def get_macro_calendar_data(lookahead_days=14, lookback_days=2):
    now = now_utc()
    start_dt = (now - timedelta(days=lookback_days)).date()
    end_dt = (now + timedelta(days=lookahead_days)).date()
    live_events, feed_errors, feed_sources, fetched_at = _fetch_forexfactory_calendar_cached()
    combined = []
    for event in live_events:
        if start_dt.isoformat() <= event["date"] <= end_dt.isoformat():
            surprise_score, macro_lean, surprise = _score_macro_surprise(event)
            combined.append({**event, "surprise_score": surprise_score, "macro_lean": macro_lean, "surprise": surprise})

    custom_rows = database_read(
        "SELECT id,event_date,time_utc,currency,event_name,impact,macro_lean,notes FROM macro_calendar WHERE event_date BETWEEN ? AND ? ORDER BY event_date,time_utc",
        (start_dt.isoformat(), end_dt.isoformat())
    )
    if not custom_rows.empty:
        for _, row in custom_rows.iterrows():
            combined.append({
                "id": int(row["id"]), "date": str(row["event_date"]), "time_utc": str(row["time_utc"]),
                "currency": str(row["currency"]), "event": str(row["event_name"]), "impact": str(row["impact"]).upper(),
                "actual": "", "forecast": "", "previous": "", "surprise": "MANUAL EVENT - NO RELEASE DATA",
                "surprise_score": 0.0, "macro_lean": str(row["macro_lean"] or "NEUTRAL"),
                "source": "CUSTOM_DB", "notes": str(row["notes"] or "")
            })

    enriched = []
    next_high_impact = next_minor_impact = imminent_high_impact = imminent_medium_impact = last_released_event = None
    min_future_high_hours = min_future_any_hours = None
    for ev in combined:
        try:
            if ev.get("datetime_utc"):
                ev_dt = datetime.fromisoformat(ev["datetime_utc"].replace("Z", "+00:00")).astimezone(timezone.utc)
            else:
                hh, mm = [int(x) for x in (ev.get("time_utc") or "00:00").split(":")[:2]]
                y, m, d = [int(x) for x in ev["date"].split("-")[:3]]
                ev_dt = datetime(y, m, d, hh, mm, tzinfo=timezone.utc)
        except Exception:
            continue
        hours_until = (ev_dt - now).total_seconds() / 3600
        impact = str(ev.get("impact", "LOW")).upper()
        stance = _macro_event_stance(ev)
        ev_enriched = {**ev, "hours_until": round(hours_until, 2), "stance": stance, "_dt": ev_dt}
        if hours_until <= 0:
            if last_released_event is None or ev_dt > last_released_event["_dt"]:
                last_released_event = ev_enriched
        if hours_until >= 0:
            min_future_any_hours = hours_until if min_future_any_hours is None else min(min_future_any_hours, hours_until)
            if impact == "HIGH":
                min_future_high_hours = hours_until if min_future_high_hours is None else min(min_future_high_hours, hours_until)
                if next_high_impact is None:
                    next_high_impact = {k: v for k, v in ev_enriched.items() if k != "_dt"}
                if hours_until <= 2 and (imminent_high_impact is None or hours_until < imminent_high_impact["hours_until"]):
                    imminent_high_impact = {k: v for k, v in ev_enriched.items() if k != "_dt"}
            else:
                if next_minor_impact is None:
                    next_minor_impact = {k: v for k, v in ev_enriched.items() if k != "_dt"}
                if impact == "MEDIUM" and hours_until <= 2 and (imminent_medium_impact is None or hours_until < imminent_medium_impact["hours_until"]):
                    imminent_medium_impact = {k: v for k, v in ev_enriched.items() if k != "_dt"}
        enriched.append({k: v for k, v in ev_enriched.items() if k != "_dt"})

    if last_released_event is not None:
        last_released_event = {k: v for k, v in last_released_event.items() if k != "_dt"}

    enriched.sort(key=lambda x: (x["date"], x["time_utc"], x["currency"], x["event"]))
    if not feed_sources:
        risk_window = "CALENDAR DATA UNAVAILABLE"
    elif imminent_high_impact is not None:
        risk_window = "IMMINENT / ACTIVE HIGH-IMPACT EVENT (<=2h)"
    elif min_future_high_hours is not None and min_future_high_hours <= 24:
        risk_window = "ELEVATED HIGH-IMPACT MACRO RISK (<=24h)"
    elif imminent_medium_impact is not None:
        risk_window = "MEDIUM-IMPACT EVENT WINDOW (<=2h)"
    elif min_future_any_hours is not None and min_future_any_hours <= 12:
        risk_window = "MACRO EVENT ACTIVE / UPCOMING (<=12h)"
    else:
        risk_window = "NORMAL - LIVE CALENDAR CHECKED"
    return {
        "mode": "LIVE_FOREX_FACTORY_JSON" if feed_sources else "LIVE_CALENDAR_UNAVAILABLE",
        "source": "Forex Factory public economic calendar JSON",
        "source_urls": feed_sources,
        "fetched_at": fetched_at,
        "feed_errors": feed_errors,
        "calendar_available": bool(feed_sources),
        "risk_window": risk_window,
        "next_high_impact": next_high_impact, "next_minor_impact": next_minor_impact,
        "last_released_event": last_released_event,
        "imminent_high_impact": imminent_high_impact, "imminent_medium_impact": imminent_medium_impact,
        "min_abs_hours": round(min_future_high_hours, 2) if min_future_high_hours is not None else None,
        "min_any_hours": round(min_future_any_hours, 2) if min_future_any_hours is not None else None,
        "events": enriched
    }

def _lean_to_signed_tilt(lean):
    value = str(lean or "NEUTRAL").upper()
    if value in ("HAWKISH_RISK", "BEARISH_BTC"): return -1.0
    if value in ("DOVISH_RELIEF", "BULLISH_BTC"): return 1.0
    return 0.0

def analyze_macro_signal(macro_calendar, news_analysis=None, funding=None, directional_weight=8.0):
    risk_window = macro_calendar.get("risk_window", "CALENDAR DATA UNAVAILABLE")
    next_hi, next_minor = macro_calendar.get("next_high_impact"), macro_calendar.get("next_minor_impact")
    imminent_hi, imminent_med = macro_calendar.get("imminent_high_impact"), macro_calendar.get("imminent_medium_impact")
    score_adjustment, block_new_trade = 0.0, False
    status, bias, directional_score, reasons = "CLEAR", "NEUTRAL", 0.0, []
    calendar_available = bool(macro_calendar.get("calendar_available"))
    if not calendar_available:
        status, bias, score_adjustment, block_new_trade = "CALENDAR DATA UNAVAILABLE", "UNKNOWN - DO NOT ASSUME CLEAR", -10.0, True
        reasons.append("Live economic calendar could not be verified; no synthetic events were substituted. Macro gate is holding new entries until the feed is restored.")
    elif imminent_hi is not None and safe_float(imminent_hi.get("hours_until"), 999) <= 1:
        status, score_adjustment, block_new_trade, bias = "HIGH RISK - IMMINENT HIGH-IMPACT EVENT (<=1h)", -25.0, True, "DEFENSIVE / NO NEW ENTRY"
        reasons.append(f"Verified HIGH-impact event ({imminent_hi.get('currency')} {imminent_hi.get('event')}) within 1h ({imminent_hi.get('hours_until')}h).")
    elif risk_window.startswith("IMMINENT"):
        status, score_adjustment, bias = "HIGH RISK - EVENT WINDOW (<=2h)", -15.0, "DEFENSIVE"
        reasons.append("Verified HIGH-impact macro event within 2h; volatility and liquidity-sweep risk elevated.")
    elif "ELEVATED HIGH" in risk_window:
        status, score_adjustment, bias = "ELEVATED MACRO (<=24h)", -5.0, "CAUTIOUS"
        if next_hi: reasons.append(f"Upcoming verified HIGH event in {next_hi.get('hours_until')}h: {next_hi.get('currency')} {next_hi.get('event')}.")
    elif imminent_med is not None:
        status, score_adjustment, bias = "MEDIUM MACRO WATCH (<=2h)", -4.0, "CAUTIOUS (MEDIUM MACRO)"
        reasons.append(f"Verified MEDIUM event ({imminent_med.get('currency')} {imminent_med.get('event')}) within 2h.")
    else:
        reasons.append("No verified HIGH-impact calendar event in the immediate risk window.")

    surprise_events = [e for e in macro_calendar.get("events", []) if e.get("source") == "FOREX_FACTORY_LIVE" and safe_float(e.get("hours_until"), 999) <= 0 and safe_float(e.get("hours_until"), -999) >= -36 and abs(safe_float(e.get("surprise_score"))) > 0]
    surprise_total = 0.0
    for event in surprise_events:
        contribution = safe_float(event.get("surprise_score")) * 1.8
        surprise_total += contribution
        reasons.append(f"Released {event.get('event')}: actual {event.get('actual')} vs forecast {event.get('forecast')} ({event.get('surprise')}); BTC tilt {contribution:+.2f}.")
    if surprise_events:
        directional_score += clamp(surprise_total, -4.0, 4.0)

    if isinstance(news_analysis, dict):
        ns = safe_float(news_analysis.get("score", 0))
        if abs(ns) > 0.2:
            directional_score += clamp(ns * 0.35 * directional_weight, -0.45 * directional_weight, 0.45 * directional_weight)
            reasons.append(f"News sentiment ({news_analysis.get('classification')}, {ns:+.2f}) factored into macro tilt.")
    if isinstance(funding, dict) and funding.get("rate") is not None:
        fr = safe_float(funding.get("rate"))
        if fr > 0.0008:
            directional_score -= 0.2 * directional_weight
            reasons.append("Elevated positive funding adds squeeze/pullback headwind for longs.")
        elif fr < -0.0008:
            directional_score += 0.2 * directional_weight
            reasons.append("Negative funding adds short-squeeze tailwind.")
    directional_score = round(clamp(directional_score, -max(0, safe_float(directional_weight)), max(0, safe_float(directional_weight))), 2)
    if not block_new_trade and calendar_available:
        if directional_score >= 2: bias = "MACRO BULLISH TILT"
        elif directional_score <= -2: bias = "MACRO BEARISH / DEFENSIVE TILT"
        elif bias == "NEUTRAL": bias = "NEUTRAL / NO STRONG MACRO EDGE"
    return {
        "status": status, "bias": bias, "score_adjustment": score_adjustment,
        "directional_score": directional_score, "block_new_trade": block_new_trade,
        "next_high_impact": next_hi, "next_minor_impact": next_minor,
        "calendar_available": calendar_available,
        "reason": " ".join(reasons)
    }

def get_macro_status(macro_cal=None, macro_signal=None):
    if macro_cal is None: macro_cal = get_macro_calendar_data()
    if macro_signal is None: macro_signal = analyze_macro_signal(macro_cal)
    available = bool(macro_cal.get("calendar_available"))
    return {
        "available": False, "dxy": None, "us10y": None, "fed": None,
        "calendar_available": available,
        "calendar_risk_window": macro_cal["risk_window"], "macro_status": macro_signal["status"],
        "macro_bias": macro_signal["bias"], "macro_directional_score": macro_signal.get("directional_score", 0),
        "macro_reason": macro_signal["reason"], "next_high_impact": macro_cal["next_high_impact"],
        "next_minor_impact": macro_cal.get("next_minor_impact"),
        "message": ("Live Forex Factory economic calendar connected. Actual, forecast and previous values are sourced from the feed; missing values are not invented." if available else "Live economic calendar unavailable. No synthetic release dates or fabricated Actual/Forecast/Previous values are used. Check network/feed availability.")
    }

# ---------------- ENTRY QUALITY (S/R ADDED) ----------------
def calculate_entry_quality(df, structure, regime, oi, delta, book, liquidity, mtf, funding, retest, macro_signal=None, sr_analysis=None):
    components = {
        "structure": 0, "order_flow": 0, "open_interest": 0, "liquidity": 0,
        "multi_timeframe": 0, "breakout_retest": 0, "funding": 0, "support_resistance": 0, "macro": 0
    }
    if df.empty:
        return {"direction": "WAIT", "raw_score": 0, "score": 0, "quality": 0, "stage": "WAIT", "components": components}
    if structure["direction"] == "BULLISH": components["structure"] = 20
    elif structure["direction"] == "BEARISH": components["structure"] = -20
    components["order_flow"] = clamp(delta["delta_pct"] / 100 * 20, -20, 20)
    oi_state = oi["classification"]
    if oi_state == "LONG BUILDING": components["open_interest"] = 15
    elif oi_state == "SHORT BUILDING": components["open_interest"] = -15
    elif oi_state == "SHORT CLOSING": components["open_interest"] = 7
    elif oi_state == "LONG CLOSING": components["open_interest"] = -7
    components["liquidity"] = book["imbalance"] * 15
    if liquidity["sweep"] == "BULLISH LIQUIDITY SWEEP": components["liquidity"] += 5
    elif liquidity["sweep"] == "BEARISH LIQUIDITY SWEEP": components["liquidity"] -= 5
    components["liquidity"] = clamp(components["liquidity"], -15, 15)
    weights = {"1m": 1, "5m": 2, "15m": 3, "1H": 4, "4H": 5}
    mtf_score = 0
    for tf, weight in weights.items():
        direction = mtf.get(tf, {}).get("structure", "UNKNOWN")
        if direction == "BULLISH": mtf_score += weight
        elif direction == "BEARISH": mtf_score -= weight
    components["multi_timeframe"] = mtf_score / sum(weights.values()) * 15
    if retest == "BULLISH BREAKOUT RETEST": components["breakout_retest"] = 10
    elif retest == "BEARISH BREAKOUT RETEST": components["breakout_retest"] = -10
    if funding["classification"] == "EXTREME LONG CROWDING": components["funding"] = -5
    elif funding["classification"] == "EXTREME SHORT CROWDING": components["funding"] = 5
    if isinstance(sr_analysis, dict):
        components["support_resistance"] = clamp(safe_float(sr_analysis.get("directional_score")), -12, 12)
    raw_score = sum(v for k, v in components.items() if k not in ("macro", "support_resistance"))
    raw_score += components["support_resistance"]
    macro_adj = safe_float(macro_signal.get("score_adjustment", 0)) if isinstance(macro_signal, dict) else 0
    macro_dir_score = safe_float(macro_signal.get("directional_score", 0)) if isinstance(macro_signal, dict) else 0
    macro_component = macro_dir_score
    if macro_adj < 0:
        projected = raw_score + macro_component
        if projected > 0: macro_component -= min(projected, abs(macro_adj))
        elif projected < 0: macro_component += min(abs(projected), abs(macro_adj))
    components["macro"] = round(macro_component, 2)
    score = raw_score + components["macro"]
    direction = "LONG" if score > 0 else "SHORT" if score < 0 else "WAIT"

    # --- Ellenállás előtti LONG ellenőrzés (room-to-resistance check) ---
    sr_blocked = False
    if direction == "LONG" and isinstance(sr_analysis, dict):
        price = safe_float(df["close"].iloc[-1])
        atr = safe_float(df["atr"].iloc[-1])
        b_state = str(sr_analysis.get("breakout_state", ""))
        has_confirmed_breakout = b_state.startswith("BULLISH S/R BREAKOUT") or "BULLISH RETEST" in b_state
        if not has_confirmed_breakout and price > 0:
            candidates = [z for z in (sr_analysis.get("nearest_resistance"), sr_analysis.get("strongest_resistance")) if isinstance(z, dict)]
            for res in candidates:
                res_lower = safe_float(res.get("lower"))
                res_score = safe_float(res.get("score"))
                res_tfs = res.get("timeframes") or []
                is_strong_or_htf = res_score >= 60 or any(tf in ("1H", "4H") for tf in res_tfs)
                if res_lower > price and is_strong_or_htf:
                    room_abs = res_lower - price
                    room_pct = room_abs / price * 100
                    min_room = max(atr * 1.2 if atr > 0 else 0.0, price * 0.0045)
                    if room_abs < min_room or room_pct < 0.45:
                        direction = "WAIT"
                        score = 0.0
                        sr_blocked = True
                        break

    quality = 0.0 if sr_blocked else abs(score)
    stage = "RESISTANCE BLOCK - WAIT" if sr_blocked else ("HIGH QUALITY SETUP" if quality >= 85 else "STRONG SETUP" if quality >= 75 else "EARLY SETUP" if quality >= 65 else "WATCH" if quality >= 50 else "WAIT")
    if macro_signal and macro_signal.get("block_new_trade"): stage = "MACRO EVENT HOLD (<=1h)"
    elif regime["volatility"] == "EXTREME": stage = "HIGH VOLATILITY - CAUTION"
    return {"direction": direction, "raw_score": round(raw_score, 2), "score": round(score, 2), "quality": round(quality, 2), "stage": stage, "components": components}

# ---------------- REVERSALS ----------------
def analyze_reversals(df, structure, delta, liquidity, divergences):
    signals = []
    if df.empty: return signals
    rsi = safe_float(df.iloc[-1]["rsi"])
    if rsi < 30 and delta["delta_pct"] > 10: signals.append("POSSIBLE BULLISH MOMENTUM REVERSAL")
    if rsi > 70 and delta["delta_pct"] < -10: signals.append("POSSIBLE BEARISH MOMENTUM REVERSAL")
    if liquidity["sweep"] == "BULLISH LIQUIDITY SWEEP": signals.append("BULLISH LIQUIDITY REVERSAL SETUP")
    if liquidity["sweep"] == "BEARISH LIQUIDITY SWEEP": signals.append("BEARISH LIQUIDITY REVERSAL SETUP")
    signals.extend(divergences)
    return signals or ["NO CONFIRMED REVERSAL"]

# ---------------- NEWS ----------------
def fetch_news():
    articles, errors = [], []
    for source, url in NEWS_FEEDS.items():
        try:
            response = requests.get(url, timeout=REQUEST_TIMEOUT, headers={"User-Agent": "Mozilla/5.0 SALIGA AI"})
            response.raise_for_status()
            root = ET.fromstring(response.content)
            for item in root.findall(".//item")[:12]:
                title = item.findtext("title") or ""
                description = item.findtext("description") or ""
                published = item.findtext("pubDate") or ""
                link = item.findtext("link") or ""
                if title:
                    articles.append({
                        "source": source, "title": " ".join(title.split())[:300],
                        "description": " ".join(description.split())[:600],
                        "published": published, "link": link
                    })
        except Exception as error:
            errors.append(f"{source}: {error}")
    unique = {}
    for article in articles:
        key = hashlib.sha256(article["title"].lower().encode()).hexdigest()
        unique[key] = article
    return list(unique.values())[:20], errors

POSITIVE_WORDS = ["approval", "adoption", "institutional", "inflow", "bullish", "surge", "record high", "partnership", "reserve", "accumulation", "positive", "rate cut", "dovish"]
NEGATIVE_WORDS = ["hack", "exploit", "lawsuit", "ban", "outflow", "liquidation", "crash", "bearish", "fraud", "investigation", "selloff", "bankruptcy", "hawkish", "hot inflation"]

def analyze_news_sentiment(news):
    scored = []
    for article in news:
        content = (article["title"] + " " + article["description"]).lower()
        positive = sum(word in content for word in POSITIVE_WORDS)
        negative = sum(word in content for word in NEGATIVE_WORDS)
        raw_s = clamp(positive - negative, -2, 2)
        stance = "Bullish" if raw_s > 0 else "Bearish" if raw_s < 0 else "Neutral"
        scored.append({**article, "sentiment": raw_s, "stance": stance})
    if not scored:
        return {"score": 0, "classification": "NO NEWS DATA", "articles": []}
    average = float(np.mean([x["sentiment"] for x in scored]))
    classification = "POSITIVE NEWS FLOW" if average > 0.5 else "NEGATIVE NEWS FLOW" if average < -0.5 else "MIXED / NEUTRAL NEWS FLOW"
    return {"score": average, "classification": classification, "articles": scored}

def calculate_news_hash(news):
    return hashlib.sha256("|".join(x["title"] for x in news).encode()).hexdigest()

# ---------------- GROQ AI ----------------
def call_groq(payload):
    if not GROQ_API_KEY:
        raise RuntimeError("GROQ_API_KEY is missing from secrets.toml or environment.")
    system_prompt = (
        "You are SALIGA AI PRO 31.2, an analytical BTC market intelligence engine. "
        "Output valid JSON only. Analyze technicals, order flow, liquidity, macro, news and especially the supplied support_resistance engine. "
        "S/R zones are high-priority context: evaluate strength, touches, timeframe confluence, rejection, volume confirmation, distance, role reversal, breakout/retest and false-breakout state. "
        "Do not call a zone a confirmed breakout unless candle closes beyond its boundary. Distinguish wick-only false breaks. "
        "Do not let S/R alone force a trade. Respect macro gate, technical conflicts and risk management. "
        "If imminent HIGH impact macro event within 1h, lean WAIT or conflict=true unless a clear post-event move is established. "
        "Use the live economic calendar Actual vs Forecast vs Previous fields. Only treat a release as published when Actual is present; never invent missing values or release dates. Explain the likely BTC impact of verified surprises, and distinguish scheduled-event volatility risk from directional macro bias. If the calendar feed is unavailable, explicitly say macro calendar data is unavailable rather than assuming no events. "
        "Return this exact JSON structure: "
        '{"direction":"LONG","confidence":0,"conflict":false,"summary":"string",'
        '"technical_analysis":"string","order_flow_analysis":"string","liquidity_analysis":"string",'
        '"support_resistance_analysis":"string","macro_analysis":"string","news_impact":"string",'
        '"risk":"string","invalidation":"string"}'
    )
    request_data = {
        "model": GROQ_MODEL, "temperature": 0.1, "max_tokens": 1700,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False, separators=(",", ":"))}
        ]
    }
    response = requests.post(
        GROQ_API_URL,
        headers={"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"},
        json=request_data, timeout=45
    )
    if response.status_code == 429:
        raise RuntimeError(f"Groq rate limit 429. Retry-After: {response.headers.get('Retry-After', 'not provided')}. AI result not marked successful.")
    if response.status_code >= 400:
        raise RuntimeError(f"Groq HTTP {response.status_code}: {response.text[:1000]}")
    content = response.json()["choices"][0]["message"]["content"]
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        parsed = json.loads(content.replace("```json", "").replace("```", "").strip())
    if not isinstance(parsed, dict):
        raise RuntimeError("Groq returned an invalid response structure.")
    direction = str(parsed.get("direction", "WAIT")).upper()
    parsed["direction"] = direction if direction in ("LONG", "SHORT", "WAIT") else "WAIT"
    parsed["conflict"] = bool(parsed.get("conflict", False))
    for key in ("macro_analysis", "support_resistance_analysis"):
        if not parsed.get(key):
            parsed[key] = "No separate analysis returned."
    return parsed

# ---------------- AI SCHEDULER ----------------
def ai_due(news_id, oi, delta, liquidity, macro_signal=None):
    current = time.time()
    normal_due = current - st.session_state.last_ai_time >= ai_interval * 60
    event_detected = False
    oi_change = oi.get("change_pct") if oi else None
    if oi_change is not None and abs(oi_change) > 0.5: event_detected = True
    if abs(delta["delta_pct"]) > 25: event_detected = True
    if liquidity["sweep"] not in ("NO CONFIRMED SWEEP", "UNKNOWN"): event_detected = True
    if macro_signal and macro_signal.get("status", "").startswith("HIGH RISK"): event_detected = True
    previous_hash = st.session_state.last_news_hash
    if previous_hash is not None and news_id != previous_hash: event_detected = True
    cooldown_passed = current - st.session_state.last_trigger_time >= ai_cooldown * 60
    if normal_due: return True, "NORMAL SCHEDULE"
    if event_detected and cooldown_passed: return True, "MARKET / MACRO EVENT"
    return False, "NOT DUE"

# ---------------- AI / MACRO GATE ----------------
def check_ai_gate(entry, ai_result, macro_signal=None, macro_gate_active=True):
    if macro_gate_active and macro_signal and macro_signal.get("block_new_trade"):
        return {"allowed": False, "reason": f"Blocked by Macro Gate: {macro_signal.get('reason')}"}
    if ai_result is None:
        return {"allowed": False, "reason": "No successful AI validation available."}
    if ai_result.get("conflict", False):
        return {"allowed": False, "reason": "AI detected conflicting technical/macro/flow evidence."}
    ai_direction = ai_result.get("direction", "WAIT")
    if ai_direction == "WAIT":
        return {"allowed": False, "reason": "AI recommends WAIT."}
    if entry["direction"] in ("LONG", "SHORT") and ai_direction != entry["direction"]:
        return {"allowed": False, "reason": "AI and technical direction conflict."}
    return {"allowed": True, "reason": "AI, technicals, and macro gate aligned."}

def build_unified_signal(entry, ai_result, ai_gate, macro_signal, risk_levels):
    tech_dir, quality = entry["direction"], safe_float(entry["quality"])
    ai_dir = ai_result.get("direction", "N/A") if isinstance(ai_result, dict) else "N/A"
    macro_status = macro_signal.get("status", "CLEAR") if isinstance(macro_signal, dict) else "CLEAR"
    if ai_gate["allowed"] and tech_dir in ("LONG", "SHORT") and quality >= 75 and risk_levels is not None:
        label, actionable = f"ACTIVE {tech_dir} SIGNAL", True
    elif tech_dir in ("LONG", "SHORT") and quality >= 65 and not ai_gate["allowed"]:
        label, actionable = f"FILTERED {tech_dir} (GATE BLOCKED)", False
    elif tech_dir in ("LONG", "SHORT") and quality >= 50:
        label, actionable = f"WATCH {tech_dir}", False
    else:
        label, actionable = "NO TRADE / WAIT", False
    parts = [f"Tech: {tech_dir} ({quality:.1f}/100)", f"AI: {ai_dir}", f"Macro: {macro_status} ({safe_float(macro_signal.get('directional_score', 0)):+.1f})"]
    return {
        "label": label, "direction": tech_dir if actionable else "WAIT", "actionable": actionable,
        "quality": quality, "macro_status": macro_status,
        "macro_bias": macro_signal.get("bias", "NEUTRAL") if isinstance(macro_signal, dict) else "NEUTRAL",
        "gate_reason": ai_gate["reason"], "summary": " | ".join(parts)
    }

# ---------------- RISK ----------------
def calculate_risk_levels(price, atr, direction, regime):
    if price <= 0 or atr <= 0: return None
    multiplier = 2.2 if regime["volatility"] == "HIGH" else 2.8 if regime["volatility"] == "EXTREME" else 1.5 if regime["volatility"] == "LOW" else 1.8
    distance = atr * multiplier
    if direction == "LONG":
        stop, tp1, tp2, tp3 = price - distance, price + distance * 1.5, price + distance * 2.5, price + distance * 3.5
    elif direction == "SHORT":
        stop, tp1, tp2, tp3 = price + distance, price - distance * 1.5, price - distance * 2.5, price - distance * 3.5
    else:
        return None
    return {"entry": price, "stop": stop, "tp1": tp1, "tp2": tp2, "tp3": tp3}

# ---------------- PAPER TRADING ----------------
def get_open_trade():
    connection = get_connection()
    row = connection.execute("SELECT * FROM paper_trades WHERE status='OPEN' ORDER BY id DESC LIMIT 1").fetchone()
    connection.close()
    return dict(row) if row else None

def get_paper_equity():
    closed = database_read("SELECT COALESCE(SUM(net_pnl),0) AS pnl FROM paper_trades WHERE status='CLOSED'")
    pnl = safe_float(closed.iloc[0]["pnl"]) if not closed.empty else 0
    return starting_balance + pnl

def open_paper_trade(direction, levels, score):
    if not paper_enabled or levels is None or get_open_trade() is not None: return
    equity = get_paper_equity()
    risk_amount = equity * risk_percent / 100
    distance = abs(levels["entry"] - levels["stop"])
    if distance <= 0: return
    quantity = risk_amount / distance
    connection = get_connection()
    connection.execute(
        """INSERT INTO paper_trades (opened,direction,entry,stop,tp1,tp2,tp3,quantity,score,status)
        VALUES (?,?,?,?,?,?,?,?,?,'OPEN')""",
        (now_string(), direction, levels["entry"], levels["stop"], levels["tp1"], levels["tp2"], levels["tp3"], quantity, score)
    )
    connection.commit()
    connection.close()
    add_event("PAPER TRADE", f"Opened {direction} at {levels['entry']:.2f}")

def close_paper_trade(trade, exit_price, reason, funding_rate=0):
    entry, quantity = safe_float(trade["entry"]), safe_float(trade["quantity"])
    gross = ((exit_price - entry) if trade["direction"] == "LONG" else (entry - exit_price)) * quantity
    notional_entry, notional_exit = entry * quantity, exit_price * quantity
    fees = (notional_entry + notional_exit) * fee_percent / 100
    slippage = (notional_entry + notional_exit) * slippage_percent / 100
    funding = notional_entry * funding_rate * (1 if trade["direction"] == "LONG" else -1)
    net = gross - fees - slippage - funding
    connection = get_connection()
    connection.execute(
        """UPDATE paper_trades SET closed=?,exit_price=?,gross_pnl=?,fees=?,slippage=?,funding=?,net_pnl=?,status='CLOSED',reason=? WHERE id=?""",
        (now_string(), exit_price, gross, fees, slippage, funding, net, reason, trade["id"])
    )
    connection.commit()
    connection.close()
    add_event("PAPER TRADE", f"Closed {trade['direction']} | {reason} | Net PnL {net:.2f} USDT")

def manage_paper_trade(df, funding_rate=0):
    trade = get_open_trade()
    if trade is None or df.empty: return
    latest = df.iloc[-1]
    high, low = safe_float(latest["high"]), safe_float(latest["low"])
    stop_hit = low <= trade["stop"] if trade["direction"] == "LONG" else high >= trade["stop"]
    target_hit = high >= trade["tp1"] if trade["direction"] == "LONG" else low <= trade["tp1"]
    if stop_hit:
        close_paper_trade(trade, trade["stop"], "STOP LOSS", funding_rate)
    elif target_hit:
        close_paper_trade(trade, trade["tp1"], "TAKE PROFIT 1", funding_rate)

# ---------------- PERFORMANCE ----------------
def get_performance():
    trades = database_read("SELECT * FROM paper_trades WHERE status='CLOSED'")
    if trades.empty: return {"count": 0, "win_rate": 0, "profit_factor": 0, "net_pnl": 0}
    pnls = trades["net_pnl"].astype(float)
    wins, losses = pnls[pnls > 0], pnls[pnls < 0]
    gross_profit, gross_loss = wins.sum(), abs(losses.sum())
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf") if gross_profit > 0 else 0
    return {"count": len(trades), "win_rate": len(wins) / len(trades) * 100, "profit_factor": profit_factor, "net_pnl": pnls.sum()}

# ---------------- MARKET SNAPSHOT ----------------
def save_market_snapshot(price, oi, delta, funding, book):
    connection = get_connection()
    oi_val = oi.get("oi") if isinstance(oi, dict) else None
    delta_val = delta.get("delta") if isinstance(delta, dict) else 0
    funding_val = funding.get("rate") if isinstance(funding, dict) else None
    book_imb = book.get("imbalance") if isinstance(book, dict) else 0
    connection.execute(
        "INSERT INTO market_snapshots (timestamp,price,oi,delta,funding,book_imbalance) VALUES (?,?,?,?,?,?)",
        (time.time(), price, oi_val, delta_val, funding_val, book_imb)
    )
    connection.commit()
    connection.close()

# ================================================================
# MAIN APPLICATION
# ================================================================
st.title("₿ SALIGA AI PRO 31.2")
st.caption("ULTIMATE BTC INTELLIGENCE ENGINE | OKX MARKET DATA | GROQ AI | ENHANCED MACRO + S/R")
st.write(f"Last update: {now_string()}")
errors = []

try:
    ticker = fetch_ticker()
    price = ticker["price"]
    if price <= 0: raise RuntimeError("Invalid BTC price")
except Exception as error:
    st.error(f"OKX ticker error: {error}")
    st.stop()

st.session_state.current_price = price
timeframe_data = {}
for tf, bar in TIMEFRAMES.items():
    try:
        raw = fetch_candles(bar)
        timeframe_data[tf] = add_indicators(raw)
        if timeframe_data[tf].empty: errors.append(f"{tf}: no candle data")
    except Exception as error:
        timeframe_data[tf] = pd.DataFrame()
        errors.append(f"{tf}: {error}")

df1, df5, df15 = timeframe_data["1m"], timeframe_data["5m"], timeframe_data["15m"]
df1h, df4h = timeframe_data["1H"], timeframe_data["4H"]
main_df = df5 if not df5.empty else df1 if not df1.empty else df15
if main_df.empty:
    st.error("No usable BTC candle data.")
    st.stop()

try: oi_raw = fetch_open_interest()
except Exception as error:
    oi_raw = None
    errors.append(f"OI: {error}")
try: funding_raw = fetch_funding()
except Exception as error:
    funding_raw = None
    errors.append(f"Funding: {error}")
try: book_raw = fetch_order_book()
except Exception as error:
    book_raw = None
    errors.append(f"Order book: {error}")
try: trades_raw = fetch_recent_trades()
except Exception as error:
    trades_raw = []
    errors.append(f"Trades: {error}")

news_raw, news_errors = fetch_news()
st.session_state.last_news = news_raw
news_analysis = analyze_news_sentiment(news_raw)
news_id = calculate_news_hash(news_raw)

structure = market_structure(main_df)
regime = market_regime(main_df)
delta = calculate_delta(trades_raw)
book = analyze_order_book(book_raw)
oi = analyze_open_interest(oi_raw)
funding = analyze_funding(funding_raw)
macro_calendar = get_macro_calendar_data()
process_news_and_macro_alerts(news_analysis, macro_calendar)
macro_signal = analyze_macro_signal(macro_calendar, news_analysis=news_analysis, funding=funding, directional_weight=macro_directional_weight)
macro = get_macro_status(macro_calendar, macro_signal)
liquidity = analyze_liquidity(main_df)
retest = analyze_retest(main_df)
mtf = analyze_timeframes(timeframe_data)
divergences = analyze_divergences(main_df, delta)
flow = analyze_position_flow(oi, delta, structure)
patterns = analyze_historical_patterns(main_df)
reversals = analyze_reversals(main_df, structure, delta, liquidity, divergences)

sr_analysis = analyze_support_resistance(timeframe_data, price)

entry = calculate_entry_quality(
    main_df, structure, regime, oi, delta, book, liquidity, mtf, funding, retest,
    macro_signal=macro_signal, sr_analysis=sr_analysis
)
latest = main_df.iloc[-1]
atr = safe_float(latest["atr"])
risk_levels = calculate_risk_levels(price, atr, entry["direction"], regime)

save_market_snapshot(price, oi_raw, delta, funding_raw, book_raw)

market_payload = {
    "instrument": INSTRUMENT, "timestamp": now_string(), "price": price,
    "technical": {
        "direction": entry["direction"], "raw_score": entry["raw_score"],
        "score": entry["score"], "quality": entry["quality"], "stage": entry["stage"],
        "structure": structure, "regime": regime, "rsi": safe_float(latest["rsi"]),
        "atr": atr, "multi_timeframe": mtf, "breakout_retest": retest,
        "entry_components": entry["components"]
    },
    "support_resistance": sr_analysis,
    "order_flow": {"delta": delta, "position_flow": flow, "open_interest": oi, "funding": funding, "divergences": divergences},
    "liquidity": {"order_book": book, "liquidity_sweep": liquidity},
    "historical_patterns": patterns,
    "news": {
        "classification": news_analysis["classification"], "score": news_analysis["score"],
        "headlines": [{"source": x["source"], "title": x["title"], "description": x["description"], "sentiment": x["sentiment"], "stance": x["stance"]} for x in news_analysis["articles"][:5]]
    },
    "macro": macro, "macro_signal": macro_signal,
    "macro_calendar": {
        "risk_window": macro_calendar["risk_window"], "next_high_impact": macro_calendar["next_high_impact"],
        "next_minor_impact": macro_calendar.get("next_minor_impact"),
        "last_released_event": macro_calendar.get("last_released_event"),
        "imminent_high_impact": macro_calendar["imminent_high_impact"],
        "imminent_medium_impact": macro_calendar.get("imminent_medium_impact"),
        "upcoming_events": macro_calendar["events"][:15]
    },
    "risk_levels": risk_levels
}

should_run_ai, trigger_reason = ai_due(news_id, oi, delta, liquidity, macro_signal=macro_signal)
if ai_enabled and GROQ_API_KEY and should_run_ai:
    try:
        with st.spinner("Groq AI analyzing BTC market, S/R and macro..."):
            result = call_groq(market_payload)
        st.session_state.last_ai_result = result
        st.session_state.last_ai_time = time.time()
        st.session_state.last_trigger_time = time.time()
        st.session_state.last_news_hash = news_id
        st.session_state.last_ai_error = None
        add_event("GROQ AI", f"Successful analysis: {trigger_reason} | Macro: {macro_signal['status']} | S/R: {sr_analysis['status']}")
    except Exception as error:
        st.session_state.last_ai_error = str(error)
        add_event("GROQ AI ERROR", str(error))

ai_result = st.session_state.last_ai_result
if not GROQ_API_KEY and ai_enabled:
    st.session_state.last_ai_error = "Missing GROQ_API_KEY in secrets.toml."

ai_gate = check_ai_gate(entry, ai_result, macro_signal=macro_signal, macro_gate_active=macro_gate_enabled)
unified_signal = build_unified_signal(entry, ai_result, ai_gate, macro_signal, risk_levels)

funding_rate = funding_raw["funding"] if funding_raw and "funding" in funding_raw else 0
if paper_enabled:
    manage_paper_trade(df1 if not df1.empty else main_df, funding_rate)
    if unified_signal["actionable"]:
        open_paper_trade(unified_signal["direction"], risk_levels, entry["score"])

connection = get_connection()
connection.execute(
    """INSERT INTO signal_history (timestamp,price,direction,score,stage,regime,macro_status,signal_label,ai_gate)
    VALUES (?,?,?,?,?,?,?,?,?)""",
    (now_string(), price, entry["direction"], entry["score"], entry["stage"], regime["regime"], macro_signal["status"], unified_signal["label"], int(ai_gate["allowed"]))
)
connection.commit()
connection.close()
st.session_state.last_refresh = now_string()

# ================================================================
# DASHBOARD
# ================================================================
st.divider()
st.subheader("BTC Market Overview")
c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("BTC PRICE", f"${price:,.2f}")
c2.metric("24H HIGH", f"${ticker['high24h']:,.2f}")
c3.metric("24H LOW", f"${ticker['low24h']:,.2f}")
c4.metric("24H VOLUME", f"{ticker['volume24h']:,.2f}")
c5.metric("FUNDING", f"{funding['rate'] * 100:.5f}%" if funding["rate"] is not None else "N/A")

st.divider()
st.subheader("SMART ENTRY & SIGNAL ENGINE (TECHNICAL + S/R + MACRO + AI)")
sig1, sig2, sig3, sig4 = st.columns(4)
sig1.metric("UNIFIED SIGNAL", unified_signal["label"])
sig2.metric("TECHNICAL DIRECTION", entry["direction"])
sig3.metric("ENTRY QUALITY", f"{entry['quality']:.1f}/100")
sig4.metric("MACRO SIGNAL STATUS", macro_signal["status"])
st.write(f"**Signal breakdown:** {unified_signal['summary']}")
st.write(f"**Entry stage:** {entry['stage']}")
st.write(f"**Market regime:** {regime['regime']} | **Volatility:** {regime['volatility']}")
st.write(f"**Macro bias & note:** {macro_signal['bias']} (Tilt: {macro_signal['directional_score']:+.2f}) — {macro_signal['reason']}")
st.write(f"**AI & Macro gate:** {ai_gate['reason']}")
st.bar_chart(pd.DataFrame({"Score": entry["components"]}))

# ---------------- SUPPORT / RESISTANCE DASHBOARD ----------------
st.divider()
st.subheader("Institutional Support / Resistance Intelligence")
sr1, sr2, sr3, sr4 = st.columns(4)
sr1.metric("S/R STATE", sr_analysis["breakout_state"])
sr2.metric("S/R DIRECTIONAL WEIGHT", f"{sr_analysis['directional_score']:+.2f}")
ns, nr = sr_analysis["nearest_support"], sr_analysis["nearest_resistance"]
sr3.metric("NEAREST SUPPORT", f"{ns['lower']:,.2f}–{ns['upper']:,.2f}" if ns else "N/A")
sr4.metric("NEAREST RESISTANCE", f"{nr['lower']:,.2f}–{nr['upper']:,.2f}" if nr else "N/A")
st.write(f"**S/R assessment:** {sr_analysis['summary']}")
if ns:
    st.write(f"Support strength: {ns['score']}/100 | touches: {ns['touches']} | MTF: {', '.join(ns['timeframes'])} | distance: {ns['distance_pct']:.3f}% | role: {ns['role']}")
if nr:
    st.write(f"Resistance strength: {nr['score']}/100 | touches: {nr['touches']} | MTF: {', '.join(nr['timeframes'])} | distance: {nr['distance_pct']:.3f}% | role: {nr['role']}")
if sr_analysis["zones"]:
    st.dataframe(pd.DataFrame(sr_analysis["zones"]), use_container_width=True, hide_index=True)
else:
    st.info("Not enough confirmed pivots to build reliable S/R zones.")

# ---------------- RISK LEVELS ----------------
st.divider()
st.subheader("Risk Management")
if risk_levels:
    r1, r2, r3, r4 = st.columns(4)
    r1.metric("ENTRY", f"${risk_levels['entry']:,.2f}")
    r2.metric("STOP LOSS", f"${risk_levels['stop']:,.2f}")
    r3.metric("TP1", f"${risk_levels['tp1']:,.2f}")
    r4.metric("TP2", f"${risk_levels['tp2']:,.2f}")
    st.write(f"TP3: ${risk_levels['tp3']:,.2f}")
else:
    st.info("No directional risk levels.")

# ---------------- MARKET STRUCTURE ----------------
st.divider()
st.subheader("Market Structure")
m1, m2, m3 = st.columns(3)
m1.metric("Direction", structure["direction"])
m2.metric("Strength", structure["strength"])
m3.metric("Efficiency", f"{regime['efficiency']:.3f}")
st.write(f"Legacy swing support: {structure['support']}")
st.write(f"Legacy swing resistance: {structure['resistance']}")

# ---------------- MTF ----------------
st.divider()
st.subheader("Multi-Timeframe Intelligence")
mtf_rows = [{"Timeframe": tf, "Direction": item["direction"], "Structure": item["structure"], "RSI": item["rsi"], "ROC": item["roc"]} for tf, item in mtf.items()]
st.dataframe(pd.DataFrame(mtf_rows), use_container_width=True, hide_index=True)

# ---------------- ORDER FLOW ----------------
st.divider()
st.subheader("Institutional Order Flow")
f1, f2, f3 = st.columns(3)
f1.metric("BUY VOLUME", f"{delta['buy_volume']:.4f}")
f2.metric("SELL VOLUME", f"{delta['sell_volume']:.4f}")
f3.metric("DELTA", f"{delta['delta_pct']:.2f}%")
st.write(f"Delta classification: {delta['classification']}")
st.caption("Delta uses the latest 500 OKX trades, not a fixed-duration aggregation.")
st.subheader("Position Flow")
for event in flow: st.write(f"- {event}")
st.write(f"Open Interest: {oi['classification']}")
st.write(f"OI change: {oi['change_pct']}")

# ---------------- LIQUIDITY ----------------
st.divider()
st.subheader("Liquidity Intelligence")
l1, l2, l3 = st.columns(3)
l1.metric("Order Book Imbalance", f"{book['imbalance'] * 100:.2f}%")
l2.metric("Order Book", book["classification"])
l3.metric("Liquidity Sweep", liquidity["sweep"])
st.write(f"Breakout: {liquidity['breakout']}")
st.write(f"Breakout / Retest: {retest}")
st.write(f"Bid wall: {book['bid_wall']}")
st.write(f"Ask wall: {book['ask_wall']}")
st.caption("Order book is a current snapshot; no historical heatmap or verified liquidation-level feed is available.")

# ---------------- FUNDING ----------------
st.divider()
st.subheader("Funding Intelligence")
st.write(funding["classification"])

# ---------------- REVERSALS ----------------
st.divider()
st.subheader("Reversal Intelligence")
for item in reversals: st.write(f"- {item}")
st.subheader("Divergences")
if divergences:
    for item in divergences: st.warning(item)
else:
    st.info("No divergence detected.")

# ---------------- HISTORICAL PATTERNS ----------------
st.divider()
st.subheader("Historical Pattern Intelligence")
p1, p2, p3, p4 = st.columns(4)
p1.metric("Similar Patterns", patterns["matches"])
p2.metric("Bullish Rate", f"{patterns['bullish_rate']:.1f}%" if patterns["bullish_rate"] is not None else "N/A")
p3.metric("Bearish Rate", f"{patterns['bearish_rate']:.1f}%" if patterns["bearish_rate"] is not None else "N/A")
p4.metric("Average Return", f"{patterns['average_return']:.3f}%" if patterns["average_return"] is not None else "N/A")
st.caption("Historical similarity is descriptive, not a calibrated prediction.")

# ---------------- NEWS ----------------
st.divider()
st.subheader("News Intelligence")
n1, n2 = st.columns(2)
n1.metric("News Sentiment", news_analysis["classification"])
n2.metric("Sentiment Score", f"{news_analysis['score']:.2f}")
if news_analysis["articles"]:
    for article in news_analysis["articles"]:
        stance = article.get("stance", "Neutral")
        badge_color = "#2e7d32" if stance == "Bullish" else "#c62828" if stance == "Bearish" else "#607d8b"
        st.markdown(
            f"<div style='margin-bottom:4px;'><span style='color:#9aa7b8;font-size:0.85em;'>{article['published']}</span> "
            f"<span style='color:{badge_color};font-weight:700;margin-left:6px;'>{stance}</span></div>",
            unsafe_allow_html=True
        )
        with st.expander(f"{article['source']} | {article['title']}"):
            st.write(article["description"])
            st.write(f"Sentiment: {article['sentiment']} ({stance})")
            st.write(f"Published: {article['published']}")
            if article["link"]: st.markdown(f"[Open article]({article['link']})")
else:
    st.warning("No news articles available.")
if news_errors:
    st.warning("RSS source errors")
    for error in news_errors: st.write(f"- {error}")

# ---------------- MACRO ----------------
st.divider()
st.subheader("Macro Intelligence & Live Economic Calendar (Forex Factory)")
st.info(macro["message"])
st.caption(f"Source: {macro_calendar.get('source', 'Forex Factory')} | Mode: {macro_calendar.get('mode')} | Last fetch: {macro_calendar.get('fetched_at')}")
if macro_calendar.get("feed_errors"):
    with st.expander("Calendar feed warnings"):
        for feed_error in macro_calendar["feed_errors"]: st.write(f"- {feed_error}")
mc1, mc2, mc3, mc4 = st.columns(4)
mc1.metric("Macro Signal Status", macro_signal["status"])
mc2.metric("Macro Risk Window", macro_calendar["risk_window"])
next_hi, next_min = macro_calendar["next_high_impact"], macro_calendar.get("next_minor_impact")
mc3.metric("Next High-Impact Event", f"{next_hi['currency']} {next_hi['event']} ({next_hi['hours_until']}h)" if next_hi else "None in window")
mc4.metric("Next Medium/Low Event", f"[{next_min['impact']}] {next_min['currency']} {next_min['event']} ({next_min['hours_until']}h)" if next_min else "None in window")
last_ev = macro_calendar.get("last_released_event")
if last_ev:
    ev_stance = last_ev.get("stance", "Neutral")
    ev_color = "#2e7d32" if ev_stance == "Bullish" else "#c62828" if ev_stance == "Bearish" else "#607d8b"
    st.markdown(
        f"<div style='margin:6px 0 8px 0;padding:8px 12px;border-left:3px solid {ev_color};background:#111a27;border-radius:6px;'>"
        f"<span style='color:#9aa7b8;font-size:0.85em;'>{last_ev['date']} {last_ev['time_utc']} UTC</span> "
        f"<span style='color:{ev_color};font-weight:700;margin-left:6px;'>{ev_stance}</span><br>"
        f"<span style='font-weight:600;'>{last_ev['currency']} – {last_ev['event']}</span> "
        f"<span style='color:#9aa7b8;font-size:0.85em;'>(Actual: {last_ev.get('actual') or '-'} | Forecast: {last_ev.get('forecast') or '-'} | Previous: {last_ev.get('previous') or '-'})</span>"
        f"</div>",
        unsafe_allow_html=True
    )
st.write(f"**Macro directional tilt:** {macro_signal['directional_score']:+.2f}")
st.write(f"**Macro signal note:** {macro_signal['reason']}")
if macro_calendar["events"]:
    macro_table = pd.DataFrame(macro_calendar["events"])
    display_columns = [c for c in ["date", "time_utc", "currency", "impact", "event", "stance", "actual", "forecast", "previous", "surprise", "source", "hours_until"] if c in macro_table.columns]
    st.dataframe(macro_table[display_columns], use_container_width=True, hide_index=True)
else:
    st.warning("No calendar events returned for the selected date window. This is not interpreted as proof that no economic events exist.")

with st.expander("Add / Manage Custom Macro Events (Manual SQLite Events)"):
    with st.form("add_macro_event_form", clear_on_submit=True):
        f_col1, f_col2, f_col3, f_col4, f_col5 = st.columns(5)
        ev_d = f_col1.date_input("Date (UTC)", value=now_utc().date())
        ev_t = f_col2.text_input("Time UTC (HH:MM)", value="13:30")
        ev_c = f_col3.selectbox("Currency", ["USD", "EUR", "JPY", "BTC", "GLOBAL"])
        ev_i = f_col4.selectbox("Impact", ["HIGH", "MEDIUM", "LOW"])
        ev_l = f_col5.selectbox("Macro Lean", ["NEUTRAL", "HAWKISH_RISK", "VOLATILITY_RISK", "DOVISH_RELIEF", "BULLISH_BTC", "BEARISH_BTC"])
        ev_name = st.text_input("Event Name", placeholder="e.g. Fed Chair Speech / ETF Flow / CPI")
        ev_notes = st.text_input("Notes", placeholder="Optional note")
        submitted_macro = st.form_submit_button("Add Macro Event")
        if submitted_macro and ev_name.strip():
            try:
                datetime.strptime(ev_t.strip(), "%H:%M")
                add_macro_event(ev_d.isoformat(), ev_t.strip(), ev_c, ev_name.strip(), ev_i, ev_l, ev_notes.strip())
                st.rerun()
            except ValueError:
                st.error("Time must use HH:MM format.")
    custom_db_df = database_read("SELECT id,event_date,time_utc,currency,event_name,impact,macro_lean,notes FROM macro_calendar ORDER BY id DESC")
    if not custom_db_df.empty:
        del_id = st.selectbox("Delete custom event by ID", options=custom_db_df["id"].tolist())
        if st.button("Delete Selected Event"):
            delete_macro_event(del_id)
            st.rerun()

# ---------------- GROQ ----------------
st.divider()
st.subheader("GROQ AI INTELLIGENCE")
if not GROQ_API_KEY:
    st.error("Groq API key not found. Check .streamlit/secrets.toml.")
elif not ai_enabled:
    st.info("Groq AI is disabled.")
else:
    if st.session_state.last_ai_error: st.error(st.session_state.last_ai_error)
    if ai_result:
        st.success("Last successful AI analysis")
        a1, a2 = st.columns(2)
        a1.metric("AI Direction", ai_result.get("direction", "UNKNOWN"))
        a2.metric("AI Confidence", ai_result.get("confidence", "N/A"))
        st.write("**Summary:** " + ai_result.get("summary", "No summary."))
        st.write("**Technical:** " + ai_result.get("technical_analysis", ""))
        st.write("**Order Flow:** " + ai_result.get("order_flow_analysis", ""))
        st.write("**Liquidity:** " + ai_result.get("liquidity_analysis", ""))
        st.write("**Support / Resistance:** " + ai_result.get("support_resistance_analysis", ""))
        st.write("**Macro Analysis:** " + ai_result.get("macro_analysis", ""))
        st.write("**News Impact:** " + ai_result.get("news_impact", ""))
        st.write("**Risk:** " + ai_result.get("risk", ""))
        st.write("**Invalidation:** " + ai_result.get("invalidation", ""))
        with st.expander("Full AI response"): st.json(ai_result)
    else:
        st.info("No successful AI analysis yet.")
    st.caption(f"AI trigger: {trigger_reason}")
    if st.session_state.last_ai_time:
        st.caption("Last successful AI update: " + datetime.fromtimestamp(st.session_state.last_ai_time, timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"))

# ---------------- AGREEMENT ----------------
st.divider()
st.subheader("AI / Macro / Technical Agreement")
if ai_result:
    st.write(f"Technical: {entry['direction']}")
    st.write(f"AI: {ai_result['direction']}")
    st.write(f"Macro Signal: {macro_signal['status']} ({macro_signal['bias']})")
    if ai_gate["allowed"]:
        st.success(ai_gate["reason"])
    else:
        st.error(ai_gate["reason"])
else:
    st.warning("No successful AI validation. New paper trades are blocked.")

# ---------------- PAPER TRADING DASHBOARD ----------------
st.divider()
st.subheader("Paper Trading Performance")
performance = get_performance()
equity = get_paper_equity()
q1, q2, q3, q4 = st.columns(4)
q1.metric("Equity", f"{equity:,.2f} USDT")
q2.metric("Closed Trades", performance["count"])
q3.metric("Win Rate", f"{performance['win_rate']:.2f}%")
q4.metric("Net PnL", f"{performance['net_pnl']:.2f} USDT")
st.metric("Profit Factor", f"{performance['profit_factor']:.3f}" if np.isfinite(performance["profit_factor"]) else "Infinite")
open_trade = get_open_trade()
if open_trade:
    st.subheader("Open Paper Position")
    st.dataframe(pd.DataFrame([open_trade]), use_container_width=True, hide_index=True)
else:
    st.info("No open paper position.")
all_trades = database_read("SELECT * FROM paper_trades ORDER BY id DESC")
if not all_trades.empty:
    st.subheader("Paper Trade History")
    st.dataframe(all_trades, use_container_width=True, hide_index=True)
    st.download_button("Download Paper Trade CSV", all_trades.to_csv(index=False).encode("utf-8"), file_name="saliga_paper_trades.csv", mime="text/csv")

# ---------------- SIGNAL JOURNAL ----------------
st.divider()
st.subheader("Signal Journal")
signal_history = database_read("SELECT * FROM signal_history ORDER BY id DESC LIMIT 500")
if not signal_history.empty:
    st.dataframe(signal_history, use_container_width=True, hide_index=True)
    st.download_button("Download Signal Journal", signal_history.to_csv(index=False).encode("utf-8"), file_name="saliga_signal_history.csv", mime="text/csv")

# ---------------- EVENT JOURNAL ----------------
st.divider()
st.subheader("System Event Journal")
event_history = database_read("SELECT * FROM event_log ORDER BY id DESC LIMIT 300")
if not event_history.empty:
    st.dataframe(event_history, use_container_width=True, hide_index=True)

# ---------------- SNAPSHOT HISTORY ----------------
st.divider()
st.subheader("Market Snapshot History")
snapshot_history = database_read("SELECT * FROM market_snapshots ORDER BY id DESC LIMIT 200")
if not snapshot_history.empty:
    st.dataframe(snapshot_history, use_container_width=True, hide_index=True)

# ---------------- CHART ----------------
st.divider()
st.subheader("BTC Chart")
try:
    import plotly.graph_objects as go
    chart_df = df1h if not df1h.empty else main_df
    if not chart_df.empty:
        chart = go.Figure()
        chart.add_trace(go.Candlestick(
            x=pd.to_datetime(chart_df["timestamp"], unit="ms"),
            open=chart_df["open"], high=chart_df["high"], low=chart_df["low"],
            close=chart_df["close"], name="BTC"
        ))
        x0 = pd.to_datetime(chart_df["timestamp"].iloc[0], unit="ms")
        x1 = pd.to_datetime(chart_df["timestamp"].iloc[-1], unit="ms")
        for zone in sr_analysis.get("zones", []):
            color = "rgba(0,180,100,0.10)" if zone["type"] == "SUPPORT" else "rgba(220,70,70,0.10)"
            chart.add_shape(
                type="rect", xref="x", yref="y", x0=x0, x1=x1,
                y0=zone["lower"], y1=zone["upper"],
                fillcolor=color, line={"width": 0}, layer="below"
            )
            chart.add_annotation(
                x=x1, y=zone["center"], text=f"{zone['type']} {zone['score']:.0f}",
                showarrow=False, xanchor="right", font={"size": 9}
            )
        if structure["support"] is not None:
            chart.add_hline(y=structure["support"], line_dash="dot", annotation_text="Legacy Support")
        if structure["resistance"] is not None:
            chart.add_hline(y=structure["resistance"], line_dash="dot", annotation_text="Legacy Resistance")
        chart.update_layout(template="plotly_dark", height=650, xaxis_rangeslider_visible=False, title="BTC-USDT-SWAP | Multi-Timeframe S/R Zones")
        st.plotly_chart(chart, use_container_width=True)
except Exception as error:
    st.warning(f"Chart error: {error}")

# ---------------- SYSTEM HEALTH ----------------
st.divider()
st.subheader("System Health")
health_rows = [{"Timeframe": tf, "Available": not df.empty, "Candles": len(df)} for tf, df in timeframe_data.items()]
st.dataframe(pd.DataFrame(health_rows), use_container_width=True, hide_index=True)
if errors:
    st.warning("Some market data sources returned errors.")
    for error in errors: st.write(f"- {error}")
else:
    st.success("No market data errors detected.")
st.write(f"Last refresh: {st.session_state.last_refresh}")

# ---------------- AUTO REFRESH ----------------
if auto_refresh:
    try:
        from streamlit_autorefresh import st_autorefresh
        st_autorefresh(interval=refresh_seconds * 1000, key="saliga_auto_refresh")
    except ImportError:
        st.warning("Install streamlit-autorefresh for automatic refresh.")

# ================================================================
# END OF SALIGA AI PRO 31.2
# ================================================================
