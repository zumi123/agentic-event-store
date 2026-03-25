-- Support Document extension: Applicant Registry (read-only external boundary)
-- This schema is treated as an external CRM-like system: agents may query it but MUST NOT write to it.

BEGIN;

CREATE SCHEMA IF NOT EXISTS applicant_registry;

CREATE TABLE IF NOT EXISTS applicant_registry.companies (
  company_id        TEXT PRIMARY KEY,
  company_name      TEXT NOT NULL,
  jurisdiction      TEXT NOT NULL,
  legal_type        TEXT NOT NULL,
  founded_year      INT NOT NULL,
  created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS applicant_registry.financial_history (
  company_id        TEXT NOT NULL REFERENCES applicant_registry.companies(company_id),
  fiscal_year       INT NOT NULL,
  revenue_usd       DOUBLE PRECISION,
  ebitda_usd        DOUBLE PRECISION,
  net_income_usd    DOUBLE PRECISION,
  total_assets_usd  DOUBLE PRECISION,
  total_liabilities_usd DOUBLE PRECISION,
  PRIMARY KEY (company_id, fiscal_year)
);

CREATE TABLE IF NOT EXISTS applicant_registry.compliance_flags (
  flag_id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  company_id        TEXT NOT NULL REFERENCES applicant_registry.companies(company_id),
  flag_type         TEXT NOT NULL,
  is_active         BOOLEAN NOT NULL DEFAULT TRUE,
  flag_reason       TEXT,
  created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_registry_flags_company ON applicant_registry.compliance_flags(company_id, is_active);

CREATE TABLE IF NOT EXISTS applicant_registry.loan_relationships (
  relationship_id   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  company_id        TEXT NOT NULL REFERENCES applicant_registry.companies(company_id),
  counterparty      TEXT NOT NULL DEFAULT 'apex_bank',
  default_occurred  BOOLEAN NOT NULL DEFAULT FALSE,
  opened_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  closed_at         TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_registry_loans_company ON applicant_registry.loan_relationships(company_id, default_occurred);

COMMIT;

