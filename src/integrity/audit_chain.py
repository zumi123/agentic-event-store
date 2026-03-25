from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone

from src.event_store import EventStore
from src.models.events import AuditIntegrityCheckRun, StoredEvent

# Rate limit: at most one integrity run per entity per minute (spec hint).
_last_run_at: dict[tuple[str, str], float] = {}


@dataclass(frozen=True)
class IntegrityCheckResult:
    entity_type: str
    entity_id: str
    events_verified: int
    chain_valid: bool
    tamper_detected: bool
    new_hash: str
    previous_hash: str | None


def _hash_event(ev: StoredEvent) -> str:
    blob = json.dumps(
        {
            "event_id": str(ev.event_id),
            "stream_id": ev.stream_id,
            "stream_position": ev.stream_position,
            "global_position": ev.global_position,
            "event_type": ev.event_type,
            "event_version": ev.event_version,
            "payload": ev.payload,
            "metadata": ev.metadata,
            "recorded_at": ev.recorded_at.isoformat(),
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def _chain_hash(previous_hash: str | None, event_hashes: list[str]) -> str:
    h = hashlib.sha256()
    h.update((previous_hash or "").encode("utf-8"))
    for eh in event_hashes:
        h.update(eh.encode("utf-8"))
    return h.hexdigest()


def full_stream_integrity_hash(events: list[StoredEvent]) -> str:
    """Cumulative SHA-256 chain over all events in order (genesis)."""
    prev: str | None = None
    for ev in events:
        prev = _chain_hash(prev, [_hash_event(ev)])
    return prev or hashlib.sha256(b"").hexdigest()


async def run_integrity_check(
    store: EventStore,
    entity_type: str,
    entity_id: str,
    *,
    role: str | None = None,
    skip_rate_limit: bool = True,
) -> IntegrityCheckResult:
    """Append an AuditIntegrityCheckRun with a full-stream hash chain. Tamper if same-length snapshot hash diverges."""

    key = (entity_type, entity_id)
    now = time.monotonic()
    if not skip_rate_limit:
        last = _last_run_at.get(key)
        if last is not None and (now - last) < 60.0:
            raise RuntimeError("Integrity check rate limited to once per minute per entity")
    _last_run_at[key] = now

    primary_stream = f"{entity_type}-{entity_id}"
    audit_stream = f"audit-{entity_type}-{entity_id}"

    primary_events = await store.load_stream(primary_stream)
    audit_events = await store.load_stream(audit_stream)

    full_hash = full_stream_integrity_hash(primary_events)

    last_check = next((e for e in reversed(audit_events) if e.event_type == "AuditIntegrityCheckRun"), None)
    previous_hash = last_check.payload.get("integrity_hash") if last_check else None

    tamper_detected = False
    if last_check is not None:
        last_count = int(last_check.payload.get("events_verified_count", 0))
        last_hash = last_check.payload.get("integrity_hash")
        if last_count == len(primary_events) and last_hash != full_hash:
            tamper_detected = True

    chain_valid = not tamper_detected

    ev = AuditIntegrityCheckRun(
        entity_id=entity_id,
        check_timestamp=datetime.now(tz=timezone.utc),
        events_verified_count=len(primary_events),
        integrity_hash=full_hash,
        previous_hash=previous_hash,
    )

    audit_version = await store.stream_version(audit_stream)
    await store.append(
        stream_id=audit_stream,
        events=[ev],
        expected_version=-1 if audit_version == 0 else audit_version,
        aggregate_type="AuditLedger",
    )

    return IntegrityCheckResult(
        entity_type=entity_type,
        entity_id=entity_id,
        events_verified=len(primary_events),
        chain_valid=chain_valid,
        tamper_detected=tamper_detected,
        new_hash=full_hash,
        previous_hash=previous_hash,
    )
