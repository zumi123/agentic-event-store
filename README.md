# The Ledger (TRP1 Week 5)

PostgreSQL-backed, append-only event store with optimistic concurrency, projections (CQRS), upcasting, and audit integrity primitives.

## Prereqs

- Python 3.13+
- [`uv`](https://github.com/astral-sh/uv)
- A PostgreSQL 16+ instance (local install, or Docker)

## Quickstart

Install deps:

```bash
uv sync
```

Start Postgres (Docker, no compose):

```bash
docker run --rm --name ledger-postgres \
  -e POSTGRES_PASSWORD=postgres \
  -e POSTGRES_USER=postgres \
  -e POSTGRES_DB=ledger \
  -p 5432:5432 \
  postgres:16
```

In another terminal, apply schema:

```bash
export DATABASE_URL="postgresql://postgres:postgres@localhost:5432/ledger"
psql "$DATABASE_URL" -f src/schema.sql
```

Run tests:

```bash
uv run pytest -q
```

## Configuration

Tests and runtime use `DATABASE_URL` (e.g. `postgresql://user:pass@host:5432/dbname`).

