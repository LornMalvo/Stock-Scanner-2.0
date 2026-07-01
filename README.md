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
  yfinance_provider.py   -> Yahoo Finance, gratuito, sin API key
  fmp_provider.py        -> Financial Modeling Prep (fundamentales, consenso analistas)
  polygon_provider.py    -> Polygon.io (precios ajustados de alta fiabilidad)
engines/
  fundamental.py         -> Motor 1: PER ajustado, PEG, EV/EBITDA, FCF Yield, P/B, hyper-growth, Regla del 40
  technical.py            -> Motor 2: indicadores técnicos (RSI/CMF/OBV en pandas/numpy puro), Piotroski vectorizado, scoring ponderado
  scanner.py              -> orquestador: caché -> Motor 1 -> Motor 2 -> resultado consolidado
app.py                   -> UI Streamlit (escaneo de mercado + análisis individual)
```

**Nota sobre dependencias**: los indicadores técnicos (RSI, Chaikin Money
Flow, OBV) se calculan con pandas/numpy puro en `engines/technical.py`, sin
depender de la librería `pandas-ta`. Ese paquete está sin mantenimiento desde
2021, falla al instalarse en entornos de build aislados modernos (p. ej.
Streamlit Community Cloud con Python 3.12+) y usa internamente `numpy.NaN`,
un atributo eliminado en numpy≥1.24. Si en el futuro quieres indicadores más
avanzados, considera `ta` (bukosabino/ta) o `TA-Lib` como alternativas mejor
mantenidas — ambas requieren adaptar `add_technical_indicators()`.

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

## Proveedores de datos disponibles

| Proveedor | API key | Fiabilidad fundamentales | Notas |
|---|---|---|---|
| `demo` | No | N/A (sintético) | Solo para validar la lógica/arquitectura |
| `yfinance` | **No** | Media — campos de Yahoo pueden faltar o cambiar de nombre | Gratuito, ideal para empezar; sin benchmarks de sector reales (usa una tabla estática de defaults, ver `yfinance_provider._SECTOR_DEFAULTS`) |
| `fmp` | Sí | Alta | Recomendado para uso serio: estimaciones de analistas, insider trading, sector PE |
| `polygon` | Sí | N/A (solo precios) | Combínalo con `fmp` para fundamentales si necesitas el mejor histórico de precios |

`yfinance` es la opción por defecto recomendada si no quieres gestionar
ninguna clave: se selecciona igual que el resto desde el desplegable
"Proveedor de datos" de la barra lateral. Limitaciones a tener en cuenta:

- El universo del S&P 500 se obtiene haciendo scraping de la tabla de
  Wikipedia (`get_sp500_universe()`); si Wikipedia cambia su estructura o no
  hay red, cae a una lista reducida de fallback para que la app no se rompa.
- No hay "múltiplo justo de sector" real: se usa una tabla estática
  razonable por sector GICS. Si quieres mayor precisión, sustituye
  `get_sector_benchmark()` por un cálculo de mediana de peers reales
  (mismo patrón que se sugiere para `fmp_provider.py` en el roadmap).
- Los insiders (`get_insider_activity`) se leen de `Ticker.insider_transactions`,
  un campo no documentado oficialmente por Yahoo — puede venir vacío para
  algunos tickers.

## Configurar la API key de FMP en Streamlit Community Cloud

`config.py` lee las claves con `os.environ.get("FMP_API_KEY", "")`, así que
basta con que la variable exista en el entorno del proceso — no hace falta
tocar código para pasar de local a Cloud:

1. En tu app dentro de share.streamlit.io, entra en **⋮ (menú) → Settings → Secrets**.
2. Pega, en formato TOML **plano** (sin sección `[...]`):
   ```toml
   FMP_API_KEY = "tu_clave_real_aqui"
   SCREENER_PROVIDER = "fmp"
   ```
3. Guarda. Streamlit Cloud inyecta automáticamente cada clave de nivel raíz
   tanto en `st.secrets` como en `os.environ`, así que `config.FMP_API_KEY`
   la recogerá sin cambios adicionales.
4. Reinicia la app (Manage app → Reboot) para que tome los secrets nuevos.

En local, en vez de secrets de Streamlit, simplemente copia `.env.example`
a `.env` y rellénalo (`python-dotenv` ya está en `requirements.txt`; si
quieres que se cargue automáticamente añade `from dotenv import load_dotenv;
load_dotenv()` al principio de `app.py`, o exporta las variables en tu shell).

**Importante**: nunca subas tu `.env` ni pegues la clave directamente en
`config.py` o en el código — así evitas que quede expuesta en el repo de
GitHub (aunque sea privado).

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

Matriz de condiciones y pesos (score máximo = 24 pts, señal ≥ 65%):

| Peso | Condición |
|---|---|
| 3 | Margen de Seguridad ≥10% (≥30% si S&P500 < su MM200) |
| 3 | Piotroski F-Score ≥ 7 |
| 3 | VCP detectado (squeeze de volatilidad + contracción de volumen) |
| 2 | Dilución YoY ≤ 3% |
| 2 | Pullback: Precio > MM200 y < MM50 |
| 2 | RSI(14) < 50 |
| 2 | CMF > 0 o pendiente MM20(OBV) > 0 |
| 2 | PEG actual < PEG medio sector |
| 2 | Precio ≤ 90% del máximo 52 semanas |
| 2 | Insider Buying (compras netas de directivos último trimestre) |
| 1 | Precio < precio objetivo medio analistas |

**VCP (Patrón de Contracción de Volatilidad)**: `BBW = (Banda_Superior -
Banda_Inferior) / MM20` (Bollinger 20, 2σ). La señal se activa cuando la BBW
actual cae en el percentil ≤20 de su propia distribución de los últimos ~6
meses **y**, a la vez, el volumen medio de 10 sesiones se ha contraído
≥20% frente al de 50 sesiones — "el mercado se seca de vendedores". Es una
condición independiente que complementa (no sustituye) al pullback de MM50:
ambas pueden coexistir y sumar por separado. Parámetros en `config.py`
(`VCP_*`).

**Insider Buying**: agrega, vía el endpoint de FMP `/v4/insider-trading`,
todas las transacciones Form 4 (compra "A" / venta "D") de directivos en los
últimos `INSIDER_LOOKBACK_DAYS` (90 días por defecto). La señal se activa si
el valor comprado supera `INSIDER_MIN_BUY_VALUE_USD` **y** es al menos
`INSIDER_BUY_SELL_RATIO_MIN` veces el valor vendido (o no hubo ventas). No es
un método abstracto de `DataProvider`: proveedores que no lo soporten (ej.
Polygon) heredan una implementación por defecto que devuelve "sin señal" sin
romper el pipeline.

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
