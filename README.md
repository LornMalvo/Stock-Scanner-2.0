# S&P 500 Value & Timing Screener

Screener cuantitativo modular que combina **Motor 1 (Valoración Fundamental)**
y **Motor 2 (Timing Técnico + Scoring ponderado)** para priorizar acciones
infravaloradas del S&P 500 con timing técnico favorable. Interfaz en Streamlit.

## Arquitectura

```
config.py              -> parámetros centralizados (pesos, thresholds, TTLs de caché)
database.py             -> capa SQLite: caché de precios, fundamentales, benchmarks, macro
data_providers/
  base.py               -> contrato abstracto DataProvider
  demo_provider.py       -> datos sintéticos (sin API key) para desarrollo/testing
  fmp_provider.py        -> Financial Modeling Prep (fundamentales, consenso analistas)
  polygon_provider.py    -> Polygon.io (precios ajustados de alta fiabilidad)
engines/
  fundamental.py         -> Motor 1: PER ajustado, PEG, EV/EBITDA, FCF Yield, P/B, hyper-growth, Regla del 40
  technical.py            -> Motor 2: indicadores técnicos, Piotroski vectorizado, scoring ponderado
  scanner.py              -> orquestador: caché -> Motor 1 -> Motor 2 -> resultado consolidado
app.py                   -> UI Streamlit (escaneo de mercado + análisis individual)
```

**Por qué esta separación**: cada proveedor de datos implementa la misma
interfaz (`DataProvider`), así que cambiar de FMP a Polygon a IBKR es
implementar una clase nueva, sin tocar `engines/` ni `app.py`. Los motores
(`fundamental.py`, `technical.py`) son funciones puras (dict/DataFrame in,
dict/DataFrame out), fáciles de testear unitariamente sin red ni Streamlit.

## Instalación

```bash
python -m venv .venv && source .venv/bin/activate   # o .venv\Scripts\activate en Windows
pip install -r requirements.txt
cp .env.example .env      # y rellena tus API keys si vas a usar fmp/polygon
streamlit run app.py
```

Sin ninguna API key, la app funciona igualmente con el **proveedor `demo`**
(datos sintéticos deterministas), lo que permite validar toda la lógica de
los dos motores y la UI de principio a fin.

## Motor 1 — Valoración Fundamental

Cada método devuelve un "valor justo" independiente; se promedian los que
sean aplicables a la empresa concreta para obtener el **Margen de Seguridad**:

| Método | Fórmula | Aplica cuando |
|---|---|---|
| PER Ajustado | `PER_justo_sector * EPS_forward` (×1.10 si crecimiento EPS fwd > 20%) | EPS forward > 0 |
| PEG | `EPS_forward * PEG_justo_sector * (tasa_crecimiento * 100)` | EPS forward > 0 |
| EV/EBITDA | `precio_actual * (EV_EBITDA_justo_sector / EV_EBITDA_actual)` | siempre que haya datos |
| FCF Yield Dinámico | `(FCF / acciones) / (US10Y + 5%)` | siempre que haya datos |
| Price/Book | `BVPS * PB_justo_sector` | solo empresas financieras |
| Fallback Hyper-Growth | EV/Sales fwd vs sector | EPS ≤ 0 **y** crecimiento ingresos ≥ 25% (excluye PER/PEG) |

La **tasa exigida del FCF Yield se recalcula dinámicamente** a partir del
US10Y cacheado con TTL corto (`MACRO_CACHE_TTL_HOURS`), tal y como pide el
requisito — nunca se persiste una valoración final, solo sus inputs.

**Regla del 40**: etiqueta informativa (`Crecimiento ingresos + Margen EBITDA >= 40%`).

## Motor 2 — Timing técnico y Scoring

Piotroski F-Score (9 señales contables, **vectorizado con pandas/numpy**,
sin bucles fila a fila): rentabilidad (ROA>0, ROA↑, CFO>0, CFO>BN),
apalancamiento/liquidez (deuda/activos↓, current ratio↑, sin dilución) y
eficiencia operativa (margen bruto↑, rotación de activos↑).

Matriz de condiciones y pesos (score máximo = 19 pts, señal ≥ 65%):

| Peso | Condición |
|---|---|
| 3 | Margen de Seguridad ≥10% (≥30% si S&P500 < su MM200) |
| 3 | Piotroski F-Score ≥ 7 |
| 2 | Dilución YoY ≤ 3% |
| 2 | Pullback: Precio > MM200 y < MM50 |
| 2 | RSI(14) < 50 |
| 2 | CMF > 0 o pendiente MM20(OBV) > 0 |
| 2 | PEG actual < PEG medio sector |
| 2 | Precio ≤ 90% del máximo 52 semanas |
| 1 | Precio < precio objetivo medio analistas |

Todos los thresholds viven en `config.py` — cambiar el umbral de señal o
cualquier peso es editar una constante, no el código de los motores.

## Ideas de mejora (roadmap sugerido)

- **Backtesting**: módulo `engines/backtest.py` que aplique la matriz de
  scoring históricamente día a día y mida forward returns (1M/3M/6M/12M) por
  bucket de score, para validar empíricamente los pesos actuales en vez de
  fijarlos a criterio.
- **Ponderación dinámica de pesos** vía regresión/ML sobre el backtest
  anterior en lugar de pesos fijos "a mano".
- **Ampliar el universo**: generalizar `get_sp500_universe()` a Russell 3000
  / Stoxx 600 reutilizando la misma interfaz `DataProvider`.
- **Alertas**: guardar el histórico de `senal_entrada` por ticker en SQLite
  y notificar (email/Telegram) cuando un ticker *entra* en señal por primera vez.
- **Position sizing**: añadir un módulo de tamaño de posición basado en
  volatilidad (ATR) y en el propio margen de seguridad, para pasar de "qué
  comprar" a "cuánto comprar".
- **Calidad de sector benchmark**: en vez de un múltiplo "justo" fijo por
  sector, calcularlo como mediana móvil de los peers reales del sector
  (excluyendo outliers) actualizada semanalmente — ahora mismo
  `fmp_provider.get_sector_benchmark()` deja el punto de extensión listo.
- **Riesgo de datos**: añadir un `DataQualityFlag` por ticker (nº de métodos
  de valoración con datos vs. total) para que la UI avise cuando el margen de
  seguridad se apoya en muy pocos métodos.
- **Multi-tasa**: sustituir el spread fijo de +5% sobre el US10Y por un
  spread ajustado por beta/sector (equity risk premium sectorial).

## Notas importantes

- El proveedor `demo` genera datos **ficticios**; nunca debe usarse para
  decisiones de inversión reales, solo para validar la arquitectura.
- Los proveedores `fmp` y `polygon` están implementados contra sus APIs
  públicas documentadas, pero los campos exactos disponibles dependen del
  plan contratado — revisa `_map_fundamentals()` en `fmp_provider.py` si tu
  plan expone nombres de campo distintos.
- Este software es una herramienta de apoyo cuantitativo, no constituye
  asesoramiento financiero.
