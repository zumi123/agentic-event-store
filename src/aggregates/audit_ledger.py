from __future__ import annotations

from dataclasses import dataclass

from src.event_store import EventStore
from src.models.events import StoredEvent


@dataclass
class AuditLedgerAggregate:
    """Append-only audit-{entity_type}-{entity_id} — integrity checkpoints only."""

    entity_type: str
    entity_id: str
    version: int = 0
    last_events_verified: int = 0
    last_integrity_hash: str | None = None

    @classmethod
    async def load(cls, store: EventStore, entity_type: str, entity_id: str) -> "AuditLedgerAggregate":
        stream_id = f"audit-{entity_type}-{entity_id}"
        events = await store.load_stream(stream_id)
        agg = cls(entity_type=entity_type, entity_id=entity_id)
        for ev in events:
            agg._apply(ev)
        return agg

    def _apply(self, event: StoredEvent) -> None:
        handler = getattr(self, f"_on_{event.event_type}", None)
        if handler:
            handler(event)
        self.version = event.stream_position

    def expected_version_for_append(self) -> int:
        return -1 if self.version == 0 else self.version

    def _on_AuditIntegrityCheckRun(self, event: StoredEvent) -> None:
        self.last_events_verified = int(event.payload.get("events_verified_count", 0))
        self.last_integrity_hash = event.payload.get("integrity_hash")
