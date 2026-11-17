import time
import requests
import numpy as np
import pandas as pd
import streamlit as st
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import ccxt
import feedparser

from datetime import datetime, timezone
from streamlit_autorefresh import st_autorefresh


# =========================================================
# SALIGA AI INSTITUTIONAL PRO V2
# AUTOMATIC EXCHANGE FAILOVER
# =========================================================

st.set_page_config(
    page_title="SALIGA AI INSTITUTIONAL PRO",
    layout="wide"
)

st_autorefresh(interval=30000, key="saliga_refresh")

st.title("⚡ SALIGA AI – INSTITUTIONAL PRO")

st.caption(
    "MULTI-ASSET INTELLIGENCE | POSITION FLOW | "
    "LIQUIDITY | MARKET STRUCTURE | AI DECISION ENGINE"
)


# =========================================================
# SIDEBAR
# =========================================================

with st.sidebar:

    st.header("⚙️ Beállítások")

    account_balance = st.number_input(
        "Számlaegyenleg USDT",
        min_value=10.0,
        value=1000.0
    )

    risk_percent = st.slider(
        "Kockázat kötésenként (%)",
        0.5, 5.0, 1.0, 0.5
    )

    st.markdown("---")

    groq_key = st.text_input(
        "Groq API kulcs",
        type="password"
    )

    show_chart = st.checkbox(
        "Grafikon megjelenítése",
        True
    )

    st.markdown("---")

    st.info(
        "Elsődleges: OKX\n\n"
        "Tartalék: Bybit\n\n"
        "Automatikus adatforrás-váltás"
    )


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
# EXCHANGE MANAGER
# =========================================================

@st.cache_resource
def create_exchange(name):

    if name == "OKX":

        return ccxt.okx({
            "enableRateLimit": True,
            "timeout": 12000,
            "options": {
                "defaultType": "swap"
            }
        })

    if name == "Bybit":

        return ccxt.bybit({
            "enableRateLimit": True,
            "timeout": 12000,
            "options": {
                "defaultType": "swap"
            }
        })


def get_market_symbol(exchange, symbol):

    markets = exchange.load_markets()

    swap_symbol = symbol + ":USDT"

    if swap_symbol in markets:
        return swap_symbol

    if symbol in markets:
        return symbol

    raise ValueError(
        f"Nem található piac: {symbol}"
    )


# =========================================================
# EXCHANGE CONNECTION TEST
# =========================================================

def test_exchange(name, symbol):

    try:

        ex = create_exchange(name)

        market_symbol = get_market_symbol(
            ex,
            symbol
        )

        ticker = ex.fetch_ticker(
            market_symbol
        )

        price = ticker.get("last")

        if price is None or price <= 0:
            raise ValueError("Érvénytelen árfolyam")

        return True, float(price), "OK"

    except Exception as e:

        return False, None, str(e)


# =========================================================
# MARKET DATA
# =========================================================

def fetch_market_data(name, symbol):

    ex = create_exchange(name)

    market_symbol = get_market_symbol(
        ex,
        symbol
    )

    frames = {}

    for tf in TIMEFRAMES:

        data = ex.fetch_ohlcv(
            market_symbol,
            timeframe=tf,
            limit=300
        )

        if not data or len(data) < 60:

            raise ValueError(
                f"Hiányos OHLCV adat: {tf}"
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

        if df[["open", "high", "low", "close"]].isna().any().any():
            raise ValueError(
                f"Hibás gyertyaadat: {tf}"
            )

        if (df["close"] <= 0).any():
            raise ValueError(
                f"Érvénytelen árfolyam: {tf}"
            )

        frames[tf] = df

    ticker = ex.fetch_ticker(
        market_symbol
    )

    price = ticker.get("last")

    if price is None or price <= 0:
        raise ValueError("Nincs aktuális árfolyam")

    return frames, float(price), ex, market_symbol


# =========================================================
# AUTOMATIC FAILOVER
# =========================================================

def get_market_with_failover(symbol):

    providers = [
        "OKX",
        "Bybit"
    ]

    errors = []

    for provider in providers:

        try:

            frames, price, ex, market_symbol = (
                fetch_market_data(
                    provider,
                    symbol
                )
            )

            return {
                "success": True,
                "exchange": provider,
                "frames": frames,
                "price": price,
                "client": ex,
                "market_symbol": market_symbol,
                "error": None
            }

        except Exception as e:

            errors.append(
                f"{provider}: {str(e)[:250]}"
            )

            continue

    return {
        "success": False,
        "exchange": None,
        "frames": None,
        "price": None,
        "client": None,
        "market_symbol": None,
        "error": " | ".join(errors)
    }


# =========================================================
# OPEN INTEREST HISTORY
# =========================================================

def get_open_interest_data(ex, symbol):

    result = {
        "current": None,
        "change": None,
        "trend": "N/A"
    }

    try:

        current = ex.fetch_open_interest(symbol)

        value = current.get("openInterestValue")

        if value is None:
            value = current.get("openInterestAmount")

        if value is None:
            value = current.get("openInterest")

        if value is not None:
            result["current"] = float(value)

    except Exception:
        pass

    try:

        if ex.has.get("fetchOpenInterestHistory"):

            history = ex.fetch_open_interest_history(
                symbol,
                timeframe="5m",
                limit=6
            )

            values = []

            for item in history:

                value = item.get("openInterestAmount")

                if value is None:
                    value = item.get("openInterestValue")

                if value is not None:
                    values.append(float(value))

            if len(values) >= 2 and values[0] != 0:

                change = (
                    (values[-1] - values[0])
                    / values[0]
                ) * 100

                result["change"] = change

                if change > 0.1:
                    result["trend"] = "NÖVEKVŐ OI"

                elif change < -0.1:
                    result["trend"] = "CSÖKKENŐ OI"

                else:
                    result["trend"] = "STABIL OI"

    except Exception:
        pass

    return result


# =========================================================
# FUNDING
# =========================================================

def get_funding_data(ex, symbol):

    try:

        data = ex.fetch_funding_rate(symbol)

        rate = data.get("fundingRate")

        if rate is not None:

            return float(rate) * 100

    except Exception:
        pass

    return None


# =========================================================
# CVD ENGINE
# =========================================================

def get_cvd_data(ex, symbol):

    try:

        trades = ex.fetch_trades(
            symbol,
            limit=500
        )

        buy = 0.0
        sell = 0.0

        for trade in trades:

            amount = float(
                trade.get("amount") or 0
            )

            if trade.get("side") == "buy":
                buy += amount

            elif trade.get("side") == "sell":
                sell += amount

        total = buy + sell

        if total == 0:
            return None

        cvd = buy - sell

        ratio = cvd / total * 100

        return {
            "buy": buy,
            "sell": sell,
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

    x["rsi"] = 100 - (
        100 / (1 + rs)
    )

    prev = x["close"].shift(1)

    tr = pd.concat(
        [
            x["high"] - x["low"],
            abs(x["high"] - prev),
            abs(x["low"] - prev)
        ],
        axis=1
    ).max(axis=1)

    x["atr"] = tr.rolling(14).mean()

    x["volume_avg"] = (
        x["volume"].rolling(20).mean()
    )

    x["volume_ratio"] = (
        x["volume"] / x["volume_avg"]
    )

    return x


# =========================================================
# MARKET STRUCTURE
# =========================================================

def market_structure(df):

    if len(df) < 35:

        return {
            "direction": 0,
            "text": "Nincs adat",
            "bos": "N/A"
        }

    x = df.copy()

    last = x.iloc[-2]

    previous = x.iloc[-3]

    reference = x.iloc[-32:-2]

    resistance = reference["high"].max()

    support = reference["low"].min()

    if last["close"] > resistance:

        direction = 1
        bos = "BULLISH BOS"

    elif last["close"] < support:

        direction = -1
        bos = "BEARISH BOS"

    else:

        direction = (
            1 if last["close"] > previous["close"]
            else -1 if last["close"] < previous["close"]
            else 0
        )

        bos = "NINCS MEGERŐSÍTETT BOS"

    text = (
        "BULLISH" if direction == 1
        else "BEARISH" if direction == -1
        else "SEMLEGES"
    )

    return {
        "direction": direction,
        "text": text,
        "bos": bos
    }


# =========================================================
# LIQUIDITY ENGINE
# =========================================================

def liquidity_engine(df):

    x = df.tail(100)

    upper = float(
        x["high"].iloc[:-1].max()
    )

    lower = float(
        x["low"].iloc[:-1].min()
    )

    return {
        "upper": upper,
        "lower": lower
    }


# =========================================================
# POSITION FLOW
# =========================================================

def position_flow(df, oi_change):

    if oi_change is None:

        return {
            "text": "OI ADAT HIÁNYZIK",
            "score": None
        }

    last = df.iloc[-2]

    previous = df.iloc[-3]

    price_change = (
        last["close"] - previous["close"]
    )

    if price_change > 0 and oi_change > 0:

        return {
            "text": "LONG ÉPÜLÉS GYANÚJA",
            "score": 1
        }

    if price_change < 0 and oi_change > 0:

        return {
            "text": "SHORT ÉPÜLÉS GYANÚJA",
            "score": -1
        }

    if price_change > 0 and oi_change < 0:

        return {
            "text": "SHORT ZÁRÁS GYANÚJA",
            "score": 0.5
        }

    if price_change < 0 and oi_change < 0:

        return {
            "text": "LONG ZÁRÁS GYANÚJA",
            "score": -0.5
        }

    return {
        "text": "SEMLEGES",
        "score": 0
    }


# =========================================================
# DXY
# =========================================================

@st.cache_data(ttl=300)
def get_dxy():

    try:

        url = (
            "https://query1.finance.yahoo.com/v8/"
            "finance/chart/DX-Y.NYB"
            "?interval=1h&range=5d"
        )

        r = requests.get(
            url,
            headers={
                "User-Agent": "Mozilla/5.0"
            },
            timeout=8
        )

        r.raise_for_status()

        data = r.json()

        closes = data[
            "chart"
        ]["result"][0][
            "indicators"
        ]["quote"][0]["close"]

        values = [
            v for v in closes
            if v is not None
        ]

        if len(values) < 2:
            return None

        return (
            (values[-1] - values[-2])
            / values[-2]
        ) * 100

    except Exception:

        return None


# =========================================================
# NEWS
# =========================================================

@st.cache_data(ttl=300)
def get_news():

    try:

        feed = feedparser.parse(
            "https://cointelegraph.com/rss"
        )

        return [
            e.title
            for e in feed.entries[:10]
        ]

    except Exception:

        return []


def analyze_news(news, api_key):

    if not news:
        return None, "Nincs hír."

    if not api_key:
        return None, "AI kulcs hiányzik."

    prompt = f"""
    Elemezd a kriptovaluta-piaci híreket.

    Értékelés -2 és +2 között.

    -2 erősen bearish
    -1 bearish
     0 semleges
    +1 bullish
    +2 erősen bullish

    Ne találj ki információkat.

    Hírek:
    {chr(10).join(news)}

    Kizárólag ezt a formátumot használd:

    SCORE: 0.5
    EXPLANATION: Magyar magyarázat
    """

    try:

        r = requests.post(
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

        r.raise_for_status()

        content = r.json()[
            "choices"
        ][0]["message"]["content"]

        score_line = next(
            line for line in content.splitlines()
            if line.strip().startswith("SCORE:")
        )

        score = float(
            score_line.split(":", 1)[1].strip()
        )

        explanation = next(
            (
                line.split(":", 1)[1].strip()
                for line in content.splitlines()
                if line.strip().startswith("EXPLANATION:")
            ),
            "Nincs magyarázat."
        )

        return float(
            np.clip(score, -2, 2)
        ), explanation

    except Exception:

        return None, "AI hírértelmezési hiba."


# =========================================================
# MACRO SCORE
# =========================================================

def macro_score(dxy):

    if dxy is None:
        return None

    if dxy > 0.05:
        return -1

    if dxy < -0.05:
        return 1

    return 0


# =========================================================
# CENTRAL DECISION ENGINE
# =========================================================

def decision_engine(data, dxy, news_score):

    frames = data["frames"]

    structures = {
        tf: market_structure(frames[tf])
        for tf in TIMEFRAMES
    }

    structure = (
        structures["4h"]["direction"] * 0.35
        + structures["1h"]["direction"] * 0.30
        + structures["15m"]["direction"] * 0.20
        + structures["5m"]["direction"] * 0.15
    )

    last = frames["1h"].iloc[-2]

    momentum = 0

    if last["rsi"] > 55:
        momentum = 1

    elif last["rsi"] < 45:
        momentum = -1

    volume = 0

    if last["volume_ratio"] > 1.5:

        if last["close"] > last["open"]:
            volume = 1

        elif last["close"] < last["open"]:
            volume = -1

    cvd = data["cvd"]

    cvd_score = None

    if cvd is not None:

        cvd_score = float(
            np.clip(
                cvd["ratio"] / 30,
                -1,
                1
            )
        )

    funding = data["funding"]

    funding_score = None

    if funding is not None:

        if funding > 0.05:
            funding_score = -0.5

        elif funding < -0.05:
            funding_score = 0.5

        else:
            funding_score = 0

    position = position_flow(
        frames["1h"],
        data["oi"]["change"]
    )

    scores = {

        "structure": structure,

        "momentum": momentum,

        "volume": volume,

        "cvd": cvd_score,

        "position": position["score"],

        "funding": funding_score,

        "macro": macro_score(dxy),

        "news": (
            news_score / 2
            if news_score is not None
            else None
        )
    }

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

    available = 0

    for name, weight in weights.items():

        value = scores[name]

        if value is not None:

            total += value * weight

            available += weight

    normalized = (
        total / available
        if available > 0 else 0
    )

    # Legalább 60% adatlefedettség szükséges.
    if available < 0.60:

        signal = "KIVÁRÁS – HIÁNYOS ADAT"

    elif normalized >= 0.55:

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

        "strength": abs(normalized) * 100,

        "coverage": available * 100,

        "structures": structures,

        "position": position,

        "scores": scores

    }


# =========================================================
# MAIN
# =========================================================

dxy = get_dxy()

news = get_news()

news_score, news_explanation = analyze_news(
    news,
    groq_key
)

results = {}

with st.spinner(
    "SALIGA AI – Adatok lekérése és elemzése..."
):

    for symbol in SYMBOLS:

        market = get_market_with_failover(
            symbol
        )

        if not market["success"]:

            results[symbol] = {
                "success": False,
                "error": market["error"]
            }

            continue

        try:

            frames = {
                tf: add_indicators(df)
                for tf, df in market["frames"].items()
            }

            oi = get_open_interest_data(
                market["client"],
                market["market_symbol"]
            )

            funding = get_funding_data(
                market["client"],
                market["market_symbol"]
            )

            cvd = get_cvd_data(
                market["client"],
                market["market_symbol"]
            )

            data = {

                "frames": frames,

                "price": market["price"],

                "exchange": market["exchange"],

                "oi": oi,

                "funding": funding,

                "cvd": cvd

            }

            decision = decision_engine(
                data,
                dxy,
                news_score
            )

            data["decision"] = decision

            data["success"] = True

            results[symbol] = data

        except Exception as e:

            results[symbol] = {
                "success": False,
                "error": str(e)
            }


# =========================================================
# CONNECTION STATUS
# =========================================================

st.markdown("---")

st.subheader("🌐 ADATKAPCSOLATI ÁLLAPOT")

for symbol in SYMBOLS:

    data = results[symbol]

    if data["success"]:

        st.success(
            f"{symbol} | "
            f"Kapcsolódva: {data['exchange']} | "
            f"Ár: {data['price']:,.4f} USDT"
        )

    else:

        st.error(
            f"{symbol} | Nincs adatkapcsolat | "
            f"{data['error']}"
        )


# =========================================================
# FINAL SIGNALS
# =========================================================

st.markdown("---")

st.header("🎯 SALIGA AI – VÉGSŐ SZIGNÁLOK")

cols = st.columns(3)

for col, symbol in zip(cols, SYMBOLS):

    data = results[symbol]

    with col:

        if not data["success"]:

            st.error(
                f"{symbol}\n\nADATKAPCSOLATI HIBA"
            )

            continue

        decision = data["decision"]

        signal = decision["signal"]

        if "LONG" in signal:

            color = "#14532d"

            icon = "🟢"

        elif "SHORT" in signal:

            color = "#7f1d1d"

            icon = "🔴"

        else:

            color = "#854d0e"

            icon = "🟡"

        st.markdown(
            f"""
            <div style="
                background:{color};
                padding:20px;
                border-radius:12px;
                text-align:center;
                color:white;
            ">
                <h2>{symbol}</h2>
                <h1>{icon} {signal}</h1>
                <h3>
                Modellpontszám:
                {decision['strength']:.1f}/100
                </h3>
                <p>
                Adatlefedettség:
                {decision['coverage']:.0f}%
                </p>
                <p>
                Adatforrás: {data['exchange']}
                </p>
            </div>
            """,
            unsafe_allow_html=True
        )

        st.metric(
            "Aktuális ár",
            f"{data['price']:,.4f}"
        )

        st.metric(
            "OI változás",
            (
                f"{data['oi']['change']:+.3f}%"
                if data["oi"]["change"] is not None
                else "N/A"
            )
        )


# =========================================================
# DETAILED ANALYSIS
# =========================================================

st.markdown("---")

st.header("📊 RÉSZLETES ELEMZÉS")

available_symbols = [
    s for s in SYMBOLS
    if results[s]["success"]
]

if available_symbols:

    selected = st.selectbox(
        "Elemzendő piac",
        available_symbols
    )

    data = results[selected]

    decision = data["decision"]

    structures = decision["structures"]

    a, b, c, d = st.columns(4)

    a.metric(
        "Végső szignál",
        decision["signal"]
    )

    b.metric(
        "Modellpontszám",
        f"{decision['strength']:.1f}/100"
    )

    c.metric(
        "Adatlefedettség",
        f"{decision['coverage']:.0f}%"
    )

    d.metric(
        "Adatforrás",
        data["exchange"]
    )

    st.subheader("⚡ Position Flow")

    p1, p2, p3 = st.columns(3)

    p1.metric(
        "Pozícióáramlás",
        decision["position"]["text"]
    )

    p2.metric(
        "Funding",
        (
            f"{data['funding']:.4f}%"
            if data["funding"] is not None
            else "N/A"
        )
    )

    p3.metric(
        "CVD",
        (
            f"{data['cvd']['ratio']:.2f}%"
            if data["cvd"] is not None
            else "N/A"
        )
    )

    st.subheader("🏗️ Market Structure")

    structure_table = []

    for tf, value in structures.items():

        structure_table.append({

            "Idősík": tf,

            "Irány": value["text"],

            "BOS": value["bos"]

        })

    st.dataframe(
        pd.DataFrame(structure_table),
        use_container_width=True,
        hide_index=True
    )

    st.subheader("💧 Liquidity Intelligence")

    liquidity = liquidity_engine(
        data["frames"]["1h"]
    )

    l1, l2 = st.columns(2)

    l1.metric(
        "Felső szint",
        f"{liquidity['upper']:,.4f}"
    )

    l2.metric(
        "Alsó szint",
        f"{liquidity['lower']:,.4f}"
    )

    st.subheader("🧠 AI döntési háttér")

    score_table = pd.DataFrame([

        {
            "Elemzőmotor": name.upper(),

            "Pontszám": (
                f"{value:+.2f}"
                if value is not None
                else "N/A"
            )

        }

        for name, value in decision["scores"].items()

    ])

    st.dataframe(
        score_table,
        use_container_width=True,
        hide_index=True
    )

    st.info(
        f"""
        **SALIGA AI Döntési összegzés**

        Végső szignál: {decision['signal']}

        Piac: {selected}

        Adatforrás: {data['exchange']}

        Modellpontszám: {decision['strength']:.1f}/100

        Makro DXY: {
            f'{dxy:+.3f}%'
            if dxy is not None else 'N/A'
        }

        Hírhangulat: {news_explanation}

        A pontszám modellérték, nem igazolt találati
        valószínűség.
        """
    )

    # =====================================================
    # RISK ENGINE
    # =====================================================

    st.markdown("---")

    st.header("🛡️ RISK MANAGEMENT")

    price = data["price"]

    atr = float(
        data["frames"]["1h"]["atr"].iloc[-2]
    )

    signal = decision["signal"]

    risk_amount = (
        account_balance * risk_percent / 100
    )

    if "LONG" in signal:

        sl = price - 1.5 * atr

        tp = price + 3 * atr

    elif "SHORT" in signal:

        sl = price + 1.5 * atr

        tp = price - 3 * atr

    else:

        sl = None

        tp = None

    if sl is not None and atr > 0:

        distance = abs(price - sl)

        quantity = risk_amount / distance

        rr = abs(tp - price) / distance

        r1, r2, r3, r4 = st.columns(4)

        r1.metric(
            "Referenciaár",
            f"{price:,.4f}"
        )

        r2.metric(
            "Stop Loss",
            f"{sl:,.4f}"
        )

        r3.metric(
            "Take Profit",
            f"{tp:,.4f}"
        )

        r4.metric(
            "R:R",
            f"1:{rr:.2f}"
        )

        st.write(
            f"Becsült pozícióméret: {quantity:.5f}"
        )

        st.write(
            f"Kockáztatott összeg: "
            f"{risk_amount:.2f} USDT"
        )

    else:

        st.warning(
            "Nincs kereskedési javaslat. KIVÁRÁS."
        )

    # =====================================================
    # CHART
    # =====================================================

    if show_chart:

        st.markdown("---")

        st.header("📈 1H CHART")

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
                name=selected
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
            template="plotly_dark",
            height=600,
            xaxis_rangeslider_visible=False
        )

        st.plotly_chart(
            fig,
            use_container_width=True
        )

else:

    st.error(
        "Egyik adatforrás sem elérhető. "
        "A SALIGA AI nem ad kereskedési szignált."
    )


st.markdown("---")

st.caption(
    "SALIGA AI INSTITUTIONAL PRO V2 | "
    "Automatic Exchange Failover | "
    "Analytical System – Not Financial Advice"
)
