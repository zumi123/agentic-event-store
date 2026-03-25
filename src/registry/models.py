from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class Company(BaseModel):
    model_config = ConfigDict(extra="forbid")

    company_id: str
    company_name: str
    jurisdiction: str
    legal_type: str
    founded_year: int
    created_at: datetime


class FinancialHistoryRow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    company_id: str
    fiscal_year: int
    revenue_usd: float | None = None
    ebitda_usd: float | None = None
    net_income_usd: float | None = None
    total_assets_usd: float | None = None
    total_liabilities_usd: float | None = None


class ComplianceFlag(BaseModel):
    model_config = ConfigDict(extra="forbid")

    flag_id: str
    company_id: str
    flag_type: str
    is_active: bool
    flag_reason: str | None = None
    created_at: datetime


class LoanRelationship(BaseModel):
    model_config = ConfigDict(extra="forbid")

    relationship_id: str
    company_id: str
    counterparty: str
    default_occurred: bool
    opened_at: datetime
    closed_at: datetime | None = None

