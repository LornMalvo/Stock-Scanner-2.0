"""
data_providers/base.py
=======================
Contrato que debe cumplir cualquier proveedor de datos (FMP, Polygon, IBKR,
demo...). El resto de la app (database.py, engines/*, app.py) programa
contra esta interfaz, nunca contra un proveedor concreto. Así añadir un
nuevo proveedor (ej. IBKR) es implementar esta clase, sin tocar nada más.
"""

from abc import ABC, abstractmethod


class DataProvider(ABC):

    name = "base"

    @abstractmethod
    def get_sp500_universe(self) -> list:
        """Devuelve lista de dicts: [{ticker, name, sector, industry, is_financial}, ...]"""
        raise NotImplementedError

    @abstractmethod
    def get_price_history(self, ticker: str, lookback_days: int = 400):
        """Devuelve DataFrame con columnas: date, open, high, low, close_adj, volume.
        close_adj DEBE estar ajustado por splits y dividendos."""
        raise NotImplementedError

    @abstractmethod
    def get_fundamentals(self, ticker: str) -> dict:
        """Devuelve snapshot fundamental con, como mínimo, las claves usadas
        en engines/fundamental.py y engines/technical.py (ver README)."""
        raise NotImplementedError

    @abstractmethod
    def get_sector_benchmark(self, sector: str) -> dict:
        """Devuelve múltiplos 'justos' de sector: per_justo, peg_justo,
        ev_ebitda_justo, pb_justo, ev_sales_justo."""
        raise NotImplementedError

    @abstractmethod
    def get_risk_free_rate(self) -> float:
        """Rendimiento actual del bono US10Y, en decimal (ej. 0.042)."""
        raise NotImplementedError

    @abstractmethod
    def get_market_regime(self) -> dict:
        """Devuelve {'price': ..., 'mm200': ..., 'below_mm200': bool} para el S&P 500."""
        raise NotImplementedError
