"""Cross-stream validation for DecisionGenerated (business rule 6)."""

from __future__ import annotations

from src.event_store import EventStore
from src.models.events import DomainError, StoredEvent

async def assert_contributing_sessions_have_application_work(
    store: EventStore,
    *,
    application_id: str,
    contributing_agent_sessions: list[str],
) -> None:
    for stream_id in contributing_agent_sessions:
        if not stream_id.startswith("agent-"):
            raise DomainError(f"Invalid contributing session stream id: {stream_id!r}")
        events = await store.load_stream(stream_id)
        if not events:
            raise DomainError(f"Contributing session {stream_id} is empty")
        has_context = any(e.event_type == "AgentContextLoaded" for e in events)
        mentions_app = any(_mentions_application(e, application_id) for e in events)
        if not (has_context or mentions_app):
            raise DomainError(
                f"Causal chain: {stream_id} must have AgentContextLoaded or events for application {application_id}"
            )


def _mentions_application(ev: StoredEvent, application_id: str) -> bool:
    p = ev.payload
    if p.get("application_id") == application_id:
        return True
    if ev.event_type == "AgentOutputWritten":
        for w in p.get("events_written") or []:
            if isinstance(w, dict) and w.get("payload", {}).get("application_id") == application_id:
                return True
    return False
