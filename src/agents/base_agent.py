from __future__ import annotations

import time
from dataclasses import dataclass

from src.event_store import EventStore
from src.models.events import (
    AgentNodeExecuted,
    AgentSessionCompleted,
    AgentSessionFailed,
    AgentSessionStarted,
    AgentToolCalled,
    BaseEvent,
)


@dataclass
class BaseApexAgent:
    """
    Support-document style base agent.

    Gas Town rule: AgentSessionStarted MUST be the first event in the session stream.
    One event per node: every node appends AgentNodeExecuted at the end.
    """

    store: EventStore
    agent_type: str
    session_id: str
    model_version: str
    context_source: str = "fresh"
    context_token_count: int = 0

    _node_seq: int = 0
    _llm_calls: int = 0
    _tokens_used: int = 0
    _cost_usd: float = 0.0

    @property
    def stream_id(self) -> str:
        return f"agent-{self.agent_type}-{self.session_id}"

    async def start_session(self) -> None:
        # Enforce Gas Town: first event must be session start.
        current = await self.store.stream_version(self.stream_id)
        if current != 0:
            # Session already exists; don't append a duplicate start.
            return
        ev = AgentSessionStarted(
            session_id=self.session_id,
            agent_type=self.agent_type,
            model_version=self.model_version,
            context_source=self.context_source,
            context_token_count=self.context_token_count,
        )
        await self.store.append(
            stream_id=self.stream_id,
            events=[ev],
            expected_version=-1,
            aggregate_type="AgentSession",
        )

    async def _append_session_event(self, ev: BaseEvent) -> None:
        expected = await self.store.stream_version(self.stream_id)
        await self.store.append(
            stream_id=self.stream_id,
            events=[ev],
            expected_version=-1 if expected == 0 else expected,
            aggregate_type="AgentSession",
        )

    async def record_node_execution(
        self,
        *,
        node_name: str,
        input_keys: list[str],
        output_keys: list[str],
        duration_ms: int,
        llm_called: bool = False,
        llm_tokens_input: int | None = None,
        llm_tokens_output: int | None = None,
        llm_cost_usd: float | None = None,
    ) -> None:
        self._node_seq += 1
        if llm_called:
            self._llm_calls += 1
        if llm_tokens_input:
            self._tokens_used += llm_tokens_input
        if llm_tokens_output:
            self._tokens_used += llm_tokens_output
        if llm_cost_usd:
            self._cost_usd += llm_cost_usd

        ev = AgentNodeExecuted(
            node_name=node_name,
            node_sequence=self._node_seq,
            input_keys=input_keys,
            output_keys=output_keys,
            llm_called=llm_called,
            llm_tokens_input=llm_tokens_input,
            llm_tokens_output=llm_tokens_output,
            llm_cost_usd=llm_cost_usd,
            duration_ms=duration_ms,
        )
        await self._append_session_event(ev)

    async def record_tool_call(
        self,
        *,
        tool_name: str,
        tool_input_summary: str,
        tool_output_summary: str,
        duration_ms: int,
    ) -> None:
        ev = AgentToolCalled(
            tool_name=tool_name,
            tool_input_summary=tool_input_summary,
            tool_output_summary=tool_output_summary,
            tool_duration_ms=duration_ms,
        )
        await self._append_session_event(ev)

    async def complete(self, *, next_agent_triggered: str | None = None) -> None:
        ev = AgentSessionCompleted(
            total_nodes_executed=self._node_seq,
            total_llm_calls=self._llm_calls,
            total_tokens_used=self._tokens_used,
            total_cost_usd=self._cost_usd,
            next_agent_triggered=next_agent_triggered,
        )
        await self._append_session_event(ev)

    async def fail(self, *, error_type: str, error_message: str, last_successful_node: str | None = None) -> None:
        ev = AgentSessionFailed(
            error_type=error_type,
            error_message=error_message,
            last_successful_node=last_successful_node,
            recoverable=False,
        )
        await self._append_session_event(ev)


class NodeTimer:
    def __enter__(self) -> "NodeTimer":
        self._t0 = time.time()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:  # noqa: ANN001
        self.duration_ms = int((time.time() - self._t0) * 1000)

