from __future__ import annotations

from datetime import datetime, timezone

import psycopg
import pytest

from src.agents.fraud_detection_agent import FraudDetectionAgent
from src.event_store import EventStore
from src.models.events import (
    ApplicationSubmitted,
    CreditAnalysisCompleted,
    CreditAnalysisRequested,
    ExtractionCompleted,
    PackageCreated,
)
from src.projections.application_summary import ApplicationSummaryProjection
from src.projections.agent_performance import AgentPerformanceLedgerProjection
from src.projections.compliance_audit import ComplianceAuditViewProjection
from src.projections.daemon import ProjectionDaemon


@pytest.mark.asyncio
async def test_fraud_agent_appends_fraud_and_triggers_compliance(dsn: str) -> None:
    store = EventStore(dsn=dsn)

    # Seed registry
    with psycopg.connect(dsn) as conn:
        conn.execute(
            "INSERT INTO applicant_registry.companies (company_id, company_name, jurisdiction, legal_type, founded_year) "
            "VALUES ('co-1','Acme','CA','Corporation',2010) ON CONFLICT DO NOTHING"
        )
        conn.commit()

    app_id = "app-fraud-1"

    # Seed loan stream into AnalysisComplete
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
            CreditAnalysisCompleted(
                application_id=app_id,
                agent_id="credit-analysis",
                session_id="s1",
                model_version="stub",
                confidence_score=0.8,
                risk_tier="LOW",
                recommended_limit_usd=50_000.0,
                analysis_duration_ms=10,
                input_data_hash="h",
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

    agent = FraudDetectionAgent(store=store, agent_type="fraud-detection", session_id="s1", model_version="stub")
    await agent.process_application(application_id=app_id, company_id="co-1")

    fraud = await store.load_stream(f"fraud-{app_id}")
    assert any(e.event_type == "FraudScreeningCompleted" for e in fraud)

    loan = await store.load_stream(f"loan-{app_id}")
    assert any(e.event_type == "ComplianceCheckRequested" for e in loan)

    # Projections update: application_summary should include fraud_score and compliance_status.
    daemon = ProjectionDaemon(
        store,
        projections=[ApplicationSummaryProjection(), AgentPerformanceLedgerProjection(), ComplianceAuditViewProjection()],
    )
    await daemon.run_once(batch_size=1000)

    import psycopg as _psycopg
    from psycopg.rows import dict_row as _dict_row

    async with await _psycopg.AsyncConnection.connect(dsn, row_factory=_dict_row) as conn:
        cur = await conn.execute("SELECT fraud_score, compliance_status FROM application_summary WHERE application_id=%s", (app_id,))
        row = await cur.fetchone()
        assert row is not None
        assert row["fraud_score"] is not None
        assert row["compliance_status"] == "PENDING"

