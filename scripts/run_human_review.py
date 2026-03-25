from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.commands.handlers import HumanReviewCompletedCommand, handle_human_review_completed  # noqa: E402
from src.event_store import EventStore  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Record human review and (optionally) finalize the application.")
    parser.add_argument("--dsn", default=os.environ.get("DATABASE_URL"), help="Postgres DSN (or set DATABASE_URL).")
    parser.add_argument("--app", required=True, help="Application id, e.g. app-1")
    parser.add_argument("--reviewer", required=True, help="Reviewer id, e.g. loan-officer-1")
    parser.add_argument("--final", required=True, choices=["APPROVE", "DECLINE", "REFER"])
    parser.add_argument("--override", action="store_true")
    parser.add_argument("--override-reason", default=None)
    args = parser.parse_args()

    if not args.dsn:
        raise SystemExit("DATABASE_URL (or --dsn) is required")

    import asyncio

    async def run() -> None:
        store = EventStore(dsn=args.dsn)
        cmd = HumanReviewCompletedCommand(
            application_id=args.app,
            reviewer_id=args.reviewer,
            override=args.override,
            final_decision=args.final,
            override_reason=args.override_reason,
        )
        await handle_human_review_completed(cmd, store)

    asyncio.run(run())
    print(f"Recorded human review for {args.app}: final={args.final}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

