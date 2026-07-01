"""
app.py
=======
Interfaz Streamlit del screener. Dos modos:
  1) Escaneo de mercado (S&P 500): tabla ordenada por score con filtros.
  2) Análisis individual de un ticker: desglose completo de Motor 1 y Motor 2.

Lanzar con:  streamlit run app.py
"""

import streamlit as st
import pandas as pd
import plotly.graph_objects as go

try:
    from dotenv import load_dotenv
    load_dotenv()  # no-op si no existe .env (p.ej. en Streamlit Cloud, que usa Secrets)
except ImportError:
    pass

import config
import database as db
from data_providers import get_provider
from engines import scanner

st.set_page_config(page_title="S&P 500 Value & Timing Screener", layout="wide")
db.init_db()


# ---------------------------------------------------------------------------
# Sidebar: configuración global
# ---------------------------------------------------------------------------
st.sidebar.title("⚙️ Configuración")
PROVIDERS = ["demo", "yfinance", "fmp", "polygon"]
provider_name = st.sidebar.selectbox(
    "Proveedor de datos",
    options=PROVIDERS,
    index=PROVIDERS.index(config.DEFAULT_PROVIDER) if config.DEFAULT_PROVIDER in PROVIDERS else 0,
    help="'demo' genera datos sintéticos y no requiere API key. "
         "'yfinance' (Yahoo Finance) es gratuito y no requiere API key, pero "
         "sus fundamentales son menos completos/fiables que un proveedor de "
         "pago. 'fmp' y 'polygon' requieren definir las claves de API en el "
         "entorno (.env o Secrets de Streamlit Cloud)."
)

if provider_name in ("fmp", "polygon"):
    st.sidebar.warning(
        "Este proveedor requiere una API key válida en el entorno "
        f"({'FMP_API_KEY' if provider_name == 'fmp' else 'POLYGON_API_KEY'})."
    )
elif provider_name == "yfinance":
    st.sidebar.info(
        "Yahoo Finance no requiere API key, pero los múltiplos 'justos' de "
        "sector son una aproximación estática (no hay benchmark de peers "
        "real) y algunos campos fundamentales pueden faltar según el ticker."
    )

try:
    provider = get_provider(provider_name)
except Exception as e:
    st.sidebar.error(f"No se pudo inicializar el proveedor: {e}")
    st.stop()

st.sidebar.markdown("---")
mode = st.sidebar.radio("Modo", ["📊 Escaneo de Mercado (S&P 500)", "🔎 Análisis Individual"])

st.sidebar.markdown("---")
st.sidebar.caption(
    f"Umbral de señal: ≥{config.SIGNAL_SCORE_THRESHOLD_PCT*100:.0f}% del score "
    f"({config.TOTAL_WEIGHT} pts totales). Piotroski mínimo: {config.PIOTROSKI_MIN_SCORE}/9."
)


# ---------------------------------------------------------------------------
# Componentes reutilizables
# ---------------------------------------------------------------------------
def render_valuation_breakdown(valuation: dict):
    fair_values = valuation.get("fair_values", {})
    if not fair_values:
        st.info("No hay métodos de valoración aplicables con los datos disponibles.")
        return
    fig = go.Figure()
    metodos = list(fair_values.keys())
    valores = list(fair_values.values())
    fig.add_bar(x=metodos, y=valores, name="Valor justo por método")
    fig.add_hline(
        y=valuation.get("precio_actual", 0), line_dash="dash", line_color="red",
        annotation_text="Precio actual"
    )
    fig.add_hline(
        y=valuation.get("fair_value_promedio", 0), line_dash="dot", line_color="green",
        annotation_text="Valor justo promedio"
    )
    fig.update_layout(height=380, title="Desglose de Valor Justo por Método", showlegend=False)
    st.plotly_chart(fig, use_container_width=True)


def render_price_chart(price_df):
    if price_df.empty or "mm50" not in price_df.columns:
        st.info("Histórico de precios insuficiente para graficar.")
        return
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=price_df["date"], y=price_df["close_adj"], name="Precio (ajustado)"))
    fig.add_trace(go.Scatter(x=price_df["date"], y=price_df["mm50"], name="MM50"))
    fig.add_trace(go.Scatter(x=price_df["date"], y=price_df["mm200"], name="MM200"))
    fig.update_layout(height=380, title="Precio vs Medias Móviles")
    st.plotly_chart(fig, use_container_width=True)

    col1, col2 = st.columns(2)
    with col1:
        fig_rsi = go.Figure()
        fig_rsi.add_trace(go.Scatter(x=price_df["date"], y=price_df["rsi14"], name="RSI(14)"))
        fig_rsi.add_hline(y=config.RSI_MAX, line_dash="dash", line_color="orange")
        fig_rsi.update_layout(height=280, title="RSI(14)")
        st.plotly_chart(fig_rsi, use_container_width=True)
    with col2:
        fig_cmf = go.Figure()
        fig_cmf.add_trace(go.Scatter(x=price_df["date"], y=price_df["cmf"], name="CMF(20)"))
        fig_cmf.add_hline(y=0, line_dash="dash", line_color="gray")
        fig_cmf.update_layout(height=280, title="Chaikin Money Flow")
        st.plotly_chart(fig_cmf, use_container_width=True)

    if "bbw" in price_df.columns:
        fig_bbw = go.Figure()
        fig_bbw.add_trace(go.Scatter(x=price_df["date"], y=price_df["bbw"], name="BBW"))
        if "vcp_signal" in price_df.columns:
            vcp_points = price_df[price_df["vcp_signal"] == True]  # noqa: E712
            if not vcp_points.empty:
                fig_bbw.add_trace(go.Scatter(
                    x=vcp_points["date"], y=vcp_points["bbw"], mode="markers",
                    marker=dict(color="red", size=6), name="VCP detectado"
                ))
        fig_bbw.update_layout(height=280, title="Bollinger Band Width (squeeze de volatilidad)")
        st.plotly_chart(fig_bbw, use_container_width=True)


def render_score_detail(score: dict, conditions: dict):
    st.metric(
        "Score de Timing",
        f"{score['score_total']} / {score['score_max']} pts ({score['score_pct']:.1f}%)",
        delta="SEÑAL DE ENTRADA ✅" if score["senal_entrada"] else "Sin señal ❌",
    )
    detail = score["detalle"]
    rows = []
    labels = {
        "margen_seguridad": f"Margen de Seguridad (mín. exigido {conditions.get('_margen_minimo_exigido_pct', 0):.0f}%)",
        "piotroski": f"Piotroski F-Score (≥{config.PIOTROSKI_MIN_SCORE}/9)",
        "vcp_detectado": "VCP — Patrón de Contracción de Volatilidad",
        "dilucion_controlada": "Control de Dilución (≤3% YoY)",
        "pullback_tendencia": "Pullback en Tendencia (>MM200 y <MM50)",
        "rsi_bajo": f"RSI(14) < {config.RSI_MAX}",
        "acumulacion_institucional": "Acumulación Institucional (CMF>0 u OBV MM20 ↑)",
        "peg_atractivo": "PEG < PEG medio sector",
        "alejado_maximos": "Precio ≤ 90% del máx. 52 semanas",
        "insider_buying": "Insider Buying (compras netas de directivos)",
        "consenso_analistas": "Precio < Precio objetivo analistas",
    }
    for key, info in detail.items():
        rows.append({
            "Condición": labels.get(key, key),
            "Peso": info["peso"],
            "¿Cumple?": "✅" if info["cumplida"] else "❌",
            "Puntos": info["puntos"],
        })
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)


# ---------------------------------------------------------------------------
# Modo 1: Escaneo de mercado
# ---------------------------------------------------------------------------
if mode.startswith("📊"):
    st.title("📊 Screener S&P 500 — Valoración + Timing")
    st.caption(
        "Combina Motor 1 (Margen de Seguridad fundamental) con Motor 2 "
        "(scoring técnico ponderado) para priorizar oportunidades infravaloradas "
        "con timing técnico favorable."
    )

    col_a, col_b, col_c = st.columns(3)
    with col_a:
        run_scan = st.button("🚀 Ejecutar escaneo", type="primary")
    with col_b:
        min_score_filter = st.slider("Score mínimo (%)", 0, 100, 0, step=5)
    with col_c:
        solo_senales = st.checkbox("Solo mostrar SEÑAL DE ENTRADA", value=False)

    if run_scan:
        progress = st.progress(0.0, text="Iniciando escaneo...")

        def _cb(done, total, ticker):
            progress.progress(done / total, text=f"Analizando {ticker} ({done}/{total})")

        with st.spinner("Descargando/actualizando datos y calculando..."):
            df_scan = scanner.scan_universe(provider, progress_cb=_cb)
        progress.empty()
        st.session_state["df_scan"] = df_scan

    df_scan = st.session_state.get("df_scan")
    if df_scan is not None and not df_scan.empty:
        view = df_scan.copy()
        if "score_pct" in view.columns:
            view = view[view["score_pct"].fillna(0) >= min_score_filter]
        if solo_senales and "senal_entrada" in view.columns:
            view = view[view["senal_entrada"] == True]  # noqa: E712

        st.dataframe(
            view.style.format({
                "precio": "{:.2f}", "valor_justo": "{:.2f}",
                "margen_seguridad_pct": "{:.1f}%", "score_pct": "{:.1f}%",
            }, na_rep="—"),
            use_container_width=True, hide_index=True
        )
        st.caption(f"{len(view)} de {len(df_scan)} tickers tras filtros.")
    else:
        st.info("Pulsa **Ejecutar escaneo** para analizar el universo (proveedor: "
                f"`{provider_name}`).")


# ---------------------------------------------------------------------------
# Modo 2: Análisis individual
# ---------------------------------------------------------------------------
else:
    st.title("🔎 Análisis Individual de Ticker")
    ticker_input = st.text_input("Ticker (ej. AAPL, VALCO en modo demo)", value="").upper().strip()

    if provider_name == "demo" and not ticker_input:
        st.caption("Tickers disponibles en modo demo: VALCO, QUALX, GROWZ, HYPRN, BANKA, "
                   "UTILB, CYCLC, ENRGX, SAASY, PHARM")

    if ticker_input:
        with st.spinner(f"Analizando {ticker_input}..."):
            try:
                result = scanner.analyze_ticker(ticker_input, provider)
            except Exception as e:
                st.error(f"No se pudo analizar {ticker_input}: {e}")
                st.stop()

        meta = result["meta"]
        v = result["valuation"]
        s = result["score"]

        st.subheader(f"{meta['name']} ({result['ticker']}) — {meta['sector']}")

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Precio actual", f"${v.get('precio_actual', 0):.2f}" if v.get("precio_actual") else "N/D")
        c2.metric("Valor justo promedio", f"${v.get('fair_value_promedio', 0):.2f}" if v.get("fair_value_promedio") else "N/D")
        margen = v.get("margen_seguridad_pct")
        c3.metric("Margen de Seguridad", f"{margen:.1f}%" if margen is not None else "N/D")
        c4.metric("Piotroski F-Score", f"{result['piotroski_score']}/9")

        badges = []
        if v.get("hyper_growth_mode"):
            badges.append("🚀 Hyper-Growth (EPS neg., EV/Sales)")
        if v.get("rule_of_40_ok"):
            badges.append(f"🏆 Regla del 40 ({v.get('rule_of_40_score', 0):.0f}%)")
        if meta.get("is_financial"):
            badges.append("🏦 Financiera (incluye Price/Book)")
        if result["technical_snapshot"].get("vcp_signal"):
            badges.append("🌀 VCP detectado (squeeze de volatilidad)")
        insider = result.get("insider_activity", {})
        if insider.get("signal"):
            badges.append(f"💼 Insider Buying (compras netas ${insider.get('net_value', 0):,.0f})")
        if badges:
            st.write(" · ".join(badges))

        st.markdown("---")
        left, right = st.columns([1, 1])
        with left:
            render_valuation_breakdown(v)
        with right:
            render_score_detail(s, result["conditions"])

        st.markdown("---")
        render_price_chart(result["price_history"])

        with st.expander("Ver datos fundamentales crudos (payload del proveedor)"):
            st.json(result["fundamentals_raw"])
        with st.expander("Ver actividad de insiders (compras/ventas de directivos)"):
            st.json(result["insider_activity"])
        with st.expander("Ver benchmark sectorial usado"):
            st.json(result["sector_benchmark"])
        with st.expander("Ver contexto macro (US10Y, régimen S&P 500)"):
            st.json(result["macro"])
