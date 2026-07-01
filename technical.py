"""
data_providers/polygon_provider.py
====================================
Proveedor especializado en PRECIOS (Polygon.io tiene el mejor histórico
ajustado por splits/dividendos con "adjusted=true"). Se puede combinar con
FMPProvider para fundamentales: ver engines/scanner.py -> HybridProvider.

Requiere POLYGON_API_KEY en el entorno.
"""

from datetime import datetime, timedelta
import requests
import pandas as pd

from .base import DataProvider
import config

BASE_URL = "https://api.polygon.io"


class PolygonProvider(DataProvider):
    name = "polygon"

    def __init__(self, api_key: str = None):
        self.api_key = api_key or config.POLYGON_API_KEY
        if not self.api_key:
            raise ValueError("Falta POLYGON_API_KEY. Defínela en el entorno o en .env")

    def _get(self, path, **params):
        params["apiKey"] = self.api_key
        resp = requests.get(f"{BASE_URL}{path}", params=params, timeout=20)
        resp.raise_for_status()
        return resp.json()

    def get_price_history(self, ticker: str, lookback_days: int = 400):
        end = datetime.utcnow().date()
        start = end - timedelta(days=lookback_days)
        data = self._get(
            f"/v2/aggs/ticker/{ticker}/range/1/day/{start.isoformat()}/{end.isoformat()}",
            adjusted="true", sort="asc", limit=50000
        )
        results = data.get("results", [])
        if not results:
            return pd.DataFrame(columns=["date", "open", "high", "low", "close_adj", "volume"])
        df = pd.DataFrame(results)
        df["date"] = pd.to_datetime(df["t"], unit="ms")
        df = df.rename(columns={"o": "open", "h": "high", "l": "low", "c": "close_adj", "v": "volume"})
        return df[["date", "open", "high", "low", "close_adj", "volume"]]

    def get_market_regime(self) -> dict:
        df = self.get_price_history("I:SPX", lookback_days=250)
        if df.empty or len(df) < 200:
            return {"price": None, "mm200": None, "below_mm200": False}
        mm200 = df["close_adj"].rolling(200).mean().iloc[-1]
        price = df["close_adj"].iloc[-1]
        return {"price": float(price), "mm200": float(mm200), "below_mm200": bool(price < mm200)}

    # Polygon no es una fuente primaria de fundamentales estructurados/consenso
    # de analistas a nivel retail; se recomienda usarlo en modo híbrido junto
    # a FMPProvider (ver engines/scanner.HybridProvider). Se implementan estos
    # métodos igualmente para cumplir el contrato de DataProvider.
    def get_sp500_universe(self) -> list:
        data = self._get("/v3/reference/tickers", market="stocks", active="true", limit=1000)
        out = []
        for row in data.get("results", []):
            out.append({
                "ticker": row.get("ticker"),
                "name": row.get("name"),
                "sector": row.get("sic_description", "Unknown"),
                "industry": row.get("sic_description", "Unknown"),
                "is_financial": "bank" in (row.get("sic_description", "").lower()),
            })
        return out

    def get_fundamentals(self, ticker: str) -> dict:
        data = self._get(f"/vX/reference/financials", ticker=ticker, limit=2, timeframe="annual")
        raise NotImplementedError(
            "PolygonProvider.get_fundamentals: usa HybridProvider (Polygon precios + FMP fundamentales)."
        )

    def get_sector_benchmark(self, sector: str) -> dict:
        raise NotImplementedError("Usa FMPProvider para benchmarks de sector.")

    def get_risk_free_rate(self) -> float:
        raise NotImplementedError("Usa FMPProvider o FRED para US10Y.")
