SALIGA AI — MARKET INTELLIGENCE PRO v3.0

TELEPÍTÉS
1. Telepíts Python 3.10 vagy újabb verziót.
2. Csomagold ki a ZIP-et egy mappába.
3. Nyiss terminált ebben a mappában.
4. Telepítsd a függőségeket:
   pip install -r requirements.txt
5. Indítsd el:
   streamlit run app.py

ADATFORRÁSOK
- Elsődleges: OKX USDT perpetual piacok.
- Tartalék: Bybit USDT perpetual piacok.
- Binance API-t nem használ.
- Ha egy tőzsde elérhetetlen (például ország-/hálózati 403), megpróbálja a következő forrást.
- Egy adott coin összes gyertyáját egyetlen tőzsdéről kéri le; nem keveri a venue-k árait.

FUNKCIÓK
- BTC, ETH, SOL egyidejű vizsgálata.
- 1m, 5m, 15m, 1h, 4h, 1d OHLCV.
- Több idősíkos piaci struktúra és BOS-közelítés.
- RSI, ATR, SMA20/SMA50 (EMA nincs).
- OI és funding lekérdezési kísérlet.
- Taker oldali delta a legutóbb lekért kötésekből.
- Orderbook bid/ask egyensúly.
- Equal highs/lows, liquidity sweep és FVG-közelítés.
- OHLCV-alapú volume profile proxy.
- DXY, VIX, US10Y, NASDAQ, S&P500 makróváltozás.
- Kriptós RSS hírek kulcsszavas értékelése.
- Opcionális Groq AI magyarázat (nem ez számolja a jelet).
- ATR-alapú kockázati terv.
- Tartós SQLite jelzésnapló és CSV export.

FONTOS KORLÁTOK
- A CVD itt nem teljes tőzsdei kumulatív delta: az utolsó lekért kötések taker oldali mintája.
- OI-history támogatás tőzsde- és CCXT-verziófüggő; ha nincs adat, a rendszer jelzi.
- A flow besorolás ár + OI + delta alapján következtetés, nem bizonyítja a piaci szereplők szándékát.
- A liquidity / FVG / volume profile részek OHLCV-alapú közelítések, nem teljes order-flow vagy likvidációs heatmap.
- A makró Yahoo Finance adatforrásból érkezik, késhet vagy időnként hiányozhat.
- A modellpontszám nem találati valószínűség. A rendszer még nem tartalmaz validált backtestet vagy statisztikai kalibrációt.
- Éles kereskedés előtt paper trading, költségmodellezés és független tesztelés szükséges.

A program nem küld megbízást, nem kezel API-kulcsos kereskedési hozzáférést, és nem automata kereskedő.
