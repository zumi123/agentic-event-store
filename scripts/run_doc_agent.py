from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.agents.document_processing_agent import DocumentProcessingAgent  # noqa: E402
from src.event_store import EventStore  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Run DocumentProcessingAgent on one application (stub extraction).")
    parser.add_argument("--dsn", default=os.environ.get("DATABASE_URL"), help="Postgres DSN (or set DATABASE_URL).")
    parser.add_argument("--app", required=True, help="Application id, e.g. app-1")
    parser.add_argument("--company", required=True, help="Company id, e.g. co-1")
    parser.add_argument("--assigned-agent-id", default="credit-agent-1")
    parser.add_argument("--session-id", default="s1")
    parser.add_argument("--model-version", default="stub")
    parser.add_argument("--docs-dir", default=str(ROOT / "documents"))
    args = parser.parse_args()

    if not args.dsn:
        raise SystemExit("DATABASE_URL (or --dsn) is required")

    docs_dir = Path(args.docs_dir)
    income = docs_dir / args.company / "income_statement_2024.pdf"
    balance = docs_dir / args.company / "balance_sheet_2024.pdf"
    if not income.exists() or not balance.exists():
        raise SystemExit(f"Missing dummy docs. Expected: {income} and {balance}. Run datagen/minimal_generate.py first.")

    import asyncio

    async def run() -> None:
        store = EventStore(dsn=args.dsn)
        agent = DocumentProcessingAgent(
            store=store,
            agent_type="document-processing",
            session_id=args.session_id,
            model_version=args.model_version,
        )
        await agent.process_application(
            application_id=args.app,
            assigned_agent_id=args.assigned_agent_id,
            company_id=args.company,
            income_statement_path=str(income),
            balance_sheet_path=str(balance),
        )

    asyncio.run(run())
    print(f"Processed {args.app} with DocumentProcessingAgent session={args.session_id}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

