"""
data_providers/fmp_provider.py
================================
Proveedor "profesional" de fundamentales usando Financial Modeling Prep.
Requiere FMP_API_KEY en el entorno (ver .env.example).

NOTA DE DISEÑO: los endpoints/campos exactos de FMP cambian con el plan
contratado; este módulo centraliza el mapeo "respuesta FMP -> payload
interno" en _map_fundamentals(), que es el único sitio a tocar si FMP
cambia su esquema. El resto de la app nunca ve el JSON crudo de FMP.
"""

import requests
import pandas as pd

from .base import DataProvider
import config

BASE_URL = "https://financialmodelingprep.com/api/v3"
BASE_URL_STABLE = "https://financialmodelingprep.com/stable"


class FMPProvider(DataProvider):
    name = "fmp"

    def __init__(self, api_key: str = None):
        self.api_key = api_key or config.FMP_API_KEY
        if not self.api_key:
            raise ValueError("Falta FMP_API_KEY. Defínela en el entorno o en .env")

    # -- helpers ------------------------------------------------------------
    def _get(self, path, base=BASE_URL, **params):
        params["apikey"] = self.api_key
        resp = requests.get(f"{base}/{path}", params=params, timeout=20)
        resp.raise_for_status()
        return resp.json()

    # -- universo -------------------------------------------------------------
    def get_sp500_universe(self) -> list:
        data = self._get("sp500_constituent")
        out = []
        financial_sectors = {"Financial Services", "Financials", "Banks", "Insurance"}
        for row in data:
            sector = row.get("sector", "")
            out.append({
                "ticker": row.get("symbol"),
                "name": row.get("name"),
                "sector": sector,
                "industry": row.get("subSector", sector),
                "is_financial": sector in financial_sectors,
            })
        return out

    # -- precios --------------------------------------------------------------
    def get_price_history(self, ticker: str, lookback_days: int = 400):
        data = self._get(
            f"historical-price-full/{ticker}",
            serietype="line", timeseries=lookback_days
        )
        hist = data.get("historical", [])
        if not hist:
            return pd.DataFrame(columns=["date", "open", "high", "low", "close_adj", "volume"])
        df = pd.DataFrame(hist)
        # FMP "adjClose" ya viene ajustado por splits/dividendos
        df = df.rename(columns={"adjClose": "close_adj"})
        for col in ("open", "high", "low", "close_adj", "volume"):
            if col not in df.columns:
                df[col] = df.get("close", df.get("close_adj"))
        df["date"] = pd.to_datetime(df["date"])
        return df[["date", "open", "high", "low", "close_adj", "volume"]].sort_values("date")

    # -- fundamentales ----------------------------------------------------------
    def get_fundamentals(self, ticker: str) -> dict:
        profile = self._get(f"profile/{ticker}")
        quote = self._get(f"quote/{ticker}")
        key_metrics = self._get(f"key-metrics-ttm/{ticker}")
        ratios = self._get(f"ratios-ttm/{ticker}")
        estimates = self._get(f"analyst-estimates/{ticker}", period="annual", limit=1)
        growth = self._get(f"financial-growth/{ticker}", period="annual", limit=2)
        price_target = self._get(f"price-target-summary/{ticker}")
        cashflow = self._get(f"cash-flow-statement/{ticker}", period="annual", limit=2)
        balance = self._get(f"balance-sheet-statement/{ticker}", period="annual", limit=2)
        income = self._get(f"income-statement/{ticker}", period="annual", limit=2)

        return self._map_fundamentals(
            profile, quote, key_metrics, ratios, estimates, growth,
            price_target, cashflow, balance, income
        )

    def _map_fundamentals(self, profile, quote, key_metrics, ratios, estimates,
                           growth, price_target, cashflow, balance, income):
        p = profile[0] if profile else {}
        q = quote[0] if quote else {}
        km = key_metrics[0] if key_metrics else {}
        rt = ratios[0] if ratios else {}
        est = estimates[0] if estimates else {}
        g_now = growth[0] if growth else {}
        pt = price_target[0] if price_target else {}
        cf_now = cashflow[0] if cashflow else {}
        cf_prior = cashflow[1] if len(cashflow) > 1 else {}
        bs_now = balance[0] if balance else {}
        bs_prior = balance[1] if len(balance) > 1 else {}
        inc_now = income[0] if income else {}
        inc_prior = income[1] if len(income) > 1 else {}

        shares_now = q.get("sharesOutstanding")
        shares_prior = bs_prior.get("commonStock")  # aproximación si no hay serie de acciones

        return {
            "price": q.get("price"),
            "eps_forward": est.get("estimatedEpsAvg"),
            "eps_ttm": q.get("eps"),
            "growth_eps_fwd": g_now.get("epsgrowth"),
            "revenue_growth": g_now.get("revenueGrowth"),
            "fcf": km.get("freeCashFlowTTM") or cf_now.get("freeCashFlow"),
            "shares_outstanding": shares_now,
            "shares_outstanding_prior_year": shares_prior,
            "ev": km.get("enterpriseValueTTM"),
            "ev_ebitda_actual": km.get("evToEbitdaTTM") or rt.get("enterpriseValueMultipleTTM"),
            "ev_sales_fwd": km.get("evToSalesTTM"),
            "ebitda": inc_now.get("ebitda"),
            "ebitda_margin": (inc_now.get("ebitda", 0) / inc_now.get("revenue", 1)) if inc_now.get("revenue") else None,
            "net_income": inc_now.get("netIncome"),
            "net_income_prior": inc_prior.get("netIncome"),
            "cfo": cf_now.get("operatingCashFlow"),
            "total_assets": bs_now.get("totalAssets"),
            "total_assets_prior": bs_prior.get("totalAssets"),
            "current_assets": bs_now.get("totalCurrentAssets"),
            "current_assets_prior": bs_prior.get("totalCurrentAssets"),
            "current_liabilities": bs_now.get("totalCurrentLiabilities"),
            "current_liabilities_prior": bs_prior.get("totalCurrentLiabilities"),
            "long_term_debt": bs_now.get("longTermDebt"),
            "long_term_debt_prior": bs_prior.get("longTermDebt"),
            "gross_profit": inc_now.get("grossProfit"),
            "gross_profit_prior": inc_prior.get("grossProfit"),
            "revenue": inc_now.get("revenue"),
            "revenue_prior": inc_prior.get("revenue"),
            "book_value_per_share": km.get("bookValuePerShareTTM"),
            "peg": rt.get("pegRatioTTM"),
            "price_target_avg": pt.get("lastMonthAvgPriceTarget") or pt.get("allTimeAvgPriceTarget"),
            "num_analysts": pt.get("lastMonthCount"),
            "week52_high": q.get("yearHigh"),
        }

    # -- sector / macro ---------------------------------------------------------
    def get_sector_benchmark(self, sector: str) -> dict:
        # FMP no ofrece un "múltiplo justo de sector" directo y fiable en todos
        # los planes; se recomienda calcularlo agregando (mediana) los múltiplos
        # de los peers del sector obtenidos vía /stock-screener. Este método
        # deja el punto de extensión listo; por defecto usa el endpoint sector-pe.
        data = self._get("sector_price_earning_ratio", base=BASE_URL, exchange="NYSE")
        row = next((d for d in data if d.get("sector") == sector), None)
        per_justo = float(row["pe"]) if row else 20.0
        return {
            "per_justo": per_justo,
            "peg_justo": 1.5,
            "ev_ebitda_justo": 12.0,
            "pb_justo": 2.5,
            "ev_sales_justo": 4.0,
        }

    def get_risk_free_rate(self) -> float:
        data = self._get("treasury", base=BASE_URL, from_="2024-01-01")
        if isinstance(data, list) and data:
            return float(data[0].get("year10", 0.04))
        return 0.04

    def get_market_regime(self) -> dict:
        df = self.get_price_history("%5EGSPC", lookback_days=250)
        if df.empty or len(df) < 200:
            return {"price": None, "mm200": None, "below_mm200": False}
        mm200 = df["close_adj"].rolling(200).mean().iloc[-1]
        price = df["close_adj"].iloc[-1]
        return {"price": float(price), "mm200": float(mm200), "below_mm200": bool(price < mm200)}

    # -- insider trading ---------------------------------------------------------
    def get_insider_activity(self, ticker: str) -> dict:
        """
        Endpoint FMP de transacciones de insiders (Form 4 de la SEC).
        Agrega el flujo neto de compras vs. ventas en los últimos
        config.INSIDER_LOOKBACK_DAYS días para producir una señal booleana.

        NOTA: el nombre/version exacto del endpoint (`/v4/insider-trading`)
        puede variar según el plan de FMP contratado; si tu plan expone un
        endpoint distinto, este es el único método a adaptar.
        """
        from datetime import datetime, timedelta
        import config as _cfg

        cutoff = datetime.utcnow() - timedelta(days=_cfg.INSIDER_LOOKBACK_DAYS)

        try:
            data = self._get(
                "insider-trading", base="https://financialmodelingprep.com/api/v4",
                symbol=ticker, page=0, limit=1000
            )
        except requests.RequestException:
            data = []

        buy_value, sell_value, buy_count, sell_count = 0.0, 0.0, 0, 0

        for tx in data:
            tx_date_str = tx.get("transactionDate") or tx.get("filingDate")
            if not tx_date_str:
                continue
            try:
                tx_date = datetime.fromisoformat(tx_date_str[:10])
            except ValueError:
                continue
            if tx_date < cutoff:
                continue

            acq_disp = (tx.get("acquisitionOrDisposition") or "").upper()
            shares = float(tx.get("securitiesTransacted") or 0)
            price = float(tx.get("price") or 0)
            value = shares * price

            if acq_disp == "A":
                buy_value += value
                buy_count += 1
            elif acq_disp == "D":
                sell_value += value
                sell_count += 1

        net_value = buy_value - sell_value
        signal = (
            buy_value >= _cfg.INSIDER_MIN_BUY_VALUE_USD
            and (
                sell_value == 0
                or buy_value >= sell_value * _cfg.INSIDER_BUY_SELL_RATIO_MIN
            )
        )

        return {
            "buy_value": buy_value, "sell_value": sell_value,
            "buy_count": buy_count, "sell_count": sell_count,
            "net_value": net_value, "signal": signal, "source": "fmp",
        }
