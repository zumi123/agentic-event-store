from __future__ import annotations

from datetime import datetime, timezone

import pytest

from datagen.minimal_generate import apply_schema, ensure_dummy_documents, seed_registry
from scripts.export_audit_report import export_audit_report
from scripts.run_pipeline import run_pipeline
from src.event_store import EventStore
from src.models.events import ApplicationSubmitted


@pytest.mark.asyncio
async def test_audit_report_export_contains_final_state_and_integrity(dsn: str, tmp_path) -> None:
    apply_schema(dsn)
    seed_registry(dsn, company_id="co-1", company_name="Company 1")
    ensure_dummy_documents((__import__("pathlib").Path(__file__).resolve().parents[1] / "documents"), "co-1")

    store = EventStore(dsn=dsn)
    app_id = "app-audit-1"

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
            )
        ],
        expected_version=-1,
        aggregate_type="LoanApplication",
    )

    await run_pipeline(
        dsn=dsn,
        application_id=app_id,
        company_id="co-1",
        reviewer_id="loan-officer-1",
        final="APPROVE",
    )

    out = tmp_path / "audit.json"
    report = await export_audit_report(dsn=dsn, application_id=app_id, out_path=out)

    assert out.exists()
    assert report["application_summary"]["state"] == "FinalApproved"
    assert report["application_summary"]["human_reviewer_id"] == "loan-officer-1"
    assert report["integrity_check"]["chain_valid"] is True
    assert report["integrity_check"]["tamper_detected"] is False
    assert isinstance(report["integrity_check"]["new_hash"], str)

