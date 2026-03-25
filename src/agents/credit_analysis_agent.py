from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import time

from src.agents.base_agent import BaseApexAgent, NodeTimer
from src.aggregates.loan_application import LoanApplicationAggregate
from src.models.events import CreditAnalysisCompleted
from src.registry.client import ApplicantRegistryClient


@dataclass
class CreditAnalysisAgent(BaseApexAgent):
    """
    Support-doc style stub.

    Reads:
    - applicant_registry (read-only)
    - docpkg-{application_id} ExtractionCompleted (stub facts)

    Writes:
    - CreditAnalysisCompleted to loan-{application_id} stream
    - Session events to agent-{agent_type}-{session_id} stream
    """

    async def process_application(self, *, application_id: str, company_id: str) -> None:
        await self.start_session()

        registry = ApplicantRegistryClient(dsn=self.store._dsn)  # noqa: SLF001

        # validate_inputs
        with NodeTimer() as t:
            if not application_id or not company_id:
                raise ValueError("application_id and company_id are required")
        await self.record_node_execution(
            node_name="validate_inputs",
            input_keys=["application_id", "company_id"],
            output_keys=[],
            duration_ms=t.duration_ms,
        )

        # load_external_data
        with NodeTimer() as t:
            company = await registry.get_company(company_id)
            flags = await registry.get_active_compliance_flags(company_id)
            hist = await registry.get_financial_history(company_id)
            # docpkg extraction facts (stub)
            docpkg_events = await self.store.load_stream(f"docpkg-{application_id}")
        await self.record_node_execution(
            node_name="load_external_data",
            input_keys=["company_id", "docpkg_stream"],
            output_keys=["company", "flags", "history", "extracted_facts"],
            duration_ms=t.duration_ms,
        )

        # analyze_credit_risk (stub)
        with NodeTimer() as t:
            prior_default = False
            loans = await registry.get_loan_relationships(company_id)
            prior_default = any(l.default_occurred for l in loans)

            # Very simple stub scoring
            risk_tier = "HIGH" if prior_default else ("MEDIUM" if flags else "LOW")
            confidence = 0.8 if risk_tier != "HIGH" else 0.65
            recommended_limit = 10_000.0 if risk_tier == "HIGH" else 50_000.0
        await self.record_node_execution(
            node_name="analyze_credit_risk",
            input_keys=[],
            output_keys=["risk_tier", "confidence", "recommended_limit"],
            duration_ms=t.duration_ms,
            llm_called=False,
        )

        # write_output (append to loan stream with OCC derived from aggregate)
        with NodeTimer() as t:
            t0 = time.time()
            app = await LoanApplicationAggregate.load(self.store, application_id)
            app.assert_awaiting_credit_analysis()
            stream_id = f"loan-{application_id}"

            analysis_duration_ms = int((time.time() - t0) * 1000)
            ev = CreditAnalysisCompleted(
                application_id=application_id,
                agent_id=self.agent_type,
                session_id=self.session_id,
                model_version=self.model_version,
                confidence_score=confidence,
                risk_tier=risk_tier,
                recommended_limit_usd=recommended_limit,
                analysis_duration_ms=analysis_duration_ms,
                input_data_hash=f"registry:{company_id}",
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
            output_keys=["CreditAnalysisCompleted"],
            duration_ms=t.duration_ms,
        )

        await self.complete(next_agent_triggered="FraudDetectionAgent")

