"""
utils/db_migrate.py — SQLite → PostgreSQL (or any SQLAlchemy target) data migration.

Copies every row from each of the five SQLAlchemy-managed OpenAlgo databases
into a target database.  Historify (DuckDB) is intentionally skipped.

Usage
-----
    # Dry run — show what would be migrated without writing anything
    uv run python utils/db_migrate.py \\
        --source sqlite:///db/openalgo.db \\
        --target postgresql+pg8000://user:pass@host/openalgo \\
        --dry-run

    # Live migration of the main database
    uv run python utils/db_migrate.py \\
        --source sqlite:///db/openalgo.db \\
        --target postgresql+pg8000://user:pass@host/openalgo

    # Migrate all five databases in one command
    uv run python utils/db_migrate.py \\
        --source sqlite:///db/openalgo.db \\
        --target postgresql+pg8000://user:pass@host/openalgo \\
        --logs-source    sqlite:///db/logs.db \\
        --logs-target    postgresql+pg8000://user:pass@host/openalgo_logs \\
        --latency-source sqlite:///db/latency.db \\
        --latency-target postgresql+pg8000://user:pass@host/openalgo_latency \\
        --health-source  sqlite:///db/health.db \\
        --health-target  postgresql+pg8000://user:pass@host/openalgo_health \\
        --sandbox-source sqlite:///db/sandbox.db \\
        --sandbox-target postgresql+pg8000://user:pass@host/openalgo_sandbox

Notes
-----
- Tables are migrated in dependency order (parents before children).
- Each table is migrated in batches of 500 rows to keep memory usage low.
- If the target table already has rows the migration is SKIPPED for that table
  (use --truncate to force overwrite).
- The script does NOT migrate Historify (DuckDB) — export/import that
  database separately using DuckDB's native tooling.
- The script does NOT modify the source database.
"""

import argparse
import sys
from typing import Optional

from sqlalchemy import MetaData, Table, create_engine, inspect, text
from sqlalchemy.dialects.postgresql import insert as pg_insert

BATCH_SIZE = 500


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_engine(url: str):
    """Create an engine with sensible defaults for migration use."""
    from database.engine_factory import make_engine
    return make_engine(url)


def _tables_in_dependency_order(metadata: MetaData):
    """Return tables sorted so that parents come before children (FK order)."""
    return list(metadata.sorted_tables)


def _migrate_table(
    src_engine,
    dst_engine,
    table: Table,
    dry_run: bool,
    truncate: bool,
    verbose: bool,
) -> int:
    """
    Copy all rows from *table* in the source engine to the same table in the
    destination engine.

    Returns the number of rows copied (0 on skip/dry-run).
    """
    table_name = table.name

    with src_engine.connect() as src_conn:
        total = src_conn.execute(text(f"SELECT COUNT(*) FROM {table_name}")).scalar()  # noqa: S608

    if total == 0:
        print(f"  {table_name}: empty — skipping")
        return 0

    # Check whether destination already has data
    with dst_engine.connect() as dst_conn:
        try:
            existing = dst_conn.execute(
                text(f"SELECT COUNT(*) FROM {table_name}")  # noqa: S608
            ).scalar()
        except Exception:
            existing = 0

    if existing and not truncate:
        print(f"  {table_name}: destination has {existing} rows — skipping (use --truncate to overwrite)")
        return 0

    if dry_run:
        print(f"  {table_name}: {total} rows — DRY RUN (would copy)")
        return 0

    # Reflect destination table so we can use its insert()
    dst_meta = MetaData()
    dst_meta.reflect(bind=dst_engine, only=[table_name])
    dst_table = dst_meta.tables[table_name]

    if truncate and existing:
        with dst_engine.begin() as dst_conn:
            dst_conn.execute(dst_table.delete())
        print(f"  {table_name}: truncated {existing} existing rows")

    copied = 0
    with src_engine.connect() as src_conn:
        offset = 0
        while True:
            rows = src_conn.execute(
                text(f"SELECT * FROM {table_name} LIMIT {BATCH_SIZE} OFFSET {offset}")  # noqa: S608
            ).fetchall()
            if not rows:
                break

            row_dicts = [dict(row._mapping) for row in rows]

            with dst_engine.begin() as dst_conn:
                dst_conn.execute(dst_table.insert(), row_dicts)

            copied += len(row_dicts)
            offset += BATCH_SIZE
            if verbose:
                print(f"    {table_name}: {copied}/{total} rows", end="\r", flush=True)

    if verbose:
        print()  # newline after progress
    print(f"  {table_name}: {copied} rows copied ✓")
    return copied


def _migrate_database(
    src_url: str,
    dst_url: str,
    label: str,
    dry_run: bool,
    truncate: bool,
    verbose: bool,
) -> None:
    """Migrate all tables from source to destination for one database pair."""
    print(f"\n{'='*60}")
    print(f"Migrating {label}")
    print(f"  Source : {src_url}")
    print(f"  Target : {dst_url}")
    if dry_run:
        print("  Mode   : DRY RUN — no data will be written")
    print(f"{'='*60}")

    src_engine = _make_engine(src_url)
    dst_engine = _make_engine(dst_url)

    # Reflect source schema
    src_meta = MetaData()
    src_meta.reflect(bind=src_engine)

    if not src_meta.tables:
        print(f"  No tables found in source database — skipping")
        return

    tables = _tables_in_dependency_order(src_meta)
    print(f"  Tables to migrate: {[t.name for t in tables]}")
    print()

    total_rows = 0
    for table in tables:
        try:
            total_rows += _migrate_table(
                src_engine, dst_engine, table, dry_run, truncate, verbose
            )
        except Exception as exc:
            print(f"  ERROR migrating {table.name}: {exc}")
            if not dry_run:
                raise

    print(f"\n  Total rows {'that would be ' if dry_run else ''}copied: {total_rows}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Migrate OpenAlgo databases from SQLite to PostgreSQL (or any SQLAlchemy URL).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )

    # Main database
    p.add_argument("--source", required=True, metavar="URL",
                   help="Source DATABASE_URL (e.g. sqlite:///db/openalgo.db)")
    p.add_argument("--target", required=True, metavar="URL",
                   help="Target DATABASE_URL (e.g. postgresql+pg8000://user:pass@host/db)")

    # Ancillary databases (optional — omit to skip)
    for name in ("logs", "latency", "health", "sandbox"):
        p.add_argument(f"--{name}-source", metavar="URL",
                       help=f"Source URL for the {name} database (optional)")
        p.add_argument(f"--{name}-target", metavar="URL",
                       help=f"Target URL for the {name} database (optional)")

    p.add_argument("--dry-run", action="store_true",
                   help="Show what would be migrated without writing anything")
    p.add_argument("--truncate", action="store_true",
                   help="Truncate destination tables before inserting (allows re-run)")
    p.add_argument("--verbose", "-v", action="store_true",
                   help="Show row-level progress")

    return p


def main():
    parser = _build_parser()
    args = parser.parse_args()

    databases = [("main", args.source, args.target)]
    for name in ("logs", "latency", "health", "sandbox"):
        src = getattr(args, f"{name}_source", None)
        dst = getattr(args, f"{name}_target", None)
        if src and dst:
            databases.append((name, src, dst))
        elif src or dst:
            parser.error(
                f"--{name}-source and --{name}-target must both be provided, or both omitted."
            )

    for label, src_url, dst_url in databases:
        _migrate_database(
            src_url=src_url,
            dst_url=dst_url,
            label=label,
            dry_run=args.dry_run,
            truncate=args.truncate,
            verbose=args.verbose,
        )

    print("\nMigration complete.")


if __name__ == "__main__":
    main()
