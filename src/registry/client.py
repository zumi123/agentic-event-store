from __future__ import annotations

from collections.abc import Sequence

import psycopg
from psycopg.rows import dict_row

from src.registry.models import Company, ComplianceFlag, FinancialHistoryRow, LoanRelationship


class ApplicantRegistryClient:
    """
    Read-only client for applicant_registry.* schema.

    Support Document boundary rule: agents may query this registry, but MUST NOT write to it.
    """

    def __init__(self, *, dsn: str) -> None:
        self._dsn = dsn

    async def get_company(self, company_id: str) -> Company | None:
        async with await psycopg.AsyncConnection.connect(self._dsn, row_factory=dict_row) as conn:
            cur = await conn.execute(
                "SELECT company_id, company_name, jurisdiction, legal_type, founded_year, created_at "
                "FROM applicant_registry.companies WHERE company_id = %s",
                (company_id,),
            )
            row = await cur.fetchone()
            return Company(**row) if row else None

    async def get_financial_history(self, company_id: str, *, limit_years: int = 3) -> list[FinancialHistoryRow]:
        async with await psycopg.AsyncConnection.connect(self._dsn, row_factory=dict_row) as conn:
            cur = await conn.execute(
                "SELECT company_id, fiscal_year, revenue_usd, ebitda_usd, net_income_usd, total_assets_usd, total_liabilities_usd "
                "FROM applicant_registry.financial_history "
                "WHERE company_id = %s "
                "ORDER BY fiscal_year DESC "
                "LIMIT %s",
                (company_id, limit_years),
            )
            return [FinancialHistoryRow(**r) async for r in cur]

    async def get_active_compliance_flags(self, company_id: str) -> list[ComplianceFlag]:
        async with await psycopg.AsyncConnection.connect(self._dsn, row_factory=dict_row) as conn:
            cur = await conn.execute(
                "SELECT flag_id::text AS flag_id, company_id, flag_type, is_active, flag_reason, created_at "
                "FROM applicant_registry.compliance_flags "
                "WHERE company_id = %s AND is_active = TRUE "
                "ORDER BY created_at DESC",
                (company_id,),
            )
            return [ComplianceFlag(**r) async for r in cur]

    async def get_loan_relationships(self, company_id: str) -> list[LoanRelationship]:
        async with await psycopg.AsyncConnection.connect(self._dsn, row_factory=dict_row) as conn:
            cur = await conn.execute(
                "SELECT relationship_id::text AS relationship_id, company_id, counterparty, default_occurred, opened_at, closed_at "
                "FROM applicant_registry.loan_relationships "
                "WHERE company_id = %s "
                "ORDER BY opened_at DESC",
                (company_id,),
            )
            return [LoanRelationship(**r) async for r in cur]

