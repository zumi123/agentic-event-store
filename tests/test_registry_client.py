from __future__ import annotations

from datetime import datetime, timezone

import psycopg
from psycopg.rows import dict_row

from src.registry.client import ApplicantRegistryClient


async def test_registry_client_readonly_queries(dsn: str) -> None:
    # Ensure registry schema exists
    with psycopg.connect(dsn) as conn:
        conn.execute((__import__("pathlib").Path(__file__).resolve().parents[1] / "src/registry/schema.sql").read_text())
        conn.commit()

    company_id = "CO-1"
    now = datetime.now(tz=timezone.utc)

    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        with conn.transaction():
            conn.execute(
                "INSERT INTO applicant_registry.companies (company_id, company_name, jurisdiction, legal_type, founded_year, created_at) "
                "VALUES (%s,%s,%s,%s,%s,%s) "
                "ON CONFLICT (company_id) DO UPDATE SET "
                "company_name=EXCLUDED.company_name, jurisdiction=EXCLUDED.jurisdiction, legal_type=EXCLUDED.legal_type, founded_year=EXCLUDED.founded_year",
                (company_id, "Acme Inc", "CA", "Corporation", 2010, now),
            )
            conn.execute(
                "INSERT INTO applicant_registry.financial_history (company_id, fiscal_year, revenue_usd) "
                "VALUES (%s,%s,%s) "
                "ON CONFLICT (company_id, fiscal_year) DO UPDATE SET revenue_usd=EXCLUDED.revenue_usd",
                (company_id, 2024, 1_000_000.0),
            )
        conn.commit()

    client = ApplicantRegistryClient(dsn=dsn)
    company = await client.get_company(company_id)
    assert company is not None
    assert company.company_id == company_id

    hist = await client.get_financial_history(company_id)
    assert len(hist) == 1
    assert hist[0].fiscal_year == 2024

