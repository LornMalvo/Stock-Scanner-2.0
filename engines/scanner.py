"""
engines/scanner.py
====================
Orquestador de todo el pipeline:

  DataProvider (con caché SQLite) -> Motor 1 (fundamental.py)
                                   -> Motor 2 (technical.py)
                                   -> resultado consolidado por ticker

Expone dos funciones principales para la UI:
  * analyze_ticker(ticker, provider): análisis profundo de un único valor.
  * scan_universe(provider, tickers=None, progress_cb=None): escaneo masivo
    del S&P 500 (o subconjunto) devolviendo un DataFrame ordenado por score.
"""

from datetime import datetime
import pandas as pd

import config
import database as db
from data_providers.base import DataProvider
from engines import fundamental, technical


# ---------------------------------------------------------------------------
# Helpers de caché (provider -> DB, con TTL)
# ---------------------------------------------------------------------------
def _get_fundamentals_cached(provider: DataProvider, ticker: str) -> dict:
    payload, stale = db.load_fundamentals(ticker)
    if payload is not None and not stale:
        return payload
    fresh = provider.get_fundamentals(ticker)
    db.save_fundamentals(ticker, fresh)
    return fresh


def _get_prices_cached(provider: DataProvider, ticker: str, lookback_days=400) -> pd.DataFrame:
    if db.prices_are_fresh(ticker):
        df = db.load_prices(ticker, lookback_days)
        if not df.empty:
            return df
    fresh = provider.get_price_history(ticker, lookback_days)
    db.save_prices(ticker, fresh)
    return fresh if not fresh.empty else db.load_prices(ticker, lookback_days)


def _get_sector_benchmark_cached(provider: DataProvider, sector: str) -> dict:
    payload, stale = db.load_sector_benchmark(sector)
    if payload is not None and not stale:
        return payload
    fresh = provider.get_sector_benchmark(sector)
    db.save_sector_benchmark(sector, fresh)
    return fresh


def _get_macro_cached(provider: DataProvider) -> dict:
    """Devuelve {'us10y': float, 'regime': {...}} recalculando SIEMPRE que el
    caché esté obsoleto — la tasa libre de riesgo es el input crítico que
    debe estar lo más fresco posible porque el enunciado exige recalcular
    valoraciones dinámicamente si cambia."""
    payload, stale = db.load_macro("global")
    if payload is not None and not stale:
        return payload
    fresh = {
        "us10y": provider.get_risk_free_rate(),
        "regime": provider.get_market_regime(),
        "fetched_at": datetime.utcnow().isoformat(),
    }
    db.save_macro("global", fresh)
    return fresh


def _get_insider_activity_cached(provider: DataProvider, ticker: str) -> dict:
    payload, stale = db.load_insider_activity(ticker)
    if payload is not None and not stale:
        return payload
    fresh = provider.get_insider_activity(ticker)
    db.save_insider_activity(ticker, fresh)
    return fresh


# ---------------------------------------------------------------------------
# Análisis de un único ticker (profundo, para la vista "drill-down")
# ---------------------------------------------------------------------------
def analyze_ticker(ticker: str, provider: DataProvider, meta: dict = None) -> dict:
    ticker = ticker.upper().strip()

    if meta is None:
        meta = db.get_ticker_meta(ticker)
    if meta is None:
        # Ticker fuera del universo cacheado: se asume no financiero por defecto
        meta = {"ticker": ticker, "name": ticker, "sector": "Unknown", "industry": "Unknown", "is_financial": False}
    else:
        db.upsert_ticker_meta(ticker, meta["name"], meta["sector"], meta["industry"], meta["is_financial"])

    fund = _get_fundamentals_cached(provider, ticker)
    price_df = _get_prices_cached(provider, ticker)
    sector_bench = _get_sector_benchmark_cached(provider, meta["sector"])
    macro = _get_macro_cached(provider)

    price_df_ind = technical.add_technical_indicators(price_df)
    tech_snapshot = technical.latest_snapshot(price_df_ind)
    # el precio "oficial" viene de fundamentales (quote), pero si falta usamos el técnico
    if not fund.get("price"):
        fund["price"] = tech_snapshot.get("precio_actual")

    insider = _get_insider_activity_cached(provider, ticker)
    fund["insider_buying_signal"] = bool(insider.get("signal"))

    valuation = fundamental.compute_valuation(
        fund, sector_bench, macro["us10y"], meta["is_financial"]
    )
    piotroski = technical.piotroski_fscore_single(fund)
    conditions = technical.evaluate_conditions(
        valuation, tech_snapshot, fund, piotroski, sector_bench, macro["regime"]
    )
    score = technical.compute_score(conditions)

    return {
        "ticker": ticker,
        "meta": meta,
        "fundamentals_raw": fund,
        "sector_benchmark": sector_bench,
        "macro": macro,
        "insider_activity": insider,
        "price_history": price_df_ind,
        "technical_snapshot": tech_snapshot,
        "valuation": valuation,
        "piotroski_score": piotroski,
        "conditions": conditions,
        "score": score,
    }


# ---------------------------------------------------------------------------
# Escaneo masivo del universo (S&P 500 o subconjunto)
# ---------------------------------------------------------------------------
def scan_universe(provider: DataProvider, tickers: list = None, progress_cb=None) -> pd.DataFrame:
    universe = provider.get_sp500_universe()
    if tickers:
        wanted = set(t.upper() for t in tickers)
        universe = [u for u in universe if u["ticker"].upper() in wanted]

    macro = _get_macro_cached(provider)  # una sola vez para todo el escaneo
    rows = []

    for i, meta in enumerate(universe):
        ticker = meta["ticker"]
        try:
            result = analyze_ticker(ticker, provider, meta)
            v = result["valuation"]
            s = result["score"]
            rows.append({
                "ticker": ticker,
                "nombre": meta["name"],
                "sector": meta["sector"],
                "precio": v.get("precio_actual"),
                "valor_justo": v.get("fair_value_promedio"),
                "margen_seguridad_pct": v.get("margen_seguridad_pct"),
                "piotroski": result["piotroski_score"],
                "rsi14": result["technical_snapshot"].get("rsi14"),
                "vcp_signal": result["technical_snapshot"].get("vcp_signal"),
                "insider_signal": result["insider_activity"].get("signal"),
                "hyper_growth": v.get("hyper_growth_mode"),
                "rule_of_40": v.get("rule_of_40_ok"),
                "score_pct": s.get("score_pct"),
                "score_total": s.get("score_total"),
                "senal_entrada": s.get("senal_entrada"),
            })
        except Exception as exc:  # nunca tumbar el escaneo completo por un ticker
            rows.append({
                "ticker": ticker, "nombre": meta["name"], "sector": meta["sector"],
                "error": str(exc),
            })
        if progress_cb:
            progress_cb(i + 1, len(universe), ticker)

    df = pd.DataFrame(rows)
    if "score_pct" in df.columns:
        df = df.sort_values("score_pct", ascending=False, na_position="last").reset_index(drop=True)
    return df
