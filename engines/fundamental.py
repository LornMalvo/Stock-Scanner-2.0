"""
engines/fundamental.py
========================
Motor 1 — Valoración Fundamental ("Qué" comprar).

Calcula el Valor Justo mediante varios métodos independientes y promedia los
que sean aplicables a cada empresa para obtener un Margen de Seguridad (%).

Todas las funciones son puras (reciben dicts/floats, devuelven dicts/floats)
para que sean fáciles de testear unitariamente y de recalcular en caliente
cuando cambia la tasa libre de riesgo (requisito de recálculo dinámico).
"""

import math
import config


def _safe(value):
    """Descarta None / NaN / valores no numéricos."""
    if value is None:
        return None
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(v) or math.isinf(v):
        return None
    return v


def value_per_ajustado(eps_forward, growth_eps_fwd, per_justo_sector):
    eps_forward = _safe(eps_forward)
    per_justo_sector = _safe(per_justo_sector)
    if eps_forward is None or eps_forward <= 0 or per_justo_sector is None:
        return None
    growth = _safe(growth_eps_fwd) or 0.0
    per_ajustado = per_justo_sector
    if growth > config.PER_PREMIUM_GROWTH_THRESHOLD:
        per_ajustado *= (1 + config.PER_PREMIUM_HIGH_GROWTH)
    return per_ajustado * eps_forward


def value_peg(eps_forward, peg_justo_sector, growth_rate):
    eps_forward = _safe(eps_forward)
    peg_justo_sector = _safe(peg_justo_sector)
    growth_rate = _safe(growth_rate)
    if eps_forward is None or eps_forward <= 0 or peg_justo_sector is None or growth_rate is None:
        return None
    if growth_rate <= 0:
        return None
    return eps_forward * peg_justo_sector * (growth_rate * 100)


def value_ev_ebitda(precio_actual, ev_ebitda_justo_sector, ev_ebitda_actual):
    precio_actual = _safe(precio_actual)
    ev_ebitda_justo_sector = _safe(ev_ebitda_justo_sector)
    ev_ebitda_actual = _safe(ev_ebitda_actual)
    if not all(v is not None and v > 0 for v in [precio_actual, ev_ebitda_justo_sector, ev_ebitda_actual]):
        return None
    return precio_actual * (ev_ebitda_justo_sector / ev_ebitda_actual)


def value_fcf_yield_dinamico(fcf, acciones_en_circulacion, us10y):
    fcf = _safe(fcf)
    acciones = _safe(acciones_en_circulacion)
    us10y = _safe(us10y)
    if fcf is None or acciones is None or acciones <= 0 or us10y is None:
        return None
    tasa_exigida = us10y + config.RISK_FREE_PREMIUM
    if tasa_exigida <= 0:
        return None
    fcf_per_share = fcf / acciones
    return fcf_per_share / tasa_exigida


def value_price_book(book_value_per_share, pb_justo_sector):
    bvps = _safe(book_value_per_share)
    pb_justo = _safe(pb_justo_sector)
    if bvps is None or bvps <= 0 or pb_justo is None:
        return None
    return bvps * pb_justo


def value_hyper_growth_ev_sales(revenue, revenue_growth, shares_outstanding,
                                 ev_sales_actual, ev_sales_justo_sector, ev_actual, precio_actual):
    """Fallback cuando EPS es negativo. Requiere crecimiento de ingresos >= 25%.
    Devuelve (fair_value, aplica_bool)."""
    revenue_growth = _safe(revenue_growth)
    if revenue_growth is None or revenue_growth < config.HYPER_GROWTH_MIN_REVENUE_GROWTH:
        return None, False

    revenue = _safe(revenue)
    shares = _safe(shares_outstanding)
    ev_sales_justo = _safe(ev_sales_justo_sector)
    ev_actual = _safe(ev_actual)
    precio_actual = _safe(precio_actual)

    if not all(v is not None and v > 0 for v in [revenue, shares, ev_sales_justo]):
        return None, True

    sales_per_share = revenue / shares
    fair_ev = ev_sales_justo * revenue  # EV justo = múltiplo justo * ventas

    if ev_actual and ev_actual > 0 and precio_actual and precio_actual > 0:
        # Traducimos EV justo a precio justo usando la relación EV/precio actual
        # (mantiene deuda neta constante, aproximación estándar "EV bridge").
        fair_value = precio_actual * (fair_ev / ev_actual)
    else:
        fair_value = ev_sales_justo * sales_per_share

    return fair_value, True


def compute_rule_of_40(revenue_growth, ebitda_margin):
    revenue_growth = _safe(revenue_growth)
    ebitda_margin = _safe(ebitda_margin)
    if revenue_growth is None or ebitda_margin is None:
        return None, False
    score = (revenue_growth + ebitda_margin) * 100
    return score, score >= (config.RULE_OF_40_THRESHOLD * 100)


def compute_valuation(fund: dict, sector_bench: dict, us10y: float, is_financial: bool) -> dict:
    """
    Punto de entrada del Motor 1.

    fund: dict devuelto por DataProvider.get_fundamentals()
    sector_bench: dict devuelto por DataProvider.get_sector_benchmark()
    us10y: tasa libre de riesgo vigente (decimal)
    is_financial: bool -> activa/desactiva el método Price/Book

    Devuelve dict con: fair_values (por método), fair_value_promedio,
    margen_seguridad_pct, hyper_growth_mode, rule_of_40 (score, cumple).
    """
    precio_actual = _safe(fund.get("price"))
    eps_forward = _safe(fund.get("eps_forward"))
    eps_negativo = eps_forward is not None and eps_forward <= 0
    # Si no hay EPS forward en absoluto, lo tratamos también como "sin EPS
    # fiable" y probamos la vía hyper-growth si aplica.
    sin_eps_valido = eps_forward is None or eps_negativo

    fair_values = {}
    hyper_growth_mode = False

    if not sin_eps_valido:
        fv_per = value_per_ajustado(eps_forward, fund.get("growth_eps_fwd"), sector_bench.get("per_justo"))
        if fv_per is not None:
            fair_values["PER_ajustado"] = fv_per

        fv_peg = value_peg(eps_forward, sector_bench.get("peg_justo"), fund.get("growth_eps_fwd"))
        if fv_peg is not None:
            fair_values["PEG"] = fv_peg
    else:
        # Filtro Hyper-Growth (fallback): excluye PER/PEG, exige revenue growth >= 25%
        fv_hg, aplica = value_hyper_growth_ev_sales(
            fund.get("revenue"), fund.get("revenue_growth"), fund.get("shares_outstanding"),
            fund.get("ev_sales_fwd"), sector_bench.get("ev_sales_justo"),
            fund.get("ev"), precio_actual
        )
        hyper_growth_mode = aplica
        if fv_hg is not None:
            fair_values["EV_Sales_HyperGrowth"] = fv_hg

    fv_evebitda = value_ev_ebitda(precio_actual, sector_bench.get("ev_ebitda_justo"), fund.get("ev_ebitda_actual"))
    if fv_evebitda is not None:
        fair_values["EV_EBITDA"] = fv_evebitda

    fv_fcf = value_fcf_yield_dinamico(fund.get("fcf"), fund.get("shares_outstanding"), us10y)
    if fv_fcf is not None:
        fair_values["FCF_Yield_DCFLite"] = fv_fcf

    if is_financial:
        fv_pb = value_price_book(fund.get("book_value_per_share"), sector_bench.get("pb_justo"))
        if fv_pb is not None:
            fair_values["Price_Book"] = fv_pb

    valid_values = [v for v in fair_values.values() if v is not None and v > 0]
    fair_value_promedio = sum(valid_values) / len(valid_values) if valid_values else None

    margen_seguridad_pct = None
    if fair_value_promedio is not None and precio_actual and precio_actual > 0:
        margen_seguridad_pct = (fair_value_promedio - precio_actual) / precio_actual * 100

    rule40_score, rule40_ok = compute_rule_of_40(fund.get("revenue_growth"), fund.get("ebitda_margin"))

    # DataQualityFlag (Motor 1): nº de métodos de valoración que en teoría
    # aplican a esta empresa (según su rama: EPS+/EPS- y financiera o no)
    # frente a los que realmente se pudieron calcular con los datos
    # disponibles del proveedor. Una empresa con "2 de 2 métodos" es tan
    # fiable como pueda serlo el proveedor; una con "1 de 4" apoya su margen
    # de seguridad en muy poca información y debería tratarse con cautela.
    num_metodos_posibles = (1 if sin_eps_valido else 2) + 1 + 1 + (1 if is_financial else 0)
    num_metodos_usados = len(valid_values)
    valuation_quality_pct = (num_metodos_usados / num_metodos_posibles * 100) if num_metodos_posibles else 0.0

    return {
        "precio_actual": precio_actual,
        "fair_values": fair_values,
        "fair_value_promedio": fair_value_promedio,
        "margen_seguridad_pct": margen_seguridad_pct,
        "hyper_growth_mode": hyper_growth_mode,
        "eps_negativo": bool(eps_negativo) if eps_forward is not None else None,
        "rule_of_40_score": rule40_score,
        "rule_of_40_ok": rule40_ok,
        "num_metodos_usados": num_metodos_usados,
        "num_metodos_posibles": num_metodos_posibles,
        "valuation_quality_pct": valuation_quality_pct,
    }
