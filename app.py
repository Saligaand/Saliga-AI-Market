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
st.title("SALIGA AI – Market Intelligence + AI Sentiment")
st.caption("Kriptopiaci elemző MVP • Technikai + Derivatív + AI-alapú Hírhangulat")

# --- Settings & API Keys ---
with st.sidebar:
    st.header("Beállítások")
    exchange_choice = st.selectbox("Adatforrás tőzsde", ["OKX", "Bybit"], index=0)
    symbol = st.selectbox("Piac", ["BTC/USDT", "SOL/USDT", "ETH/USDT"], index=0)
    refresh = st.selectbox("Frissítés", ["Kézi", "30 mp", "60 mp", "5 perc"], index=0)
    show_chart = st.checkbox("Gyertyagrafikon", value=True)
    
    st.markdown("---")
    st.subheader("AI Hírhangulat Beállítás")
    groq_api_key = st.text_input("Groq API Kulcs (opcionális)", type="password", help="Ingyenesen igényelhető a console.groq.com oldalon. Ha nincs megadva, kulcsszó-alapú elemzés fut.")
    st.caption("Adatforrás: Cointelegraph RSS + LLM elemzés.")

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
    """RSS hírek lekérése a piaci hangulat felméréséhez"""
    try:
        feed = feedparser.parse("https://cointelegraph.com/rss")
        news_items = []
        for entry in feed.entries[:6]:
            news_items.append({"title": entry.title, "link": entry.link})
        return news_items
    except Exception:
        return []

def analyze_news_ai(news_items, api_key):
    """AI alapú hangulatelemzés Groq API-n keresztül (Llama 3 modell)"""
    titles = [item["title"] for item in news_items]
    titles_str = "\n".join([f"- {t}" for t in titles])
    
    # Ha nincs API kulcs, visszatérünk egy alapértelmezett semleges értékkel és kulcsszavas módszerrel
    if not api_key:
        return analyze_news_sentiment_fallback(news_items), "Kulcsszó-alapú (Nincs AI kulcs megadva)"

    prompt = f"""
    Elemezd az alábbi kriptopiaci hírlevelek címsorait a piaci hangulat szempontjából. 
    Határozz meg egyetlen számot -2.0 (extrém medvés/short) és +2.0 (extrém bikás/long) között, ami a globális piaci hatást mutatja.
    
    Címsorok:
    {titles_str}
    
    Válaszd ki pontosan a számot, majd rövid indoklás magyarul. Formátum: [SZÁM] | [Indoklás]
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
        
        # Elemzés kiszedése a válaszból
        parts = content.split("|")
        score = float(parts[0].strip().replace("[", "").replace("]", ""))
        explanation = parts[1].strip() if len(parts) > 1 else "AI elemzés sikeres."
        return max(min(score, 2.0), -2.0), explanation
    except Exception as e:
        return analyze_news_sentiment_fallback(news_items), f"AI hiba ({e}), fallback használva."

def analyze_news_sentiment_fallback(news_items):
    bullish_words = ["surge", "rally", "bull", "jump", "growth", "high", "approval", "etf", "gain", "up", "record", "bullish"]
    bearish_words = ["drop", "crash", "bear", "fall", "dump", "loss", "risk", "hack", "ban", "down", "low", "fear", "sec"]
    score = 0.0
    for item in news_items:
        title_lower = item["title"].lower()
        for w in bullish_words:
            if w in title_lower: score += 0.25
        for w in bearish_words:
            if w in title_lower: score -= 0.25
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
                result["oi_change"] = ((newest - older) / older * 100) if older else None
        except Exception:
            pass
            
        try:
            r = requests.get(
                "https://api.bybit.com/v5/market/funding/history",
                params={"category": "linear", "symbol": pair, "limit": 1},
                timeout=8
            )
            j = r.json()
            rows = j.get("result", {}).get("list", [])
            if rows:
                result["funding"] = float(rows[0]["fundingRate"]) * 100
        except Exception:
            pass
            
    elif exchange_name == "OKX":
        try:
            okx_inst = f"{symbol.split('/')[0]}-USDT-SWAP"
            r = requests.get(
                "https://www.okx.com/api/v5/public/open-interest",
                params={"instType": "SWAP", "instId": okx_inst},
                timeout=8
            )
            j = r.json()
            data = j.get("data", [])
            if data:
                result["oi_now"] = float(data[0]["oi"])
                result["oi_change"] = 0.0 
        except Exception:
            pass
            
        try:
            okx_inst = f"{symbol.split('/')[0]}-USDT-SWAP"
            r = requests.get(
                "https://www.okx.com/api/v5/public/funding-rate",
                params={"instId": okx_inst},
                timeout=8
            )
            j = r.json()
            data = j.get("data", [])
            if data:
                result["funding"] = float(data[0]["fundingRate"]) * 100
        except Exception:
            pass
            
    return result

def add_indicators(df):
    x = df.copy()
    x["sma20"] = x["close"].rolling(20).mean()
    x["sma50"] = x["close"].rolling(50).mean()
    delta = x["close"].diff()
    gain = delta.clip(lower=0).rolling(14).mean()
    loss = (-delta.clip(upper=0)).rolling(14).mean()
    rs = gain / loss.replace(0, np.nan)
    x["rsi"] = 100 - (100 / (1 + rs))
    prev_close = x["close"].shift(1)
    tr = pd.concat([
        x["high"] - x["low"],
        (x["high"] - prev_close).abs(),
        (x["low"] - prev_close).abs()
    ], axis=1).max(axis=1)
    x["atr"] = tr.rolling(14).mean()
    x["est_delta"] = np.where(x["close"] >= x["open"], x["volume"], -x["volume"])
    x["delta_sum"] = x["est_delta"].rolling(20).sum()
    x["vol_avg"] = x["volume"].rolling(20).mean()
    return x

def structure_score(df):
    x = add_indicators(df).dropna()
    if len(x) < 5:
        return 0, {}
    last = x.iloc[-1]
    prev = x.iloc[-2]
    score = 0
    details = {}
    if last.close > last.sma20: score += 1
    else: score -= 1
    if last.sma20 > last.sma50: score += 1
    else: score -= 1
    if last.close > prev.high: score += 1
    elif last.close < prev.low: score -= 1
    if last.rsi >= 55: score += 1
    elif last.rsi <= 45: score -= 1
    if last.delta_sum > 0: score += 1
    elif last.delta_sum < 0: score -= 1
    if last.volume > last.vol_avg * 1.2:
        details["volume"] = "Átlag feletti"
    else:
        details["volume"] = "Átlagos/alacsony"
    details.update({
        "RSI": round(float(last.rsi), 1),
        "SMA20": float(last.sma20),
        "SMA50": float(last.sma50),
        "Becsült delta": "Pozitív" if last.delta_sum > 0 else "Negatív",
        "ATR": float(last.atr),
        "score": score
    })
    return score, details

def label(score):
    if score >= 3: return "LONG irányú fölény", "success"
    if score >= 1: return "Enyhe LONG fölény", "success"
    if score <= -3: return "SHORT irányú fölény", "error"
    if score <= -1: return "Enyhe SHORT fölény", "error"
    return "Semleges / kivárás", "warning"

@st.cache_data(ttl=15)
def load_all(exc, sym):
    return {tf: get_ohlcv(exc, sym, tf, 300) for tf in TIMEFRAMES}

try:
    with st.spinner(f"Piaci adatok lekérése és AI hír-elemzés ({exchange_choice})..."):
        frames = load_all(exchange_choice, symbol)
        deriv = get_derivatives_data(exchange_choice, symbol)
        news_items = get_crypto_news()
        
        # AI vagy kulcsszavas elemzés hívása
        if groq_api_key:
            news_score, news_explanation = analyze_news_ai(news_items, groq_api_key)
        else:
            news_score = analyze_news_sentiment_fallback(news_items)
            news_explanation = "Kulcsszó-alapú elemzés (adj meg Groq API kulcsot az AI-hoz)."

    analyzed = {tf: add_indicators(df) for tf, df in frames.items()}
    scores = {}
    details = {}
    for tf, df in frames.items():
        scores[tf], details[tf] = structure_score(df)

    # Technikai multi-timeframe súlyozás
    tech_weighted = (
        scores["4h"] * 0.30 +
        scores["1h"] * 0.30 +
        scores["15m"] * 0.20 +
        scores["5m"] * 0.12 +
        scores["1m"] * 0.08
    )
    
    # Végső pontszám: 70% Technikai + 30% AI Hírhangulat
    weighted = tech_weighted * 0.70 + news_score * 0.30

    oi = deriv.get("oi_change")
    funding = deriv.get("funding")
    oi_note = "Nincs adat"
    if oi is not None:
        if exchange_choice == "Bybit":
            oi_note = f"{oi:+.3f}% az utolsó két 5 perces mérés között"
        else:
            oi_note = f"Aktuális OI: {deriv.get('oi_now', 0):,.0f}"

    if funding is not None:
        if funding > 0.03: weighted -= 0.15
        elif funding < -0.03: weighted += 0.15

    direction, status = label(weighted)
    confidence = min(90, 50 + abs(weighted) * 8)
    price = float(frames["1m"].iloc[-1]["close"])

    a, b, c = st.columns(3)
    a.metric("Aktuális ár", f"{price:,.4f} USDT")
    b.metric("Összesített komplex pontszám", f"{weighted:+.2f} / 5")
    c.metric("Jelzés erőssége (Tech + AI Hírek)", f"{confidence:.0f}%")

    if status == "success": st.success(direction)
    elif status == "error": st.error(direction)
    else: st.warning(direction)

    # Két oszlop: Balra a Technikai idősíkok, Jobbra az AI Hírhangulat panel
    col_left, col_right = st.columns([1.2, 1])

    with col_left:
        st.subheader("Idősíkok összehasonlítása")
        rows = []
        for tf in ["4h", "1h", "15m", "5m", "1m"]:
            sc = scores[tf]
            d = details[tf]
            rows.append({
                "Idősík": tf,
                "Pontszám": sc,
                "Irány": "LONG" if sc > 0 else ("SHORT" if sc < 0 else "Semleges"),
                "RSI": d.get("RSI"),
                "Becsült delta": d.get("Becsült delta")
            })
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

    with col_right:
        st.subheader("AI Piaci Hangulat & Hírek")
        if news_items:
            for item in news_items[:3]:
                st.markdown(f"- [{item['title']}]({item['link']})")
            st.info(f"**AI Értékelés:** {news_explanation} (Pontszám: {news_score:+.2f})")
        else:
            st.write("Nem sikerült híreket lekérni.")

    st.subheader(f"Derivatív adatok – {exchange_choice}")
    d1, d2 = st.columns(2)
    d1.metric("Open Interest", oi_note)
    d2.metric("Funding rate", f"{funding:.5f}%" if funding is not None else "Nincs adat")

    if show_chart:
        st.subheader("1 órás chart")
        chart = analyzed["1h"].tail(180)
        fig = make_subplots(rows=2, cols=1, shared_xaxes=True,
                            vertical_spacing=0.04, row_heights=[0.72, 0.28])
        fig.add_trace(go.Candlestick(
            x=chart["time"], open=chart["open"], high=chart["high"],
            low=chart["low"], close=chart["close"], name=symbol
        ), row=1, col=1)
        fig.add_trace(go.Scatter(x=chart["time"], y=chart["sma20"], name="SMA20"), row=1, col=1)
        fig.add_trace(go.Scatter(x=chart["time"], y=chart["sma50"], name="SMA50"), row=1, col=1)
        fig.add_trace(go.Bar(x=chart["time"], y=chart["est_delta"], name="Becsült delta"), row=2, col=1)
        fig.update_layout(height=600, xaxis_rangeslider_visible=False)
        st.plotly_chart(fig, use_container_width=True)

except Exception as e:
    st.error(f"Adatlekérési hiba: {e}")
    st.write("Ellenőrizd az internetkapcsolatot vagy az API kulcsot.")

if refresh != "Kézi":
    seconds = {"30 mp": 30, "60 mp": 60, "5 perc": 300}[refresh]
    time.sleep(seconds)
    st.rerun()
