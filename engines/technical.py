"""
engines/technical.py
======================
Motor 2 — Timing técnico y Scoring ("Cuándo" comprar).

Contiene:
  * add_technical_indicators(): MM50/MM200, RSI14, CMF, OBV + pendiente MM20
    del OBV, máximo de 52 semanas, sobre el histórico de precios.
  * piotroski_fscore_vectorized(): calcula los 9 puntos contables de
    Piotroski de forma vectorizada sobre un DataFrame con todos los tickers.
  * evaluate_conditions() / compute_score(): matriz de condiciones booleanas
    ponderadas del enunciado, agregación y determinación de "SEÑAL DE ENTRADA".
"""

import numpy as np
import pandas as pd

import config


# ---------------------------------------------------------------------------
# NOTA DE ARQUITECTURA:
# Los indicadores técnicos (RSI, CMF, OBV) se implementan aquí con
# pandas/numpy puro en lugar de depender de la librería `pandas-ta`.
# Motivo: el paquete `pandas-ta` publicado en PyPI (0.3.14b0) está sin
# mantenimiento desde 2021, su instalación falla en entornos de build
# aislados modernos (Streamlit Cloud, Python 3.12+) y además usa
# internamente `numpy.NaN`, un atributo eliminado en numpy>=1.24. Calcular
# estos tres indicadores estándar directamente elimina esa dependencia
# frágil sin perder precisión ni funcionalidad.
# ---------------------------------------------------------------------------
def _rsi(close: pd.Series, length: int = 14) -> pd.Series:
    """RSI de Wilder (suavizado exponencial equivalente a la media móvil
    de Wilder), el estándar de facto usado por la mayoría de plataformas."""
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)

    avg_gain = gain.ewm(alpha=1 / length, min_periods=length, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / length, min_periods=length, adjust=False).mean()

    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    # Cuando avg_loss es 0 y avg_gain > 0, el RSI es 100 por definición
    rsi = rsi.where(~((avg_loss == 0) & (avg_gain > 0)), 100.0)
    return rsi


def _cmf(high: pd.Series, low: pd.Series, close: pd.Series, volume: pd.Series, length: int = 20) -> pd.Series:
    """Chaikin Money Flow estándar: suma(Money Flow Volume) / suma(Volumen) en la ventana."""
    hl_range = (high - low).replace(0, np.nan)
    mf_multiplier = ((close - low) - (high - close)) / hl_range
    mf_volume = mf_multiplier * volume
    cmf = mf_volume.rolling(length).sum() / volume.rolling(length).sum().replace(0, np.nan)
    return cmf


def _obv(close: pd.Series, volume: pd.Series) -> pd.Series:
    """On-Balance Volume: acumula +volumen si el cierre sube, -volumen si baja."""
    direction = np.sign(close.diff().fillna(0))
    return (direction * volume).cumsum()


def _bollinger_bandwidth(close: pd.Series, length: int = 20, std_mult: float = 2.0):
    """Bandas de Bollinger estándar y su anchura normalizada (BBW).

    BBW = (Banda_Superior - Banda_Inferior) / Media_Móvil_20
    """
    ma = close.rolling(length).mean()
    std = close.rolling(length).std()
    upper = ma + std_mult * std
    lower = ma - std_mult * std
    bbw = (upper - lower) / ma.replace(0, np.nan)
    return ma, upper, lower, bbw


def _detect_vcp(df: pd.DataFrame) -> pd.Series:
    """
    Patrón de Contracción de Volatilidad (VCP): el mercado "se seca" de
    vendedores. Señal = TRUE cuando se cumplen a la vez:
      1) Squeeze de volatilidad: la BBW actual está en un mínimo relativo
         (percentil bajo) respecto a su propia distribución reciente.
      2) Confirmación por volumen: el volumen medio reciente se ha
         contraído frente a un periodo más largo (compradores/vendedores
         "agotados", sin presión vendedora activa).
    Todo vectorizado con pandas rolling — sin bucles fila a fila.
    """
    bbw_threshold = df["bbw"].rolling(
        config.VCP_BBW_LOOKBACK, min_periods=30
    ).quantile(config.VCP_BBW_PERCENTILE)
    squeeze = df["bbw"] <= bbw_threshold

    vol_short = df["volume"].rolling(config.VCP_VOLUME_SHORT).mean()
    vol_long = df["volume"].rolling(config.VCP_VOLUME_LONG).mean()
    volumen_contraido = vol_short <= (vol_long * config.VCP_VOLUME_CONTRACTION_RATIO)

    return (squeeze & volumen_contraido).fillna(False)


# ---------------------------------------------------------------------------
# Indicadores técnicos sobre el histórico de precios de UN ticker
# ---------------------------------------------------------------------------
def add_technical_indicators(price_df: pd.DataFrame) -> pd.DataFrame:
    """price_df: columnas date, open, high, low, close_adj, volume (orden asc por fecha)."""
    df = price_df.copy().sort_values("date").reset_index(drop=True)
    if df.empty or len(df) < 20:
        return df

    df["mm50"] = df["close_adj"].rolling(50).mean()
    df["mm200"] = df["close_adj"].rolling(200).mean()
    df["rsi14"] = _rsi(df["close_adj"], length=14)

    # Chaikin Money Flow (20 periodos, estándar)
    df["cmf"] = _cmf(df["high"], df["low"], df["close_adj"], df["volume"], length=20)

    # On-Balance Volume + pendiente de su media móvil de 20 sesiones
    df["obv"] = _obv(df["close_adj"], df["volume"])
    df["obv_mm20"] = df["obv"].rolling(20).mean()
    df["obv_mm20_slope"] = df["obv_mm20"].diff()

    # Máximo de las últimas 52 semanas (~252 sesiones bursátiles)
    window_52w = min(len(df), 252)
    df["high_52w"] = df["close_adj"].rolling(window_52w, min_periods=20).max()

    # Bandas de Bollinger + VCP (Patrón de Contracción de Volatilidad)
    df["bb_ma20"], df["bb_upper"], df["bb_lower"], df["bbw"] = _bollinger_bandwidth(
        df["close_adj"], length=config.VCP_BB_LENGTH, std_mult=config.VCP_BB_STD
    )
    df["vcp_signal"] = _detect_vcp(df)

    return df


def latest_snapshot(df_with_indicators: pd.DataFrame) -> dict:
    """Extrae la última fila de indicadores como dict plano, listo para el scoring."""
    if df_with_indicators.empty:
        return {}
    row = df_with_indicators.iloc[-1]
    return {
        "precio_actual": _f(row.get("close_adj")),
        "mm50": _f(row.get("mm50")),
        "mm200": _f(row.get("mm200")),
        "rsi14": _f(row.get("rsi14")),
        "cmf": _f(row.get("cmf")),
        "obv_mm20_slope": _f(row.get("obv_mm20_slope")),
        "high_52w": _f(row.get("high_52w")),
        "bbw": _f(row.get("bbw")),
        "vcp_signal": bool(row.get("vcp_signal")) if row.get("vcp_signal") is not None else False,
    }


def _f(v):
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return None
    return float(v)


# ---------------------------------------------------------------------------
# Piotroski F-Score — 9 señales contables puras, vectorizado
# ---------------------------------------------------------------------------
# Columnas de entrada requeridas por cada una de las 9 señales. Se usan para
# medir "completitud de datos" (DataQualityFlag): una señal que da False
# porque el dato falta no es lo mismo que una señal que da False porque la
# empresa realmente no la cumple, y de cara al usuario conviene distinguirlo.
_PIOTROSKI_DEPS = {
    "p1_roa_positivo": ["net_income", "total_assets"],
    "p2_cfo_positivo": ["cfo"],
    "p3_roa_creciente": ["net_income", "total_assets", "net_income_prior", "total_assets_prior"],
    "p4_calidad_beneficio": ["cfo", "net_income"],
    "p5_apalancamiento_baja": ["long_term_debt", "total_assets", "long_term_debt_prior", "total_assets_prior"],
    "p6_liquidez_sube": ["current_assets", "current_liabilities", "current_assets_prior", "current_liabilities_prior"],
    "p7_sin_dilucion": ["shares_outstanding", "shares_outstanding_prior_year"],
    "p8_margen_bruto_sube": ["gross_profit", "revenue", "gross_profit_prior", "revenue_prior"],
    "p9_rotacion_activos_sube": ["revenue", "total_assets", "revenue_prior", "total_assets_prior"],
}


def piotroski_fscore_vectorized(df: pd.DataFrame) -> pd.DataFrame:
    """
    df: una fila por ticker con las columnas fundamentales necesarias (actual
    y del ejercicio previo). Devuelve el mismo df con una columna 'piotroski'
    (0-9), las 9 subseñales booleanas y 'piotroski_completeness' (0-9: en
    cuántas de las 9 señales estaban disponibles TODOS los datos de entrada
    necesarios), todo calculado con operaciones vectorizadas de
    pandas/numpy (sin bucles fila a fila).
    """
    out = df.copy()

    # Asegurar que existen todas las columnas requeridas (proveedores
    # incompletos pueden no devolver alguna clave)
    required_cols = sorted({c for deps in _PIOTROSKI_DEPS.values() for c in deps})
    for col in required_cols:
        if col not in out.columns:
            out[col] = np.nan

    roa = out["net_income"] / out["total_assets"]
    roa_prior = out["net_income_prior"] / out["total_assets_prior"]
    current_ratio = out["current_assets"] / out["current_liabilities"]
    current_ratio_prior = out["current_assets_prior"] / out["current_liabilities_prior"]
    lt_debt_ratio = out["long_term_debt"] / out["total_assets"]
    lt_debt_ratio_prior = out["long_term_debt_prior"] / out["total_assets_prior"]
    gross_margin = out["gross_profit"] / out["revenue"]
    gross_margin_prior = out["gross_profit_prior"] / out["revenue_prior"]
    asset_turnover = out["revenue"] / out["total_assets"]
    asset_turnover_prior = out["revenue_prior"] / out["total_assets_prior"]

    p1_roa_positivo = (roa > 0)
    p2_cfo_positivo = (out["cfo"] > 0)
    p3_roa_creciente = (roa > roa_prior)
    p4_calidad_beneficio = (out["cfo"] > out["net_income"])
    p5_apalancamiento_baja = (lt_debt_ratio < lt_debt_ratio_prior)
    p6_liquidez_sube = (current_ratio > current_ratio_prior)
    p7_sin_dilucion = (out["shares_outstanding"] <= out["shares_outstanding_prior_year"])
    p8_margen_bruto_sube = (gross_margin > gross_margin_prior)
    p9_rotacion_activos_sube = (asset_turnover > asset_turnover_prior)

    signal_cols = list(_PIOTROSKI_DEPS.keys())
    signals = pd.concat([
        p1_roa_positivo, p2_cfo_positivo, p3_roa_creciente, p4_calidad_beneficio,
        p5_apalancamiento_baja, p6_liquidez_sube, p7_sin_dilucion,
        p8_margen_bruto_sube, p9_rotacion_activos_sube
    ], axis=1)
    signals.columns = signal_cols
    # NaN (datos insuficientes) cuenta como señal no cumplida, nunca como True
    signals = signals.fillna(False).astype(bool)

    # Completitud: para cada una de las 9 señales, ¿estaban TODOS sus inputs
    # presentes (no NaN)? Se suma cuántas de las 9 pudieron evaluarse con
    # datos reales, independientemente de si el resultado fue True o False.
    completeness_per_signal = pd.concat(
        [out[deps].notna().all(axis=1).rename(name) for name, deps in _PIOTROSKI_DEPS.items()],
        axis=1
    )
    out["piotroski_completeness"] = completeness_per_signal.sum(axis=1)

    out = pd.concat([out, signals], axis=1)
    out["piotroski"] = signals.sum(axis=1)
    return out


def piotroski_fscore_single(fund: dict) -> dict:
    """Wrapper cómodo para calcular Piotroski de UN ticker reutilizando la
    versión vectorizada (DataFrame de una fila). Devuelve
    {'score': 0-9, 'completeness': 0-9} en vez de solo el score, para poder
    mostrar el DataQualityFlag en la UI."""
    df = pd.DataFrame([fund])
    result = piotroski_fscore_vectorized(df)
    return {
        "score": int(result["piotroski"].iloc[0]),
        "completeness": int(result["piotroski_completeness"].iloc[0]),
    }


# ---------------------------------------------------------------------------
# Matriz de condiciones booleanas ponderadas + scoring final
# ---------------------------------------------------------------------------
def evaluate_conditions(valuation: dict, tech: dict, fund: dict, piotroski_score: int,
                         sector_bench: dict, market_regime: dict) -> dict:
    """
    Evalúa cada condición booleana de la matriz de pesos.
    valuation: salida de engines.fundamental.compute_valuation()
    tech: salida de engines.technical.latest_snapshot()
    fund: snapshot fundamental crudo (para dilución, PEG, analistas)
    market_regime: {'below_mm200': bool, ...} del S&P 500
    """
    below_mm200 = bool(market_regime.get("below_mm200"))
    margen_minimo = config.MARGIN_OF_SAFETY_MIN_BEAR if below_mm200 else config.MARGIN_OF_SAFETY_MIN_BULL

    margen = valuation.get("margen_seguridad_pct")
    cond_margen = margen is not None and margen >= margen_minimo * 100

    cond_piotroski = piotroski_score is not None and piotroski_score >= config.PIOTROSKI_MIN_SCORE

    shares_now = fund.get("shares_outstanding")
    shares_prior = fund.get("shares_outstanding_prior_year")
    dilucion = None
    if shares_now and shares_prior:
        dilucion = (shares_now - shares_prior) / shares_prior
    cond_dilucion = dilucion is not None and dilucion <= config.MAX_DILUTION_YOY

    precio = tech.get("precio_actual")
    mm50, mm200 = tech.get("mm50"), tech.get("mm200")
    cond_pullback = all(v is not None for v in [precio, mm50, mm200]) and (precio > mm200) and (precio < mm50)

    rsi = tech.get("rsi14")
    cond_rsi = rsi is not None and rsi < config.RSI_MAX

    cmf = tech.get("cmf")
    obv_slope = tech.get("obv_mm20_slope")
    cond_acumulacion = (cmf is not None and cmf > 0) or (obv_slope is not None and obv_slope > 0)

    peg_actual = fund.get("peg")
    peg_sector = sector_bench.get("peg_justo")
    cond_peg = peg_actual is not None and peg_sector is not None and peg_actual < peg_sector

    high_52w = tech.get("high_52w")
    cond_alejado_max = (
        precio is not None and high_52w is not None and high_52w > 0
        and precio <= high_52w * config.PCT_OF_52W_HIGH_MAX
    )

    price_target = fund.get("price_target_avg")
    cond_consenso = precio is not None and price_target is not None and precio < price_target

    # VCP (Patrón de Contracción de Volatilidad): señal explosiva de Motor 2,
    # complementaria al pullback de MM50 (no lo sustituye en el cálculo del
    # score porque ambas condiciones son independientes y suman por separado;
    # ver nota en compute_score sobre cómo interpretarlas juntas en la UI).
    cond_vcp = bool(tech.get("vcp_signal"))

    # Insider Buying: la señal llega precalculada desde el proveedor de datos
    # (ver data_providers.fmp_provider.get_insider_activity / scanner.py),
    # que agrega el flujo neto de compras vs. ventas de directivos del último
    # trimestre según config.INSIDER_BUY_SELL_RATIO_MIN.
    cond_insider = bool(fund.get("insider_buying_signal"))

    return {
        "margen_seguridad": cond_margen,
        "piotroski": cond_piotroski,
        "vcp_detectado": cond_vcp,
        "insider_buying": cond_insider,
        "dilucion_controlada": cond_dilucion,
        "pullback_tendencia": cond_pullback,
        "rsi_bajo": cond_rsi,
        "acumulacion_institucional": cond_acumulacion,
        "peg_atractivo": cond_peg,
        "alejado_maximos": cond_alejado_max,
        "consenso_analistas": cond_consenso,
        # metadatos útiles para mostrar en la UI
        "_margen_minimo_exigido_pct": margen_minimo * 100,
        "_dilucion_pct": dilucion * 100 if dilucion is not None else None,
    }


def compute_score(conditions: dict) -> dict:
    total = 0
    detail = {}
    for key, weight in config.SCORING_WEIGHTS.items():
        cumplida = bool(conditions.get(key))
        detail[key] = {"cumplida": cumplida, "peso": weight, "puntos": weight if cumplida else 0}
        total += weight if cumplida else 0

    pct = total / config.TOTAL_WEIGHT
    signal = pct >= config.SIGNAL_SCORE_THRESHOLD_PCT

    return {
        "score_total": total,
        "score_max": config.TOTAL_WEIGHT,
        "score_pct": pct * 100,
        "senal_entrada": signal,
        "detalle": detail,
    }


# ---------------------------------------------------------------------------
# DataQualityFlag — indicador compuesto de cuánta información real hay
# detrás del análisis de un ticker (no confundir con el score de timing)
# ---------------------------------------------------------------------------
def compute_data_quality(valuation: dict, piotroski_completeness: int) -> dict:
    """
    Combina dos señales de completitud de datos en un único indicador:
      - valuation_quality_pct: % de métodos de valoración del Motor 1 que
        se pudieron calcular (de los que en teoría aplican a esa empresa).
      - piotroski_pct: % de las 9 señales de Piotroski para las que había
        todos los datos contables necesarios.

    No mide si la empresa es "buena", mide cuánto puedes fiarte del número
    que ves — un margen de seguridad del 40% apoyado en 1 de 4 métodos vale
    mucho menos que uno apoyado en 4 de 4.
    """
    valuation_pct = valuation.get("valuation_quality_pct") or 0.0
    piotroski_pct = (piotroski_completeness / 9 * 100) if piotroski_completeness is not None else 0.0
    overall_pct = (valuation_pct + piotroski_pct) / 2

    if overall_pct >= config.DATA_QUALITY_HIGH_PCT:
        level, emoji = "alta", "🟢"
    elif overall_pct >= config.DATA_QUALITY_MEDIUM_PCT:
        level, emoji = "media", "🟡"
    else:
        level, emoji = "baja", "🔴"

    return {
        "valuation_quality_pct": valuation_pct,
        "piotroski_quality_pct": piotroski_pct,
        "overall_pct": overall_pct,
        "level": level,
        "emoji": emoji,
    }
