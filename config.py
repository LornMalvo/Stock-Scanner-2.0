"""
config.py
=========
Configuración centralizada del screener. Aquí viven todos los "números mágicos"
del sistema (pesos, thresholds, TTLs de caché) para que Motor 1, Motor 2 y la UI
lean siempre de la misma fuente de verdad.
"""

import os
from pathlib import Path

# ---------------------------------------------------------------------------
# Rutas / almacenamiento
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent
DB_PATH = os.environ.get("SCREENER_DB_PATH", str(BASE_DIR / "screener.db"))

# ---------------------------------------------------------------------------
# API Keys (se leen de variables de entorno / .env). Nunca hardcodear.
# ---------------------------------------------------------------------------
FMP_API_KEY = os.environ.get("FMP_API_KEY", "")
POLYGON_API_KEY = os.environ.get("POLYGON_API_KEY", "")

# Proveedor por defecto si el usuario no elige otro en la UI.
# "demo" no requiere API key y genera datos sintéticos reproducibles,
# pensado para desarrollo/testing de la lógica de los dos motores.
DEFAULT_PROVIDER = os.environ.get("SCREENER_PROVIDER", "demo")

# ---------------------------------------------------------------------------
# Política de caché (para minimizar consumo de API)
# ---------------------------------------------------------------------------
PRICE_CACHE_TTL_HOURS = 20          # precios: refrescar ~1 vez al día
FUNDAMENTALS_CACHE_TTL_DAYS = 7     # fundamentales: refrescar semanalmente
SECTOR_BENCHMARK_TTL_DAYS = 7
MACRO_CACHE_TTL_HOURS = 12          # US10Y, SPX vs MM200

# ---------------------------------------------------------------------------
# Motor 1 — Valoración Fundamental
# ---------------------------------------------------------------------------
RISK_FREE_PREMIUM = 0.05            # prima de riesgo sobre el US10Y para el FCF Yield (DCF Lite)
PER_PREMIUM_HIGH_GROWTH = 0.10      # +10% al múltiplo PER justo si crecimiento EPS fwd > 20%
PER_PREMIUM_GROWTH_THRESHOLD = 0.20
HYPER_GROWTH_MIN_REVENUE_GROWTH = 0.25   # exigido para usar el fallback EV/Sales con EPS negativo
RULE_OF_40_THRESHOLD = 0.40

# ---------------------------------------------------------------------------
# Motor 2 — Timing técnico / Scoring
# ---------------------------------------------------------------------------
# Pesos de cada condición booleana (clave -> peso). Deben coincidir con las
# claves devueltas por engines.technical.evaluate_conditions().
SCORING_WEIGHTS = {
    "margen_seguridad": 3,
    "piotroski": 3,
    "vcp_detectado": 3,
    "dilucion_controlada": 2,
    "pullback_tendencia": 2,
    "rsi_bajo": 2,
    "acumulacion_institucional": 2,
    "peg_atractivo": 2,
    "alejado_maximos": 2,
    "insider_buying": 2,
    "consenso_analistas": 1,
}
TOTAL_WEIGHT = sum(SCORING_WEIGHTS.values())  # 24

# % del peso total que debe alcanzarse para considerar "SEÑAL DE ENTRADA"
SIGNAL_SCORE_THRESHOLD_PCT = 0.65  # >=65% del TOTAL_WEIGHT vigente

# Margen de seguridad mínimo exigido (peso 3). Sube en régimen bajista de mercado.
MARGIN_OF_SAFETY_MIN_BULL = 0.10
MARGIN_OF_SAFETY_MIN_BEAR = 0.30   # cuando SPX < su MM200

# Piotroski F-Score mínimo para puntuar la condición de peso 3
PIOTROSKI_MIN_SCORE = 7

# Dilución YoY máxima aceptable
MAX_DILUTION_YOY = 0.03

# RSI(14) techo para considerar "no sobrecomprado / con recorrido"
RSI_MAX = 50

# Descuento exigido respecto al máximo de 52 semanas
PCT_OF_52W_HIGH_MAX = 0.90

# ---------------------------------------------------------------------------
# Universo de análisis
# ---------------------------------------------------------------------------
BENCHMARK_TICKER = "^GSPC"  # S&P 500 para el filtro macro de régimen de mercado

# ---------------------------------------------------------------------------
# VCP — Volatility Contraction Pattern (Bollinger Band Width + Volumen)
# ---------------------------------------------------------------------------
# BBW = (Banda_Superior - Banda_Inferior) / Media_Móvil_20  (Bollinger 20, 2 sigma)
VCP_BB_LENGTH = 20
VCP_BB_STD = 2.0
# Ventana histórica (sesiones) sobre la que se mide si la BBW actual está en
# un mínimo relativo ("el mercado se seca de vendedores").
VCP_BBW_LOOKBACK = 126  # ~6 meses bursátiles
# La BBW actual debe estar por debajo de este percentil de su propia
# distribución en la ventana anterior para considerarse "squeeze".
VCP_BBW_PERCENTILE = 0.20  # <= percentil 20
# Confirmación por volumen: la media de volumen reciente debe haberse
# contraído frente a la media de volumen de un periodo más largo.
VCP_VOLUME_SHORT = 10
VCP_VOLUME_LONG = 50
VCP_VOLUME_CONTRACTION_RATIO = 0.80  # vol_media_10 <= 0.80 * vol_media_50

# ---------------------------------------------------------------------------
# Insider Buying — flujo neto de transacciones de directivos (FMP)
# ---------------------------------------------------------------------------
INSIDER_CACHE_TTL_DAYS = 3
INSIDER_LOOKBACK_DAYS = 90  # "último trimestre"
# Señal activa si el valor comprado por insiders supera este múltiplo del
# valor vendido (si no hubo ventas y sí compras, también se activa).
INSIDER_BUY_SELL_RATIO_MIN = 2.0
# Filtro mínimo de compras para evitar que una única compra pequeña dispare
# la señal (ruido). Umbral en USD.
INSIDER_MIN_BUY_VALUE_USD = 50_000

