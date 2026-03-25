from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import psycopg
from psycopg.rows import dict_row


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.event_store import EventStore  # noqa: E402
from src.projections.application_summary import ApplicationSummaryProjection  # noqa: E402
from src.projections.agent_performance import AgentPerformanceLedgerProjection  # noqa: E402
from src.projections.compliance_audit import ComplianceAuditViewProjection  # noqa: E402
from src.projections.daemon import ProjectionDaemon  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Run projections once and print application_summary row.")
    parser.add_argument("--dsn", default=os.environ.get("DATABASE_URL"), help="Postgres DSN (or set DATABASE_URL).")
    parser.add_argument("--app", required=True, help="Application id, e.g. app-1")
    args = parser.parse_args()

    if not args.dsn:
        raise SystemExit("DATABASE_URL (or --dsn) is required")

    import asyncio

    async def run() -> None:
        store = EventStore(dsn=args.dsn)
        daemon = ProjectionDaemon(
            store,
            projections=[
                ApplicationSummaryProjection(),
                AgentPerformanceLedgerProjection(),
                ComplianceAuditViewProjection(),
            ],
        )
        await daemon.run_once(batch_size=1000)

    asyncio.run(run())

    with psycopg.connect(args.dsn, row_factory=dict_row) as conn:
        row = conn.execute(
            "SELECT * FROM application_summary WHERE application_id = %s",
            (args.app,),
        ).fetchone()
        if not row:
            print(f"No application_summary row found for {args.app}. Did you run agents first?")
            return 1
        print(dict(row))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

