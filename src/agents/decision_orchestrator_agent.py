from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from src.agents.base_agent import BaseApexAgent, NodeTimer
from src.aggregates.loan_application import LoanApplicationAggregate
from src.models.events import DecisionGenerated
from src.projections.compliance_audit import get_current_compliance


@dataclass
class DecisionOrchestratorAgent(BaseApexAgent):
    """
    Support-doc style stub.

    Reads:
    - loan-{application_id} for CreditAnalysisCompleted
    - fraud-{application_id} for FraudScreeningCompleted
    - compliance-{application_id} for ComplianceCheckCompleted (via compliance audit view reducer)

    Writes:
    - DecisionGenerated to loan-{application_id}
    - Session events to agent-decision-orchestrator-{session_id}
    """

    async def process_application(self, *, application_id: str) -> None:
        await self.start_session()

        # validate_inputs
        with NodeTimer() as t:
            if not application_id:
                raise ValueError("application_id is required")
        await self.record_node_execution(
            node_name="validate_inputs",
            input_keys=["application_id"],
            output_keys=[],
            duration_ms=t.duration_ms,
        )

        # load_analysis_outputs
        with NodeTimer() as t:
            loan_events = await self.store.load_stream(f"loan-{application_id}")
            fraud_events = await self.store.load_stream(f"fraud-{application_id}")
            compliance_state = await get_current_compliance(self.store._dsn, application_id)  # noqa: SLF001

            credit = next((e for e in reversed(loan_events) if e.event_type == "CreditAnalysisCompleted"), None)
            fraud = next((e for e in reversed(fraud_events) if e.event_type == "FraudScreeningCompleted"), None)

        await self.record_node_execution(
            node_name="load_analysis_outputs",
            input_keys=["loan_stream", "fraud_stream", "compliance_view"],
            output_keys=["credit", "fraud", "compliance_state"],
            duration_ms=t.duration_ms,
        )

        if credit is None:
            raise ValueError("Missing CreditAnalysisCompleted")
        if fraud is None:
            raise ValueError("Missing FraudScreeningCompleted")

        # synthesize_decision (stub rules)
        with NodeTimer() as t:
            credit_conf = float(credit.payload.get("confidence_score") or 0.0)
            fraud_score = float(fraud.payload.get("fraud_score") or 0.0)

            overall_verdict = compliance_state.get("overall_verdict")
            has_hard_block = bool(compliance_state.get("has_hard_block", False))

            recommendation = "APPROVE"
            if has_hard_block or overall_verdict == "BLOCKED":
                recommendation = "DECLINE"
            elif credit_conf < 0.6:
                recommendation = "REFER"
            elif fraud_score > 0.6:
                recommendation = "REFER"

            confidence = max(credit_conf, 0.0)
            basis = (
                f"credit_conf={credit_conf:.2f}, fraud_score={fraud_score:.2f}, "
                f"compliance_verdict={overall_verdict}"
            )

        await self.record_node_execution(
            node_name="synthesize_decision",
            input_keys=[],
            output_keys=["recommendation", "confidence"],
            duration_ms=t.duration_ms,
            llm_called=False,
        )

        # write_output (append to loan stream with OCC derived from aggregate)
        with NodeTimer() as t:
            app = await LoanApplicationAggregate.load(self.store, application_id)
            stream_id = f"loan-{application_id}"

            ev = DecisionGenerated(
                application_id=application_id,
                orchestrator_agent_id=self.agent_type,
                recommendation=recommendation,  # type: ignore[arg-type]
                confidence_score=confidence,
                contributing_agent_sessions=[
                    f"agent-credit-analysis-{self.session_id}",
                    f"agent-fraud-detection-{self.session_id}",
                    f"agent-compliance-{self.session_id}",
                ],
                decision_basis_summary=basis,
                model_versions={"orchestrator": self.model_version},
            )

            await self.store.append(
                stream_id=stream_id,
                events=[ev],
                expected_version=app.expected_version_for_append(),
                aggregate_type="LoanApplication",
            )

        await self.record_node_execution(
            node_name="write_output",
            input_keys=[],
            output_keys=["DecisionGenerated"],
            duration_ms=t.duration_ms,
        )

        await self.complete(next_agent_triggered="HumanReview" if recommendation == "REFER" else None)

