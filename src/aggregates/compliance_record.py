from __future__ import annotations

from dataclasses import dataclass, field

from src.event_store import EventStore
from src.models.events import DomainError, StoredEvent


@dataclass
class ComplianceRecordAggregate:
    """Regulatory checks for compliance-{application_id}."""

    application_id: str
    version: int = 0
    regulation_set_version: str | None = None
    checks_required: list[str] = field(default_factory=list)
    passed_rule_ids: set[str] = field(default_factory=set)
    failed_rule_ids: set[str] = field(default_factory=set)
    noted_rule_ids: set[str] = field(default_factory=set)
    overall_verdict: str | None = None
    has_hard_block: bool = False

    @classmethod
    async def load(cls, store: EventStore, application_id: str) -> "ComplianceRecordAggregate":
        stream_id = f"compliance-{application_id}"
        events = await store.load_stream(stream_id)
        agg = cls(application_id=application_id)
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

    def _on_ComplianceCheckRequested(self, event: StoredEvent) -> None:
        self.regulation_set_version = event.payload.get("regulation_set_version")
        self.checks_required = list(event.payload.get("checks_required") or [])

    def _on_ComplianceRulePassed(self, event: StoredEvent) -> None:
        rid = event.payload.get("rule_id")
        if rid:
            self.passed_rule_ids.add(rid)

    def _on_ComplianceRuleFailed(self, event: StoredEvent) -> None:
        rid = event.payload.get("rule_id")
        if rid:
            self.failed_rule_ids.add(rid)

    def _on_ComplianceRuleNoted(self, event: StoredEvent) -> None:
        rid = event.payload.get("rule_id")
        if rid:
            self.noted_rule_ids.add(rid)

    def _on_ComplianceCheckCompleted(self, event: StoredEvent) -> None:
        self.overall_verdict = event.payload.get("overall_verdict")
        self.has_hard_block = bool(event.payload.get("has_hard_block", False))

    def mandatory_checks_satisfied(self) -> bool:
        """Rule 5: every required check has a passed rule event; no failed mandatory path."""
        if self.failed_rule_ids:
            return False
        if not self.checks_required:
            return bool(self.passed_rule_ids) or self.overall_verdict == "CLEAR"

        def _satisfied(rid: str) -> bool:
            return rid in self.passed_rule_ids or rid in self.noted_rule_ids

        return all(_satisfied(rid) for rid in self.checks_required)

    def assert_allows_approval(self) -> None:
        if not self.mandatory_checks_satisfied():
            raise DomainError(
                "ApplicationApproved requires all mandatory compliance checks passed in compliance stream"
            )
