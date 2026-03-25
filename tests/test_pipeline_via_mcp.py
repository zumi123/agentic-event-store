from __future__ import annotations

from datetime import datetime, timezone

import pytest

from datagen.minimal_generate import apply_schema, ensure_dummy_documents, seed_registry
from scripts.run_pipeline import run_pipeline
from src.event_store import EventStore
from src.mcp.server import LedgerMCP
from src.models.events import ApplicationSubmitted


@pytest.mark.asyncio
async def test_pipeline_reaches_final_state_and_mcp_reads_it(dsn: str) -> None:
    # Ensure registry is ready and has a company for doc agents.
    apply_schema(dsn)
    seed_registry(dsn, company_id="co-1", company_name="Company 1")
    ensure_dummy_documents((__import__("pathlib").Path(__file__).resolve().parents[1] / "documents"), "co-1")

    store = EventStore(dsn=dsn)
    app_id = "app-pipe-1"
    company_id = "co-1"

    # Seed the initial loan event so the pipeline can proceed.
    await store.append(
        stream_id=f"loan-{app_id}",
        events=[
            ApplicationSubmitted(
                application_id=app_id,
                applicant_id=company_id,
                requested_amount_usd=10_000.0,
                loan_purpose="capex",
                submission_channel="test",
                submitted_at=datetime.now(tz=timezone.utc),
            )
        ],
        expected_version=-1,
        aggregate_type="LoanApplication",
    )

    await run_pipeline(
        dsn=dsn,
        application_id=app_id,
        company_id=company_id,
        reviewer_id="loan-officer-1",
        final="APPROVE",
    )

    mcp = LedgerMCP(store)
    summary = await mcp.read_resource(f"ledger://applications/{app_id}")
    assert summary is not None
    assert summary["state"] == "FinalApproved"
    assert summary["human_reviewer_id"] == "loan-officer-1"

