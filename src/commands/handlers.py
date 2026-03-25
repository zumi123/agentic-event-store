from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict, Field

from src.aggregates.agent_session import AgentSessionAggregate
from src.aggregates.loan_application import LoanApplicationAggregate
from src.event_store import EventStore
from src.models.events import (
    AgentContextLoaded,
    ApplicationApproved,
    ApplicationSubmitted,
    ApplicationDeclined,
    CreditAnalysisCompleted,
    CreditAnalysisRequested,
    HumanReviewCompleted,
)


class SubmitApplicationCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")

    application_id: str
    applicant_id: str
    requested_amount_usd: float
    loan_purpose: str
    submission_channel: str = "api"
    submitted_at: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))
    correlation_id: str | None = None
    causation_id: str | None = None


class StartAgentSessionCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent_id: str
    session_id: str
    context_source: str
    event_replay_from_position: int = 0
    context_token_count: int = 0
    model_version: str
    correlation_id: str | None = None
    causation_id: str | None = None


class CreditAnalysisCompletedCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")

    application_id: str
    agent_id: str
    session_id: str
    model_version: str
    confidence_score: float | None = None
    risk_tier: str
    recommended_limit_usd: float
    duration_ms: int
    input_data_hash: str
    correlation_id: str | None = None
    causation_id: str | None = None


class HumanReviewCompletedCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")

    application_id: str
    reviewer_id: str
    override: bool = False
    final_decision: str  # APPROVE | DECLINE | REFER
    override_reason: str | None = None
    correlation_id: str | None = None
    causation_id: str | None = None


async def handle_submit_application(cmd: SubmitApplicationCommand, store: EventStore) -> int:
    app = await LoanApplicationAggregate.load(store, cmd.application_id)
    app.assert_new()

    ev = ApplicationSubmitted(
        application_id=cmd.application_id,
        applicant_id=cmd.applicant_id,
        requested_amount_usd=cmd.requested_amount_usd,
        loan_purpose=cmd.loan_purpose,
        submission_channel=cmd.submission_channel,
        submitted_at=cmd.submitted_at,
    )
    stream_id = f"loan-{cmd.application_id}"
    return await store.append(
        stream_id=stream_id,
        events=[ev],
        expected_version=app.expected_version_for_append(),
        correlation_id=cmd.correlation_id,
        causation_id=cmd.causation_id,
        aggregate_type="LoanApplication",
    )


async def handle_start_agent_session(cmd: StartAgentSessionCommand, store: EventStore) -> int:
    agent = await AgentSessionAggregate.load(store, cmd.agent_id, cmd.session_id)
    agent.assert_new_session()

    ev = AgentContextLoaded(
        agent_id=cmd.agent_id,
        session_id=cmd.session_id,
        context_source=cmd.context_source,
        event_replay_from_position=cmd.event_replay_from_position,
        context_token_count=cmd.context_token_count,
        model_version=cmd.model_version,
    )
    stream_id = f"agent-{cmd.agent_id}-{cmd.session_id}"
    return await store.append(
        stream_id=stream_id,
        events=[ev],
        expected_version=agent.expected_version_for_append(),
        correlation_id=cmd.correlation_id,
        causation_id=cmd.causation_id,
        aggregate_type="AgentSession",
    )


async def handle_credit_analysis_completed(cmd: CreditAnalysisCompletedCommand, store: EventStore) -> int:
    # 1. Load current state
    app = await LoanApplicationAggregate.load(store, cmd.application_id)
    agent = await AgentSessionAggregate.load(store, cmd.agent_id, cmd.session_id)

    # 2. Validate
    app.assert_awaiting_credit_analysis()
    agent.assert_context_loaded()
    agent.assert_model_version_current(cmd.model_version)

    # 3. Determine new events
    new_events = [
        CreditAnalysisCompleted(
            application_id=cmd.application_id,
            agent_id=cmd.agent_id,
            session_id=cmd.session_id,
            model_version=cmd.model_version,
            confidence_score=cmd.confidence_score,
            risk_tier=cmd.risk_tier,
            recommended_limit_usd=cmd.recommended_limit_usd,
            analysis_duration_ms=cmd.duration_ms,
            input_data_hash=cmd.input_data_hash,
        )
    ]

    # 4. Append atomically to loan stream (optimistic concurrency via app.version)
    return await store.append(
        stream_id=f"loan-{cmd.application_id}",
        events=new_events,
        expected_version=app.expected_version_for_append(),
        correlation_id=cmd.correlation_id,
        causation_id=cmd.causation_id,
        aggregate_type="LoanApplication",
    )


async def handle_credit_analysis_requested(application_id: str, assigned_agent_id: str, store: EventStore) -> int:
    app = await LoanApplicationAggregate.load(store, application_id)
    app.assert_allows_credit_analysis_requested()

    ev = CreditAnalysisRequested(
        application_id=application_id,
        assigned_agent_id=assigned_agent_id,
        requested_at=datetime.now(tz=timezone.utc),
        priority=0,
    )
    return await store.append(
        stream_id=f"loan-{application_id}",
        events=[ev],
        expected_version=app.expected_version_for_append(),
        aggregate_type="LoanApplication",
    )


async def handle_human_review_completed(cmd: HumanReviewCompletedCommand, store: EventStore) -> int:
    app = await LoanApplicationAggregate.load(store, cmd.application_id)
    stream_id = f"loan-{cmd.application_id}"

    app.assert_allows_human_review()

    events = [
        HumanReviewCompleted(
            application_id=cmd.application_id,
            reviewer_id=cmd.reviewer_id,
            override=cmd.override,
            final_decision=cmd.final_decision,  # type: ignore[arg-type]
            override_reason=cmd.override_reason,
        )
    ]

    now = datetime.now(tz=timezone.utc)
    if cmd.final_decision == "APPROVE":
        events.append(
            ApplicationApproved(
                application_id=cmd.application_id,
                approved_amount_usd=float(app.requested_amount_usd or 0.0),
                interest_rate=0.12,
                conditions=[],
                approved_by=cmd.reviewer_id,
                effective_date=now,
            )
        )
    elif cmd.final_decision == "DECLINE":
        events.append(
            ApplicationDeclined(
                application_id=cmd.application_id,
                decline_reasons=["Human review decline"],
                declined_by=cmd.reviewer_id,
                adverse_action_notice_required=True,
            )
        )

    return await store.append(
        stream_id=stream_id,
        events=events,
        expected_version=app.expected_version_for_append(),
        correlation_id=cmd.correlation_id,
        causation_id=cmd.causation_id,
        aggregate_type="LoanApplication",
    )

