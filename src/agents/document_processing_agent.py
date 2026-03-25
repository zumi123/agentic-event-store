from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from src.agents.base_agent import BaseApexAgent, NodeTimer
from src.commands.handlers import handle_credit_analysis_requested
from src.models.events import (
    DomainError,
    DocumentAdded,
    DocumentFormatValidated,
    DocumentUploaded,
    ExtractionCompleted,
    ExtractionStarted,
    PackageCreated,
    PackageReadyForAnalysis,
    QualityAssessmentCompleted,
 )


@dataclass
class DocumentProcessingAgent(BaseApexAgent):
    """
    Support-doc stub: validates inputs and document formats, then triggers credit analysis.

    This is intentionally a stub (no real PDF extraction yet). The Week 3 pipeline can be wired
    into extract_* nodes once we finalize the event schema for docpkg-* streams.
    """

    async def process_application(
        self,
        *,
        application_id: str,
        assigned_agent_id: str,
        company_id: str,
        income_statement_path: str,
        balance_sheet_path: str,
    ) -> None:
        await self.start_session()

        package_id = f"docpkg-{application_id}"

        # validate_inputs
        with NodeTimer() as t:
            # Placeholder: validate that required ids exist
            _ = (application_id, assigned_agent_id, company_id)
        await self.record_node_execution(
            node_name="validate_inputs",
            input_keys=["application_id", "assigned_agent_id", "company_id"],
            output_keys=[],
            duration_ms=t.duration_ms,
        )

        # open_aggregate_record (docpkg stream)
        with NodeTimer() as t:
            existing = await self.store.stream_version(package_id)
            if existing == 0:
                await self.store.append(
                    stream_id=package_id,
                    events=[
                        PackageCreated(
                            package_id=package_id,
                            application_id=application_id,
                            created_at=datetime.now(tz=timezone.utc),
                        ),
                        DocumentAdded(package_id=package_id, document_type="income_statement", file_path=income_statement_path),
                        DocumentAdded(package_id=package_id, document_type="balance_sheet", file_path=balance_sheet_path),
                    ],
                    expected_version=-1,
                    aggregate_type="DocumentPackage",
                )
        await self.record_node_execution(
            node_name="open_aggregate_record",
            input_keys=[],
            output_keys=["package_id"],
            duration_ms=t.duration_ms,
        )

        # validate_document_formats
        with NodeTimer() as t:
            ver = await self.store.stream_version(package_id)
            await self.store.append(
                stream_id=package_id,
                events=[
                    DocumentFormatValidated(
                        package_id=package_id,
                        document_type="income_statement",
                        is_valid=True,
                        notes=None,
                    ),
                    DocumentFormatValidated(
                        package_id=package_id,
                        document_type="balance_sheet",
                        is_valid=True,
                        notes=None,
                    ),
                ],
                expected_version=-1 if ver == 0 else ver,
                aggregate_type="DocumentPackage",
            )
        await self.record_node_execution(
            node_name="validate_document_formats",
            input_keys=[],
            output_keys=[],
            duration_ms=t.duration_ms,
        )

        # extract_income_statement
        with NodeTimer() as t:
            ver = await self.store.stream_version(package_id)
            await self.store.append(
                stream_id=package_id,
                events=[
                    ExtractionStarted(
                        package_id=package_id,
                        document_type="income_statement",
                        started_at=datetime.now(tz=timezone.utc),
                    ),
                    ExtractionCompleted(
                        package_id=package_id,
                        document_type="income_statement",
                        completed_at=datetime.now(tz=timezone.utc),
                        extracted_facts={},
                    ),
                ],
                expected_version=-1 if ver == 0 else ver,
                aggregate_type="DocumentPackage",
            )
        await self.record_node_execution(
            node_name="extract_income_statement",
            input_keys=[],
            output_keys=[],
            duration_ms=t.duration_ms,
        )

        # extract_balance_sheet
        with NodeTimer() as t:
            ver = await self.store.stream_version(package_id)
            await self.store.append(
                stream_id=package_id,
                events=[
                    ExtractionStarted(
                        package_id=package_id,
                        document_type="balance_sheet",
                        started_at=datetime.now(tz=timezone.utc),
                    ),
                    ExtractionCompleted(
                        package_id=package_id,
                        document_type="balance_sheet",
                        completed_at=datetime.now(tz=timezone.utc),
                        extracted_facts={},
                    ),
                ],
                expected_version=-1 if ver == 0 else ver,
                aggregate_type="DocumentPackage",
            )
        await self.record_node_execution(
            node_name="extract_balance_sheet",
            input_keys=[],
            output_keys=[],
            duration_ms=t.duration_ms,
        )

        # assess_quality (LLM in support doc; stub here)
        with NodeTimer() as t:
            ver = await self.store.stream_version(package_id)
            await self.store.append(
                stream_id=package_id,
                events=[
                    QualityAssessmentCompleted(
                        package_id=package_id,
                        overall_confidence=1.0,
                        is_coherent=True,
                        anomalies=[],
                        critical_missing_fields=[],
                        reextraction_recommended=False,
                        auditor_notes="stub",
                    ),
                    PackageReadyForAnalysis(package_id=package_id, ready_at=datetime.now(tz=timezone.utc)),
                ],
                expected_version=-1 if ver == 0 else ver,
                aggregate_type="DocumentPackage",
            )
        await self.record_node_execution(
            node_name="assess_quality",
            input_keys=[],
            output_keys=[],
            duration_ms=t.duration_ms,
            llm_called=False,
        )

        # write_output: trigger next agent by appending CreditAnalysisRequested on loan stream
        with NodeTimer() as t:
            # Also append DocumentUploaded on loan stream (support-doc lifecycle)
            loan_stream = f"loan-{application_id}"
            loan_ver = await self.store.stream_version(loan_stream)
            if loan_ver == 0:
                raise DomainError(
                    f"Cannot upload documents to {loan_stream}: application does not exist yet. "
                    "Seed ApplicationSubmitted first (e.g. run datagen/minimal_generate.py)."
                )
            await self.store.append(
                stream_id=loan_stream,
                events=[
                    DocumentUploaded(
                        application_id=application_id,
                        document_type="income_statement",
                        file_path=income_statement_path,
                        uploaded_at=datetime.now(tz=timezone.utc),
                    ),
                    DocumentUploaded(
                        application_id=application_id,
                        document_type="balance_sheet",
                        file_path=balance_sheet_path,
                        uploaded_at=datetime.now(tz=timezone.utc),
                    ),
                ],
                expected_version=loan_ver,
                aggregate_type="LoanApplication",
            )
            await handle_credit_analysis_requested(application_id, assigned_agent_id, self.store)
        await self.record_node_execution(
            node_name="write_output",
            input_keys=[],
            output_keys=[],
            duration_ms=t.duration_ms,
        )

        await self.complete(next_agent_triggered="CreditAnalysisAgent")

