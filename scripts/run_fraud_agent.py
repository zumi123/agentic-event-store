from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.agents.fraud_detection_agent import FraudDetectionAgent  # noqa: E402
from src.event_store import EventStore  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Run FraudDetectionAgent (stub) on one application.")
    parser.add_argument("--dsn", default=os.environ.get("DATABASE_URL"), help="Postgres DSN (or set DATABASE_URL).")
    parser.add_argument("--app", required=True, help="Application id, e.g. app-1")
    parser.add_argument("--company", required=True, help="Company id, e.g. co-1")
    parser.add_argument("--session-id", default="s1")
    parser.add_argument("--model-version", default="stub")
    args = parser.parse_args()

    if not args.dsn:
        raise SystemExit("DATABASE_URL (or --dsn) is required")

    import asyncio

    async def run() -> None:
        store = EventStore(dsn=args.dsn)
        agent = FraudDetectionAgent(
            store=store,
            agent_type="fraud-detection",
            session_id=args.session_id,
            model_version=args.model_version,
        )
        await agent.process_application(application_id=args.app, company_id=args.company)

    asyncio.run(run())
    print(f"Processed {args.app} with FraudDetectionAgent session={args.session_id}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

