# SALIGA AI – Market Intelligence (MVP)

## Mi ez?
Egy első, futtatható Python-alapú kriptopiaci elemző felület. BTC, SOL és ETH piacot figyel,
1m/5m/15m/1h/4h idősíkokat hasonlít össze, és nyilvános Binance + Bybit adatokat használ.

## Telepítés Windows alatt (PyCharm vagy terminál)
1. Telepítsd a Python 3.11 vagy 3.12 verziót.
2. Csomagold ki a ZIP-et.
3. Nyisd meg a mappát PyCharmban.
4. A PyCharm Terminal ablakában futtasd:
   `python -m venv .venv`
5. Aktiválás Windows PowerShellben:
   `.venv\\Scripts\\Activate.ps1`
6. Függőségek telepítése:
   `pip install -r requirements.txt`
7. Indítás:
   `streamlit run app.py`
8. A program a böngészőben nyílik meg (általában http://localhost:8501).

## Fontos
- Az alapverzióhoz nem kell API-kulcs.
- A program nem ad automatikus megbízást, csak elemzést.
- A százalékos „jelzés erőssége” jelenleg pontszámból képzett kijelzés, NEM valódi,
  historikusan kalibrált valószínűség vagy garantált találati arány.
- A gyertyából számolt delta csak közelítés, nem valódi bid/ask CVD.
- A következő fejlesztési lépés a historikus backtest, majd a valós order-flow adatok
  és makrogazdasági naptár integrálása.
