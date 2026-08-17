"""End-to-end test against the consolidated single-service (Render-shape) app.

Uses httpx (installed already) so SSE + parallel POSTs don't deadlock.
"""
from __future__ import annotations

import json
import os
import sys
import time

import httpx

BACKEND = os.getenv("BACKEND_URL", "http://127.0.0.1:8000")
PASSWORD = os.getenv("APP_PASSWORD", "test123")


def main() -> int:
    # Client for cookies + posts; separate client for the SSE stream.
    with httpx.Client(base_url=BACKEND, timeout=15, follow_redirects=False) as c:
        r = c.post("/login", data={"password": PASSWORD})
        print(f"login -> {r.status_code}")
        assert r.status_code in (302, 303), f"unexpected: {r.status_code} {r.text}"

        st = c.get("/api/status").json()
        print("status:", st)
        assert st["llm_online"], "LLM must be online"

        run = c.post("/api/onboard", json={}).json()
        run_id = run["run_id"]
        print(f"run_id = {run_id}")

        stages_seen: set[str] = set()
        start = time.time()

        # SSE stream on a second client (shares cookies via headers)
        cookie_header = "; ".join(f"{k}={v}" for k, v in c.cookies.items())
        with httpx.Client(base_url=BACKEND, timeout=None) as sc:
            with sc.stream("GET", f"/api/runs/{run_id}/events",
                           headers={"Accept": "text/event-stream", "Cookie": cookie_header}) as resp:
                kind = ""
                buf: list[str] = []
                for line in resp.iter_lines():
                    if line == "":
                        if buf:
                            payload = "\n".join(buf)
                            try:
                                ev = json.loads(payload)
                            except json.JSONDecodeError:
                                ev = None
                            if ev and kind != "heartbeat":
                                stage = ev.get("stage", "-")
                                stages_seen.add(stage)
                                prefix = {"ok": "[OK]", "warn": "[!]", "err": "[X]"}.get(ev.get("level", ""), "   ")
                                print(f"  [{stage:<8}] {prefix} {ev.get('agent','')}: {ev.get('message','')}")
                                if kind == "approval_required":
                                    gate = (ev.get("payload") or {}).get("gate_id")
                                    if gate:
                                        c.post(f"/api/runs/{run_id}/approvals/{gate}", json={"approved": True})
                                        print(f"     > auto-approved gate '{gate}'")
                                if kind == "pipeline_done":
                                    dur = time.time() - start
                                    print(f"\n[DONE] {ev.get('message')} · wall={dur:.1f}s")
                                    print(f"stages seen: {sorted(stages_seen)}")
                                    return 0
                                if kind == "error":
                                    print(f"[FAIL] {ev.get('message')}")
                                    return 1
                            kind = ""
                            buf = []
                        continue
                    if line.startswith("event:"):
                        kind = line.split(":", 1)[1].strip()
                    elif line.startswith("data:"):
                        buf.append(line.split(":", 1)[1].strip())
    return 0


if __name__ == "__main__":
    sys.exit(main())
