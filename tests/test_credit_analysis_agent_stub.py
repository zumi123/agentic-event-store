from __future__ import annotations

from datetime import datetime, timezone

import psycopg
import pytest

from src.agents.credit_analysis_agent import CreditAnalysisAgent
from src.event_store import EventStore
from src.models.events import ApplicationSubmitted, CreditAnalysisRequested, ExtractionCompleted, PackageCreated


@pytest.mark.asyncio
async def test_credit_analysis_agent_appends_credit_analysis_completed(dsn: str) -> None:
    store = EventStore(dsn=dsn)

    # Seed registry
    with psycopg.connect(dsn) as conn:
        conn.execute(
            "INSERT INTO applicant_registry.companies (company_id, company_name, jurisdiction, legal_type, founded_year) "
            "VALUES ('co-1','Acme','CA','Corporation',2010) ON CONFLICT DO NOTHING"
        )
        conn.commit()

    app_id = "app-cred-1"

    # Seed loan stream into AwaitingAnalysis
    await store.append(
        stream_id=f"loan-{app_id}",
        events=[
            ApplicationSubmitted(
                application_id=app_id,
                applicant_id="co-1",
                requested_amount_usd=10_000.0,
                loan_purpose="capex",
                submission_channel="test",
                submitted_at=datetime.now(tz=timezone.utc),
            ),
            CreditAnalysisRequested(
                application_id=app_id,
                assigned_agent_id="credit-analysis",
                requested_at=datetime.now(tz=timezone.utc),
                priority=0,
            ),
        ],
        expected_version=-1,
        aggregate_type="LoanApplication",
    )

    # Seed docpkg stream (minimal)
    await store.append(
        stream_id=f"docpkg-{app_id}",
        events=[
            PackageCreated(package_id=f"docpkg-{app_id}", application_id=app_id, created_at=datetime.now(tz=timezone.utc)),
            ExtractionCompleted(
                package_id=f"docpkg-{app_id}",
                document_type="income_statement",
                completed_at=datetime.now(tz=timezone.utc),
                extracted_facts={},
            ),
        ],
        expected_version=-1,
        aggregate_type="DocumentPackage",
    )

    agent = CreditAnalysisAgent(store=store, agent_type="credit-analysis", session_id="s1", model_version="stub")
    await agent.process_application(application_id=app_id, company_id="co-1")

    loan = await store.load_stream(f"loan-{app_id}")
    assert any(e.event_type == "CreditAnalysisCompleted" for e in loan)

