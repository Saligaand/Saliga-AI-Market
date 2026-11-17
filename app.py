import time
import requests
import numpy as np
import pandas as pd
import streamlit as st
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import feedparser
import ccxt

st.set_page_config(page_title="SALIGA AI Market Intelligence", layout="wide")
st.title("SALIGA AI – Market Intelligence + AI Sentiment Analízis")
st.caption("Kriptopiaci elemző MVP • Technikai + Derivatív + AI Hír-kalkuláció")

# --- Settings & API Keys ---
with st.sidebar:
    st.header("Beállítások")
    exchange_choice = st.selectbox("Adatforrás tőzsde", ["OKX", "Bybit"], index=0)
    symbol = st.selectbox("Piac", ["BTC/USDT", "SOL/USDT", "ETH/USDT"], index=0)
    refresh = st.selectbox("Frissítés", ["Kézi", "30 mp", "60 mp", "5 perc"], index=0)
    show_chart = st.checkbox("Gyertyagrafikon", value=True)
    
    st.markdown("---")
    st.subheader("AI Hírhangulat Beállítás")
    groq_api_key = st.text_input("Groq API Kulcs", type="password", help="Ingyenesen igényelhető a console.groq.com oldalon.")
    st.caption("A hírek hatása közvetlenül módosítja a végső pontszámot.")

TIMEFRAMES = {
    "1m": "1m", "5m": "5m", "15m": "15m", "1h": "1h", "4h": "4h"
}

@st.cache_data(ttl=15)
def get_ohlcv(exchange_name, symbol, timeframe, limit=300):
    if exchange_name == "OKX":
        ex = ccxt.okx({"enableRateLimits": True})
    else:
        ex = ccxt.bybit({"enableRateLimits": True, "options": {"defaultType": "future"}})
        
    data = ex.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
    df = pd.DataFrame(data, columns=["time", "open", "high", "low", "close", "volume"])
    df["time"] = pd.to_datetime(df["time"], unit="ms", utc=True)
    return df

@st.cache_data(ttl=300)
def get_crypto_news():
    try:
        feed = feedparser.parse("https://cointelegraph.com/rss")
        news_items = []
        for entry in feed.entries[:5]:
            news_items.append({"title": entry.title, "link": entry.link})
        return news_items
    except Exception:
        return []

def analyze_news_ai(news_items, api_key):
    titles = [item["title"] for item in news_items]
    titles_str = "\n".join([f"- {t}" for t in titles])
    
    if not api_key:
        return analyze_news_sentiment_fallback(news_items), "Nincs megadva Groq API kulcs (kulcsszavas fallback használva)."

    prompt = f"""
    Elemezd az alábbi kriptopiaci hírek címsorait a piaci hatás szempontjából.
    Adj meg egy pontos számot -2.0 (extrém medvés / short hatás) és +2.0 (extrém bikás / long hatás) között.
    Emellett mondd meg, hogy a hírek összességében hogyan tolják el a piacot.
    
    Címsorok:
    {titles_str}
    
    Válasz formátum pontosan ez legyen: [SZÁM] | [Rövid elemzés, hogy miért ez a hatás a long/short irányra]
    """
    
    try:
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }
        payload = {
            "model": "llama3-8b-8192",
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.1
        }
        response = requests.post("https://api.groq.com/openai/v1/chat/completions", json=payload, headers=headers, timeout=10)
        res_json = response.json()
        content = res_json["choices"][0]["message"]["content"]
        
        parts = content.split("|")
        score = float(parts[0].strip().replace("[", "").replace("]", ""))
        explanation = parts[1].strip() if len(parts) > 1 else "AI értékelés kész."
        return max(min(score, 2.0), -2.0), explanation
    except Exception as e:
        return analyze_news_sentiment_fallback(news_items), f"AI hiba ({e}), kulcsszavas módszerre váltva."

def analyze_news_sentiment_fallback(news_items):
    bullish_words = ["surge", "rally", "bull", "jump", "growth", "high", "approval", "etf", "gain", "up", "record", "bullish"]
    bearish_words = ["drop", "crash", "bear", "fall", "dump", "loss", "risk", "hack", "ban", "down", "low", "fear", "sec"]
    score = 0.0
    for item in news_items:
        title_lower = item["title"].lower()
        for w in bullish_words:
            if w in title_lower: score += 0.3
        for w in bearish_words:
            if w in title_lower: score -= 0.3
    return max(min(score, 2.0), -2.0)

@st.cache_data(ttl=30)
def get_derivatives_data(exchange_name, symbol):
    pair = symbol.replace("/", "")
    result = {"oi_change": None, "funding": None, "oi_now": None}
    
    if exchange_name == "Bybit":
        try:
            r = requests.get(
                "https://api.bybit.com/v5/market/open-interest",
                params={"category": "linear", "symbol": pair, "intervalTime": "5min", "limit": 2},
                timeout=8
            )
            j = r.json()
            rows = j.get("result", {}).get("list", [])
            if len(rows) >= 2:
                newest = float(rows[0]["openInterest"])
                older = float(rows[1]["openInterest"])
                result["oi_now"] = newest
                result["oi_change"] = ((newest - older) / older * 1
