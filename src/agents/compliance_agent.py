from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from src.agents.base_agent import BaseApexAgent, NodeTimer
from src.aggregates.loan_application import LoanApplicationAggregate
from src.models.events import (
    ComplianceCheckCompleted,
    ComplianceRuleFailed,
    ComplianceRuleNoted,
    ComplianceRulePassed,
)
from src.registry.client import ApplicantRegistryClient


@dataclass
class ComplianceAgent(BaseApexAgent):
    """
    Deterministic compliance rule evaluator (support-doc stub).

    Implements REG-001..REG-006 per the support document (simplified).
    Writes rule events to compliance-{application_id} and a ComplianceCheckCompleted summary.
    """

    regulation_set_version: str = "2026-Q1"

    async def process_application(self, *, application_id: str, company_id: str) -> None:
        await self.start_session()
        registry = ApplicantRegistryClient(dsn=self.store._dsn)  # noqa: SLF001
        compliance_stream = f"compliance-{application_id}"

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
            app = await LoanApplicationAggregate.load(self.store, application_id)
        await self.record_node_execution(
            node_name="load_external_data",
            input_keys=["company_id", "loan_stream"],
            output_keys=["company", "flags", "requested_amount_usd"],
            duration_ms=t.duration_ms,
        )

        if company is None:
            raise ValueError(f"Company not found in registry: {company_id}")

        # evaluate rules (deterministic)
        with NodeTimer() as t:
            now = datetime.now(tz=timezone.utc)
            hard_block = False
            rules_evaluated = 0
            out_events = []

            def has_flag(flag_type: str) -> bool:
                return any(f.flag_type == flag_type and f.is_active for f in flags)

            # REG-001 AML watch (remediable)
            rules_evaluated += 1
            if has_flag("AML_WATCH"):
                out_events.append(
                    ComplianceRuleFailed(
                        application_id=application_id,
                        rule_id="REG-001",
                        rule_version=self.regulation_set_version,
                        failure_reason="AML_WATCH active",
                        remediation_required=True,
                    )
                )
            else:
                out_events.append(
                    ComplianceRulePassed(
                        application_id=application_id,
                        rule_id="REG-001",
                        rule_version=self.regulation_set_version,
                        evaluation_timestamp=now,
                        evidence_hash=f"registry:{company_id}:no_aml_watch",
                    )
                )

            # REG-002 OFAC / sanctions (hard)
            rules_evaluated += 1
            if has_flag("SANCTIONS_REVIEW"):
                hard_block = True
                out_events.append(
                    ComplianceRuleFailed(
                        application_id=application_id,
                        rule_id="REG-002",
                        rule_version=self.regulation_set_version,
                        failure_reason="SANCTIONS_REVIEW active",
                        remediation_required=False,
                    )
                )
            else:
                out_events.append(
                    ComplianceRulePassed(
                        application_id=application_id,
                        rule_id="REG-002",
                        rule_version=self.regulation_set_version,
                        evaluation_timestamp=now,
                        evidence_hash=f"registry:{company_id}:no_sanctions",
                    )
                )

            # REG-003 jurisdiction eligibility (hard): MT excluded
            rules_evaluated += 1
            if company.jurisdiction == "MT":
                hard_block = True
                out_events.append(
                    ComplianceRuleFailed(
                        application_id=application_id,
                        rule_id="REG-003",
                        rule_version=self.regulation_set_version,
                        failure_reason="Jurisdiction MT is ineligible",
                        remediation_required=False,
                    )
                )
            else:
                out_events.append(
                    ComplianceRulePassed(
                        application_id=application_id,
                        rule_id="REG-003",
                        rule_version=self.regulation_set_version,
                        evaluation_timestamp=now,
                        evidence_hash=f"registry:{company_id}:jurisdiction_ok",
                    )
                )

            # REG-004 legal entity type eligibility (remediable)
            rules_evaluated += 1
            req_amt = app.requested_amount_usd or 0.0
            if company.legal_type == "Sole Proprietor" and req_amt > 250_000:
                out_events.append(
                    ComplianceRuleFailed(
                        application_id=application_id,
                        rule_id="REG-004",
                        rule_version=self.regulation_set_version,
                        failure_reason="Sole Proprietor > 250000 requires remediation",
                        remediation_required=True,
                    )
                )
            else:
                out_events.append(
                    ComplianceRulePassed(
                        application_id=application_id,
                        rule_id="REG-004",
                        rule_version=self.regulation_set_version,
                        evaluation_timestamp=now,
                        evidence_hash=f"loan:{application_id}:legal_type_ok",
                    )
                )

            # REG-005 minimum operating history (hard)
            rules_evaluated += 1
            if (2026 - int(company.founded_year)) < 2:
                hard_block = True
                out_events.append(
                    ComplianceRuleFailed(
                        application_id=application_id,
                        rule_id="REG-005",
                        rule_version=self.regulation_set_version,
                        failure_reason="Operating history < 2 years",
                        remediation_required=False,
                    )
                )
            else:
                out_events.append(
                    ComplianceRulePassed(
                        application_id=application_id,
                        rule_id="REG-005",
                        rule_version=self.regulation_set_version,
                        evaluation_timestamp=now,
                        evidence_hash=f"registry:{company_id}:operating_history_ok",
                    )
                )

            # REG-006 CRA (informational): always noted
            rules_evaluated += 1
            out_events.append(
                ComplianceRuleNoted(
                    application_id=application_id,
                    rule_id="REG-006",
                    rule_version=self.regulation_set_version,
                    note_type="CRA_CONSIDERATION",
                    note="CRA consideration recorded (informational).",
                    evaluation_timestamp=now,
                )
            )

            # Simple stub verdict: any hard block => BLOCKED, else CLEAR.
            overall = "BLOCKED" if hard_block else "CLEAR"

            out_events.append(
                ComplianceCheckCompleted(
                    application_id=application_id,
                    regulation_set_version=self.regulation_set_version,
                    overall_verdict=overall,
                    has_hard_block=hard_block,
                    completed_at=now,
                    rules_evaluated=rules_evaluated,
                )
            )

            ver = await self.store.stream_version(compliance_stream)
            await self.store.append(
                stream_id=compliance_stream,
                events=out_events,
                expected_version=-1 if ver == 0 else ver,
                aggregate_type="ComplianceRecord",
            )

        await self.record_node_execution(
            node_name="evaluate_rules_and_write_output",
            input_keys=[],
            output_keys=["ComplianceRule*", "ComplianceCheckCompleted"],
            duration_ms=t.duration_ms,
        )

        await self.complete(next_agent_triggered="DecisionOrchestratorAgent")

