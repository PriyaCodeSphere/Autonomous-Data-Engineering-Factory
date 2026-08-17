"""End-to-end smoke test: start a run, auto-approve gates, print event stream."""
from __future__ import annotations

import json
import sys
import time
import urllib.request

BACKEND = "http://127.0.0.1:8000"


def post(path: str, body: dict | None = None) -> dict:
    data = json.dumps(body or {}).encode("utf-8")
    req = urllib.request.Request(
        BACKEND + path, data=data,
        headers={"Content-Type": "application/json"}, method="POST",
    )
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode("utf-8"))


def main() -> int:
    print("-> POST /api/onboard")
    resp = post("/api/onboard")
    run_id = resp["run_id"]
    print(f"  run_id = {run_id}")

    # Open SSE stream
    req = urllib.request.Request(f"{BACKEND}/api/runs/{run_id}/events",
                                 headers={"Accept": "text/event-stream"})
    stream = urllib.request.urlopen(req, timeout=None)

    seen_stages: set[str] = set()
    start = time.time()
    current_event: str = ""
    buffer_lines: list[str] = []

    while True:
        raw = stream.readline()
        if not raw:
            break
        line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
        if not line:
            if buffer_lines:
                payload = "\n".join(buffer_lines)
                if payload:
                    try:
                        ev = json.loads(payload)
                    except json.JSONDecodeError:
                        ev = None
                    if ev:
                        _handle_event(current_event, ev, seen_stages, run_id)
                        if current_event == "pipeline_done":
                            print(f"[{time.time()-start:.1f}s] DONE — {ev.get('message')}")
                            return 0
                        if current_event == "error":
                            print(f"[error] {ev.get('message')}")
                            return 1
                current_event = ""
                buffer_lines = []
            continue

        if line.startswith("event:"):
            current_event = line.split(":", 1)[1].strip()
        elif line.startswith("data:"):
            buffer_lines.append(line.split(":", 1)[1].strip())

    return 0


def _handle_event(kind: str, ev: dict, seen: set[str], run_id: str) -> None:
    if kind == "heartbeat":
        return
    stage = ev.get("stage", "")
    agent = ev.get("agent", "")
    msg = ev.get("message", "")
    level = ev.get("level", "info")
    prefix = {"ok": "[OK]", "warn": "[!] ", "err": "[X] "}.get(level, "    ")
    print(f"[{stage or '-':<8}] {prefix} {agent}: {msg}")

    if kind == "approval_required":
        gate = ev.get("payload", {}).get("gate_id")
        if gate:
            time.sleep(0.3)
            print(f"  > auto-approving gate '{gate}'")
            post(f"/api/runs/{run_id}/approvals/{gate}", {"approved": True})


if __name__ == "__main__":
    sys.exit(main())
