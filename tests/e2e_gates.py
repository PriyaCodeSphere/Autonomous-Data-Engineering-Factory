"""End-to-end test covering the new 4-decision approval flow.

Exercises the full pipeline with:
- Approving the new DQ gate
- Skipping the optional biz-validation gate
- Approving PII, PR, and deploy gates
"""
from __future__ import annotations

import json
import os
import sys
import time

import httpx

BACKEND = os.getenv("BACKEND_URL", "http://127.0.0.1:8000")
PASSWORD = os.getenv("APP_PASSWORD", "test123")

# Map each gate to the decision we want to send during the test.
GATE_DECISIONS = {
    "dq":             "approve",
    "pii":            "approve",
    "biz_validation": "skip",     # exercises the optional path
    "review":         "approve",
    "deploy":         "approve",
}


def main() -> int:
    with httpx.Client(base_url=BACKEND, timeout=15, follow_redirects=False) as c:
        r = c.post("/login", data={"password": PASSWORD})
        assert r.status_code in (302, 303), f"login failed: {r.status_code} {r.text}"
        print("login ok")

        run = c.post("/api/onboard", json={}).json()
        run_id = run["run_id"]
        print(f"run_id = {run_id}")

        gates_seen: list[tuple[str, str]] = []
        stages_seen: set[str] = set()
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
                                stage = ev.get("stage", "-")
                                stages_seen.add(stage)
                                if ev.get("level") != "info":
                                    print(f"  [{stage:<14}] {ev.get('level',''):>4} · {ev.get('message','')}")
                                if kind == "approval_required":
                                    gate = (ev.get("payload") or {}).get("gate_id")
                                    optional = (ev.get("payload") or {}).get("optional", False)
                                    decision = GATE_DECISIONS.get(gate, "approve")
                                    gates_seen.append((gate, decision))
                                    print(f"     -> gate '{gate}' (optional={optional}) · decision={decision}")
                                    # urllib rather than httpx here — inside the streaming
                                    # loop, httpx's shared thread state can hang on the response
                                    # read even after the server has finished.
                                    import urllib.request
                                    req = urllib.request.Request(
                                        f"{BACKEND}/api/runs/{run_id}/approvals/{gate}",
                                        data=json.dumps({"decision": decision}).encode(),
                                        headers={"Content-Type": "application/json",
                                                 "Cookie": cookie_header},
                                        method="POST",
                                    )
                                    try:
                                        urllib.request.urlopen(req, timeout=10)
                                    except Exception as e:  # noqa: BLE001
                                        print(f"     [!] approval POST error: {e}")
                                if kind == "pipeline_done":
                                    dur = time.time() - start
                                    print(f"\n[DONE] {ev.get('message')} · wall={dur:.1f}s")
                                    print(f"gates: {gates_seen}")
                                    print(f"stages: {sorted(stages_seen)}")
                                    expected = {"dq", "pii", "biz_validation", "review", "deploy"}
                                    seen_gates = {g for g, _ in gates_seen}
                                    missing = expected - seen_gates
                                    if missing:
                                        print(f"[FAIL] missing gates: {missing}")
                                        return 1
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
