import time
import requests
import numpy as np
import pandas as pd
import streamlit as st
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import ccxt
import feedparser

from streamlit_autorefresh import st_autorefresh
from datetime import datetime, timezone


# =========================================================
# SALIGA AI INSTITUTIONAL PRO
# =========================================================

st.set_page_config(
    page_title="SALIGA AI INSTITUTIONAL PRO",
    layout="wide",
    initial_sidebar_state="expanded"
)

st_autorefresh(interval=30000, key="saliga_refresh")

st.markdown("""
<style>
.main {
    background-color: #0b1019;
}
[data-testid="stMetric"] {
    background: #151d2b;
    padding: 14px;
    border-radius: 10px;
}
.signal-box {
    padding: 22px;
    border-radius: 12px;
    text-align: center;
    margin-bottom: 12px;
}
</style>
""", unsafe_allow_html=True)

st.title("⚡ SALIGA AI – INSTITUTIONAL PRO")

st.caption(
    "MULTI-ASSET INTELLIGENCE | POSITION FLOW | LIQUIDITY "
    "| MARKET STRUCTURE | AI DECISION ENGINE"
)


# =========================================================
# SIDEBAR
# =========================================================

with st.sidebar:

    st.header("⚙️ Beállítások")

    exchange_choice = st.selectbox(
        "Adatforrás",
        ["Bybit", "OKX"]
    )

    account_balance = st.number_input(
        "Számlaegyenleg USDT",
        min_value=10.0,
        value=1000.0
    )

    risk_percent = st.slider(
        "Kockázat kötésenként %",
        0.5, 5.0, 1.0, 0.5
    )

    st.markdown("---")

    groq_key = st.text_input(
        "Groq API Key",
        type="password"
    )

    st.markdown("---")

    show_charts = st.checkbox(
        "Grafikonok megjelenítése",
        True
    )

    st.markdown("---")

    st.caption("SALIGA AI ENGINE v1.0")


SYMBOLS = [
    "BTC/USDT",
    "ETH/USDT",
    "SOL/USDT"
]

TIMEFRAMES = [
    "5m",
    "15m",
    "1h",
    "4h"
]


# =========================================================
# EXCHANGE
# =========================================================

@st.cache_resource
def create_exchange(name):

    if name == "Bybit":

        return ccxt.bybit({
            "enableRateLimit": True,
            "options": {
                "defaultType": "swap"
            }
        })

    return ccxt.okx({
        "enableRateLimit": True,
        "options": {
            "defaultType": "swap"
        }
    })


exchange = create_exchange(exchange_choice)


def resolve_symbol(symbol):

    markets = exchange.load_markets()

    swap_symbol = symbol + ":USDT"

    if swap_symbol in markets:
        return swap_symbol

    return symbol


# =========================================================
# MARKET DATA
# =========================================================

@st.cache_data(ttl=15)
def get_ohlcv(exchange_name, symbol, timeframe):

    ex = create_exchange(exchange_name)

    market_symbol = resolve_symbol_for_exchange(
        ex, symbol
    )

    data = ex.fetch_ohlcv(
        market_symbol,
        timeframe=timeframe,
        limit=300
    )

    df = pd.DataFrame(
        data,
        columns=[
            "time",
            "open",
            "high",
            "low",
            "close",
            "volume"
        ]
    )

    df["time"] = pd.to_datetime(
        df["time"],
        unit="ms",
        utc=True
    )

    return df


def resolve_symbol_for_exchange(ex, symbol):

    markets = ex.load_markets()

    swap_symbol = symbol + ":USDT"

    if swap_symbol in markets:
        return swap_symbol

    return symbol


@st.cache_data(ttl=5)
def get_ticker(exchange_name, symbol):

    ex = create_exchange(exchange_name)

    market_symbol = resolve_symbol_for_exchange(
        ex, symbol
    )

    ticker = ex.fetch_ticker(market_symbol)

    return float(ticker["last"])


# =========================================================
# OPEN INTEREST
# =========================================================

@st.cache_data(ttl=30)
def get_open_interest(exchange_name, symbol):

    ex = create_exchange(exchange_name)

    market_symbol = resolve_symbol_for_exchange(
        ex, symbol
    )

    try:

        current = ex.fetch_open_interest(
            market_symbol
        )

        value = current.get("openInterestValue")

        if value is None:
            value = current.get("openInterest")

        return float(value) if value is not None else None

    except Exception:
        return None


# =========================================================
# FUNDING
# =========================================================

@st.cache_data(ttl=60)
def get_funding(exchange_name, symbol):

    ex = create_exchange(exchange_name)

    market_symbol = resolve_symbol_for_exchange(
        ex, symbol
    )

    try:

        data = ex.fetch_funding_rate(
            market_symbol
        )

        return float(data["fundingRate"]) * 100

    except Exception:
        return None


# =========================================================
# CVD ENGINE
# =========================================================

@st.cache_data(ttl=10)
def get_cvd(exchange_name, symbol):

    ex = create_exchange(exchange_name)

    market_symbol = resolve_symbol_for_exchange(
        ex, symbol
    )

    try:

        trades = ex.fetch_trades(
            market_symbol,
            limit=500
        )

        buy_volume = 0
        sell_volume = 0

        for trade in trades:

            amount = float(trade["amount"])

            if trade["side"] == "buy":
                buy_volume += amount

            elif trade["side"] == "sell":
                sell_volume += amount

        cvd = buy_volume - sell_volume

        total = buy_volume + sell_volume

        ratio = (
            cvd / total * 100
            if total > 0 else 0
        )

        return {
            "buy": buy_volume,
            "sell": sell_volume,
            "cvd": cvd,
            "ratio": ratio
        }

    except Exception:

        return None


# =========================================================
# INDICATORS
# =========================================================

def add_indicators(df):

    x = df.copy()

    x["sma20"] = x["close"].rolling(20).mean()

    x["sma50"] = x["close"].rolling(50).mean()

    delta = x["close"].diff()

    gain = delta.clip(lower=0).rolling(14).mean()

    loss = (-delta.clip(upper=0)).rolling(14).mean()

    rs = gain / loss.replace(0, np.nan)

    x["rsi"] = 100 - (100 / (1 + rs))

    previous = x["close"].shift(1)

    tr = pd.concat([
        x["high"] - x["low"],
        abs(x["high"] - previous),
        abs(x["low"] - previous)
    ], axis=1).max(axis=1)

    x["atr"] = tr.rolling(14).mean()

    x["volume_avg"] = x["volume"].rolling(20).mean()

    x["volume_ratio"] = (
        x["volume"] / x["volume_avg"]
    )

    return x


# =========================================================
# MARKET STRUCTURE ENGINE
# =========================================================

def market_structure(df):

    if len(df) < 30:
        return {
            "direction": 0,
            "text": "Nincs elegendő adat",
            "bos": "N/A",
            "change": "N/A"
        }

    x = df.copy()

    highs = x["high"].iloc[-30:-1]

    lows = x["low"].iloc[-30:-1]

    last = x.iloc[-1]

    previous_high = highs.max()

    previous_low = lows.min()

    direction = 0

    bos = "Nincs BOS"

    if last["close"] > previous_high:

        direction = 1

        bos = "BULLISH BOS"

    elif last["close"] < previous_low:

        direction = -1

        bos = "BEARISH BOS"

    else:

        if last["close"] > x["close"].iloc[-10]:
            direction = 1

        elif last["close"] < x["close"].iloc[-10]:
            direction = -1

    if direction == 1:
        text = "Bullish struktúra"

    elif direction == -1:
        text = "Bearish struktúra"

    else:
        text = "Semleges struktúra"

    return {
        "direction": direction,
        "text": text,
        "bos": bos,
        "change": "Megerősítés szükséges"
    }


# =========================================================
# LIQUIDITY ENGINE
# =========================================================

def liquidity_engine(df):

    x = df.tail(100)

    high = float(x["high"].iloc[:-1].max())

    low = float(x["low"].iloc[:-1].min())

    current = float(x["close"].iloc[-1])

    return {
        "upper": high,
        "lower": low,
        "upper_distance": (
            (high-current)/current*100
        ),
        "lower_distance": (
            (current-low)/current*100
        )
    }


# =========================================================
# POSITION FLOW ENGINE
# =========================================================

def position_flow(df, oi_change, cvd):

    if len(df) < 3:

        return {
            "text": "Nincs elegendő adat",
            "score": 0
        }

    price_change = (
        df["close"].iloc[-1] -
        df["close"].iloc[-2]
    )

    if oi_change is None:

        return {
            "text": "OI adat nem elérhető",
            "score": 0
        }

    if price_change > 0 and oi_change > 0:

        text = "LONG ÉPÜLÉS"

        score = 1

    elif price_change < 0 and oi_change > 0:

        text = "SHORT ÉPÜLÉS"

        score = -1

    elif price_change > 0 and oi_change < 0:

        text = "SHORT ZÁRÁS"

        score = 0.5

    elif price_change < 0 and oi_change < 0:

        text = "LONG ZÁRÁS"

        score = -0.5

    else:

        text = "SEMLEGES"

        score = 0

    return {
        "text": text,
        "score": score
    }


# =========================================================
# DXY MACRO ENGINE
# =========================================================

@st.cache_data(ttl=300)
def get_dxy():

    try:

        url = (
            "https://query1.finance.yahoo.com/v8/"
            "finance/chart/DX-Y.NYB"
            "?interval=1h&range=5d"
        )

        response = requests.get(
            url,
            headers={
                "User-Agent": "Mozilla/5.0"
            },
            timeout=8
        )

        response.raise_for_status()

        data = response.json()

        closes = data[
            "chart"
        ]["result"][0][
            "indicators"
        ]["quote"][0]["close"]

        values = [
            x for x in closes
            if x is not None
        ]

        if len(values) < 2:
            return None

        change = (
            (values[-1] - values[-2])
            / values[-2]
        ) * 100

        return change

    except Exception:

        return None


# =========================================================
# NEWS ENGINE
# =========================================================

@st.cache_data(ttl=300)
def get_news():

    try:

        feed = feedparser.parse(
            "https://cointelegraph.com/rss"
        )

        return [
            item.title
            for item in feed.entries[:10]
        ]

    except Exception:

        return []


def analyze_news(news, api_key):

    if not news:
        return 0, "Nincs elérhető hír."

    if not api_key:

        return 0, "AI hírkulcs hiányzik."

    prompt = f"""
    Elemezd a következő kriptopiaci híreket.

    Kizárólag a piaci irány szempontjából értékeld.

    Adj egy pontszámot -2 és +2 között.

    -2 = erősen bearish
    -1 = bearish
     0 = semleges
    +1 = bullish
    +2 = erősen bullish

    Ne találj ki információkat.

    Hírek:
    {chr(10).join(news)}

    Válasz formátuma:

    SCORE: szám
    EXPLANATION: rövid magyar magyarázat
    """

    try:

        response = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",

            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json"
            },

            json={
                "model": "openai/gpt-oss-120b",
                "messages": [
                    {
                        "role": "user",
                        "content": prompt
                    }
                ],
                "temperature": 0.1
            },

            timeout=15
        )

        response.raise_for_status()

        content = response.json()[
            "choices"
        ][0]["message"]["content"]

        score_line = next(
            line for line in content.splitlines()
            if line.strip().startswith("SCORE:")
        )

        score = float(
            score_line.split(":")[1].strip()
        )

        explanation = "\n".join(
            line for line in content.splitlines()
            if line.strip().startswith("EXPLANATION:")
        )

        return np.clip(score, -2, 2), explanation

    except Exception:

        return 0, "Hírértelmezési hiba."


# =========================================================
# CENTRAL AI DECISION ENGINE
# =========================================================

def decision_engine(
    scores,
    oi_change,
    funding,
    cvd,
    dxy,
    news_score
):

    weights = {
        "structure": 0.25,
        "position": 0.20,
        "cvd": 0.15,
        "volume": 0.10,
        "momentum": 0.10,
        "funding": 0.05,
        "macro": 0.05,
        "news": 0.10
    }

    total = 0

    available_weight = 0

    for name, weight in weights.items():

        value = scores.get(name)

        if value is not None:

            total += value * weight

            available_weight += weight

    if available_weight == 0:

        return {
            "signal": "ADATHIBA / KIVÁRÁS",
            "score": 0,
            "strength": 0,
            "confidence": 0
        }

    normalized = total / available_weight

    strength = abs(normalized) * 100

    if normalized >= 0.55:

        signal = "ERŐS LONG"

    elif normalized >= 0.20:

        signal = "LONG"

    elif normalized <= -0.55:

        signal = "ERŐS SHORT"

    elif normalized <= -0.20:

        signal = "SHORT"

    else:

        signal = "KIVÁRÁS"

    return {
        "signal": signal,
        "score": normalized,
        "strength": strength,
        "confidence": strength
    }


# =========================================================
# ANALYSIS ENGINE
# =========================================================

def analyze_market(symbol):

    frames = {}

    for tf in TIMEFRAMES:

        frames[tf] = get_ohlcv(
            exchange_choice,
            symbol,
            tf
        )

        if len(frames[tf]) < 60:

            raise ValueError(
                f"{symbol}: kevés adat ({tf})"
            )

        frames[tf] = add_indicators(
            frames[tf]
        )

    price = get_ticker(
        exchange_choice,
        symbol
    )

    oi = get_open_interest(
        exchange_choice,
        symbol
    )

    funding = get_funding(
        exchange_choice,
        symbol
    )

    cvd = get_cvd(
        exchange_choice,
        symbol
    )

    # OI változás: külön lekért pillanatnyi érték
    # önmagában nem jelent változási adatot.
    # Ezért itt csak akkor használjuk, ha van
    # historikus OI összehasonlítás.

    oi_change = None

    structures = {
        tf: market_structure(frames[tf])
        for tf in TIMEFRAMES
    }

    liquidity = liquidity_engine(
        frames["1h"]
    )

    structure_score = (
        structures["4h"]["direction"] * 0.35
        + structures["1h"]["direction"] * 0.30
        + structures["15m"]["direction"] * 0.20
        + structures["5m"]["direction"] * 0.15
    )

    last = frames["1h"].iloc[-1]

    momentum = 0

    if last["rsi"] > 55:
        momentum = 1

    elif last["rsi"] < 45:
        momentum = -1

    volume_score = 0

    if last["volume_ratio"] > 1.5:

        if last["close"] > last["open"]:
            volume_score = 1

        elif last["close"] < last["open"]:
            volume_score = -1

    cvd_score = None

    if cvd is not None:

        cvd_score = np.clip(
            cvd["ratio"] / 30,
            -1,
            1
        )

    funding_score = None

    if funding is not None:

        if funding > 0.05:
            funding_score = -0.5

        elif funding < -0.05:
            funding_score = 0.5

        else:
            funding_score = 0

    macro_score = None

    if dxy is not None:

        macro_score = (
            -1 if dxy > 0.05
            else 1 if dxy < -0.05
            else 0
        )

    position = position_flow(
        frames["1h"],
        oi_change,
        cvd
    )

    scores = {

        "structure": structure_score,

        "position": None,

        "cvd": cvd_score,

        "volume": volume_score,

        "momentum": momentum,

        "funding": funding_score,

        "macro": macro_score,

        "news": None
    }

    return {
        "symbol": symbol,
        "price": price,
        "frames": frames,
        "structures": structures,
        "liquidity": liquidity,
        "oi": oi,
        "oi_change": oi_change,
        "funding": funding,
        "cvd": cvd,
        "position": position,
        "scores": scores,
        "atr": float(last["atr"])
    }


# =========================================================
# MAIN ANALYSIS
# =========================================================

try:

    with st.spinner(
        "SALIGA AI – Piaci adatok elemzése..."
    ):

        dxy = get_dxy()

        news = get_news()

        news_score, news_explanation = analyze_news(
            news,
            groq_key
        )

        results = {}

        for symbol in SYMBOLS:

            results[symbol] = analyze_market(
                symbol
            )

            results[symbol]["scores"]["news"] = (
                news_score
            )

            decision = decision_engine(
                results[symbol]["scores"],
                results[symbol]["oi_change"],
                results[symbol]["funding"],
                results[symbol]["cvd"],
                dxy,
                news_score
            )

            results[symbol]["decision"] = decision

except Exception as e:

    st.error(f"Adatlekérési vagy elemzési hiba: {e}")

    st.stop()


# =========================================================
# DASHBOARD
# =========================================================

st.markdown("---")

st.header("🎯 SALIGA AI – VÉGSŐ PIACI SZIGNÁLOK")

columns = st.columns(3)

for col, symbol in zip(columns, SYMBOLS):

    data = results[symbol]

    decision = data["decision"]

    with col:

        signal = decision["signal"]

        if "LONG" in signal:

            color = "#123c2c"

            icon = "🟢"

        elif "SHORT" in signal:

            color = "#4b2025"

            icon = "🔴"

        else:

            color = "#4b4020"

            icon = "🟡"

        st.markdown(
            f"""
            <div class="signal-box"
            style="background:{color}">
                <h2>{symbol}</h2>
                <h1>{icon} {signal}</h1>
                <h3>
                Modellpontszám:
                {decision['strength']:.1f}/100
                </h3>
            </div>
            """,
            unsafe_allow_html=True
        )

        st.metric(
            "Aktuális ár",
            f"{data['price']:,.4f} USDT"
        )

        st.metric(
            "Funding",
            (
                f"{data['funding']:.4f}%"
                if data["funding"] is not None
                else "N/A"
            )
        )

        st.metric(
            "OI",
            (
                f"{data['oi']:,.2f}"
                if data["oi"] is not None
                else "N/A"
            )
        )


# =========================================================
# DETAILED ANALYSIS
# =========================================================

st.markdown("---")

st.header("📊 Részletes piaci intelligencia")

selected_symbol = st.selectbox(
    "Részletes piac",
    SYMBOLS
)

data = results[selected_symbol]

decision = data["decision"]

a, b, c, d = st.columns(4)

a.metric(
    "Market Bias",
    decision["signal"]
)

b.metric(
    "Modellpontszám",
    f"{decision['strength']:.1f}/100"
)

c.metric(
    "DXY változás",
    f"{dxy:+.3f}%" if dxy is not None else "N/A"
)

d.metric(
    "ATR 1H",
    f"{data['atr']:.4f}"
)


# =========================================================
# POSITION FLOW
# =========================================================

st.subheader("⚡ POSITION FLOW ENGINE")

p1, p2, p3 = st.columns(3)

p1.metric(
    "Pozícióáramlás",
    data["position"]["text"]
)

p2.metric(
    "CVD",
    (
        f"{data['cvd']['cvd']:.4f}"
        if data["cvd"] else "N/A"
    )
)

p3.metric(
    "CVD arány",
    (
        f"{data['cvd']['ratio']:.2f}%"
        if data["cvd"] else "N/A"
    )
)


# =========================================================
# MARKET STRUCTURE
# =========================================================

st.subheader("🏗️ MARKET STRUCTURE")

structure_rows = []

for tf, structure in data["structures"].items():

    structure_rows.append({

        "Idősík": tf,

        "Struktúra": structure["text"],

        "BOS": structure["bos"]

    })

st.dataframe(
    pd.DataFrame(structure_rows),
    use_container_width=True,
    hide_index=True
)


# =========================================================
# LIQUIDITY
# =========================================================

st.subheader("💧 LIQUIDITY INTELLIGENCE")

l1, l2 = st.columns(2)

l1.metric(
    "Felső likviditási szint",
    f"{data['liquidity']['upper']:,.4f}"
)

l2.metric(
    "Alsó likviditási szint",
    f"{data['liquidity']['lower']:,.4f}"
)


# =========================================================
# AI EXPLANATION
# =========================================================

st.markdown("---")

st.header("🤖 SALIGA AI – Döntési szintézis")

st.info(
    f"""
    Piac: {selected_symbol}

    Végső irány: {decision['signal']}

    Modellpontszám: {decision['strength']:.1f}/100

    4H struktúra:
    {data['structures']['4h']['text']}

    1H struktúra:
    {data['structures']['1h']['text']}

    Pozícióáramlás:
    {data['position']['text']}

    Felső likviditási szint:
    {data['liquidity']['upper']:,.4f}

    Alsó likviditási szint:
    {data['liquidity']['lower']:,.4f}
    """
)

st.write(
    "**Hírhangulat:**",
    news_explanation
)


# =========================================================
# RISK ENGINE
# =========================================================

st.markdown("---")

st.header("🛡️ RISK MANAGEMENT ENGINE")

price = data["price"]

atr = data["atr"]

risk_amount = (
    account_balance * risk_percent / 100
)

if "LONG" in decision["signal"]:

    stop_loss = price - 1.5 * atr

    take_profit = price + 3.0 * atr

elif "SHORT" in decision["signal"]:

    stop_loss = price + 1.5 * atr

    take_profit = price - 3.0 * atr

else:

    stop_loss = None

    take_profit = None


if stop_loss is not None:

    distance = abs(price - stop_loss)

    quantity = (
        risk_amount / distance
        if distance > 0 else 0
    )

    rr = (
        abs(take_profit-price) / distance
        if distance > 0 else 0
    )

    r1, r2, r3, r4 = st.columns(4)

    r1.metric(
        "Belépési referencia",
        f"{price:,.4f}"
    )

    r2.metric(
        "Stop Loss",
        f"{stop_loss:,.4f}"
    )

    r3.metric(
        "Take Profit",
        f"{take_profit:,.4f}"
    )

    r4.metric(
        "R:R",
        f"1:{rr:.2f}"
    )

    st.write(
        f"Becsült pozícióméret: "
        f"{quantity:.5f} egység"
    )

    st.write(
        f"Kockáztatott összeg: "
        f"{risk_amount:.2f} USDT"
    )

else:

    st.warning(
        "Nincs megfelelő kereskedési szignál. "
        "A rendszer kivár."
    )


# =========================================================
# CHART
# =========================================================

if show_charts:

    st.markdown("---")

    st.header("📈 Piaci grafikon")

    chart = data["frames"]["1h"].tail(150)

    fig = make_subplots(
        rows=2,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.04,
        row_heights=[0.75, 0.25]
    )

    fig.add_trace(

        go.Candlestick(

            x=chart["time"],

            open=chart["open"],

            high=chart["high"],

            low=chart["low"],

            close=chart["close"],

            name=selected_symbol

        ),

        row=1,
        col=1
    )

    fig.add_trace(

        go.Scatter(

            x=chart["time"],

            y=chart["sma20"],

            name="SMA20"

        ),

        row=1,
        col=1
    )

    fig.add_trace(

        go.Scatter(

            x=chart["time"],

            y=chart["sma50"],

            name="SMA50"

        ),

        row=1,
        col=1
    )

    fig.add_trace(

        go.Bar(

            x=chart["time"],

            y=chart["volume"],

            name="Volume"

        ),

        row=2,
        col=1
    )

    fig.update_layout(

        height=650,

        template="plotly_dark",

        xaxis_rangeslider_visible=False

    )

    st.plotly_chart(
        fig,
        use_container_width=True
    )


st.markdown("---")

st.caption(
    "SALIGA AI INSTITUTIONAL PRO | "
    "Analitikai rendszer – nem automatikus kereskedési robot."
)
