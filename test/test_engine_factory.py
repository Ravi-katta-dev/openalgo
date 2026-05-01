"""
test/test_engine_factory.py — Unit tests for database/engine_factory.py

Run with:
    uv run pytest test/test_engine_factory.py -v
"""

import os
import sys

import pytest

# Make the project root importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from database.engine_factory import (
    _is_postgres,
    _is_serverless_postgres,
    _is_sqlite,
    _build_connect_args,
    _rewrite_driver_for_eventlet,
    make_engine,
)
from sqlalchemy.pool import NullPool


# ---------------------------------------------------------------------------
# URL Classification
# ---------------------------------------------------------------------------

class TestUrlClassification:
    def test_sqlite_detected(self):
        assert _is_sqlite("sqlite:///db/openalgo.db")

    def test_sqlite_memory_detected(self):
        assert _is_sqlite("sqlite:///:memory:")

    def test_postgres_detected_full(self):
        assert _is_postgres("postgresql://user:pass@localhost/db")

    def test_postgres_alias_detected(self):
        assert _is_postgres("postgres://user:pass@localhost/db")

    def test_postgres_plus_pg8000_detected(self):
        assert _is_postgres("postgresql+pg8000://user:pass@localhost/db")

    def test_postgres_plus_psycopg2_detected(self):
        assert _is_postgres("postgresql+psycopg2://user:pass@localhost/db")

    def test_sqlite_not_postgres(self):
        assert not _is_postgres("sqlite:///db/openalgo.db")


# ---------------------------------------------------------------------------
# Serverless detection
# ---------------------------------------------------------------------------

class TestServerlessDetection:
    def test_neon_detected(self):
        assert _is_serverless_postgres(
            "postgresql+pg8000://user:pass@ep-cool-wind-123.us-east-2.aws.neon.tech/openalgo"
        )

    def test_supabase_detected(self):
        assert _is_serverless_postgres(
            "postgresql+pg8000://postgres:pass@db.abcxyz.supabase.co:5432/postgres"
        )

    def test_local_pg_not_serverless(self):
        assert not _is_serverless_postgres(
            "postgresql+pg8000://user:pass@localhost:5432/openalgo"
        )

    def test_rds_not_serverless(self):
        assert not _is_serverless_postgres(
            "postgresql+pg8000://user:pass@mydb.cluster-xyz.us-east-1.rds.amazonaws.com/openalgo"
        )


# ---------------------------------------------------------------------------
# connect_args building
# ---------------------------------------------------------------------------

class TestConnectArgs:
    def test_sqlite_check_same_thread(self):
        args = _build_connect_args("sqlite:///db/openalgo.db")
        assert args.get("check_same_thread") is False

    def test_pg_no_ssl_by_default_for_local(self):
        args = _build_connect_args("postgresql+pg8000://user:pass@localhost/db")
        assert "sslmode" not in args

    def test_neon_ssl_required_by_default(self):
        args = _build_connect_args(
            "postgresql+pg8000://user:pass@ep-xyz.neon.tech/openalgo"
        )
        assert args.get("sslmode") == "require"

    def test_supabase_ssl_required_by_default(self):
        args = _build_connect_args(
            "postgresql+pg8000://postgres:pass@db.abc.supabase.co:5432/postgres"
        )
        assert args.get("sslmode") == "require"

    def test_db_ssl_mode_env_override(self, monkeypatch):
        monkeypatch.setenv("DB_SSL_MODE", "verify-full")
        args = _build_connect_args("postgresql+pg8000://user:pass@localhost/db")
        assert args.get("sslmode") == "verify-full"

    def test_db_ssl_mode_env_override_clears_after(self, monkeypatch):
        monkeypatch.delenv("DB_SSL_MODE", raising=False)
        args = _build_connect_args("postgresql+pg8000://user:pass@localhost/db")
        assert "sslmode" not in args


# ---------------------------------------------------------------------------
# Driver rewriting for eventlet
# ---------------------------------------------------------------------------

class TestDriverRewrite:
    def test_no_rewrite_without_eventlet(self, monkeypatch):
        """Without eventlet in sys.modules, URL must not be changed."""
        monkeypatch.delitem(sys.modules, "eventlet", raising=False)
        monkeypatch.setenv("SERVER_SOFTWARE", "")
        url = "postgresql://user:pass@localhost/db"
        assert _rewrite_driver_for_eventlet(url) == url

    def test_rewrite_bare_postgresql_under_eventlet(self, monkeypatch):
        """bare postgresql:// → postgresql+pg8000:// when eventlet is present."""
        import types
        monkeypatch.setitem(sys.modules, "eventlet", types.ModuleType("eventlet"))
        url = "postgresql://user:pass@localhost/db"
        rewritten = _rewrite_driver_for_eventlet(url)
        assert rewritten.startswith("postgresql+pg8000://")

    def test_rewrite_psycopg2_under_eventlet(self, monkeypatch):
        import types
        monkeypatch.setitem(sys.modules, "eventlet", types.ModuleType("eventlet"))
        url = "postgresql+psycopg2://user:pass@localhost/db"
        rewritten = _rewrite_driver_for_eventlet(url)
        assert rewritten.startswith("postgresql+pg8000://")

    def test_pg8000_not_rewritten(self, monkeypatch):
        """Already-safe driver must not be double-rewritten."""
        import types
        monkeypatch.setitem(sys.modules, "eventlet", types.ModuleType("eventlet"))
        url = "postgresql+pg8000://user:pass@localhost/db"
        assert _rewrite_driver_for_eventlet(url) == url

    def test_sqlite_never_rewritten(self, monkeypatch):
        import types
        monkeypatch.setitem(sys.modules, "eventlet", types.ModuleType("eventlet"))
        url = "sqlite:///db/openalgo.db"
        assert _rewrite_driver_for_eventlet(url) == url


# ---------------------------------------------------------------------------
# make_engine — SQLite
# ---------------------------------------------------------------------------

class TestMakeEngineSQLite:
    def test_sqlite_returns_engine(self):
        engine = make_engine("sqlite:///:memory:")
        assert engine is not None

    def test_sqlite_uses_nullpool(self):
        engine = make_engine("sqlite:///:memory:")
        assert engine.pool.__class__ is NullPool

    def test_sqlite_pragma_listener_registered(self, tmp_path):
        """PRAGMA event listener should be attached for SQLite engines."""
        db_file = str(tmp_path / "test.db")
        engine = make_engine(f"sqlite:///{db_file}")
        # Verify a connect event fires without error (pragmas are applied)
        with engine.connect() as conn:
            result = conn.execute(
                __import__("sqlalchemy").text("PRAGMA journal_mode")
            ).scalar()
        assert result == "wal"

    def test_empty_url_raises(self):
        with pytest.raises(ValueError, match="DATABASE_URL"):
            make_engine("")

    def test_none_url_raises(self):
        with pytest.raises((ValueError, TypeError)):
            make_engine(None)


# ---------------------------------------------------------------------------
# make_engine — PostgreSQL (connection string validated without connecting)
# ---------------------------------------------------------------------------

class TestMakeEnginePostgres:
    """
    These tests build an Engine from a synthetic URL.  They do NOT connect to
    any real database — they only verify the engine configuration (pool class,
    pool parameters, connect_args).
    """

    def test_pg_engine_has_pool_pre_ping(self):
        engine = make_engine("postgresql+pg8000://user:pass@localhost/db")
        assert engine.pool._pre_ping is True

    def test_pg_engine_pool_size_default(self):
        engine = make_engine("postgresql+pg8000://user:pass@localhost/db")
        assert engine.pool.size() == int(50 * 1.0)

    def test_pg_engine_pool_scale(self):
        engine = make_engine("postgresql+pg8000://user:pass@localhost/db", pool_scale=0.4)
        expected = max(1, int(50 * 0.4))
        assert engine.pool.size() == expected

    def test_pg_env_pool_size_override(self, monkeypatch):
        monkeypatch.setenv("DB_POOL_SIZE", "7")
        engine = make_engine("postgresql+pg8000://user:pass@localhost/db")
        assert engine.pool.size() == 7
        monkeypatch.delenv("DB_POOL_SIZE")

    def test_neon_engine_smaller_pool(self):
        engine = make_engine(
            "postgresql+pg8000://user:pass@ep-xyz.neon.tech/openalgo"
        )
        # Serverless pool_size default is 5
        assert engine.pool.size() == 5

    def test_neon_engine_has_pool_pre_ping(self):
        engine = make_engine(
            "postgresql+pg8000://user:pass@ep-xyz.neon.tech/openalgo"
        )
        assert engine.pool._pre_ping is True
