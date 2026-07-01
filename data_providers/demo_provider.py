"""
data_providers/demo_provider.py
=================================
Proveedor 100% sintético (sin red, sin API key) pensado para:
  1) Desarrollo y testing de engines/fundamental.py y engines/technical.py
  2) Poder abrir la app y ver el flujo completo funcionando sin credenciales

Genera un universo reducido de "S&P 500 simulado" con distintos arquetipos
(value barato, calidad cara, hyper-growth con EPS negativo, financiera, etc.)
para que se puedan observar todas las ramas de la lógica de valoración.

IMPORTANTE: los datos son ficticios. Nunca usar DemoProvider para decisiones
de inversión reales; su único propósito es validar la arquitectura.
"""

import numpy as np
import pandas as pd
from datetime import datetime, timedelta

from .base import DataProvider

_SECTORS = ["Technology", "Healthcare", "Financial Services", "Consumer Cyclical",
            "Industrials", "Energy", "Utilities"]

_ARCHETYPES = [
    # ticker, sector, is_financial, eps_pos, growth_bucket
    ("VALCO", "Industrials", False, True, "low"),
    ("QUALX", "Healthcare", False, True, "mid"),
    ("GROWZ", "Technology", False, True, "high"),
    ("HYPRN", "Technology", False, False, "hyper"),   # EPS negativo, hyper-growth
    ("BANKA", "Financial Services", True, True, "low"),
    ("UTILB", "Utilities", False, True, "low"),
    ("CYCLC", "Consumer Cyclical", False, True, "mid"),
    ("ENRGX", "Energy", False, True, "low"),
    ("SAASY", "Technology", False, False, "hyper"),
    ("PHARM", "Healthcare", False, True, "mid"),
]


class DemoProvider(DataProvider):
    name = "demo"

    def __init__(self, seed: int = 42):
        self.rng = np.random.default_rng(seed)

    def get_sp500_universe(self) -> list:
        out = []
        for ticker, sector, is_fin, eps_pos, bucket in _ARCHETYPES:
            out.append({
                "ticker": ticker,
                "name": f"{ticker} Demo Corp",
                "sector": sector,
                "industry": sector,
                "is_financial": is_fin,
            })
        return out

    def _seeded_rng(self, ticker):
        # RNG determinista por ticker para que recargar la app dé resultados estables
        seed = abs(hash(ticker)) % (2**32)
        return np.random.default_rng(seed)

    def get_price_history(self, ticker: str, lookback_days: int = 400):
        rng = self._seeded_rng(ticker + "_price")
        n = lookback_days
        dates = pd.bdate_range(end=datetime.utcnow().date(), periods=n)

        arche = next((a for a in _ARCHETYPES if a[0] == ticker), None)
        bucket = arche[4] if arche else "mid"
        drift_map = {"low": 0.0002, "mid": 0.0004, "high": 0.0008, "hyper": 0.0010}
        vol_map = {"low": 0.012, "mid": 0.018, "high": 0.025, "hyper": 0.035}
        drift = drift_map.get(bucket, 0.0004)
        vol = vol_map.get(bucket, 0.018)

        returns = rng.normal(drift, vol, n)
        # Inyectar un pullback reciente para que algunos tickers disparen la
        # condición técnica "precio > MM200 y < MM50"
        tail = min(15, n)
        if tail > 0 and rng.random() < 0.6:
            returns[-tail:] -= rng.uniform(0.001, 0.006, tail)

        price = 100 * np.exp(np.cumsum(returns))
        close = pd.Series(price, index=dates)
        high = close * (1 + rng.uniform(0.001, 0.01, n))
        low = close * (1 - rng.uniform(0.001, 0.01, n))
        openp = close.shift(1).fillna(close.iloc[0])
        volume = rng.integers(1_000_000, 8_000_000, n)

        df = pd.DataFrame({
            "date": dates, "open": openp.values, "high": high.values,
            "low": low.values, "close_adj": close.values, "volume": volume
        })
        return df

    def get_fundamentals(self, ticker: str) -> dict:
        rng = self._seeded_rng(ticker + "_fund")
        arche = next((a for a in _ARCHETYPES if a[0] == ticker), None)
        is_financial = arche[2] if arche else False
        eps_positive = arche[3] if arche else True
        bucket = arche[4] if arche else "mid"

        price_hist = self.get_price_history(ticker, 5)
        price = float(price_hist["close_adj"].iloc[-1])

        growth_map = {"low": (0.02, 0.06), "mid": (0.08, 0.15), "high": (0.18, 0.28), "hyper": (0.25, 0.45)}
        g_lo, g_hi = growth_map.get(bucket, (0.08, 0.15))
        revenue_growth = float(rng.uniform(g_lo, g_hi))
        eps_growth_fwd = float(rng.uniform(g_lo, g_hi)) if eps_positive else None

        shares = float(rng.uniform(200e6, 3000e6))
        dilution = float(rng.uniform(-0.01, 0.05))  # puede haber recompras (negativo)
        shares_prior = shares / (1 + dilution)

        revenue = price * shares * rng.uniform(0.3, 1.2)
        revenue_prior = revenue / (1 + revenue_growth)
        ebitda_margin = float(rng.uniform(0.15, 0.45))
        ebitda = revenue * ebitda_margin
        net_income = ebitda * rng.uniform(0.3, 0.7) if eps_positive else -abs(revenue * rng.uniform(0.02, 0.15))
        net_income_prior = net_income / (1 + max(revenue_growth, 0.01)) if net_income else net_income

        cfo = net_income * rng.uniform(1.0, 1.4) if net_income and net_income > 0 else revenue * rng.uniform(0.02, 0.08)
        fcf = cfo * rng.uniform(0.6, 0.9)

        total_assets = revenue * rng.uniform(0.8, 2.0)
        total_assets_prior = total_assets * rng.uniform(0.9, 1.05)
        current_assets = total_assets * rng.uniform(0.3, 0.5)
        current_assets_prior = total_assets_prior * rng.uniform(0.3, 0.5)
        current_liabilities = total_assets * rng.uniform(0.15, 0.3)
        current_liabilities_prior = total_assets_prior * rng.uniform(0.15, 0.32)
        long_term_debt = total_assets * rng.uniform(0.1, 0.4)
        long_term_debt_prior = total_assets_prior * rng.uniform(0.12, 0.42)
        gross_profit = revenue * rng.uniform(0.35, 0.75)
        gross_profit_prior = revenue_prior * rng.uniform(0.33, 0.73)

        eps_ttm = (net_income / shares) if net_income else None
        eps_forward = eps_ttm * (1 + eps_growth_fwd) if (eps_ttm and eps_growth_fwd) else eps_ttm

        book_value_per_share = (total_assets - long_term_debt) / shares * rng.uniform(0.5, 1.0)
        peg = float(rng.uniform(0.6, 3.0))
        num_analysts = int(rng.integers(3, 30))
        price_target_avg = price * float(rng.uniform(0.85, 1.35))
        week52_high = price * float(rng.uniform(1.02, 1.45))

        return {
            "price": price,
            "eps_forward": eps_forward,
            "eps_ttm": eps_ttm,
            "growth_eps_fwd": eps_growth_fwd,
            "revenue_growth": revenue_growth,
            "fcf": fcf,
            "shares_outstanding": shares,
            "shares_outstanding_prior_year": shares_prior,
            "ev": price * shares + long_term_debt - (current_assets - current_liabilities) * 0.2,
            "ev_ebitda_actual": float(rng.uniform(6, 22)),
            "ev_sales_fwd": float(rng.uniform(2, 12)),
            "ebitda": ebitda,
            "ebitda_margin": ebitda_margin,
            "net_income": net_income,
            "net_income_prior": net_income_prior,
            "cfo": cfo,
            "total_assets": total_assets,
            "total_assets_prior": total_assets_prior,
            "current_assets": current_assets,
            "current_assets_prior": current_assets_prior,
            "current_liabilities": current_liabilities,
            "current_liabilities_prior": current_liabilities_prior,
            "long_term_debt": long_term_debt,
            "long_term_debt_prior": long_term_debt_prior,
            "gross_profit": gross_profit,
            "gross_profit_prior": gross_profit_prior,
            "revenue": revenue,
            "revenue_prior": revenue_prior,
            "book_value_per_share": book_value_per_share if is_financial else None,
            "peg": peg,
            "price_target_avg": price_target_avg,
            "num_analysts": num_analysts,
            "week52_high": week52_high,
        }

    def get_sector_benchmark(self, sector: str) -> dict:
        rng = self._seeded_rng(sector + "_bench")
        return {
            "per_justo": float(rng.uniform(14, 28)),
            "peg_justo": float(rng.uniform(1.0, 2.0)),
            "ev_ebitda_justo": float(rng.uniform(8, 16)),
            "pb_justo": float(rng.uniform(1.2, 3.0)),
            "ev_sales_justo": float(rng.uniform(3, 9)),
        }

    def get_risk_free_rate(self) -> float:
        return 0.042  # US10Y simulado

    def get_market_regime(self) -> dict:
        # Simula el S&P500 con un ligero sesgo alcista para que el usuario
        # pueda alternar manualmente el régimen desde la UI si quiere testear
        # la excepción macro del margen de seguridad.
        return {"price": 5450.0, "mm200": 5200.0, "below_mm200": False}

    def get_insider_activity(self, ticker: str) -> dict:
        rng = self._seeded_rng(ticker + "_insider")
        # ~40% de los tickers demo muestran acumulación neta de insiders
        bullish = rng.random() < 0.4
        if bullish:
            buy_value = float(rng.uniform(80_000, 900_000))
            sell_value = float(rng.uniform(0, buy_value * 0.3))
        else:
            buy_value = float(rng.uniform(0, 60_000))
            sell_value = float(rng.uniform(50_000, 500_000))

        buy_count = int(rng.integers(1, 6)) if buy_value > 0 else 0
        sell_count = int(rng.integers(0, 4)) if sell_value > 0 else 0
        net_value = buy_value - sell_value

        import config as _cfg
        signal = (
            buy_value >= _cfg.INSIDER_MIN_BUY_VALUE_USD
            and (sell_value == 0 or buy_value >= sell_value * _cfg.INSIDER_BUY_SELL_RATIO_MIN)
        )
        return {
            "buy_value": buy_value, "sell_value": sell_value,
            "buy_count": buy_count, "sell_count": sell_count,
            "net_value": net_value, "signal": signal, "source": "demo",
        }
