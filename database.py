"""
database.py
============
Capa de persistencia. Toda la app pasa por aquí para leer/escribir en SQLite.
Objetivo: minimizar llamadas a APIs externas cacheando localmente precios
históricos ajustados, fundamentales "estáticos" (por snapshot) y benchmarks
sectoriales, respetando TTLs definidos en config.py.

Las VALORACIONES (Motor 1) NUNCA se cachean como resultado final: se
recalculan siempre en caliente a partir de los inputs cacheados + la tasa
libre de riesgo vigente, tal y como pide el requisito de recálculo dinámico.
"""

import sqlite3
import json
from datetime import datetime, timedelta
from contextlib import contextmanager

import pandas as pd

import config


# ---------------------------------------------------------------------------
# Conexión / esquema
# ---------------------------------------------------------------------------
@contextmanager
def get_conn():
    conn = sqlite3.connect(config.DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL;")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    with get_conn() as conn:
        c = conn.cursor()

        c.execute("""
        CREATE TABLE IF NOT EXISTS tickers (
            ticker TEXT PRIMARY KEY,
            name TEXT,
            sector TEXT,
            industry TEXT,
            is_financial INTEGER DEFAULT 0,
            updated_at TEXT
        )""")

        c.execute("""
        CREATE TABLE IF NOT EXISTS prices (
            ticker TEXT,
            date TEXT,
            open REAL, high REAL, low REAL,
            close_adj REAL,
            volume REAL,
            PRIMARY KEY (ticker, date)
        )""")

        c.execute("""
        CREATE TABLE IF NOT EXISTS fundamentals (
            ticker TEXT PRIMARY KEY,
            payload TEXT,       -- JSON con todo el snapshot fundamental
            fetched_at TEXT
        )""")

        c.execute("""
        CREATE TABLE IF NOT EXISTS sector_benchmarks (
            sector TEXT PRIMARY KEY,
            payload TEXT,       -- JSON: per_justo, peg_justo, ev_ebitda_justo, pb_justo, ev_sales_justo
            updated_at TEXT
        )""")

        c.execute("""
        CREATE TABLE IF NOT EXISTS macro (
            key TEXT PRIMARY KEY,   -- ej: 'us10y', 'spx_regime'
            payload TEXT,
            updated_at TEXT
        )""")

        c.execute("""
        CREATE TABLE IF NOT EXISTS insider_activity (
            ticker TEXT PRIMARY KEY,
            payload TEXT,       -- JSON: buy_value, sell_value, buy_count, sell_count, net_value, signal
            updated_at TEXT
        )""")

        c.execute("""
        CREATE TABLE IF NOT EXISTS universe_cache (
            provider TEXT PRIMARY KEY,
            payload TEXT,       -- JSON: lista de tickers del universo
            source TEXT,        -- 'wikipedia' | 'fallback' | 'native' ...
            expires_at TEXT
        )""")
        c.execute("""
        CREATE INDEX IF NOT EXISTS idx_prices_ticker_date ON prices(ticker, date)
        """)


# ---------------------------------------------------------------------------
# Helpers de frescura de caché
# ---------------------------------------------------------------------------
def _is_stale(updated_at_str, ttl):
    if not updated_at_str:
        return True
    try:
        updated_at = datetime.fromisoformat(updated_at_str)
    except ValueError:
        return True
    return datetime.utcnow() - updated_at > ttl


# ---------------------------------------------------------------------------
# Tickers / universo
# ---------------------------------------------------------------------------
def upsert_ticker_meta(ticker, name, sector, industry, is_financial):
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO tickers (ticker, name, sector, industry, is_financial, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(ticker) DO UPDATE SET
                name=excluded.name, sector=excluded.sector,
                industry=excluded.industry, is_financial=excluded.is_financial,
                updated_at=excluded.updated_at
        """, (ticker, name, sector, industry, int(is_financial), datetime.utcnow().isoformat()))


def get_ticker_meta(ticker):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT ticker, name, sector, industry, is_financial FROM tickers WHERE ticker=?",
            (ticker,)
        ).fetchone()
    if not row:
        return None
    return {
        "ticker": row[0], "name": row[1], "sector": row[2],
        "industry": row[3], "is_financial": bool(row[4])
    }


# ---------------------------------------------------------------------------
# Precios históricos (ajustados por splits/dividendos)
# ---------------------------------------------------------------------------
def save_prices(ticker, df: pd.DataFrame):
    """df debe tener columnas: date, open, high, low, close_adj, volume"""
    if df.empty:
        return
    with get_conn() as conn:
        rows = [
            (ticker, str(r.date), float(r.open), float(r.high), float(r.low),
             float(r.close_adj), float(r.volume))
            for r in df.itertuples(index=False)
        ]
        conn.executemany("""
            INSERT INTO prices (ticker, date, open, high, low, close_adj, volume)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(ticker, date) DO UPDATE SET
                open=excluded.open, high=excluded.high, low=excluded.low,
                close_adj=excluded.close_adj, volume=excluded.volume
        """, rows)


def load_prices(ticker, lookback_days=400):
    cutoff = (datetime.utcnow() - timedelta(days=lookback_days)).date().isoformat()
    with get_conn() as conn:
        df = pd.read_sql_query(
            "SELECT date, open, high, low, close_adj, volume FROM prices "
            "WHERE ticker=? AND date>=? ORDER BY date ASC",
            conn, params=(ticker, cutoff)
        )
    if not df.empty:
        df["date"] = pd.to_datetime(df["date"])
    return df


def prices_are_fresh(ticker):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT MAX(date) FROM prices WHERE ticker=?", (ticker,)
        ).fetchone()
    if not row or not row[0]:
        return False
    last_date = datetime.fromisoformat(row[0])
    return datetime.utcnow() - last_date < timedelta(hours=config.PRICE_CACHE_TTL_HOURS)


# ---------------------------------------------------------------------------
# Fundamentales (snapshot JSON por ticker)
# ---------------------------------------------------------------------------
def save_fundamentals(ticker, payload: dict):
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO fundamentals (ticker, payload, fetched_at)
            VALUES (?, ?, ?)
            ON CONFLICT(ticker) DO UPDATE SET
                payload=excluded.payload, fetched_at=excluded.fetched_at
        """, (ticker, json.dumps(payload), datetime.utcnow().isoformat()))


def load_fundamentals(ticker):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT payload, fetched_at FROM fundamentals WHERE ticker=?", (ticker,)
        ).fetchone()
    if not row:
        return None, True  # (payload, is_stale)
    payload = json.loads(row[0])
    stale = _is_stale(row[1], timedelta(days=config.FUNDAMENTALS_CACHE_TTL_DAYS))
    return payload, stale


# ---------------------------------------------------------------------------
# Benchmarks sectoriales
# ---------------------------------------------------------------------------
def save_sector_benchmark(sector, payload: dict):
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO sector_benchmarks (sector, payload, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(sector) DO UPDATE SET
                payload=excluded.payload, updated_at=excluded.updated_at
        """, (sector, json.dumps(payload), datetime.utcnow().isoformat()))


def load_sector_benchmark(sector):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT payload, updated_at FROM sector_benchmarks WHERE sector=?", (sector,)
        ).fetchone()
    if not row:
        return None, True
    payload = json.loads(row[0])
    stale = _is_stale(row[1], timedelta(days=config.SECTOR_BENCHMARK_TTL_DAYS))
    return payload, stale


# ---------------------------------------------------------------------------
# Macro (US10Y, régimen del S&P 500 respecto a su MM200)
# ---------------------------------------------------------------------------
def save_macro(key, payload: dict):
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO macro (key, payload, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET
                payload=excluded.payload, updated_at=excluded.updated_at
        """, (key, json.dumps(payload), datetime.utcnow().isoformat()))


def load_macro(key):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT payload, updated_at FROM macro WHERE key=?", (key,)
        ).fetchone()
    if not row:
        return None, True
    payload = json.loads(row[0])
    stale = _is_stale(row[1], timedelta(hours=config.MACRO_CACHE_TTL_HOURS))
    return payload, stale


# ---------------------------------------------------------------------------
# Actividad de insiders (compras/ventas de directivos)
# ---------------------------------------------------------------------------
def save_insider_activity(ticker, payload: dict):
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO insider_activity (ticker, payload, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(ticker) DO UPDATE SET
                payload=excluded.payload, updated_at=excluded.updated_at
        """, (ticker, json.dumps(payload), datetime.utcnow().isoformat()))


def load_insider_activity(ticker):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT payload, updated_at FROM insider_activity WHERE ticker=?", (ticker,)
        ).fetchone()
    if not row:
        return None, True
    payload = json.loads(row[0])
    stale = _is_stale(row[1], timedelta(days=config.INSIDER_CACHE_TTL_DAYS))
    return payload, stale


# ---------------------------------------------------------------------------
# Universo de tickers (S&P 500 u otro) — cacheado por proveedor
# ---------------------------------------------------------------------------
def save_universe(provider_name: str, tickers: list, source: str):
    ttl = (
        timedelta(days=config.UNIVERSE_CACHE_TTL_DAYS)
        if source == "wikipedia"
        else timedelta(hours=config.UNIVERSE_FALLBACK_RETRY_HOURS)
    )
    expires_at = (datetime.utcnow() + ttl).isoformat()
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO universe_cache (provider, payload, source, expires_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(provider) DO UPDATE SET
                payload=excluded.payload, source=excluded.source, expires_at=excluded.expires_at
        """, (provider_name, json.dumps(tickers), source, expires_at))


def load_universe(provider_name: str):
    """Devuelve (tickers|None, source|None, stale: bool)."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT payload, source, expires_at FROM universe_cache WHERE provider=?", (provider_name,)
        ).fetchone()
    if not row:
        return None, None, True
    tickers = json.loads(row[0])
    stale = datetime.utcnow() > datetime.fromisoformat(row[2])
    return tickers, row[1], stale
