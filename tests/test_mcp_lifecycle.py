from __future__ import annotations

from datetime import datetime, timezone

import pytest

from src.event_store import EventStore
from src.mcp.server import LedgerMCP


@pytest.mark.asyncio
async def test_mcp_basic_lifecycle(dsn: str) -> None:
    store = EventStore(dsn=dsn)
    mcp = LedgerMCP(store)

    app_id = "app-mcp-1"

    await mcp.call_tool(
        "submit_application",
        {
            "application_id": app_id,
            "applicant_id": "user-1",
            "requested_amount_usd": 100_000.0,
            "loan_purpose": "capex",
            "submission_channel": "mcp",
            "submitted_at": datetime.now(tz=timezone.utc),
        },
    )

    await mcp.call_tool(
        "request_credit_analysis",
        {"application_id": app_id, "assigned_agent_id": "agent-1"},
    )

    await mcp.call_tool(
        "start_agent_session",
        {
            "agent_id": "agent-1",
            "session_id": "s1",
            "context_source": "replay",
            "event_replay_from_position": 0,
            "context_token_count": 123,
            "model_version": "v2.3",
        },
    )

    await mcp.call_tool(
        "record_credit_analysis",
        {
            "application_id": app_id,
            "agent_id": "agent-1",
            "session_id": "s1",
            "model_version": "v2.3",
            "confidence_score": 0.9,
            "risk_tier": "MEDIUM",
            "recommended_limit_usd": 80_000.0,
            "duration_ms": 10,
            "input_data_hash": "h",
        },
    )

    # Process projections and query via resources.
    await mcp.daemon.run_once()

    summary = await mcp.read_resource(f"ledger://applications/{app_id}")
    assert summary is not None
    assert summary["application_id"] == app_id
    assert summary["state"] in ("Submitted", "AwaitingAnalysis", "AnalysisComplete")


@pytest.mark.asyncio
async def test_mcp_tools_only_full_lifecycle_to_approval(dsn: str) -> None:
    """End-to-end using only LedgerMCP tools: submit → credit → fraud → compliance → decision → human → integrity + reads."""
    store = EventStore(dsn=dsn)
    mcp = LedgerMCP(store)

    app_id = "app-mcp-full-1"
    reg = "2026-Q1"
    credit_agent = "credit-agent"
    credit_sess = "sess-credit"
    fraud_agent = "fraud-agent"
    fraud_sess = "sess-fraud"
    stream_credit = f"agent-{credit_agent}-{credit_sess}"
    stream_fraud = f"agent-{fraud_agent}-{fraud_sess}"

    await mcp.call_tool(
        "submit_application",
        {
            "application_id": app_id,
            "applicant_id": "user-1",
            "requested_amount_usd": 50_000.0,
            "loan_purpose": "capex",
            "submission_channel": "mcp",
            "submitted_at": datetime.now(tz=timezone.utc),
        },
    )

    await mcp.call_tool("request_credit_analysis", {"application_id": app_id, "assigned_agent_id": credit_agent})

    await mcp.call_tool(
        "start_agent_session",
        {
            "agent_id": credit_agent,
            "session_id": credit_sess,
            "context_source": "replay",
            "event_replay_from_position": 0,
            "context_token_count": 50,
            "model_version": "v2.3",
        },
    )

    await mcp.call_tool(
        "record_credit_analysis",
        {
            "application_id": app_id,
            "agent_id": credit_agent,
            "session_id": credit_sess,
            "model_version": "v2.3",
            "confidence_score": 0.85,
            "risk_tier": "LOW",
            "recommended_limit_usd": 45_000.0,
            "duration_ms": 12,
            "input_data_hash": "hash-credit",
        },
    )

    await mcp.call_tool(
        "start_agent_session",
        {
            "agent_id": fraud_agent,
            "session_id": fraud_sess,
            "context_source": "replay",
            "event_replay_from_position": 0,
            "context_token_count": 20,
            "model_version": "fraud-v1",
        },
    )

    await mcp.call_tool(
        "record_fraud_screening",
        {
            "application_id": app_id,
            "agent_id": fraud_agent,
            "session_id": fraud_sess,
            "model_version": "fraud-v1",
            "fraud_score": 0.12,
            "anomaly_flags": [],
            "screening_model_version": "screen-v1",
            "input_data_hash": "hash-fraud",
        },
    )

    for rid in ("REG-001", "REG-002", "REG-003", "REG-004", "REG-005", "REG-006"):
        await mcp.call_tool(
            "record_compliance_check",
            {
                "application_id": app_id,
                "outcome": "passed",
                "rule_id": rid,
                "rule_version": reg,
                "regulation_set_version": reg,
            },
        )

    await mcp.call_tool(
        "generate_decision",
        {
            "application_id": app_id,
            "orchestrator_agent_id": "orchestrator-1",
            "session_id": "orch-s1",
            "recommendation": "APPROVE",
            "confidence_score": 0.82,
            "contributing_agent_sessions": [stream_credit, stream_fraud],
            "decision_basis_summary": "MCP integration test",
            "model_versions": {"credit": "v2.3", "fraud": "fraud-v1"},
        },
    )

    await mcp.call_tool(
        "record_human_review",
        {
            "application_id": app_id,
            "reviewer_id": "officer-1",
            "override": False,
            "final_decision": "APPROVE",
        },
    )

    integ = await mcp.call_tool("run_integrity_check", {"entity_type": "loan", "entity_id": app_id})
    assert integ["tamper_detected"] is False
    assert integ["chain_valid"] is True
    assert integ["events_verified"] >= 1

    await mcp.daemon.run_once()

    summary = await mcp.read_resource(f"ledger://applications/{app_id}")
    assert summary["application_id"] == app_id
    assert summary["state"] == "FinalApproved"

    comp = await mcp.read_resource(f"ledger://applications/{app_id}/compliance")
    assert comp is not None

    trail = await mcp.read_resource(f"ledger://applications/{app_id}/audit-trail")
    assert isinstance(trail, list)
    assert len(trail) >= 1

    sess = await mcp.read_resource(f"ledger://agents/{credit_agent}/sessions/{credit_sess}")
    assert sess is not None

    perf = await mcp.read_resource(f"ledger://agents/{credit_agent}/performance")
    assert perf is not None

    health = await mcp.read_resource("ledger://ledger/health")
    assert health is not None

