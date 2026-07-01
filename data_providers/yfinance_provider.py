"""
data_providers/yfinance_provider.py
=====================================
Proveedor gratuito basado en Yahoo Finance (librería `yfinance`), sin
necesidad de API key. Útil para arrancar sin credenciales y con datos reales
(no sintéticos), a cambio de menor fiabilidad/consistencia que FMP:

  * yfinance expone campos de `Ticker.info` que Yahoo puede cambiar sin
    aviso (nombres de campo, disponibilidad por ticker). Este módulo lee
    todo de forma defensiva (try/except + varios nombres candidatos) y
    devuelve None en lo que no encuentra, en vez de romper el pipeline.
  * No existe un "múltiplo justo de sector" en Yahoo Finance. Se aproxima
    con una tabla estática de defaults razonables por sector (ver
    _SECTOR_DEFAULTS) — mejorable calculando medianas de peers reales.
  * El histórico de precios SÍ es fiable y viene ajustado por splits y
    dividendos (`auto_adjust=True`), cumpliendo el requisito de "adjusted
    close" del enunciado.
"""

from datetime import datetime, timedelta
from io import StringIO
import time

import numpy as np
import pandas as pd
import requests

from .base import DataProvider
import config

try:
    import yfinance as yf
except ImportError:  # pragma: no cover
    yf = None

# Wikipedia (y Yahoo) rechazan peticiones sin cabecera de navegador con un
# 403/errores de parseo intermitentes. Con esta cabecera se scrapea de forma
# mucho más fiable.
_BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
}


def _retry(fn, retries=None, backoff=None, label=""):
    """Ejecuta fn() con reintentos y backoff exponencial. Pensado para las
    llamadas más propensas a rate-limiting de Yahoo (429 / 'Too Many
    Requests'). Si todos los intentos fallan, relanza la última excepción."""
    retries = config.YF_MAX_RETRIES if retries is None else retries
    backoff = config.YF_BACKOFF_BASE_SECONDS if backoff is None else backoff
    last_exc = None
    for attempt in range(retries + 1):
        try:
            return fn()
        except Exception as exc:  # yfinance lanza distintos tipos según versión
            last_exc = exc
            if attempt < retries:
                time.sleep(backoff * (2 ** attempt))
                continue
    raise last_exc


# Múltiplos "justos" de sector por defecto — aproximación razonable cuando
# no se dispone de un proveedor con benchmarks de sector reales (tipo FMP).
_SECTOR_DEFAULTS = {
    "Technology": {"per_justo": 26, "peg_justo": 1.8, "ev_ebitda_justo": 16, "pb_justo": 6.0, "ev_sales_justo": 6.0},
    "Healthcare": {"per_justo": 22, "peg_justo": 1.6, "ev_ebitda_justo": 14, "pb_justo": 4.0, "ev_sales_justo": 4.5},
    "Financial Services": {"per_justo": 13, "peg_justo": 1.2, "ev_ebitda_justo": 10, "pb_justo": 1.5, "ev_sales_justo": 3.0},
    "Consumer Cyclical": {"per_justo": 20, "peg_justo": 1.5, "ev_ebitda_justo": 11, "pb_justo": 3.5, "ev_sales_justo": 2.0},
    "Consumer Defensive": {"per_justo": 20, "peg_justo": 1.8, "ev_ebitda_justo": 12, "pb_justo": 4.5, "ev_sales_justo": 2.0},
    "Industrials": {"per_justo": 19, "peg_justo": 1.5, "ev_ebitda_justo": 11, "pb_justo": 3.5, "ev_sales_justo": 2.0},
    "Energy": {"per_justo": 12, "peg_justo": 1.3, "ev_ebitda_justo": 6, "pb_justo": 1.8, "ev_sales_justo": 1.5},
    "Utilities": {"per_justo": 17, "peg_justo": 2.5, "ev_ebitda_justo": 10, "pb_justo": 1.8, "ev_sales_justo": 2.5},
    "Basic Materials": {"per_justo": 15, "peg_justo": 1.4, "ev_ebitda_justo": 8, "pb_justo": 2.0, "ev_sales_justo": 1.5},
    "Real Estate": {"per_justo": 18, "peg_justo": 2.0, "ev_ebitda_justo": 16, "pb_justo": 2.0, "ev_sales_justo": 6.0},
    "Communication Services": {"per_justo": 20, "peg_justo": 1.6, "ev_ebitda_justo": 10, "pb_justo": 3.0, "ev_sales_justo": 3.0},
}
_DEFAULT_BENCHMARK = {"per_justo": 20.0, "peg_justo": 1.6, "ev_ebitda_justo": 12.0, "pb_justo": 3.0, "ev_sales_justo": 3.0}

# Fallback SOLO para cuando el scraping de Wikipedia falla por completo (sin
# red, bloqueo, cambio de estructura de la página...). NO pretende ser el
# S&P 500 completo — es un conjunto diversificado de ~50 blue chips que
# cubre las 11 categorías GICS, para que un escaneo degradado siga siendo
# mínimamente representativo en vez de limitarse a un puñado de tickers.
# Se marca explícitamente como 'fallback' (ver last_universe_source) para
# que la UI avise de que no es el universo completo.
_FALLBACK_UNIVERSE = [
    ("AAPL", "Apple Inc.", "Technology"), ("MSFT", "Microsoft Corp.", "Technology"),
    ("NVDA", "NVIDIA Corp.", "Technology"), ("AVGO", "Broadcom Inc.", "Technology"),
    ("ORCL", "Oracle Corp.", "Technology"), ("CRM", "Salesforce Inc.", "Technology"),
    ("AMZN", "Amazon.com Inc.", "Consumer Cyclical"), ("TSLA", "Tesla Inc.", "Consumer Cyclical"),
    ("HD", "Home Depot Inc.", "Consumer Cyclical"), ("MCD", "McDonald's Corp.", "Consumer Cyclical"),
    ("NKE", "Nike Inc.", "Consumer Cyclical"), ("LOW", "Lowe's Companies Inc.", "Consumer Cyclical"),
    ("GOOGL", "Alphabet Inc.", "Communication Services"), ("META", "Meta Platforms Inc.", "Communication Services"),
    ("NFLX", "Netflix Inc.", "Communication Services"), ("DIS", "Walt Disney Co.", "Communication Services"),
    ("JPM", "JPMorgan Chase & Co.", "Financial Services"), ("BAC", "Bank of America Corp.", "Financial Services"),
    ("WFC", "Wells Fargo & Co.", "Financial Services"), ("GS", "Goldman Sachs Group Inc.", "Financial Services"),
    ("V", "Visa Inc.", "Financial Services"), ("MA", "Mastercard Inc.", "Financial Services"),
    ("JNJ", "Johnson & Johnson", "Healthcare"), ("UNH", "UnitedHealth Group Inc.", "Healthcare"),
    ("PFE", "Pfizer Inc.", "Healthcare"), ("LLY", "Eli Lilly and Co.", "Healthcare"),
    ("ABBV", "AbbVie Inc.", "Healthcare"), ("MRK", "Merck & Co. Inc.", "Healthcare"),
    ("XOM", "Exxon Mobil Corp.", "Energy"), ("CVX", "Chevron Corp.", "Energy"),
    ("COP", "ConocoPhillips", "Energy"),
    ("PG", "Procter & Gamble Co.", "Consumer Defensive"), ("KO", "Coca-Cola Co.", "Consumer Defensive"),
    ("PEP", "PepsiCo Inc.", "Consumer Defensive"), ("WMT", "Walmart Inc.", "Consumer Defensive"),
    ("COST", "Costco Wholesale Corp.", "Consumer Defensive"),
    ("GE", "General Electric Co.", "Industrials"), ("CAT", "Caterpillar Inc.", "Industrials"),
    ("BA", "Boeing Co.", "Industrials"), ("UPS", "United Parcel Service Inc.", "Industrials"),
    ("HON", "Honeywell International Inc.", "Industrials"),
    ("NEE", "NextEra Energy Inc.", "Utilities"), ("DUK", "Duke Energy Corp.", "Utilities"),
    ("SO", "Southern Co.", "Utilities"),
    ("LIN", "Linde plc", "Basic Materials"), ("APD", "Air Products and Chemicals Inc.", "Basic Materials"),
    ("FCX", "Freeport-McMoRan Inc.", "Basic Materials"),
    ("PLD", "Prologis Inc.", "Real Estate"), ("AMT", "American Tower Corp.", "Real Estate"),
    ("EQIX", "Equinix Inc.", "Real Estate"),
]


def _num(value):
    if value is None:
        return None
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return None if (np.isnan(v) or np.isinf(v)) else v


def _row(df: pd.DataFrame, candidates, col_idx=0):
    """Busca la primera fila cuyo índice coincida con alguno de los nombres
    candidatos (yfinance cambia el naming entre versiones) y devuelve el
    valor en la columna col_idx (0 = periodo más reciente)."""
    if df is None or df.empty:
        return None
    for name in candidates:
        if name in df.index:
            try:
                val = df.loc[name].iloc[col_idx]
                return _num(val)
            except (IndexError, KeyError):
                continue
    return None


class YFinanceProvider(DataProvider):
    name = "yfinance"
    request_delay_seconds = config.YF_REQUEST_DELAY_SECONDS

    def __init__(self):
        if yf is None:
            raise ImportError(
                "Falta la librería 'yfinance'. Añádela a requirements.txt e "
                "instala con `pip install yfinance`."
            )
        self.last_universe_source = "native"

    # -- universo -------------------------------------------------------------
    def get_sp500_universe(self) -> list:
        """
        Lee la composición actual del S&P 500 desde Wikipedia (no requiere
        API key). Usa cabeceras de navegador porque tanto Wikipedia como
        algunos proxies rechazan peticiones sin User-Agent.

        Si falla (sin red, bloqueo, cambio de estructura de la página...),
        cae a `_FALLBACK_UNIVERSE` (un subconjunto diversificado, NO el
        S&P 500 completo) y lo marca en `self.last_universe_source =
        'fallback'` para que engines/scanner.py pueda avisar en la UI.
        """
        financial_sectors = {"Financial Services", "Financials"}
        try:
            resp = requests.get(
                "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies",
                headers=_BROWSER_HEADERS, timeout=15
            )
            resp.raise_for_status()
            tables = pd.read_html(StringIO(resp.text))
            table = tables[0]
            out = []
            for _, row in table.iterrows():
                sector = str(row.get("GICS Sector", "Unknown"))
                out.append({
                    "ticker": str(row.get("Symbol", "")).replace(".", "-"),  # BRK.B -> BRK-B (formato Yahoo)
                    "name": str(row.get("Security", "")),
                    "sector": sector,
                    "industry": str(row.get("GICS Sub-Industry", sector)),
                    "is_financial": sector in financial_sectors,
                })
            out = [o for o in out if o["ticker"]]
            if len(out) < 400:  # sanity check: si la tabla vino incompleta, no la demos por buena
                raise ValueError(f"Tabla de Wikipedia con solo {len(out)} filas (esperadas ~500)")
            self.last_universe_source = "wikipedia"
            return out
        except Exception:
            self.last_universe_source = "fallback"
            return [{
                "ticker": t, "name": n, "sector": s, "industry": s,
                "is_financial": s in financial_sectors,
            } for t, n, s in _FALLBACK_UNIVERSE]

    # -- precios --------------------------------------------------------------
    def get_price_history_bulk(self, tickers: list, lookback_days: int = 400) -> dict:
        """
        Descarga en un único lote (o pocos lotes) el histórico de todos los
        tickers, en vez de hacer una petición yf.Ticker(t).history() por
        ticker. Reduce drásticamente el número de peticiones HTTP en un
        escaneo masivo, que es la principal causa de rate-limiting (429) de
        Yahoo en IPs compartidas como Streamlit Community Cloud.
        """
        if not tickers:
            return {}
        start = (datetime.utcnow() - timedelta(days=lookback_days)).date().isoformat()
        out = {}

        # yf.download también puede ser rate-limitado con universos grandes;
        # se trocea en lotes moderados + reintentos por lote.
        batch_size = 60
        for i in range(0, len(tickers), batch_size):
            batch = tickers[i:i + batch_size]
            try:
                data = _retry(lambda: yf.download(
                    tickers=batch, start=start, auto_adjust=True,
                    group_by="ticker", threads=True, progress=False
                ))
            except Exception:
                data = pd.DataFrame()

            for t in batch:
                try:
                    sub = data[t] if isinstance(data.columns, pd.MultiIndex) else data
                    sub = sub.dropna(how="all").reset_index()
                    sub = sub.rename(columns={
                        "Date": "date", "Open": "open", "High": "high",
                        "Low": "low", "Close": "close_adj", "Volume": "volume",
                    })
                    sub["date"] = pd.to_datetime(sub["date"]).dt.tz_localize(None)
                    out[t] = sub[["date", "open", "high", "low", "close_adj", "volume"]].dropna(subset=["close_adj"])
                except Exception:
                    out[t] = pd.DataFrame(columns=["date", "open", "high", "low", "close_adj", "volume"])

            if i + batch_size < len(tickers):
                time.sleep(config.YF_REQUEST_DELAY_SECONDS)

        return out

    def get_price_history(self, ticker: str, lookback_days: int = 400):
        t = yf.Ticker(ticker)
        start = (datetime.utcnow() - timedelta(days=lookback_days)).date()
        # auto_adjust=True -> 'Close' ya viene ajustado por splits y dividendos
        hist = t.history(start=start.isoformat(), auto_adjust=True)
        if hist.empty:
            return pd.DataFrame(columns=["date", "open", "high", "low", "close_adj", "volume"])
        hist = hist.reset_index()
        hist = hist.rename(columns={
            "Date": "date", "Open": "open", "High": "high",
            "Low": "low", "Close": "close_adj", "Volume": "volume",
        })
        hist["date"] = pd.to_datetime(hist["date"]).dt.tz_localize(None)
        return hist[["date", "open", "high", "low", "close_adj", "volume"]]

    # -- fundamentales ----------------------------------------------------------
    def get_fundamentals(self, ticker: str) -> dict:
        t = yf.Ticker(ticker)
        try:
            info = _retry(lambda: t.info) or {}
        except Exception:
            info = {}

        financials = getattr(t, "financials", pd.DataFrame())
        balance = getattr(t, "balance_sheet", pd.DataFrame())
        cashflow = getattr(t, "cashflow", pd.DataFrame())

        price = _num(info.get("currentPrice") or info.get("regularMarketPrice"))
        eps_ttm = _num(info.get("trailingEps"))
        eps_forward = _num(info.get("forwardEps")) or eps_ttm
        growth_eps_fwd = _num(info.get("earningsGrowth"))
        revenue_growth = _num(info.get("revenueGrowth"))

        net_income = _row(financials, ["Net Income", "NetIncome", "Net Income Common Stockholders"], 0)
        net_income_prior = _row(financials, ["Net Income", "NetIncome", "Net Income Common Stockholders"], 1)
        revenue = _row(financials, ["Total Revenue", "TotalRevenue"], 0)
        revenue_prior = _row(financials, ["Total Revenue", "TotalRevenue"], 1)
        gross_profit = _row(financials, ["Gross Profit", "GrossProfit"], 0)
        gross_profit_prior = _row(financials, ["Gross Profit", "GrossProfit"], 1)

        cfo = _row(cashflow, ["Operating Cash Flow", "Total Cash From Operating Activities",
                               "CashFlowFromContinuingOperatingActivities"], 0)
        fcf = _num(info.get("freeCashflow"))

        total_assets = _row(balance, ["Total Assets", "TotalAssets"], 0)
        total_assets_prior = _row(balance, ["Total Assets", "TotalAssets"], 1)
        current_assets = _row(balance, ["Current Assets", "Total Current Assets", "CurrentAssets"], 0)
        current_assets_prior = _row(balance, ["Current Assets", "Total Current Assets", "CurrentAssets"], 1)
        current_liabilities = _row(balance, ["Current Liabilities", "Total Current Liabilities",
                                              "CurrentLiabilities"], 0)
        current_liabilities_prior = _row(balance, ["Current Liabilities", "Total Current Liabilities",
                                                     "CurrentLiabilities"], 1)
        long_term_debt = _row(balance, ["Long Term Debt", "LongTermDebt"], 0)
        long_term_debt_prior = _row(balance, ["Long Term Debt", "LongTermDebt"], 1)

        shares_now = _num(info.get("sharesOutstanding"))
        shares_prior = None
        try:
            shares_hist = _retry(lambda: t.get_shares_full(
                start=(datetime.utcnow() - timedelta(days=420)).date().isoformat()
            ))
            if shares_hist is not None and not shares_hist.empty:
                shares_prior = _num(shares_hist.iloc[0])
        except Exception:
            pass
        if shares_prior is None:
            shares_prior = shares_now  # sin dato -> asumimos sin dilución (neutral, no penaliza ni premia)

        ebitda = _num(info.get("ebitda"))
        ebitda_margin = (ebitda / revenue) if (ebitda and revenue) else None

        return {
            "price": price,
            "eps_forward": eps_forward,
            "eps_ttm": eps_ttm,
            "growth_eps_fwd": growth_eps_fwd,
            "revenue_growth": revenue_growth,
            "fcf": fcf,
            "shares_outstanding": shares_now,
            "shares_outstanding_prior_year": shares_prior,
            "ev": _num(info.get("enterpriseValue")),
            "ev_ebitda_actual": _num(info.get("enterpriseToEbitda")),
            "ev_sales_fwd": _num(info.get("enterpriseToRevenue")),
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
            "book_value_per_share": _num(info.get("bookValue")),
            "peg": _num(info.get("pegRatio") or info.get("trailingPegRatio")),
            "price_target_avg": _num(info.get("targetMeanPrice")),
            "num_analysts": info.get("numberOfAnalystOpinions"),
            "week52_high": _num(info.get("fiftyTwoWeekHigh")),
        }

    # -- sector / macro ---------------------------------------------------------
    def get_sector_benchmark(self, sector: str) -> dict:
        return dict(_SECTOR_DEFAULTS.get(sector, _DEFAULT_BENCHMARK))

    def get_risk_free_rate(self) -> float:
        try:
            hist = yf.Ticker("^TNX").history(period="5d")
            if hist.empty:
                return 0.04
            # ^TNX cotiza el rendimiento del US10Y * 10 (ej. 42.5 = 4.25%)
            return float(hist["Close"].iloc[-1]) / 1000.0
        except Exception:
            return 0.04

    def get_market_regime(self) -> dict:
        df = self.get_price_history("^GSPC", lookback_days=280)
        if df.empty or len(df) < 200:
            return {"price": None, "mm200": None, "below_mm200": False}
        mm200 = df["close_adj"].rolling(200).mean().iloc[-1]
        price = df["close_adj"].iloc[-1]
        return {"price": float(price), "mm200": float(mm200), "below_mm200": bool(price < mm200)}

    # -- insider trading ---------------------------------------------------------
    def get_insider_activity(self, ticker: str) -> dict:
        cutoff = datetime.utcnow() - timedelta(days=config.INSIDER_LOOKBACK_DAYS)

        try:
            t = yf.Ticker(ticker)
            tx = _retry(lambda: t.insider_transactions)
        except Exception:
            tx = None

        buy_value, sell_value, buy_count, sell_count = 0.0, 0.0, 0, 0
        if tx is not None and not tx.empty:
            date_col = "Start Date" if "Start Date" in tx.columns else None
            for _, r in tx.iterrows():
                if date_col:
                    try:
                        tx_date = pd.to_datetime(r[date_col]).tz_localize(None)
                    except Exception:
                        continue
                    if tx_date < cutoff:
                        continue
                text = str(r.get("Transaction", "")).lower()
                value = _num(r.get("Value")) or 0.0
                if "purchase" in text or "buy" in text:
                    buy_value += value
                    buy_count += 1
                elif "sale" in text or "sell" in text:
                    sell_value += value
                    sell_count += 1

        net_value = buy_value - sell_value
        signal = (
            buy_value >= config.INSIDER_MIN_BUY_VALUE_USD
            and (sell_value == 0 or buy_value >= sell_value * config.INSIDER_BUY_SELL_RATIO_MIN)
        )
        return {
            "buy_value": buy_value, "sell_value": sell_value,
            "buy_count": buy_count, "sell_count": sell_count,
            "net_value": net_value, "signal": signal, "source": "yfinance",
        }
