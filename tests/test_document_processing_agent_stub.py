from __future__ import annotations

from datetime import datetime, timezone

import pytest

from src.agents.document_processing_agent import DocumentProcessingAgent
from src.event_store import EventStore
from src.models.events import ApplicationSubmitted


@pytest.mark.asyncio
async def test_document_processing_agent_writes_session_and_docpkg_events(dsn: str) -> None:
    store = EventStore(dsn=dsn)

    application_id = "app-doc-1"
    # Seed loan stream with submit
    await store.append(
        stream_id=f"loan-{application_id}",
        events=[
            ApplicationSubmitted(
                application_id=application_id,
                applicant_id="co-1",
                requested_amount_usd=10_000.0,
                loan_purpose="capex",
                submission_channel="test",
                submitted_at=datetime.now(tz=timezone.utc),
            )
        ],
        expected_version=-1,
        aggregate_type="LoanApplication",
    )

    agent = DocumentProcessingAgent(
        store=store,
        agent_type="document-processing",
        session_id="s1",
        model_version="stub",
    )

    await agent.process_application(
        application_id=application_id,
        assigned_agent_id="credit-agent-1",
        company_id="co-1",
        income_statement_path="documents/co-1/income_statement_2024.pdf",
        balance_sheet_path="documents/co-1/balance_sheet_2024.pdf",
    )

    # Agent session stream: must start with AgentSessionStarted and have node events.
    sess = await store.load_stream(agent.stream_id)
    assert sess[0].event_type == "AgentSessionStarted"
    assert any(e.event_type == "AgentNodeExecuted" for e in sess)
    assert sess[-1].event_type == "AgentSessionCompleted"

    # Document package stream exists and starts with PackageCreated.
    pkg = await store.load_stream(f"docpkg-{application_id}")
    assert pkg[0].event_type == "PackageCreated"
    assert any(e.event_type == "ExtractionCompleted" for e in pkg)
    assert any(e.event_type == "PackageReadyForAnalysis" for e in pkg)

    # Loan stream should now include DocumentUploaded and CreditAnalysisRequested.
    loan = await store.load_stream(f"loan-{application_id}")
    assert any(e.event_type == "DocumentUploaded" for e in loan)
    assert any(e.event_type == "CreditAnalysisRequested" for e in loan)

