from __future__ import annotations

import argparse
import os
import random
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import psycopg


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.event_store import EventStore  # noqa: E402
from src.models.events import ApplicationSubmitted  # noqa: E402


def _read_sql(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def apply_schema(dsn: str) -> None:
    with psycopg.connect(dsn) as conn:
        conn.execute(_read_sql(ROOT / "src" / "schema.sql"))
        conn.execute(_read_sql(ROOT / "src" / "registry" / "schema.sql"))
        conn.commit()


def ensure_dummy_documents(docs_dir: Path, company_id: str) -> tuple[str, str]:
    company_dir = docs_dir / company_id
    company_dir.mkdir(parents=True, exist_ok=True)

    income = company_dir / "income_statement_2024.pdf"
    balance = company_dir / "balance_sheet_2024.pdf"

    income.touch(exist_ok=True)
    balance.touch(exist_ok=True)
    return str(income), str(balance)


def seed_registry(dsn: str, *, company_id: str, company_name: str) -> None:
    now = datetime.now(tz=timezone.utc)
    with psycopg.connect(dsn) as conn:
        with conn.transaction():
            conn.execute(
                "INSERT INTO applicant_registry.companies "
                "(company_id, company_name, jurisdiction, legal_type, founded_year, created_at) "
                "VALUES (%s,%s,%s,%s,%s,%s) "
                "ON CONFLICT (company_id) DO NOTHING",
                (company_id, company_name, "CA", "Corporation", 2010, now),
            )
            conn.execute(
                "INSERT INTO applicant_registry.financial_history "
                "(company_id, fiscal_year, revenue_usd, net_income_usd, total_assets_usd, total_liabilities_usd) "
                "VALUES (%s,%s,%s,%s,%s,%s) "
                "ON CONFLICT (company_id, fiscal_year) DO NOTHING",
                (company_id, 2024, 1_000_000.0, 120_000.0, 2_000_000.0, 800_000.0),
            )
        conn.commit()


async def seed_application_events(store: EventStore, *, application_id: str, applicant_id: str) -> None:
    stream_id = f"loan-{application_id}"
    current_version = await store.stream_version(stream_id)
    if current_version != 0:
        # Idempotent behavior: if already seeded, don't try to recreate as new.
        return

    await store.append(
        stream_id=stream_id,
        events=[
            ApplicationSubmitted(
                application_id=application_id,
                applicant_id=applicant_id,
                requested_amount_usd=10_000.0,
                loan_purpose="capex",
                submission_channel="datagen",
                submitted_at=datetime.now(tz=timezone.utc),
            )
        ],
        expected_version=-1,
        aggregate_type="LoanApplication",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Minimal generator for Support-Doc overlay demo data.")
    parser.add_argument("--dsn", default=os.environ.get("DATABASE_URL"), help="Postgres DSN (or set DATABASE_URL).")
    parser.add_argument("--companies", type=int, default=1)
    parser.add_argument("--applications", type=int, default=1)
    parser.add_argument("--docs-dir", default=str(ROOT / "documents"))
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    if not args.dsn:
        raise SystemExit("DATABASE_URL (or --dsn) is required")

    random.seed(args.seed)
    docs_dir = Path(args.docs_dir)
    docs_dir.mkdir(parents=True, exist_ok=True)

    apply_schema(args.dsn)

    # Seed companies + documents
    companies: list[str] = []
    for i in range(1, args.companies + 1):
        cid = f"co-{i}"
        seed_registry(args.dsn, company_id=cid, company_name=f"Company {i}")
        ensure_dummy_documents(docs_dir, cid)
        companies.append(cid)

    # Seed applications (each references a company_id as applicant_id in this minimal demo)
    import asyncio

    store = EventStore(dsn=args.dsn)
    for j in range(1, args.applications + 1):
        app_id = f"app-{j}"
        company_id = companies[(j - 1) % len(companies)]
        asyncio.run(seed_application_events(store, application_id=app_id, applicant_id=company_id))

    print(f"Seeded {len(companies)} companies into applicant_registry, {args.applications} loan applications into ledger.")
    print(f"Dummy documents created under: {docs_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

