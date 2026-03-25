from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.agents.compliance_agent import ComplianceAgent  # noqa: E402
from src.agents.credit_analysis_agent import CreditAnalysisAgent  # noqa: E402
from src.agents.decision_orchestrator_agent import DecisionOrchestratorAgent  # noqa: E402
from src.agents.document_processing_agent import DocumentProcessingAgent  # noqa: E402
from src.agents.fraud_detection_agent import FraudDetectionAgent  # noqa: E402
from src.commands.handlers import HumanReviewCompletedCommand, handle_human_review_completed  # noqa: E402
from src.event_store import EventStore  # noqa: E402
from src.projections.application_summary import ApplicationSummaryProjection  # noqa: E402
from src.projections.agent_performance import AgentPerformanceLedgerProjection  # noqa: E402
from src.projections.compliance_audit import ComplianceAuditViewProjection  # noqa: E402
from src.projections.daemon import ProjectionDaemon  # noqa: E402


async def run_pipeline(
    *,
    dsn: str,
    application_id: str,
    company_id: str,
    reviewer_id: str,
    final: str,
    assigned_agent_id: str = "credit-agent-1",
    session_id: str = "s1",
    model_version: str = "stub",
    regulation_version: str = "2026-Q1",
    docs_dir: str | None = None,
) -> None:
    from datetime import datetime, timezone

    from datagen.minimal_generate import apply_schema, ensure_dummy_documents, seed_application_events, seed_registry
    from src.models.events import ApplicationSubmitted

    apply_schema(dsn)
    seed_registry(dsn, company_id=company_id, company_name=company_id)

    store = EventStore(dsn=dsn)

    docs_root = Path(docs_dir) if docs_dir else (ROOT / "documents")
    ensure_dummy_documents(docs_root, company_id)
    income = docs_root / company_id / "income_statement_2024.pdf"
    balance = docs_root / company_id / "balance_sheet_2024.pdf"
    if not income.exists() or not balance.exists():
        raise SystemExit(f"Missing dummy docs. Expected: {income} and {balance}. Run datagen/minimal_generate.py first.")

    # Ensure the loan stream starts correctly (or seed it if missing).
    stream_id = f"loan-{application_id}"
    ver = await store.stream_version(stream_id)
    if ver == 0:
        await seed_application_events(store, application_id=application_id, applicant_id=company_id)
    else:
        first = (await store.load_stream(stream_id, from_position=0, to_position=1))[0]
        if first.event_type != "ApplicationSubmitted":
            raise SystemExit(
                f"{stream_id} is out-of-order (first event is {first.event_type}). "
                "Use a fresh --app id (e.g. app-2) or reset your database."
            )

    await DocumentProcessingAgent(store=store, agent_type="document-processing", session_id=session_id, model_version=model_version).process_application(
        application_id=application_id,
        assigned_agent_id=assigned_agent_id,
        company_id=company_id,
        income_statement_path=str(income),
        balance_sheet_path=str(balance),
    )

    await CreditAnalysisAgent(store=store, agent_type="credit-analysis", session_id=session_id, model_version=model_version).process_application(
        application_id=application_id,
        company_id=company_id,
    )

    await FraudDetectionAgent(store=store, agent_type="fraud-detection", session_id=session_id, model_version=model_version).process_application(
        application_id=application_id,
        company_id=company_id,
    )

    await ComplianceAgent(
        store=store,
        agent_type="compliance",
        session_id=session_id,
        model_version=model_version,
        regulation_set_version=regulation_version,
    ).process_application(application_id=application_id, company_id=company_id)

    await DecisionOrchestratorAgent(store=store, agent_type="decision-orchestrator", session_id=session_id, model_version=model_version).process_application(
        application_id=application_id
    )

    await handle_human_review_completed(
        HumanReviewCompletedCommand(
            application_id=application_id,
            reviewer_id=reviewer_id,
            override=False,
            final_decision=final,
        ),
        store,
    )

    daemon = ProjectionDaemon(
        store,
        projections=[ApplicationSummaryProjection(), AgentPerformanceLedgerProjection(), ComplianceAuditViewProjection()],
    )
    await daemon.run_once(batch_size=2000)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run full document-to-decision pipeline + human review + projections.")
    parser.add_argument("--dsn", default=os.environ.get("DATABASE_URL"), help="Postgres DSN (or set DATABASE_URL).")
    parser.add_argument("--app", required=True, help="Application id, e.g. app-1")
    parser.add_argument("--company", required=True, help="Company id, e.g. co-1")
    parser.add_argument("--reviewer", default="loan-officer-1")
    parser.add_argument("--final", required=True, choices=["APPROVE", "DECLINE", "REFER"])
    parser.add_argument("--assigned-agent-id", default="credit-agent-1")
    parser.add_argument("--session-id", default="s1")
    parser.add_argument("--model-version", default="stub")
    parser.add_argument("--regulation-version", default="2026-Q1")
    parser.add_argument("--docs-dir", default=None)
    args = parser.parse_args()

    if not args.dsn:
        raise SystemExit("DATABASE_URL (or --dsn) is required")

    import asyncio

    asyncio.run(
        run_pipeline(
            dsn=args.dsn,
            application_id=args.app,
            company_id=args.company,
            reviewer_id=args.reviewer,
            final=args.final,
            assigned_agent_id=args.assigned_agent_id,
            session_id=args.session_id,
            model_version=args.model_version,
            regulation_version=args.regulation_version,
            docs_dir=args.docs_dir,
        )
    )
    print(f"Pipeline complete for {args.app}. Final={args.final}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

