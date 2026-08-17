"""Shared agent runtime + context."""
from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..events import Event, bus


ROOT = Path(__file__).resolve().parents[2]
ARTIFACTS = ROOT / "artifacts"


@dataclass
class RunContext:
    run_id: str
    source_url: str
    source_token: str
    request: dict[str, Any]
    artifacts_dir: Path
    outputs: dict[str, Any] = field(default_factory=dict)  # cross-agent shared state

    def artifact_path(self, *parts: str) -> Path:
        p = self.artifacts_dir.joinpath(*parts)
        p.parent.mkdir(parents=True, exist_ok=True)
        return p

    def write_json(self, path_parts: tuple[str, ...] | list[str] | str, obj: Any) -> Path:
        parts = (path_parts,) if isinstance(path_parts, str) else tuple(path_parts)
        p = self.artifact_path(*parts)
        p.write_text(json.dumps(obj, indent=2, default=str), encoding="utf-8")
        return p

    def write_text(self, path_parts: tuple[str, ...] | list[str] | str, text: str) -> Path:
        parts = (path_parts,) if isinstance(path_parts, str) else tuple(path_parts)
        p = self.artifact_path(*parts)
        p.write_text(text, encoding="utf-8")
        return p


class Agent:
    """Base class: subclasses set `id`, `name`, `stage`, and implement `run()`.

    The stage id matches the sidebar stage in index.html (plan/pipeline/dbt/...).
    """

    id: str = ""
    name: str = ""
    stage: str = ""

    async def run(self, ctx: RunContext) -> dict[str, Any]:  # pragma: no cover - abstract
        raise NotImplementedError

    # -- helpers exposed to subclasses --
    def emit(self, ctx: RunContext, message: str, *, level: str = "info", kind: str = "log", payload: dict | None = None) -> None:
        bus.emit(Event(
            run_id=ctx.run_id,
            stage=self.stage,
            agent=self.name,
            kind=kind,
            level=level,
            message=message,
            payload=payload or {},
        ))

    def started(self, ctx: RunContext) -> None:
        self.emit(ctx, f"{self.name} started", kind="started")

    def done(self, ctx: RunContext, message: str, payload: dict | None = None) -> None:
        self.emit(ctx, message, kind="done", level="ok", payload=payload or {})

    def artifact(self, ctx: RunContext, label: str, path: Path, preview: str = "", extra: dict | None = None) -> None:
        rel = str(path.relative_to(ctx.artifacts_dir))
        payload = {"label": label, "path": rel, "size": path.stat().st_size, "preview": preview[:5000]}
        if extra:
            payload.update(extra)
        self.emit(ctx, f"artifact · {label}", kind="artifact", payload=payload)

    async def wait_for_approval(self, ctx: RunContext, gate_id: str, title: str, body: str) -> bool:
        gate = bus.request_approval(ctx.run_id, gate_id)
        self.emit(ctx, title, kind="approval_required", level="warn",
                  payload={"gate_id": gate_id, "title": title, "body": body})
        await gate.wait()
        approved = bus.approval_decision(ctx.run_id, gate_id)
        self.emit(ctx,
                  f"{title} — {'approved' if approved else 'declined'}",
                  kind="log",
                  level="ok" if approved else "warn")
        return approved

    async def slight_pause(self, seconds: float = 0.15) -> None:
        await asyncio.sleep(seconds)
