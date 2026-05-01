# Database Backends

OpenAlgo stores its data in five separate SQLAlchemy databases (plus one DuckDB database for historical market data). By default every database is SQLite — a great choice for a single-server deployment. For higher availability, cloud hosting, or managed database services you can point any of them at PostgreSQL.

> **DuckDB (Historify)** is not covered here. It uses a separate engine and cannot be migrated with this tooling.

---

## Quick Reference

| Variable | Default | Purpose |
|---|---|---|
| `DATABASE_URL` | `sqlite:///db/openalgo.db` | Main app data (users, orders, settings) |
| `LOGS_DATABASE_URL` | `sqlite:///db/logs.db` | Traffic & API logs |
| `LATENCY_DATABASE_URL` | `sqlite:///db/latency.db` | Latency monitoring |
| `HEALTH_DATABASE_URL` | `sqlite:///db/health.db` | Health monitoring |
| `SANDBOX_DATABASE_URL` | `sqlite:///db/sandbox.db` | Paper trading (analyzer mode) |

All five accept the same URL formats.

---

## Supported URL Formats

```bash
# SQLite (default)
DATABASE_URL = 'sqlite:///db/openalgo.db'

# PostgreSQL — pg8000 driver (pure-Python, required under Gunicorn+eventlet)
DATABASE_URL = 'postgresql+pg8000://user:password@localhost:5432/openalgo'

# PostgreSQL — psycopg2 driver (only for non-eventlet deployments)
DATABASE_URL = 'postgresql+psycopg2://user:password@localhost:5432/openalgo'

# Neon — serverless PostgreSQL
DATABASE_URL = 'postgresql+pg8000://user:password@ep-xyz.neon.tech/openalgo?sslmode=require'

# Supabase — direct connection
DATABASE_URL = 'postgresql+pg8000://postgres:password@db.xxx.supabase.co:5432/postgres'
```

> **Important**: OpenAlgo runs under Gunicorn + eventlet in production.  
> eventlet monkey-patches the stdlib but **not** the C-extension psycopg2 driver.  
> The engine factory automatically rewrites bare `postgresql://` and `postgresql+psycopg2://` URLs to `postgresql+pg8000://` when eventlet is detected.  
> You can also set the pg8000 driver explicitly in your URL to be safe.

---

## Provider Setup Guides

### Local PostgreSQL (Docker Compose)

Add this service to your `docker-compose.yml`:

```yaml
services:
  postgres:
    image: postgres:16-alpine
    restart: unless-stopped
    environment:
      POSTGRES_USER: openalgo
      POSTGRES_PASSWORD: changeme
      POSTGRES_DB: openalgo
    ports:
      - "5432:5432"
    volumes:
      - pgdata:/var/lib/postgresql/data

volumes:
  pgdata:
```

Then in your `.env`:

```bash
DATABASE_URL = 'postgresql+pg8000://openalgo:changeme@localhost:5432/openalgo'
```

### Neon (Serverless PostgreSQL)

Neon provides serverless PostgreSQL with a generous free tier.

1. Create a project at [neon.tech](https://neon.tech).
2. In the Neon dashboard → **Connection Details**, select **Pooled connection** (important — use the pooled string for the app).
3. Copy the connection string and add the `+pg8000` driver:

```bash
# Neon pooled connection string (recommended for the app)
DATABASE_URL = 'postgresql+pg8000://user:password@ep-xyz-pooler.neon.tech/openalgo?sslmode=require'
```

**Important notes for Neon:**

- Use the **pooled** endpoint (`-pooler.` in the hostname) for the running app.
- Use the **direct** (non-pooled) endpoint when running `alembic upgrade head` for schema migrations, because Neon's pooler uses PgBouncer in transaction mode which doesn't support DDL reliably.
- Neon free tier has a limit of ~100 simultaneous connections. The engine factory automatically applies a smaller pool (`pool_size=5, max_overflow=10`) when it detects a `*.neon.tech` hostname.
- `sslmode=require` is applied automatically for Neon even if you don't include it in the URL.

```bash
# For running Alembic migrations on Neon, use the *direct* (non-pooler) URL:
uv run alembic upgrade head
# (set DATABASE_URL to the direct URL temporarily, or pass --url on the CLI)
```

### Supabase

Supabase offers managed PostgreSQL with a REST API layer.

1. Create a project at [supabase.com](https://supabase.com).
2. Go to **Settings → Database → Connection string**.
3. Choose **Direct connection** (not the pooler) for migrations:

```bash
# Supabase direct connection (for migrations AND the app if you have few connections)
DATABASE_URL = 'postgresql+pg8000://postgres:password@db.xxx.supabase.co:5432/postgres'
```

**Important notes for Supabase:**

- The Supabase **connection pooler** (port 6543) uses PgBouncer in transaction mode. `CREATE TABLE` and DDL statements do not work through the pooler — always use the direct connection (port 5432) for `alembic upgrade head`.
- For the running app you can use either, but port 5432 direct is simpler and more reliable.
- SSL is required; use `DB_SSL_MODE=require` in your `.env` or include `?sslmode=require` in the URL.
- Supabase free tier limits connections to ~60. Reduce pool size: `DB_POOL_SIZE=3 DB_MAX_OVERFLOW=7`.

---

## Pool Tuning

The following environment variables tune the PostgreSQL connection pool. They have no effect on SQLite.

| Variable | Default | Description |
|---|---|---|
| `DB_POOL_SIZE` | `50` | Persistent connections per worker |
| `DB_MAX_OVERFLOW` | `100` | Burst connections above `pool_size` |
| `DB_POOL_TIMEOUT` | `10` | Seconds to wait for a connection |
| `DB_SSL_MODE` | _(auto)_ | `require`, `verify-full`, or `disable` |

For **Neon free tier**:
```bash
DB_POOL_SIZE=5
DB_MAX_OVERFLOW=5
DB_POOL_TIMEOUT=30
DB_SSL_MODE=require
```

For **Supabase free tier**:
```bash
DB_POOL_SIZE=3
DB_MAX_OVERFLOW=7
DB_POOL_TIMEOUT=30
DB_SSL_MODE=require
```

---

## Schema Migrations with Alembic

OpenAlgo uses [Alembic](https://alembic.sqlalchemy.org/) for schema migrations. The initial migration captures the complete current schema.

### Apply migrations (any database)

```bash
# Apply all pending migrations to the database pointed to by DATABASE_URL
uv run alembic upgrade head
```

This command is run automatically by `start.sh` on every deploy, so schema changes are applied without manual intervention.

### Create a new migration after adding/changing a model

```bash
uv run alembic revision --autogenerate -m "describe your change"
uv run alembic upgrade head
```

### Roll back the last migration

```bash
uv run alembic downgrade -1
```

---

## Migrating from SQLite to PostgreSQL

If you already have data in SQLite and want to move to PostgreSQL, use the built-in migration script:

### 1. Prepare the target database

Create an empty PostgreSQL database and apply the schema:

```bash
# Point DATABASE_URL at your new PostgreSQL instance
export DATABASE_URL="postgresql+pg8000://user:pass@host/openalgo"
uv run alembic upgrade head
```

### 2. Run the data migration script

```bash
# Dry run first — verify what will be migrated
uv run python utils/db_migrate.py \
    --source sqlite:///db/openalgo.db \
    --target postgresql+pg8000://user:pass@host/openalgo \
    --dry-run

# Live migration
uv run python utils/db_migrate.py \
    --source sqlite:///db/openalgo.db \
    --target postgresql+pg8000://user:pass@host/openalgo
```

### 3. Migrate ancillary databases (optional)

```bash
uv run python utils/db_migrate.py \
    --source         sqlite:///db/openalgo.db \
    --target         postgresql+pg8000://user:pass@host/openalgo \
    --logs-source    sqlite:///db/logs.db \
    --logs-target    postgresql+pg8000://user:pass@host/openalgo_logs \
    --latency-source sqlite:///db/latency.db \
    --latency-target postgresql+pg8000://user:pass@host/openalgo_latency \
    --health-source  sqlite:///db/health.db \
    --health-target  postgresql+pg8000://user:pass@host/openalgo_health \
    --sandbox-source sqlite:///db/sandbox.db \
    --sandbox-target postgresql+pg8000://user:pass@host/openalgo_sandbox
```

### 4. Update your `.env`

```bash
DATABASE_URL = 'postgresql+pg8000://user:pass@host/openalgo'
LOGS_DATABASE_URL    = 'postgresql+pg8000://user:pass@host/openalgo_logs'
LATENCY_DATABASE_URL = 'postgresql+pg8000://user:pass@host/openalgo_latency'
HEALTH_DATABASE_URL  = 'postgresql+pg8000://user:pass@host/openalgo_health'
SANDBOX_DATABASE_URL = 'postgresql+pg8000://user:pass@host/openalgo_sandbox'
```

### 5. Restart the application

```bash
# Development
uv run app.py

# Production
uv run gunicorn --worker-class eventlet -w 1 app:app
```

---

## Manual Verification Checklist

After switching to PostgreSQL, verify everything works:

- [ ] `uv run alembic upgrade head` completes with no errors
- [ ] Application starts, login page loads
- [ ] Log in and check the dashboard — positions and orders load
- [ ] Place a paper trade in Analyzer mode — the trade appears in the sandbox
- [ ] Check logs at `/log` — traffic logs are being written to PostgreSQL
- [ ] Run `uv run python utils/db_migrate.py --source ... --target ... --dry-run` and verify row counts match

---

## Troubleshooting

### `SSL connection is required`
Add `?sslmode=require` to your URL or set `DB_SSL_MODE=require`.

### `could not connect to server: Connection refused`
- Verify the hostname, port, and firewall rules.
- For Neon: make sure you're using the pooled endpoint for the app.
- For Supabase: make sure you're using port 5432 (direct), not 6543 (pooler).

### `too many connections`
Reduce `DB_POOL_SIZE` and `DB_MAX_OVERFLOW`. For Neon free tier use `DB_POOL_SIZE=5`.

### `alembic upgrade head` fails on Neon/Supabase pooler
Use the **direct** (non-pooled) connection string for Alembic only. DDL statements are not supported through PgBouncer in transaction mode.

### `psycopg2` import errors under eventlet
The engine factory automatically switches to the pure-Python `pg8000` driver when running under eventlet. If you see psycopg2 errors, explicitly use `postgresql+pg8000://` in your URL.
