from __future__ import annotations

from psycopg import AsyncConnection
from psycopg.types.json import Jsonb

from src.projections.base import Projection
from src.models.events import StoredEvent


class ApplicationSummaryProjection(Projection):
    name = "ApplicationSummary"
    subscribed_event_types = {
        "ApplicationSubmitted",
        "CreditAnalysisRequested",
        "CreditAnalysisCompleted",
        "FraudScreeningCompleted",
        "ComplianceCheckRequested",
        "ComplianceRulePassed",
        "ComplianceRuleFailed",
        "ComplianceCheckCompleted",
        "DecisionGenerated",
        "HumanReviewCompleted",
        "ApplicationApproved",
        "ApplicationDeclined",
    }

    async def handle(self, conn: AsyncConnection, event: StoredEvent) -> None:
        et = event.event_type
        payload = event.payload

        if et == "ApplicationSubmitted":
            await conn.execute(
                "INSERT INTO application_summary "
                "(application_id, state, applicant_id, requested_amount_usd, last_event_type, last_event_at) "
                "VALUES (%s, %s, %s, %s, %s, %s) "
                "ON CONFLICT (application_id) DO UPDATE SET "
                "state = EXCLUDED.state, applicant_id = EXCLUDED.applicant_id, requested_amount_usd = EXCLUDED.requested_amount_usd, "
                "last_event_type = EXCLUDED.last_event_type, last_event_at = EXCLUDED.last_event_at",
                (
                    payload["application_id"],
                    "Submitted",
                    payload.get("applicant_id"),
                    payload.get("requested_amount_usd"),
                    et,
                    event.recorded_at,
                ),
            )
            return

        application_id = payload.get("application_id")
        if not application_id:
            return

        if et == "CreditAnalysisRequested":
            await conn.execute(
                "UPDATE application_summary SET state=%s, last_event_type=%s, last_event_at=%s WHERE application_id=%s",
                ("AwaitingAnalysis", et, event.recorded_at, application_id),
            )
        elif et == "CreditAnalysisCompleted":
            await conn.execute(
                "UPDATE application_summary SET state=%s, risk_tier=%s, last_event_type=%s, last_event_at=%s WHERE application_id=%s",
                ("AnalysisComplete", payload.get("risk_tier"), et, event.recorded_at, application_id),
            )
        elif et == "FraudScreeningCompleted":
            await conn.execute(
                "UPDATE application_summary SET fraud_score=%s, last_event_type=%s, last_event_at=%s WHERE application_id=%s",
                (payload.get("fraud_score"), et, event.recorded_at, application_id),
            )
        elif et == "ComplianceCheckRequested":
            await conn.execute(
                "UPDATE application_summary SET state=%s, compliance_status=%s, last_event_type=%s, last_event_at=%s WHERE application_id=%s",
                ("ComplianceReview", "PENDING", et, event.recorded_at, application_id),
            )
        elif et == "ComplianceRuleFailed":
            await conn.execute(
                "UPDATE application_summary SET compliance_status=%s, last_event_type=%s, last_event_at=%s WHERE application_id=%s",
                ("FAILED", et, event.recorded_at, application_id),
            )
        elif et == "ComplianceRulePassed":
            # Keep as PENDING until completed, but record last_event.
            await conn.execute(
                "UPDATE application_summary SET last_event_type=%s, last_event_at=%s WHERE application_id=%s",
                (et, event.recorded_at, application_id),
            )
        elif et == "ComplianceCheckCompleted":
            verdict = payload.get("overall_verdict")
            status = "PASSED" if verdict == "CLEAR" else ("FAILED" if verdict == "BLOCKED" else "CONDITIONAL")
            await conn.execute(
                "UPDATE application_summary SET compliance_status=%s, last_event_type=%s, last_event_at=%s WHERE application_id=%s",
                (status, et, event.recorded_at, application_id),
            )
        elif et == "DecisionGenerated":
            await conn.execute(
                "UPDATE application_summary SET state=%s, decision=%s, last_event_type=%s, last_event_at=%s WHERE application_id=%s",
                ("PendingDecision", payload.get("recommendation"), et, event.recorded_at, application_id),
            )
        elif et == "HumanReviewCompleted":
            await conn.execute(
                "UPDATE application_summary SET human_reviewer_id=%s, final_decision_at=%s, last_event_type=%s, last_event_at=%s WHERE application_id=%s",
                (payload.get("reviewer_id"), event.recorded_at, et, event.recorded_at, application_id),
            )
        elif et == "ApplicationApproved":
            await conn.execute(
                "UPDATE application_summary SET state=%s, approved_amount_usd=%s, last_event_type=%s, last_event_at=%s WHERE application_id=%s",
                ("FinalApproved", payload.get("approved_amount_usd"), et, event.recorded_at, application_id),
            )
        elif et == "ApplicationDeclined":
            await conn.execute(
                "UPDATE application_summary SET state=%s, last_event_type=%s, last_event_at=%s WHERE application_id=%s",
                ("FinalDeclined", et, event.recorded_at, application_id),
            )

