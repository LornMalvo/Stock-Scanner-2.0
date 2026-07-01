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
    "dilucion_controlada": 2,
    "pullback_tendencia": 2,
    "rsi_bajo": 2,
    "acumulacion_institucional": 2,
    "peg_atractivo": 2,
    "alejado_maximos": 2,
    "consenso_analistas": 1,
}
TOTAL_WEIGHT = sum(SCORING_WEIGHTS.values())  # 19

# % del peso total que debe alcanzarse para considerar "SEÑAL DE ENTRADA"
SIGNAL_SCORE_THRESHOLD_PCT = 0.65  # >=65% de 19 => >= 12.35 puntos

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
