from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict, Field

from src.aggregates.agent_session import AgentSessionAggregate
from src.aggregates.compliance_record import ComplianceRecordAggregate
from src.aggregates.loan_application import ApplicationState, LoanApplicationAggregate
from src.domain.causal import assert_contributing_sessions_have_application_work
from src.event_store import EventStore
from src.models.events import (
    AgentContextLoaded,
    ApplicationApproved,
    ApplicationSubmitted,
    ApplicationDeclined,
    ComplianceCheckCompleted,
    ComplianceCheckRequested,
    ComplianceRuleFailed,
    ComplianceRuleNoted,
    ComplianceRulePassed,
    CreditAnalysisCompleted,
    CreditAnalysisRequested,
    DecisionGenerated,
    DomainError,
    FraudScreeningCompleted,
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


class FraudScreeningCompletedCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")

    application_id: str
    agent_id: str
    session_id: str
    model_version: str
    fraud_score: float
    anomaly_flags: list[str] = Field(default_factory=list)
    screening_model_version: str
    input_data_hash: str
    correlation_id: str | None = None
    causation_id: str | None = None


class RecordComplianceRuleCommand(BaseModel):
    """One rule outcome (passed / failed / noted), or init to append ComplianceCheckRequested."""

    model_config = ConfigDict(extra="forbid")

    application_id: str
    outcome: str  # passed | failed | noted | init
    rule_id: str = ""
    rule_version: str = ""
    regulation_set_version: str = ""
    init_checks_required: list[str] = Field(default_factory=list)
    overall_verdict: str | None = None
    has_hard_block: bool = False
    rules_evaluated: int = 0
    evaluation_timestamp: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))
    failure_reason: str | None = None
    remediation_required: bool = False
    note_type: str | None = None
    note: str | None = None
    evidence_hash: str = "stub-evidence"
    correlation_id: str | None = None
    causation_id: str | None = None


class GenerateDecisionCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")

    application_id: str
    orchestrator_agent_id: str
    session_id: str
    recommendation: str  # APPROVE | DECLINE | REFER
    confidence_score: float
    contributing_agent_sessions: list[str]
    decision_basis_summary: str
    model_versions: dict[str, str] = Field(default_factory=dict)
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
    if cmd.override and not (cmd.override_reason and cmd.override_reason.strip()):
        raise DomainError("override=True requires override_reason")

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
        comp = await ComplianceRecordAggregate.load(store, cmd.application_id)
        comp.assert_allows_approval()
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


async def handle_record_fraud_screening(cmd: FraudScreeningCompletedCommand, store: EventStore) -> int:
    if not 0.0 <= cmd.fraud_score <= 1.0:
        raise DomainError("fraud_score must be between 0.0 and 1.0")
    agent = await AgentSessionAggregate.load(store, cmd.agent_id, cmd.session_id)
    agent.assert_context_loaded()
    agent.assert_model_version_current(cmd.model_version)

    loan_app = await LoanApplicationAggregate.load(store, cmd.application_id)
    if loan_app.state != ApplicationState.ANALYSIS_COMPLETE:
        raise DomainError(
            "Loan not ready for fraud screening; state must be AnalysisComplete (credit analysis completed)"
        )

    stream_id = f"fraud-{cmd.application_id}"
    ver = await store.stream_version(stream_id)
    ev = FraudScreeningCompleted(
        application_id=cmd.application_id,
        agent_id=cmd.agent_id,
        fraud_score=cmd.fraud_score,
        anomaly_flags=cmd.anomaly_flags,
        screening_model_version=cmd.screening_model_version,
        input_data_hash=cmd.input_data_hash,
    )
    v = await store.append(
        stream_id=stream_id,
        events=[ev],
        expected_version=-1 if ver == 0 else ver,
        correlation_id=cmd.correlation_id,
        causation_id=cmd.causation_id,
        aggregate_type="FraudScreening",
    )

    # Align with FraudDetectionAgent: fraud screening triggers compliance on loan + compliance streams.
    compliance_ev = ComplianceCheckRequested(
        application_id=cmd.application_id,
        regulation_set_version="2026-Q1",
        checks_required=["REG-001", "REG-002", "REG-003", "REG-004", "REG-005", "REG-006"],
    )
    loan_stream = f"loan-{cmd.application_id}"
    await store.append(
        stream_id=loan_stream,
        events=[compliance_ev],
        expected_version=loan_app.expected_version_for_append(),
        correlation_id=cmd.correlation_id,
        causation_id=cmd.causation_id,
        aggregate_type="LoanApplication",
    )
    comp_stream = f"compliance-{cmd.application_id}"
    comp_ver = await store.stream_version(comp_stream)
    await store.append(
        stream_id=comp_stream,
        events=[compliance_ev],
        expected_version=-1 if comp_ver == 0 else comp_ver,
        correlation_id=cmd.correlation_id,
        causation_id=cmd.causation_id,
        aggregate_type="ComplianceRecord",
    )
    return v


async def handle_record_compliance_rule(cmd: RecordComplianceRuleCommand, store: EventStore) -> int:
    comp = await ComplianceRecordAggregate.load(store, cmd.application_id)
    stream_id = f"compliance-{cmd.application_id}"

    if cmd.outcome == "init":
        if comp.version != 0:
            raise DomainError("Compliance stream already initialized")
        if not cmd.regulation_set_version or not cmd.init_checks_required:
            raise DomainError("init requires regulation_set_version and init_checks_required")
        ev = ComplianceCheckRequested(
            application_id=cmd.application_id,
            regulation_set_version=cmd.regulation_set_version,
            checks_required=list(cmd.init_checks_required),
        )
        return await store.append(
            stream_id=stream_id,
            events=[ev],
            expected_version=-1,
            correlation_id=cmd.correlation_id,
            causation_id=cmd.causation_id,
            aggregate_type="ComplianceRecord",
        )

    if cmd.outcome == "complete":
        if comp.version == 0:
            raise DomainError("ComplianceCheckRequested must exist before ComplianceCheckCompleted")
        if not cmd.overall_verdict or not cmd.regulation_set_version:
            raise DomainError("complete requires overall_verdict and regulation_set_version")
        ev = ComplianceCheckCompleted(
            application_id=cmd.application_id,
            regulation_set_version=cmd.regulation_set_version,
            overall_verdict=cmd.overall_verdict,  # type: ignore[arg-type]
            has_hard_block=cmd.has_hard_block,
            completed_at=cmd.evaluation_timestamp,
            rules_evaluated=cmd.rules_evaluated,
        )
        ver = comp.expected_version_for_append()
        return await store.append(
            stream_id=stream_id,
            events=[ev],
            expected_version=ver,
            correlation_id=cmd.correlation_id,
            causation_id=cmd.causation_id,
            aggregate_type="ComplianceRecord",
        )

    if comp.version == 0:
        raise DomainError("ComplianceCheckRequested must exist before recording compliance rules")
    if cmd.outcome == "passed":
        if not cmd.rule_id or not cmd.rule_version:
            raise DomainError("passed outcome requires rule_id and rule_version")
    if comp.checks_required and cmd.rule_id and cmd.rule_id not in comp.checks_required:
        raise DomainError(
            f"rule_id {cmd.rule_id!r} is not in active checks_required for this compliance record"
        )
    if comp.regulation_set_version and cmd.regulation_set_version and comp.regulation_set_version != cmd.regulation_set_version:
        raise DomainError("regulation_set_version does not match active ComplianceCheckRequested")

    ver = comp.expected_version_for_append()

    if cmd.outcome == "passed":
        ev = ComplianceRulePassed(
            application_id=cmd.application_id,
            rule_id=cmd.rule_id,
            rule_version=cmd.rule_version,
            evaluation_timestamp=cmd.evaluation_timestamp,
            evidence_hash=cmd.evidence_hash,
        )
    elif cmd.outcome == "failed":
        if not cmd.rule_id or not cmd.rule_version:
            raise DomainError("failed outcome requires rule_id and rule_version")
        ev = ComplianceRuleFailed(
            application_id=cmd.application_id,
            rule_id=cmd.rule_id,
            rule_version=cmd.rule_version,
            failure_reason=cmd.failure_reason or "failed",
            remediation_required=cmd.remediation_required,
        )
    elif cmd.outcome == "noted":
        if not cmd.rule_id or not cmd.rule_version:
            raise DomainError("noted outcome requires rule_id and rule_version")
        if not cmd.note_type or not cmd.note:
            raise DomainError("noted outcome requires note_type and note")
        ev = ComplianceRuleNoted(
            application_id=cmd.application_id,
            rule_id=cmd.rule_id,
            rule_version=cmd.rule_version,
            note_type=cmd.note_type,
            note=cmd.note,
            evaluation_timestamp=cmd.evaluation_timestamp,
        )
    else:
        raise DomainError("outcome must be init, complete, passed, failed, or noted")

    return await store.append(
        stream_id=stream_id,
        events=[ev],
        expected_version=ver,
        correlation_id=cmd.correlation_id,
        causation_id=cmd.causation_id,
        aggregate_type="ComplianceRecord",
    )


async def handle_generate_decision(cmd: GenerateDecisionCommand, store: EventStore) -> int:
    app = await LoanApplicationAggregate.load(store, cmd.application_id)
    if app.state not in (ApplicationState.COMPLIANCE_REVIEW, ApplicationState.ANALYSIS_COMPLETE):
        raise DomainError(
            "generate_decision requires loan state ComplianceReview or AnalysisComplete"
        )

    conf = float(cmd.confidence_score)
    rec = cmd.recommendation
    if conf < 0.6:
        rec = "REFER"

    await assert_contributing_sessions_have_application_work(
        store,
        application_id=cmd.application_id,
        contributing_agent_sessions=cmd.contributing_agent_sessions,
    )

    ev = DecisionGenerated(
        application_id=cmd.application_id,
        orchestrator_agent_id=cmd.orchestrator_agent_id,
        recommendation=rec,  # type: ignore[arg-type]
        confidence_score=conf,
        contributing_agent_sessions=cmd.contributing_agent_sessions,
        decision_basis_summary=cmd.decision_basis_summary,
        model_versions=cmd.model_versions,
    )
    return await store.append(
        stream_id=f"loan-{cmd.application_id}",
        events=[ev],
        expected_version=app.expected_version_for_append(),
        correlation_id=cmd.correlation_id,
        causation_id=cmd.causation_id,
        aggregate_type="LoanApplication",
    )

