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
st.title("SALIGA AI – Intézményi Market Intelligence + ATR Stop/TP & Auto Telegram")
st.caption("Kriptopiaci elemző MVP • Technikai + ATR Kockázatkezelés + OI + AI Hír + Minta + Auto Riasztás")

# --- Settings & API Keys ---
with st.sidebar:
    st.header("Beállítások")
    exchange_choice = st.selectbox("Adatforrás tőzsde", ["OKX", "Bybit"], index=0)
    symbol = st.selectbox("Piac", ["BTC/USDT", "SOL/USDT", "ETH/USDT"], index=0)
    show_chart = st.checkbox("Gyertyagrafikon", value=True)
    
    st.markdown("---")
    st.subheader("AI Hírhangulat Beállítás")
    groq_api_key = st.text_input("Groq API Kulcs", type="password", help="Ingyenesen igényelhető a console.groq.com oldalon.")
    
    st.markdown("---")
    st.subheader("Telegram Riasztás Beállítások")
    telegram_token = st.text_input("Telegram Bot Token", type="password", help="BotFather-től kapott HTTP API token.")
    telegram_chat_id = st.text_input("Telegram Chat ID", type="password", help="A cél chat vagy csatorna ID-ja.")
    
    auto_alert_enabled = st.checkbox("Automatikus riasztás bekapcsolva", value=False)
    alert_threshold = st.slider("Min. Erősség küszöb (%)", min_value=50, max_value=90, value=75, step=5)
    
    send_alert_btn = st.button("Riasztás küldése Telegramra most")

TIMEFRAMES = {
    "1m": "1m", "5m": "5m", "15m": "15m", "1h": "1h", "4h": "4h"
}

def get_timeframe_ms(tf):
    mapping = {"1m": 60000, "5m": 300000, "15m": 900000, "1h": 3600000, "4h": 14400000}
    return mapping.get(tf, 3600000)

@st.cache_data(ttl=60)
def get_ohlcv(exchange_name, symbol, timeframe, target_limit=5000):
    if exchange_name == "OKX":
        ex = ccxt.okx({"enableRateLimits": True})
    else:
        ex = ccxt.bybit({"enableRateLimits": True, "options": {"defaultType": "future"}})
        
    all_data = []
    since = None
    batch_size = 1000
    
    while len(all_data) < target_limit:
        try:
            data = ex.fetch_ohlcv(symbol, timeframe=timeframe, limit=batch_size, since=since)
            if not data:
                break
            if all_data and data[0][0] == all_data[0][0]:
                break
            
            all_data = data + all_data
            since = data[0][0] - (batch_size * get_timeframe_ms(timeframe))
            time.sleep(0.1)
            
            if len(data) < batch_size:
                break
        except Exception:
            break
            
    df = pd.DataFrame(all_data[-target_limit:], columns=["time", "open", "high", "low", "close", "volume"])
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

def pattern_matching_analysis(df, window_size=20):
    if len(df) < window_size * 5:
        return 0.0, "Nincs elég múltbeli adat a mintakereséshez."
    
    closes = df['close'].values
    current_window = closes[-window_size:]
    
    curr_norm = (current_window - np.mean(current_window)) / (np.std(current_window) + 1e-8)
    
    matches = []
    for i in range(len(closes) - window_size * 2):
        hist_window = closes[i:i+window_size]
        hist_norm = (hist_window - np.mean(hist_window)) / (np.std(hist_window) + 1e-8)
        
        dist = np.linalg.norm(curr_norm - hist_norm)
        
        future_idx = i + window_size
        if future_idx + 5 < len(closes):
            future_return = (closes[future_idx + 5] - closes[future_idx]) / closes[future_idx]
            matches.append((dist, future_return))
            
    if not matches:
        return 0.0, "Nem talált elegendő hasonló múltbeli mintát."
        
    matches.sort(key=lambda x: x[0])
    top_matches = matches[:5]
    
    avg_future_return = np.mean([m[1] for m in top_matches])
    bullish_count = sum(1 for m in top_matches if m[1] > 0)
    
    pattern_score = (bullish_count / len(top_matches)) * 4.0 - 2.0 
    explanation = f"A leginkább hasonló 5 múltbeli minta közül {bullish_count} esetben váltotta fel emelkedés (átlagos hozam: {avg_future_return*100:.2f}%)."
    return max(min(pattern_score, 2.0), -2.0), explanation

@st.cache_data(ttl=30)
def get_derivatives_data(exchange_name, symbol):
    pair = symbol.replace("/", "")
    result = {"oi_change": None, "funding": None, "oi_trend": "Semleges"}
    
    if exchange_name == "Bybit":
        try:
            r = requests.get(
                "https://api.bybit.com/v5/market/open-interest",
                params={"category": "linear", "symbol": pair, "intervalTime": "5min", "limit": 3},
                timeout=8
            )
            j = r.json()
            rows = j.get("result", {}).get("list", [])
            if len(rows) >= 2:
                newest = float(rows[0]["openInterest"])
                older = float(rows[1]["openInterest"])
                oi_diff = newest - older
                result["oi_change"] = (oi_diff / older) * 100
                if oi_diff > 0:
                    result["oi_trend"] = "Új tőke áramlik be (Növekvő OI)"
                else:
                    result["oi_trend"] = "Pozíciók leépülése / Zárás (Csökkenő OI)"
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
                result["oi_trend"] = "OKX Aktuális OI adatok elérhetők"
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

def send_telegram_message(token, chat_id, message):
    if not token or not chat_id:
        return False, "Hiányzó Telegram Token vagy Chat ID."
    try:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        payload = {"chat_id": chat_id, "text": message, "parse_mode": "Markdown"}
        response = requests.post(url, json=payload, timeout=5)
        if response.status_code == 200:
            return True, "Sikeres Telegram üzenetküldés!"
        else:
            return False, f"Telegram hiba: {response.text}"
    except Exception as e:
        return False, f"Hálózati hiba: {e}"

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
    return score, details

def label(score):
    if score >= 3: return "LONG irányú fölény", "success"
    if score >= 1: return "Enyhe LONG fölény", "success"
    if score <= -3: return "SHORT irányú fölény", "error"
    if score <= -1: return "Enyhe SHORT fölény", "error"
    return "Semleges / kivárás", "warning"

@st.cache_data(ttl=15)
def load_all(exc, sym):
    return {tf: get_ohlcv(exc, sym, tf, target_limit=5000) for tf in TIMEFRAMES}

try:
    with st.spinner(f"Elemzés futtatása ({exchange_choice}) + ATR & OI & Hírek + Mintakeresés..."):
        frames = load_all(exchange_choice, symbol)
        deriv = get_derivatives_data(exchange_choice, symbol)
        news_items = get_crypto_news()
        
        if groq_api_key:
            news_score, news_explanation = analyze_news_ai(news_items, groq_api_key)
        else:
            news_score = analyze_news_sentiment_fallback(news_items)
            news_explanation = "Kulcsszó-alapú becslés (adj meg Groq kulcsot az AI-értékeléshez)."

        pattern_score, pattern_explanation = pattern_matching_analysis(frames["1h"], window_size=20)

    analyzed = {tf: add_indicators(df) for tf, df in frames.items()}
    scores = {}
    for tf, df in frames.items():
        scores[tf], _ = structure_score(df)

    tech_weighted = (
        scores["4h"] * 0.30 +
        scores["1h"] * 0.30 +
        scores["15m"] * 0.20 +
        scores["5m"] * 0.12 +
        scores["1m"] * 0.08
    )
    
    weighted = tech_weighted * 0.45 + news_score * 0.25 + pattern_score * 0.30

    oi_change = deriv.get("oi_change")
    funding = deriv.get("funding")
    
    if oi_change is not None:
        price_direction = 1 if frames["1h"].iloc[-1]["close"] > frames["1h"].iloc[-2]["close"] else -1
        if oi_change > 0:
            weighted += 0.2 * price_direction
        else:
            weighted -= 0.1 * price_direction

    if funding is not None:
        if funding > 0.03: weighted -= 0.15
        elif funding < -0.03: weighted += 0.15

    direction, status = label(weighted)
    confidence = min(90, 50 + abs(weighted) * 8)
    
    last_row = analyzed["1h"].iloc[-1]
    price = float(last_row["close"])
    current_atr = float(last_row["atr"]) if not np.isnan(last_row["atr"]) else price * 0.01

    # ATR alapú Stop-Loss és Take-Profit kalkuláció
    if "LONG" in direction:
        stop_loss = price - (1.5 * current_atr)
        take_profit = price + (2.5 * current_atr)
    elif "SHORT" in direction:
        stop_loss = price + (1.5 * current_atr)
        take_profit = price - (2.5 * current_atr)
    else:
        stop_loss = price - (1.5 * current_atr)
        take_profit = price + (2.5 * current_atr)

    alert_msg = f"🚨 *SALIGA AI Automatikus Riasztás*\n\nPiac: `{symbol}` ({exchange_choice})\nIrány: *{direction}*\nKomplex Erősség: `{confidence:.0f}%` (Küszöb: {alert_threshold}%)\nAktuális ár: `{price:,.4f} USDT`\n\n🎯 *Ajánlott Kockázatkezelés (ATR)*:\n- Stop-Loss: `{stop_loss:,.4f}`\n- Take-Profit: `{take_profit:,.4f}`\n\n- Technikai pont: `{tech_weighted:+.2f}`\n- Hír-hangulat: `{news_score:+.2f}`\n- Múltbeli minta: `{pattern_score:+.2f}`"

    if send_alert_btn:
        success, msg = send_telegram_message(telegram_token, telegram_chat_id, alert_msg)
        if success:
            st.sidebar.success(msg)
        else:
            st.sidebar.error(msg)

    if auto_alert_enabled and confidence >= alert_threshold:
        alert_key = f"last_alert_{symbol}_{direction}_{int(confidence)}"
        if st.session_state.get(alert_key) != "sent":
            success, msg = send_telegram_message(telegram_token, telegram_chat_id, alert_msg)
            if success:
                st.session_state[alert_key] = "sent"
                st.sidebar.success("Auto riasztás elküldve Telegramra!")

    # --- Kijelzés a felületen ---
    a, b, c, d = st.columns(4)
    a.metric("Aktuális ár", f"{price:,.4f} USDT")
    b.metric("Technikai pont", f"{tech_weighted:+.2f} / 5")
    c.metric("OI Trend & Tőke", deriv.get("oi_trend", "N/A"))
    d.metric("Múltbeli Minta pont", f"{pattern_score:+.2f} (-2 / +2)")

    if status == "success": st.success(f"Irányjelzés: {direction} (Erősség: {confidence:.0f}%)")
    elif status == "error": st.error(f"Irányjelzés: {direction} (Erősség: {confidence:.0f}%)")
    else: st.warning(f"Irányjelzés: {direction} (Erősség: {confidence:.0f}%)")

    # ATR Szintek vizuális kiemelése
    st.info(f"📊 **ATR Alapú Kockázatkezelési Szintek (1h chart alapján):**  \n🔴 **Ajánlott Stop-Loss:** `{stop_loss:,.4f} USDT` | 🟢 **Ajánlott Take-Profit:** `{take_profit:,.4f} USDT` (ATR: `{current_atr:.2f}`)")

    col_left, col_right = st.columns([1.2, 1])

    with col_left:
        st.subheader("Technikai Idősíkok")
        rows = []
        for tf in ["4h", "1h", "15m", "5m", "1m"]:
            sc = scores[tf]
            rows.append({"Idősík": tf, "Pontszám": sc, "Irány": "LONG" if sc > 0 else ("SHORT" if sc < 0 else "Semleges")})
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

    with col_right:
        st.subheader("Derivatív, Hírek & Minták")
        st.info(f"**OI Változás:** {f'{oi_change:+.2f}%' if oi_change is not None else 'Nem elérhető'} | **Funding:** {f'{funding:+.4f}%' if funding is not None else 'N/A'}")
        st.write(f"**AI Hír Indoklás:** {news_explanation}")
        st.warning(f"**Mintafelismerés:** {pattern_explanation}")
        
        with st.expander("Vizsgált hírek listája"):
            for item in news_items:
                st.markdown(f"- [{item['title']}]({item['link']})")

    if show_chart:
        st.subheader("1 órás chart")
        chart = analyzed["1h"].tail(180)
        fig = make_subplots(rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.04, row_heights=[0.72, 0.28])
        fig.add_trace(go.Candlestick(x=chart["time"], open=chart["open"], high=chart["high"], low=chart["low"], close=chart["close"], name=symbol), row=1, col=1)
        fig.add_trace(go.Scatter(x=chart["time"], y=chart["sma20"], name="SMA20"), row=1, col=1)
        fig.add_trace(go.Scatter(x=chart["time"], y=chart["sma50"], name="SMA50"), row=1, col=1)
        fig.add_trace(go.Bar(x=chart["time"], y=chart["est_delta"], name="Becsült delta"), row=2, col=1)
        fig.update_layout(height=550, xaxis_rangeslider_visible=False)
        st.plotly_chart(fig, use_container_width=True)

except Exception as e:
    st.error(f"Hiba történt: {e}")
