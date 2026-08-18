"""End-to-end test of the observability schema-drift scenario."""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.request

import httpx

BACKEND = os.getenv("BACKEND_URL", "http://127.0.0.1:8000")
PASSWORD = os.getenv("APP_PASSWORD", "test123")


def main() -> int:
    with httpx.Client(base_url=BACKEND, timeout=15, follow_redirects=False) as c:
        r = c.post("/login", data={"password": PASSWORD})
        assert r.status_code in (302, 303), f"login failed: {r.status_code}"
        print("login ok")

        run = c.post("/api/observability/simulate", json={}).json()
        run_id = run["run_id"]
        print(f"observability run_id = {run_id}")

        cookie_header = "; ".join(f"{k}={v}" for k, v in c.cookies.items())
        start = time.time()
        with httpx.Client(base_url=BACKEND, timeout=None) as sc:
            with sc.stream("GET", f"/api/runs/{run_id}/events",
                           headers={"Accept": "text/event-stream", "Cookie": cookie_header}) as resp:
                kind, buf = "", []
                for line in resp.iter_lines():
                    if line == "":
                        if buf:
                            try:
                                ev = json.loads("\n".join(buf))
                            except json.JSONDecodeError:
                                ev = None
                            if ev and kind != "heartbeat":
                                lvl = ev.get("level", "")
                                prefix = {"ok": "[OK]", "warn": "[!]", "err": "[X]"}.get(lvl, "   ")
                                if lvl != "info":
                                    print(f"  {prefix} {ev.get('agent',''):<24} {ev.get('message','')}")
                                if kind == "approval_required":
                                    gate = (ev.get("payload") or {}).get("gate_id")
                                    print(f"     -> gate '{gate}' · decision=approve")
                                    urllib.request.urlopen(
                                        urllib.request.Request(
                                            f"{BACKEND}/api/runs/{run_id}/approvals/{gate}",
                                            data=json.dumps({"decision": "approve"}).encode(),
                                            headers={"Content-Type": "application/json",
                                                     "Cookie": cookie_header},
                                            method="POST",
                                        ),
                                        timeout=10,
                                    )
                                if kind == "pipeline_done":
                                    dur = time.time() - start
                                    print(f"\n[DONE] {ev.get('message')} · wall={dur:.1f}s")
                                    return 0
                                if kind == "error":
                                    print(f"[FAIL] {ev.get('message')}")
                                    return 1
                            kind, buf = "", []
                        continue
                    if line.startswith("event:"):
                        kind = line.split(":", 1)[1].strip()
                    elif line.startswith("data:"):
                        buf.append(line.split(":", 1)[1].strip())
    return 0


if __name__ == "__main__":
    sys.exit(main())
