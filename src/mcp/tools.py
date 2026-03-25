from __future__ import annotations

from typing import Any

from src.commands.handlers import (
    CreditAnalysisCompletedCommand,
    FraudScreeningCompletedCommand,
    GenerateDecisionCommand,
    HumanReviewCompletedCommand,
    RecordComplianceRuleCommand,
    StartAgentSessionCommand,
    SubmitApplicationCommand,
    handle_credit_analysis_completed,
    handle_credit_analysis_requested,
    handle_generate_decision,
    handle_human_review_completed,
    handle_record_compliance_rule,
    handle_record_fraud_screening,
    handle_start_agent_session,
    handle_submit_application,
)
from src.event_store import EventStore
from src.integrity.audit_chain import run_integrity_check
from src.mcp.errors import ToolError
from src.models.events import DomainError, OptimisticConcurrencyError

_TOOL_DOCS = {
    "submit_application": "Submit a new loan application (ApplicationSubmitted). Fails if application_id already exists.",
    "start_agent_session": (
        "Gas Town: load AgentContextLoaded before any agent decision tools. "
        "Precondition: call once per agent_id+session_id before record_credit_analysis / record_fraud_screening."
    ),
    "record_credit_analysis": (
        "Append CreditAnalysisCompleted to loan stream. Requires awaiting credit analysis state and active agent session with matching model_version."
    ),
    "record_fraud_screening": (
        "Append FraudScreeningCompleted to fraud-{application_id}; when loan is AnalysisComplete, also appends "
        "ComplianceCheckRequested to loan and compliance streams (same as FraudDetectionAgent). fraud_score 0..1."
    ),
    "record_compliance_check": (
        "Append compliance events on compliance-{application_id}. "
        "outcome: init (ComplianceCheckRequested), complete (ComplianceCheckCompleted), or passed|failed|noted per rule. "
        "rule_id must appear in checks_required when using passed|failed|noted."
    ),
    "generate_decision": (
        "Append DecisionGenerated. Enforces confidence floor (REFER if confidence<0.6) and contributing_agent_sessions causal chain. "
        "Requires loan state AnalysisComplete or ComplianceReview."
    ),
    "record_human_review": (
        "Append HumanReviewCompleted (+ ApplicationApproved/Declined when final). override=True requires override_reason. "
        "APPROVE requires all mandatory compliance checks passed."
    ),
    "run_integrity_check": (
        "Append AuditIntegrityCheckRun to audit-{entity_type}-{entity_id}. Optional role=compliance for policy hooks. "
        "Returns chain_valid and tamper_detected."
    ),
}


async def submit_application(store: EventStore, **params: Any) -> dict[str, Any]:
    try:
        cmd = SubmitApplicationCommand(**params)
        v = await handle_submit_application(cmd, store)
        return {"stream_id": f"loan-{cmd.application_id}", "initial_version": v, "doc": _TOOL_DOCS["submit_application"]}
    except Exception as e:  # noqa: BLE001
        raise ToolError("ValidationError", str(e), suggested_action="fix_parameters") from e


async def start_agent_session(store: EventStore, **params: Any) -> dict[str, Any]:
    try:
        cmd = StartAgentSessionCommand(**params)
        v = await handle_start_agent_session(cmd, store)
        return {"session_id": cmd.session_id, "context_position": v, "doc": _TOOL_DOCS["start_agent_session"]}
    except Exception as e:  # noqa: BLE001
        raise ToolError("ValidationError", str(e), suggested_action="fix_parameters") from e


async def record_credit_analysis(store: EventStore, **params: Any) -> dict[str, Any]:
    try:
        cmd = CreditAnalysisCompletedCommand(**params)
        v = await handle_credit_analysis_completed(cmd, store)
        return {"new_stream_version": v, "doc": _TOOL_DOCS["record_credit_analysis"]}
    except OptimisticConcurrencyError as e:
        raise ToolError(
            "OptimisticConcurrencyError",
            str(e),
            suggested_action="reload_stream_and_retry",
            stream_id=e.stream_id,
            expected_version=e.expected_version,
            actual_version=e.actual_version,
        ) from e
    except Exception as e:  # noqa: BLE001
        raise ToolError("DomainError", str(e), suggested_action="check_preconditions") from e


async def record_fraud_screening(store: EventStore, **params: Any) -> dict[str, Any]:
    try:
        cmd = FraudScreeningCompletedCommand(**params)
        v = await handle_record_fraud_screening(cmd, store)
        return {"new_stream_version": v, "doc": _TOOL_DOCS["record_fraud_screening"]}
    except OptimisticConcurrencyError as e:
        raise ToolError(
            "OptimisticConcurrencyError",
            str(e),
            suggested_action="reload_stream_and_retry",
            stream_id=e.stream_id,
            expected_version=e.expected_version,
            actual_version=e.actual_version,
        ) from e
    except Exception as e:  # noqa: BLE001
        raise ToolError("DomainError", str(e), suggested_action="check_preconditions") from e


async def record_compliance_check(store: EventStore, **params: Any) -> dict[str, Any]:
    try:
        cmd = RecordComplianceRuleCommand(**params)
        v = await handle_record_compliance_rule(cmd, store)
        return {"new_stream_version": v, "doc": _TOOL_DOCS["record_compliance_check"]}
    except OptimisticConcurrencyError as e:
        raise ToolError(
            "OptimisticConcurrencyError",
            str(e),
            suggested_action="reload_stream_and_retry",
            stream_id=e.stream_id,
            expected_version=e.expected_version,
            actual_version=e.actual_version,
        ) from e
    except Exception as e:  # noqa: BLE001
        raise ToolError("DomainError", str(e), suggested_action="check_preconditions") from e


async def generate_decision(store: EventStore, **params: Any) -> dict[str, Any]:
    try:
        cmd = GenerateDecisionCommand(**params)
        eff = cmd.recommendation if float(cmd.confidence_score) >= 0.6 else "REFER"
        v = await handle_generate_decision(cmd, store)
        return {
            "new_stream_version": v,
            "recommendation": eff,
            "doc": _TOOL_DOCS["generate_decision"],
        }
    except OptimisticConcurrencyError as e:
        raise ToolError(
            "OptimisticConcurrencyError",
            str(e),
            suggested_action="reload_stream_and_retry",
            stream_id=e.stream_id,
            expected_version=e.expected_version,
            actual_version=e.actual_version,
        ) from e
    except Exception as e:  # noqa: BLE001
        raise ToolError("DomainError", str(e), suggested_action="check_preconditions") from e


async def record_human_review(store: EventStore, **params: Any) -> dict[str, Any]:
    try:
        cmd = HumanReviewCompletedCommand(**params)
        v = await handle_human_review_completed(cmd, store)
        return {"new_stream_version": v, "doc": _TOOL_DOCS["record_human_review"]}
    except OptimisticConcurrencyError as e:
        raise ToolError(
            "OptimisticConcurrencyError",
            str(e),
            suggested_action="reload_stream_and_retry",
            stream_id=e.stream_id,
            expected_version=e.expected_version,
            actual_version=e.actual_version,
        ) from e
    except Exception as e:  # noqa: BLE001
        raise ToolError("DomainError", str(e), suggested_action="check_preconditions") from e


async def run_integrity_check_tool(store: EventStore, **params: Any) -> dict[str, Any]:
    try:
        entity_type = params.get("entity_type", "loan")
        entity_id = params["entity_id"]
        role = params.get("role")
        skip_rate_limit = params.get("skip_rate_limit", True)
        from dataclasses import asdict

        result = await run_integrity_check(
            store,
            entity_type,
            entity_id,
            role=role,
            skip_rate_limit=bool(skip_rate_limit),
        )
        d = asdict(result)
        d["doc"] = _TOOL_DOCS["run_integrity_check"]
        return d
    except RuntimeError as e:
        raise ToolError("RateLimited", str(e), suggested_action="wait_and_retry") from e
    except Exception as e:  # noqa: BLE001
        raise ToolError("DomainError", str(e), suggested_action="check_parameters") from e


async def request_credit_analysis(store: EventStore, application_id: str, assigned_agent_id: str) -> dict[str, Any]:
    try:
        v = await handle_credit_analysis_requested(application_id, assigned_agent_id, store)
        return {"new_stream_version": v}
    except DomainError as e:
        raise ToolError("DomainError", str(e), suggested_action="check_application_state") from e
