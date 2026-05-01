"""
database/engine_factory.py

Centralised SQLAlchemy engine factory for OpenAlgo.

All database modules should call ``make_engine(url)`` instead of
duplicating the SQLite / PostgreSQL if/else block.  The factory:

* Detects the database dialect from the URL scheme.
* Applies the correct pool strategy for each dialect.
* Handles cloud-managed PostgreSQL services (Neon, Supabase) that drop
  idle connections aggressively — pool_pre_ping and a smaller pool.
* Merges SSL options from the ``DB_SSL_MODE`` environment variable.
* Registers a SQLite-only ``connect`` event listener that sets WAL mode
  and other pragmas, so broker modules no longer need to issue PRAGMAs
  unconditionally.
* Warns (or auto-fixes) psycopg2 when running under Gunicorn + eventlet,
  which monkey-patches the stdlib but not the C-extension psycopg2 driver.
* Allows operators to tune pool settings via environment variables
  without touching code.

Public API
----------
    make_engine(url, pool_scale=1.0) -> sqlalchemy.engine.Engine
"""

import os
import sys
from urllib.parse import urlparse

from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine
from sqlalchemy.pool import NullPool

from utils.logging import get_logger

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Environment-variable overrides for pool tuning (PostgreSQL only)
# ---------------------------------------------------------------------------
_DEFAULT_POOL_SIZE = 50
_DEFAULT_MAX_OVERFLOW = 100
_DEFAULT_POOL_TIMEOUT = 10
_DEFAULT_POOL_RECYCLE = 1800  # 30 min — Neon/Supabase close connections sooner

# Neon / Supabase use a smaller pool because they have strict connection limits
_SERVERLESS_POOL_SIZE = 5
_SERVERLESS_MAX_OVERFLOW = 10


def _env_int(name: str, default: int) -> int:
    """Read an integer from an env var, falling back to *default*."""
    raw = os.getenv(name, "").strip()
    if raw.isdigit():
        return int(raw)
    return default


# ---------------------------------------------------------------------------
# URL helpers
# ---------------------------------------------------------------------------

def _is_sqlite(url: str) -> bool:
    return "sqlite" in url.lower()


def _is_postgres(url: str) -> bool:
    lower = url.lower()
    return any(
        lower.startswith(prefix)
        for prefix in ("postgresql", "postgres", "postgresql+psycopg2", "postgresql+pg8000")
    )


def _is_serverless_postgres(url: str) -> bool:
    """Detect managed serverless PG hosts that enforce strict connection limits."""
    try:
        hostname = urlparse(url).hostname or ""
    except Exception:
        hostname = ""
    return hostname.endswith(".neon.tech") or hostname.endswith(".supabase.co")


def _rewrite_driver_for_eventlet(url: str) -> str:
    """
    Under Gunicorn + eventlet the stdlib is monkey-patched, but psycopg2 is a
    C extension that bypasses the patched socket layer — this causes connection
    hangs and subtle data-race bugs.

    When we detect an eventlet environment we rewrite a bare ``postgresql://``
    or ``postgresql+psycopg2://`` URL to ``postgresql+pg8000://`` (pure Python,
    fully compatible with eventlet's green threads).

    pg8000 is a standard dependency of SQLAlchemy and requires no extra install.
    """
    if not _is_postgres(url):
        return url

    under_eventlet = "eventlet" in sys.modules
    under_gunicorn = os.getenv("SERVER_SOFTWARE", "").lower().startswith("gunicorn")

    if not (under_eventlet or under_gunicorn):
        return url

    lower = url.lower()
    # Already using a safe driver
    if "pg8000" in lower or "asyncpg" in lower:
        return url

    if lower.startswith("postgresql+psycopg2://"):
        new_url = "postgresql+pg8000://" + url[len("postgresql+psycopg2://"):]
        logger.info(
            "eventlet detected: rewrote psycopg2 driver to pg8000 for PostgreSQL URL. "
            "pg8000 is pure-Python and works correctly with eventlet green threads."
        )
        return new_url

    if lower.startswith("postgresql://") or lower.startswith("postgres://"):
        # Insert pg8000 driver
        scheme_end = url.index("://")
        new_url = "postgresql+pg8000://" + url[scheme_end + 3:]
        logger.info(
            "eventlet detected: rewrote bare postgresql:// driver to pg8000 for "
            "PostgreSQL URL."
        )
        return new_url

    return url


def _build_connect_args(url: str) -> dict:
    """
    Construct the ``connect_args`` dict appropriate for the dialect.

    * SQLite — ``check_same_thread=False`` (required for Flask/eventlet).
    * PostgreSQL — optional ``sslmode`` from ``DB_SSL_MODE`` env var; for
      serverless hosts (Neon, Supabase) ``sslmode=require`` is applied by
      default when the env var is absent.
    """
    if _is_sqlite(url):
        return {"check_same_thread": False}

    if _is_postgres(url):
        ssl_mode = os.getenv("DB_SSL_MODE", "").strip()
        if not ssl_mode and _is_serverless_postgres(url):
            ssl_mode = "require"
        if ssl_mode:
            return {"sslmode": ssl_mode}

    return {}


# ---------------------------------------------------------------------------
# SQLite PRAGMA event listener
# ---------------------------------------------------------------------------

def _register_sqlite_pragmas(engine: Engine) -> None:
    """
    Register a ``connect`` event listener that sets performance-oriented
    SQLite PRAGMAs on every new connection.

    By centralising this here, broker modules no longer need to issue
    PRAGMAs unconditionally (which would crash on PostgreSQL).
    """

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragmas(dbapi_connection, _connection_record):
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA synchronous=NORMAL")
            cursor.execute("PRAGMA temp_store=memory")
            cursor.execute("PRAGMA mmap_size=268435456")  # 256 MB
            cursor.execute("PRAGMA foreign_keys=ON")
        finally:
            cursor.close()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def make_engine(url: str, pool_scale: float = 1.0) -> Engine:
    """
    Build and return a SQLAlchemy ``Engine`` configured for *url*.

    Parameters
    ----------
    url : str
        A SQLAlchemy-compatible database URL.  Must not be ``None`` or empty.
    pool_scale : float
        Multiplier applied to the default ``pool_size`` and ``max_overflow``
        values.  Use values < 1.0 for high-churn ancillary databases (logs,
        latency, health, sandbox) that benefit from a smaller pool.

    Returns
    -------
    sqlalchemy.engine.Engine
    """
    if not url:
        raise ValueError(
            "Database URL is empty or None.  "
            "Check that the DATABASE_URL (or the relevant *_DATABASE_URL) "
            "environment variable is set in your .env file."
        )

    url = _rewrite_driver_for_eventlet(url)
    connect_args = _build_connect_args(url)

    # ------------------------------------------------------------------
    # SQLite
    # ------------------------------------------------------------------
    if _is_sqlite(url):
        engine = create_engine(
            url,
            poolclass=NullPool,
            connect_args=connect_args,
        )
        _register_sqlite_pragmas(engine)
        return engine

    # ------------------------------------------------------------------
    # PostgreSQL (and compatible: CockroachDB, AlloyDB, etc.)
    # ------------------------------------------------------------------
    if _is_postgres(url):
        if _is_serverless_postgres(url):
            pool_size = _env_int("DB_POOL_SIZE", _SERVERLESS_POOL_SIZE)
            max_overflow = _env_int("DB_MAX_OVERFLOW", _SERVERLESS_MAX_OVERFLOW)
        else:
            pool_size = _env_int(
                "DB_POOL_SIZE", max(1, int(_DEFAULT_POOL_SIZE * pool_scale))
            )
            max_overflow = _env_int(
                "DB_MAX_OVERFLOW", max(1, int(_DEFAULT_MAX_OVERFLOW * pool_scale))
            )

        pool_timeout = _env_int("DB_POOL_TIMEOUT", _DEFAULT_POOL_TIMEOUT)

        engine = create_engine(
            url,
            pool_size=pool_size,
            max_overflow=max_overflow,
            pool_timeout=pool_timeout,
            pool_recycle=_DEFAULT_POOL_RECYCLE,
            pool_pre_ping=True,  # Vital for Neon/Supabase idle-connection resets
            connect_args=connect_args,
        )
        return engine

    # ------------------------------------------------------------------
    # Unknown / other dialects — fall through with no pool configuration
    # ------------------------------------------------------------------
    logger.warning(
        "Unrecognised database URL scheme for '%s'. "
        "Falling back to default SQLAlchemy engine (no pool tuning applied).",
        url.split("://")[0],
    )
    return create_engine(url)
