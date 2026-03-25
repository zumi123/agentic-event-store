"""Post-hoc payload mutation must flip tamper_detected when event count is unchanged."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
import psycopg
from psycopg.rows import dict_row

from src.event_store import EventStore
from src.integrity.audit_chain import run_integrity_check
from src.models.events import ApplicationSubmitted


@pytest.mark.asyncio
async def test_integrity_detects_same_count_payload_tamper(dsn: str) -> None:
    store = EventStore(dsn=dsn)
    app_id = "app-tamper-1"

    await store.append(
        stream_id=f"loan-{app_id}",
        events=[
            ApplicationSubmitted(
                application_id=app_id,
                applicant_id="u1",
                requested_amount_usd=10_000.0,
                loan_purpose="test",
                submission_channel="test",
                submitted_at=datetime.now(tz=timezone.utc),
            )
        ],
        expected_version=-1,
        aggregate_type="LoanApplication",
    )

    first = await run_integrity_check(store, "loan", app_id, skip_rate_limit=True)
    assert first.tamper_detected is False
    assert first.chain_valid is True

    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE events SET payload = payload || %s::jsonb "
                "WHERE stream_id = %s AND stream_position = 1",
                ('{"_tamper_probe": true}', f"loan-{app_id}"),
            )
        conn.commit()

    second = await run_integrity_check(store, "loan", app_id, skip_rate_limit=True)
    assert second.tamper_detected is True
    assert second.chain_valid is False
