"""In-process event bus that fans agent events out to SSE subscribers."""
from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Event:
    run_id: str
    ts: float = field(default_factory=time.time)
    stage: str = ""            # e.g. "plan", "dq"
    agent: str = ""            # display name
    kind: str = "log"          # log | started | done | artifact | approval_required | pipeline_done | error
    level: str = "info"        # info | ok | warn | err
    message: str = ""
    payload: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> str:
        return json.dumps({
            "run_id": self.run_id,
            "ts": self.ts,
            "stage": self.stage,
            "agent": self.agent,
            "kind": self.kind,
            "level": self.level,
            "message": self.message,
            "payload": self.payload,
        }, default=str)


class Bus:
    def __init__(self) -> None:
        self._subs: dict[str, list[asyncio.Queue[Event]]] = {}
        self._history: dict[str, list[Event]] = {}
        self._approvals: dict[str, dict[str, asyncio.Event]] = {}
        self._approval_decisions: dict[str, dict[str, bool]] = {}

    def new_run(self, run_id: str) -> None:
        self._subs.setdefault(run_id, [])
        self._history[run_id] = []
        self._approvals[run_id] = {}
        self._approval_decisions[run_id] = {}

    def emit(self, event: Event) -> None:
        self._history.setdefault(event.run_id, []).append(event)
        for q in self._subs.get(event.run_id, []):
            q.put_nowait(event)

    async def subscribe(self, run_id: str) -> asyncio.Queue[Event]:
        q: asyncio.Queue[Event] = asyncio.Queue()
        # Backfill with history so late subscribers still see everything
        for ev in self._history.get(run_id, []):
            await q.put(ev)
        self._subs.setdefault(run_id, []).append(q)
        return q

    def unsubscribe(self, run_id: str, q: asyncio.Queue[Event]) -> None:
        subs = self._subs.get(run_id, [])
        if q in subs:
            subs.remove(q)

    def request_approval(self, run_id: str, gate_id: str) -> asyncio.Event:
        gate = asyncio.Event()
        self._approvals.setdefault(run_id, {})[gate_id] = gate
        return gate

    def resolve_approval(self, run_id: str, gate_id: str, approved: bool) -> bool:
        gate = self._approvals.get(run_id, {}).get(gate_id)
        if not gate:
            return False
        self._approval_decisions.setdefault(run_id, {})[gate_id] = approved
        gate.set()
        return True

    def approval_decision(self, run_id: str, gate_id: str) -> bool:
        return self._approval_decisions.get(run_id, {}).get(gate_id, False)


bus = Bus()
