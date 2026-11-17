import time
import requests
import numpy as np
import pandas as pd
import streamlit as st
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import feedparser
import ccxt
from streamlit_autorefresh import st_autorefresh

st.set_page_config(page_title="SALIGA AI Market Intelligence", layout="wide")

# --- 15 MÁSODPERCES AUTOMATIKUS FRISSÍTÉS ---
st_autorefresh(interval=15000, key="saliga_auto_refresh")

st.title("SALIGA AI – Intézményi Market Intelligence + Volumen Order Blockok")
st.caption("15mp Auto-Refresh • Multi-Timeframe Liq Matrix • Volumen Spike Szintek • Trade Journal • AI Mentor")

# --- Session State inicializálása a Kötési Naplóhoz ---
if "trade_journal" not in st.session_state:
    st.session_state.trade_journal = []

# --- Settings & API Keys ---
with st.sidebar:
    st.header("Beállítások & Kockázat")
    exchange_choice = st.selectbox("Adatforrás tőzsde", ["OKX", "Bybit"], index=0)
    symbol = st.selectbox("Piac", ["BTC/USDT", "SOL/USDT", "ETH/USDT"], index=0)
    show_chart = st.checkbox("Gyertyagrafikon", value=True)
    
    st.markdown("---")
    st.subheader("Pozíció méretezés")
    account_balance = st.number_input("Egyenleg (USDT)", value=1000.0, step=100.0)
    risk_percentage = st.slider("Kockázat / Kötés (%)", min_value=0.5, max_value=5.0, value=1.0, step=0.5)
    
    st.markdown("---")
    st.subheader("AI Hírhangulat & Mentor")
    default_groq = ""
    try:
        default_groq = st.secrets.get("GROQ_API_KEY", "")
    except Exception:
        pass
    groq_api_key = st.text_input("Groq API Kulcs", value=default_groq, type="password")
    
    st.markdown("---")
    st.subheader("Telegram Riasztás")
    telegram_token = st.text_input("Telegram Token", type="password")
    telegram_chat_id = st.text_input("Telegram Chat ID", type="password")
    auto_alert_enabled = st.checkbox("Auto riasztás", value=False)
    alert_threshold = st.slider("Min. Erősség (%)", 50, 90, 75, 5)
    send_alert_btn = st.button("Riasztás küldése most")

TIMEFRAMES = {"1m": "1m", "5m": "5m", "15m": "15m", "1h": "1h", "4h": "4h"}

def get_timeframe_ms(tf):
    mapping = {"1m": 60000, "5m": 300000, "15m": 900000, "1h": 3600000, "4h": 14400000}
    return mapping.get(tf, 3600000)

@st.cache_data(ttl=15)
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
            if not data: break
            if all_data and data[0][0] == all_data[0][0]: break
            all_data = data + all_data
            since = data[0][0] - (batch_size * get_timeframe_ms(timeframe))
            time.sleep(0.05)
            if len(data) < batch_size: break
        except Exception:
            break
            
    df = pd.DataFrame(all_data[-target_limit:], columns=["time", "open", "high", "low", "close", "volume"])
    df["time"] = pd.to_datetime(df["time"], unit="ms", utc=True)
    return df

@st.cache_data(ttl=300)
def get_crypto_news():
    try:
        feed = feedparser.parse("https://cointelegraph.com/rss")
        return [{"title": e.title, "link": e.link} for e in feed.entries[:5]]
    except Exception:
        return []

def analyze_news_sentiment_fallback(news_items):
    bullish = ["surge", "rally", "bull", "jump", "growth", "high", "approval", "etf", "gain", "up"]
    bearish = ["drop", "crash", "bear", "fall", "dump", "loss", "risk", "hack", "ban", "down"]
    score = 0.0
    for item in news_items:
        t = item["title"].lower()
        for w in bullish:
            if w in t: score += 0.3
        for w in bearish:
            if w in t: score -= 0.3
    return max(min(score, 2.0), -2.0)

def analyze_news_ai(news_items, api_key):
    titles_str = "\n".join([f"- {t['title']}" for t in news_items])
    if not api_key: return analyze_news_sentiment_fallback(news_items), "Nincs API kulcs."
    prompt = f"Elemezd a híreket -2.0 és +2.0 között. Címsorok:\n{titles_str}\nFormátum: [SZÁM] | [Magyarázat]"
    try:
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        payload = {"model": "openai/gpt-oss-120b", "messages": [{"role": "user", "content": prompt}], "temperature": 0.1}
        res = requests.post("https://api.groq.com/openai/v1/chat/completions", json=payload, headers=headers, timeout=10).json()
        if "choices" in res:
            content = res["choices"][0]["message"]["content"]
            parts = content.split("|")
            return float(parts[0].strip().replace("[", "").replace("]", "")), parts[1].strip()
        return analyze_news_sentiment_fallback(news_items), "AI hiba."
    except Exception:
        return analyze_news_sentiment_fallback(news_items), "AI kivétel."

def pattern_matching_analysis(df, window_size=20):
    if len(df) < window_size * 5: return 0.0, "Nincs elég adat."
    closes = df['close'].values
    curr = closes[-window_size:]
    curr_n = (curr - np.mean(curr)) / (np.std(curr) + 1e-8)
    matches = []
    for i in range(len(closes) - window_size * 2):
        hist = closes[i:i+window_size]
        hist_n = (hist - np.mean(hist)) / (np.std(hist) + 1e-8)
        dist = np.linalg.norm(curr_n - hist_n)
        future_idx = i + window_size
        if future_idx + 5 < len(closes):
            ret = (closes[future_idx + 5] - closes[future_idx]) / closes[future_idx]
            matches.append((dist, ret))
    if not matches: return 0.0, "Nincs minta."
    matches.sort(key=lambda x: x[0])
    top = matches[:5]
    bull_cnt = sum(1 for m in top if m[1] > 0)
    score = (bull_cnt / len(top)) * 4.0 - 2.0
    return max(min(score, 2.0), -2.0), f"A top 5 múltbeli mintából {bull_cnt} emelkedett."

def get_volume_spike_levels(df):
    """Keresi a legutibb kiugró volumengyertyát (Volume Spike / Order Block) és visszaadja a szintjeit"""
    x = df.copy()
    x["vol_sma"] = x["volume"].rolling(20).mean()
    # Kiugró volumen: ha a volumen legalább 2.5-szöröse az elmúlt 20 gyertya átlagának
    spikes = x[x["volume"] > x["vol_sma"] * 2.5]
    if spikes.empty:
        # Ha nincs extrém kiugrás, akkor a legutóbbi 30 gyertya legnagyobb volumenét veszi
        max_idx = x["volume"].tail(30).idxmax()
        spike_row = x.loc[max_idx]
    else:
        spike_row = spikes.iloc[-1]
    
    return float(spike_row["high"]), float(spike_row["low"]), float(spike_row["close"]) >= float(spike_row["open"])

def get_ai_trading_mentor(price, direction, conf, tech, oi, liq_sum, vol_info, rr, api_key):
    if not api_key: return "Nincs Groq kulcs."
    prompt = f"""
    Profi kvantitatív mentor vagy. Adatok: Irány: {direction} ({conf:.0f}%), Ár: {price}, Technikai pont: {tech}, OI: {oi}, Likviditás: {liq_sum}, Volumen Order Block szintek: {vol_info}, R:R arány: {rr}.
    Adj meg: 1. Szignál szintézis, 2. Kereskedési stratégia, 3. Fő kockázat. Magyarul, tömören.
    """
    try:
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        payload = {"model": "openai/gpt-oss-120b", "messages": [{"role": "user", "content": prompt}], "temperature": 0.2}
        res = requests.post("https://api.groq.com/openai/v1/chat/completions", json=payload, headers=headers, timeout=12).json()
        return res["choices"][0]["message"]["content"] if "choices" in res else "AI hiba."
    except Exception as e:
        return f"Mentor hiba: {e}"

def get_derivatives_data(exchange_name, symbol):
    pair = symbol.replace("/", "")
    res = {"oi_change": None, "funding": None, "oi_trend": "Semleges"}
    if exchange_name == "Bybit":
        try:
            r = requests.get("https://api.bybit.com/v5/market/open-interest", params={"category": "linear", "symbol": pair, "intervalTime": "5min", "limit": 3}, timeout=6).json()
            rows = r.get("result", {}).get("list", [])
            if len(rows) >= 2:
                diff = float(rows[0]["openInterest"]) - float(rows[1]["openInterest"])
                res["oi_change"] = (diff / float(rows[1]["openInterest"])) * 100
                res["oi_trend"] = "Növekvő OI (Új tőke)" if diff > 0 else "Csökkenő OI (Zárások)"
        except Exception: pass
        try:
            r = requests.get("https://api.bybit.com/v5/market/funding/history", params={"category": "linear", "symbol": pair, "limit": 1}, timeout=6).json()
            rows = r.get("result", {}).get("list", [])
            if rows: res["funding"] = float(rows[0]["fundingRate"]) * 100
        except Exception: pass
    return res

def add_indicators(df):
    x = df.copy()
    x["sma20"] = x["close"].rolling(20).mean()
    x["sma50"] = x["close"].rolling(50).mean()
    delta = x["close"].diff()
    gain = delta.clip(lower=0).rolling(14).mean()
    loss = (-delta.clip(upper=0)).rolling(14).mean()
    rs = gain / loss.replace(0, np.nan)
    x["rsi"] = 100 - (100 / (1 + rs))
    prev = x["close"].shift(1)
    tr = pd.concat([x["high"] - x["low"], (x["high"] - prev).abs(), (x["low"] - prev).abs()], axis=1).max(axis=1)
    x["atr"] = tr.rolling(14).mean()
    x["est_delta"] = np.where(x["close"] >= x["open"], x["volume"], -x["volume"])
    x["delta_sum"] = x["est_delta"].rolling(20).sum()
    return x

def structure_score(df):
    x = add_indicators(df).dropna()
    if len(x) < 5: return 0
    last, prev = x.iloc[-1], x.iloc[-2]
    s = 0
    if last.close > last.sma20: s += 1
    else: s -= 1
    if last.sma20 > last.sma50: s += 1
    else: s -= 1
    if last.close > prev.high: s += 1
    elif last.close < prev.low: s -= 1
    if last.rsi >= 55: s += 1
    elif last.rsi <= 45: s -= 1
    if last.delta_sum > 0: s += 1
    elif last.delta_sum < 0: s -= 1
    return s

def label(score):
    if score >= 3: return "LONG NYITÁS AJÁNLOTT", "success"
    if score >= 1: return "ENYHE LONG NYITÁS", "success"
    if score <= -3: return "SHORT NYITÁS AJÁNLOTT", "error"
    if score <= -1: return "ENYHE SHORT NYITÁS", "error"
    return "KIVÁRÁS / SEMLEGES", "warning"

@st.cache_data(ttl=15)
def load_all(exc, sym):
    return {tf: get_ohlcv(exc, sym, tf, target_limit=5000) for tf in TIMEFRAMES}

try:
    with st.spinner("Multi-Faktor Szignálmotor & Volumen Elemzés futtatása..."):
        frames = load_all(exchange_choice, symbol)
        deriv = get_derivatives_data(exchange_choice, symbol)
        news = get_crypto_news()
        news_score, news_exp = analyze_news_ai(news, groq_api_key) if groq_api_key else (analyze_news_sentiment_fallback(news), "Fallback")
        pat_score, pat_exp = pattern_matching_analysis(frames["1h"], 20)

    analyzed = {tf: add_indicators(df) for tf, df in frames.items()}
    scores = {tf: structure_score(df) for tf, df in frames.items()}

    tech_weighted = scores["4h"]*0.30 + scores["1h"]*0.30 + scores["15m"]*0.20 + scores["5m"]*0.12 + scores["1m"]*0.08
    last_row = analyzed["1h"].iloc[-1]
    price = float(last_row["close"])
    atr_1h = float(last_row["atr"]) if not np.isnan(last_row["atr"]) else price * 0.01

    # Multi-Timeframe Likvidációs Mátrix
    scalp_liq_short = price + (atr_1h * 0.8)
    scalp_liq_long = price - (atr_1h * 0.8)
    swing_liq_short = price + (atr_1h * 2.2)
    swing_liq_long = price - (atr_1h * 2.2)

    # Volumen Spike / Order Block Szintek (1h chart alapján)
    vol_high, vol_low, is_bullish_spike = get_volume_spike_levels(frames["1h"])

    # Volumen bias a szignálhoz (ha az ár közeleg a volumen támaszhoz/ellenálláshoz)
    vol_bias = 0.0
    if price <= vol_low * 1.005:
        vol_bias = 0.4 # Közel a volumen támaszhoz -> Long bias
    elif price >= vol_high * 0.995:
        vol_bias = -0.4 # Közel a volumen ellenálláshoz -> Short bias

    liq_bias = 0.5 if price < (scalp_liq_long + atr_1h*0.3) else (-0.5 if price > (scalp_liq_short - atr_1h*0.3) else 0.0)
    
    # Súlyozott összegzés (beleértve a volumen bias-t is)
    total_score = tech_weighted*0.30 + news_score*0.15 + pat_score*0.15 + liq_bias*0.20 + vol_bias*0.20
    
    oi_chg = deriv.get("oi_change")
    if oi_chg is not None:
        p_dir = 1 if frames["1h"].iloc[-1]["close"] > frames["1h"].iloc[-2]["close"] else -1
        total_score += (0.15 * p_dir if oi_chg > 0 else -0.1 * p_dir)

    direction, status = label(total_score)
    conf = min(95, 50 + abs(total_score) * 10)

    # ATR Stop-Loss & Take-Profit
    if "LONG" in direction:
        stop_loss = price - (1.5 * atr_1h)
        take_profit = price + (2.5 * atr_1h)
    elif "SHORT" in direction:
        stop_loss = price + (1.5 * atr_1h)
        take_profit = price - (2.5 * atr_1h)
    else:
        stop_loss = price - (1.5 * atr_1h)
        take_profit = price + (2.5 * atr_1h)

    risk_pips = abs(price - stop_loss)
    reward_pips = abs(take_profit - price)
    rr_ratio = reward_pips / risk_pips if risk_pips > 0 else 0.0
    
    risk_amount_usdt = account_balance * (risk_percentage / 100.0)
    position_size_units = risk_amount_usdt / risk_pips if risk_pips > 0 else 0.0
    leverage_rec = min(50, max(1, int((position_size_units * price) / account_balance))) if account_balance > 0 else 1

    liq_sum_text = f"Scalp Liq: Long {scalp_liq_long:,.2f} / Short {scalp_liq_short:,.2f}"
    vol_info_text = f"Volumen OB (Order Block) - Ellenállás: {vol_high:,.2f}, Támasz: {vol_low:,.2f}"

    ai_mentor = get_ai_trading_mentor(price, direction, conf, round(tech_weighted, 2), deriv.get("oi_trend"), liq_sum_text, vol_info_text, f"1:{rr_ratio:.2f}", groq_api_key)

    # --- UI: ÉLŐ JELZÉS DOBOZ ---
    st.markdown("---")
    if "LONG" in direction:
        st.markdown(f"""<div style="background-color: #d4edda; padding: 22px; border-radius: 10px; border: 2px solid #28a745; text-align: center;">
            <h1 style="color: #155724; margin: 0; font-size: 34px;">🟢 ÉLŐ JELZÉS: {direction}</h1>
            <p style="color: #155724; font-size: 17px; margin-top: 8px;">Erősség: <b>{conf:.0f}%</b> | R:R Arány: <b>1:{rr_ratio:.2f}</b> | Piac: <b>{symbol}</b></p>
        </div>""", unsafe_allow_html=True)
    elif "SHORT" in direction:
        st.markdown(f"""<div style="background-color: #f8d7da; padding: 22px; border-radius: 10px; border: 2px solid #dc3545; text-align: center;">
            <h1 style="color: #721c24; margin: 0; font-size: 34px;">🔴 ÉLŐ JELZÉS: {direction}</h1>
            <p style="color: #721c24; font-size: 17px; margin-top: 8px;">Erősség: <b>{conf:.0f}%</b> | R:R Arány: <b>1:{rr_ratio:.2f}</b> | Piac: <b>{symbol}</b></p>
        </div>""", unsafe_allow_html=True)
    else:
        st.markdown(f"""<div style="background-color: #fff3cd; padding: 22px; border-radius: 10px; border: 2px solid #ffc107; text-align: center;">
            <h1 style="color: #856404; margin: 0; font-size: 34px;">🟡 ÉLŐ JELZÉS: {direction}</h1>
            <p style="color: #856404; font-size: 17px; margin-top: 8px;">Erősség: <b>{conf:.0f}%</b> | R:R Arány: <b>1:{rr_ratio:.2f}</b> | Piac: <b>{symbol}</b></p>
        </div>""", unsafe_allow_html=True)
    st.markdown("---")

    # --- Metrikák ---
    a, b, c, d = st.columns(4)
    a.metric("Aktuális Ár", f"{price:,.4f} USDT")
    b.metric("Kockázat-Hozam (R:R)", f"1 : {rr_ratio:.2f}")
    c.metric("Ajánlott Tőkeáttétel", f"~{leverage_rec}x")
    d.metric("OI Trend", deriv.get("oi_trend", "N/A"))

    # --- Multi-Timeframe Likvidációs Mátrix & Volumen Spike Panel ---
    st.markdown("### 🗺️ Intézményi Zónák & Volumen Order Blockok")
    m1, m2, m3 = st.columns(3)
    with m1:
        st.info(f"⚡ **Scalp Liq Zónák:**\n- Short Fal: `{scalp_liq_short:,.2f}`\n- Long Zóna: `{scalp_liq_long:,.2f}`")
    with m2:
        st.warning(f"📊 **Volumen Spike Szintek:**\n- Ellenállás (High): `{vol_high:,.2f}`\n- Támasz (Low): `{vol_low:,.2f}`")
    with m3:
        st.success(f"🌊 **Swing Liq Zónák:**\n- Short Fal: `{swing_liq_short:,.2f}`\n- Long Zóna: `{swing_liq_long:,.2f}`")

    st.success(f"📊 **Kockázatkezelési Szintek:** Stop-Loss: `{stop_loss:,.4f}` | Take-Profit: `{take_profit:,.4f}` | Kockáztatott tőke: `{risk_amount_usdt:.2f} USDT`")

    col_j1, col_j2 = st.columns([2, 1])
    with col_j1:
        st.subheader("🤖 AI Kereskedési Mentor Elemzése")
        st.success(ai_mentor)
    with col_j2:
        st.subheader("📝 Kötési Napló")
        if st.button("💾 Aktuális szignál rögzítése"):
            st.session_state.trade_journal.append({
                "Idő": pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S"),
                "Piac": symbol,
                "Irány": direction,
                "Ár": price,
                "SL": stop_loss,
                "TP": take_profit,
                "R:R": f"1:{rr_ratio:.2f}",
                "Erősség": f"{conf:.0f}%"
            })
            st.success("Naplózva!")
        
        if st.session_state.trade_journal:
            with st.expander(f"Mentett ügyletek ({len(st.session_state.trade_journal)})"):
                st.dataframe(pd.DataFrame(st.session_state.trade_journal), use_container_width=True)

    # --- Technikai Táblázat & Chart ---
    col_left, col_right = st.columns([1.2, 1])
    with col_left:
        st.subheader("Technikai Idősíkok")
        rows = [{"Idősík": tf, "Pontszám": scores[tf], "Irány": "LONG" if scores[tf]>0 else "SHORT"} for tf in TIMEFRAMES]
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
    with col_right:
        st.subheader("Hírek & Minták")
        st.write(f"**AI Hír:** {news_exp}")
        st.warning(f"**Minta:** {pat_exp}")

    if show_chart:
        st.subheader("1 órás chart")
        chart = analyzed["1h"].tail(180)
        fig = make_subplots(rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.04, row_heights=[0.72, 0.28])
        fig.add_trace(go.Candlestick(x=chart["time"], open=chart["open"], high=chart["high"], low=chart["low"], close=chart["close"], name=symbol), row=1, col=1)
        fig.add_trace(go.Scatter(x=chart["time"], y=chart["sma20"], name="SMA20"), row=1, col=1)
        fig.add_trace(go.Scatter(x=chart["time"], y=chart["sma50"], name="SMA50"), row=1, col=1)
        fig.add_trace(go.Bar(x=chart["time"], y=chart["est_delta"], name="Delta"), row=2, col=1)
        fig.update_layout(height=500, xaxis_rangeslider_visible=False)
        st.plotly_chart(fig, use_container_width=True)

except Exception as e:
    st.error(f"Hiba történt: {e}")
