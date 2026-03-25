from __future__ import annotations

import pytest

from src.agents.base_agent import BaseApexAgent
from src.event_store import EventStore


@pytest.mark.asyncio
async def test_agent_session_started_is_first_event(dsn: str) -> None:
    store = EventStore(dsn=dsn)
    agent = BaseApexAgent(
        store=store,
        agent_type="test-agent",
        session_id="sess-1",
        model_version="v1",
    )

    await agent.start_session()
    events = await store.load_stream(agent.stream_id)
    assert len(events) == 1
    assert events[0].event_type == "AgentSessionStarted"
    assert events[0].stream_position == 1


