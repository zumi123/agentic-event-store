from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.event_store import EventStore  # noqa: E402
from src.integrity.audit_chain import run_integrity_check  # noqa: E402
from src.mcp.server import LedgerMCP  # noqa: E402


async def export_audit_report(
    *,
    dsn: str,
    application_id: str,
    out_path: str | Path | None = None,
) -> dict[str, Any]:
    store = EventStore(dsn=dsn)
    mcp = LedgerMCP(store)

    summary = await mcp.read_resource(f"ledger://applications/{application_id}")
    compliance = await mcp.read_resource(f"ledger://applications/{application_id}/compliance")
    audit_trail = await mcp.read_resource(f"ledger://applications/{application_id}/audit-trail")

    integrity = await run_integrity_check(store, "loan", application_id)

    report: dict[str, Any] = {
        "generated_at": datetime.now(tz=timezone.utc).isoformat(),
        "application_id": application_id,
        "application_summary": summary,
        "compliance": compliance,
        "audit_trail": audit_trail,
        "integrity_check": asdict(integrity),
    }

    if out_path is not None:
        out = Path(out_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")

    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Export an audit report JSON for one application.")
    parser.add_argument("--dsn", default=os.environ.get("DATABASE_URL"), help="Postgres DSN (or set DATABASE_URL).")
    parser.add_argument("--app", required=True, help="Application id, e.g. app-1")
    parser.add_argument("--out", required=True, help="Output JSON path, e.g. audit/app-1.json")
    args = parser.parse_args()

    if not args.dsn:
        raise SystemExit("DATABASE_URL (or --dsn) is required")

    import asyncio

    asyncio.run(export_audit_report(dsn=args.dsn, application_id=args.app, out_path=args.out))
    print(f"Wrote audit report for {args.app} to {args.out}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

