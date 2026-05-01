"""
migrations/env.py — Alembic migration environment for OpenAlgo.

Reads DATABASE_URL (main database) from the environment at runtime so the
same migration can target SQLite (development) or PostgreSQL / Neon /
Supabase (production) without changing any code.

To run migrations:
    uv run alembic upgrade head        # apply all pending migrations
    uv run alembic revision --autogenerate -m "describe change"   # create new migration

The initial migration (revision 0001) was generated from the current schema
and serves as the baseline.  Running ``upgrade head`` on a fresh database will
create all tables; running it on an existing database is a no-op.
"""

import os
import sys
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool

# ---------------------------------------------------------------------------
# Make sure the project root is on sys.path so local imports work
# ---------------------------------------------------------------------------
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

# ---------------------------------------------------------------------------
# Import all SQLAlchemy models so Alembic can autogenerate diffs
# ---------------------------------------------------------------------------
# Each import registers the model's metadata with its Base.  We collect all
# metadata objects into a combined MetaData so autogenerate sees every table.
from sqlalchemy import MetaData

combined_metadata = MetaData()


def _import_models():
    """Import every model module so their tables are registered."""
    from database.auth_db import Base as AuthBase
    from database.settings_db import Base as SettingsBase
    from database.user_db import Base as UserBase
    from database.symbol import Base as SymbolBase
    from database.analyzer_db import Base as AnalyzerBase
    from database.apilog_db import Base as ApiLogBase
    from database.action_center_db import Base as ActionCenterBase
    from database.chart_prefs_db import Base as ChartPrefsBase
    from database.chartink_db import Base as ChartinkBase
    from database.flow_db import Base as FlowBase
    from database.leverage_db import Base as LeverageBase
    from database.market_calendar_db import Base as MarketCalendarBase
    from database.master_contract_status_db import Base as MasterContractStatusBase
    from database.qty_freeze_db import Base as QtyFreezeBase
    from database.strategy_db import Base as StrategyBase
    from database.strategy_portfolio_db import Base as StrategyPortfolioBase
    from database.telegram_db import Base as TelegramBase

    all_bases = [
        AuthBase, SettingsBase, UserBase, SymbolBase, AnalyzerBase,
        ApiLogBase, ActionCenterBase, ChartPrefsBase, ChartinkBase,
        FlowBase, LeverageBase, MarketCalendarBase, MasterContractStatusBase,
        QtyFreezeBase, StrategyBase, StrategyPortfolioBase, TelegramBase,
    ]

    # Merge all table definitions into combined_metadata so autogenerate works
    for base in all_bases:
        for table in base.metadata.tables.values():
            if table.name not in combined_metadata.tables:
                table.tometadata(combined_metadata)


_import_models()

# ---------------------------------------------------------------------------
# Alembic config object
# ---------------------------------------------------------------------------
config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = combined_metadata


def _get_url() -> str:
    """Get the main database URL from the environment."""
    from dotenv import load_dotenv
    load_dotenv()
    url = os.getenv("DATABASE_URL")
    if not url:
        raise RuntimeError(
            "DATABASE_URL environment variable is not set.  "
            "Set it in your .env file before running Alembic."
        )
    return url


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode (emit SQL to stdout)."""
    url = _get_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,  # required for SQLite ALTER TABLE support
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode (apply directly to the database)."""
    from database.engine_factory import make_engine

    url = _get_url()
    connectable = make_engine(url)

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True,  # required for SQLite ALTER TABLE support
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
