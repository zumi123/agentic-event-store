from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.event_store import EventStore  # noqa: E402
from src.mcp.server import LedgerMCP  # noqa: E402


def _dump(obj: Any) -> str:
    return json.dumps(obj, indent=2, sort_keys=True, default=str)


def main() -> int:
    parser = argparse.ArgumentParser(description="Show application summary + audit trail via MCP resources.")
    parser.add_argument("--dsn", default=os.environ.get("DATABASE_URL"), help="Postgres DSN (or set DATABASE_URL).")
    parser.add_argument("--app", required=True, help="Application id, e.g. app-1")
    parser.add_argument("--audit-limit", type=int, default=50, help="Max number of audit-trail events to print.")
    parser.add_argument("--as-of", default=None, help="Optional ISO timestamp for as-of compliance read.")
    args = parser.parse_args()

    if not args.dsn:
        raise SystemExit("DATABASE_URL (or --dsn) is required")

    as_of = datetime.fromisoformat(args.as_of) if args.as_of else None

    import asyncio

    async def run() -> int:
        mcp = LedgerMCP(EventStore(dsn=args.dsn))
        summary = await mcp.read_resource(f"ledger://applications/{args.app}")
        compliance = await mcp.read_resource(f"ledger://applications/{args.app}/compliance", as_of=as_of)
        audit = await mcp.read_resource(f"ledger://applications/{args.app}/audit-trail")

        if summary is None:
            print(f"No application_summary row for {args.app}. Run projections first.")
            return 1

        print("=== application_summary ===")
        print(_dump(summary))
        print()

        print("=== compliance ===")
        print(_dump(compliance))
        print()

        print("=== audit-trail (loan stream) ===")
        audit_list = audit if isinstance(audit, list) else []
        trimmed = audit_list[-args.audit_limit :] if args.audit_limit > 0 else audit_list
        print(_dump(trimmed))
        return 0

    return asyncio.run(run())


if __name__ == "__main__":
    raise SystemExit(main())

