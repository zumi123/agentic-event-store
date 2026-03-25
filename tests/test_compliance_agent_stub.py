from __future__ import annotations

from datetime import datetime, timezone

import psycopg
import pytest

from src.agents.compliance_agent import ComplianceAgent
from src.event_store import EventStore
from src.models.events import (
    ApplicationSubmitted,
    ComplianceCheckRequested,
    CreditAnalysisCompleted,
    CreditAnalysisRequested,
    FraudScreeningCompleted,
)
from src.projections.application_summary import ApplicationSummaryProjection
from src.projections.agent_performance import AgentPerformanceLedgerProjection
from src.projections.compliance_audit import ComplianceAuditViewProjection
from src.projections.daemon import ProjectionDaemon


@pytest.mark.asyncio
async def test_compliance_agent_writes_rules_and_sets_summary_status(dsn: str) -> None:
    store = EventStore(dsn=dsn)

    # Seed registry
    with psycopg.connect(dsn) as conn:
        conn.execute(
            "INSERT INTO applicant_registry.companies (company_id, company_name, jurisdiction, legal_type, founded_year) "
            "VALUES ('co-1','Acme','CA','Corporation',2010) ON CONFLICT DO NOTHING"
        )
        conn.commit()

    app_id = "app-comp-1"

    # Seed loan stream through ComplianceReview
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
            FraudScreeningCompleted(
                application_id=app_id,
                agent_id="fraud-detection",
                fraud_score=0.1,
                anomaly_flags=[],
                screening_model_version="stub",
                input_data_hash="h",
            ),
            ComplianceCheckRequested(
                application_id=app_id,
                regulation_set_version="2026-Q1",
                checks_required=["REG-001", "REG-002", "REG-003", "REG-004", "REG-005", "REG-006"],
            ),
        ],
        expected_version=-1,
        aggregate_type="LoanApplication",
    )

    # Mirror ComplianceCheckRequested into compliance stream (like FraudDetectionAgent does)
    await store.append(
        stream_id=f"compliance-{app_id}",
        events=[
            ComplianceCheckRequested(
                application_id=app_id,
                regulation_set_version="2026-Q1",
                checks_required=["REG-001", "REG-002", "REG-003", "REG-004", "REG-005", "REG-006"],
            )
        ],
        expected_version=-1,
        aggregate_type="ComplianceRecord",
    )

    agent = ComplianceAgent(store=store, agent_type="compliance", session_id="s1", model_version="stub")
    await agent.process_application(application_id=app_id, company_id="co-1")

    comp = await store.load_stream(f"compliance-{app_id}")
    assert any(e.event_type == "ComplianceCheckCompleted" for e in comp)

    daemon = ProjectionDaemon(
        store,
        projections=[ApplicationSummaryProjection(), AgentPerformanceLedgerProjection(), ComplianceAuditViewProjection()],
    )
    await daemon.run_once(batch_size=2000)

    import psycopg as _psycopg
    from psycopg.rows import dict_row as _dict_row

    async with await _psycopg.AsyncConnection.connect(dsn, row_factory=_dict_row) as conn:
        cur = await conn.execute("SELECT compliance_status FROM application_summary WHERE application_id=%s", (app_id,))
        row = await cur.fetchone()
        assert row is not None
        assert row["compliance_status"] in ("PASSED", "FAILED", "CONDITIONAL")

