from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from src.agents.base_agent import BaseApexAgent, NodeTimer
from src.aggregates.loan_application import LoanApplicationAggregate
from src.models.events import ComplianceCheckRequested, FraudScreeningCompleted
from src.registry.client import ApplicantRegistryClient


@dataclass
class FraudDetectionAgent(BaseApexAgent):
    """
    Support-doc style stub.

    Reads:
    - applicant_registry (read-only)
    - docpkg-{application_id} extraction events (present but stub facts ok)

    Writes:
    - FraudScreeningCompleted to fraud-{application_id} stream
    - ComplianceCheckRequested to loan-{application_id} (lifecycle) and compliance-{application_id} (audit view)
    - Session events to agent-fraud-detection-{session_id} stream
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

        # load_facts
        with NodeTimer() as t:
            _docpkg = await self.store.load_stream(f"docpkg-{application_id}")
            _company = await registry.get_company(company_id)
            _hist = await registry.get_financial_history(company_id)
        await self.record_node_execution(
            node_name="load_facts",
            input_keys=["docpkg_stream", "company_id"],
            output_keys=["docpkg_events", "company", "history"],
            duration_ms=t.duration_ms,
        )

        # analyze_fraud_patterns (stub)
        with NodeTimer() as t:
            anomaly_flags: list[str] = []
            fraud_score = 0.1
            # Simple stub heuristic: if we have any active compliance flags, bump score.
            flags = await registry.get_active_compliance_flags(company_id)
            if flags:
                anomaly_flags.append("REGISTRY_FLAGS_PRESENT")
                fraud_score = 0.4
        await self.record_node_execution(
            node_name="analyze_fraud_patterns",
            input_keys=[],
            output_keys=["fraud_score", "anomaly_flags"],
            duration_ms=t.duration_ms,
            llm_called=False,
        )

        # write_output
        with NodeTimer() as t:
            # Ensure loan is ready (credit completed)
            app = await LoanApplicationAggregate.load(self.store, application_id)
            if app.state != app.state.ANALYSIS_COMPLETE:  # type: ignore[comparison-overlap]
                raise ValueError(f"Loan not ready for fraud screening; state={app.state}")

            fraud_stream = f"fraud-{application_id}"
            fraud_ver = await self.store.stream_version(fraud_stream)
            await self.store.append(
                stream_id=fraud_stream,
                events=[
                    FraudScreeningCompleted(
                        application_id=application_id,
                        agent_id=self.agent_type,
                        fraud_score=fraud_score,
                        anomaly_flags=anomaly_flags,
                        screening_model_version=self.model_version,
                        input_data_hash=f"registry:{company_id}",
                    )
                ],
                expected_version=-1 if fraud_ver == 0 else fraud_ver,
                aggregate_type="FraudScreening",
            )

            # Trigger compliance (both lifecycle stream + compliance record stream)
            compliance_ev = ComplianceCheckRequested(
                application_id=application_id,
                regulation_set_version="2026-Q1",
                checks_required=["REG-001", "REG-002", "REG-003", "REG-004", "REG-005", "REG-006"],
            )

            loan_stream = f"loan-{application_id}"
            loan_ver = await self.store.stream_version(loan_stream)
            await self.store.append(
                stream_id=loan_stream,
                events=[compliance_ev],
                expected_version=-1 if loan_ver == 0 else loan_ver,
                aggregate_type="LoanApplication",
            )

            compliance_stream = f"compliance-{application_id}"
            comp_ver = await self.store.stream_version(compliance_stream)
            await self.store.append(
                stream_id=compliance_stream,
                events=[compliance_ev],
                expected_version=-1 if comp_ver == 0 else comp_ver,
                aggregate_type="ComplianceRecord",
            )

        await self.record_node_execution(
            node_name="write_output",
            input_keys=[],
            output_keys=["FraudScreeningCompleted", "ComplianceCheckRequested"],
            duration_ms=t.duration_ms,
        )

        await self.complete(next_agent_triggered="ComplianceAgent")

