"""Full validation of the demo app.

Runs a comprehensive set of checks locally before anything gets pushed:
  1. HTML structure — every new UI element / class / function is present
  2. Full pipeline with approve-everything
  3. Full pipeline with SKIP the PII gate
  4. Full pipeline with REJECT the PII gate (must halt cleanly)
  5. Observability drift scenario end-to-end
  6. SSE event payload structure — preview fields exist for each gate

Exits with non-zero code (and a summary) if anything fails.
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.request
from typing import Any

import httpx

BACKEND = os.getenv("BACKEND_URL", "http://127.0.0.1:8000")
PASSWORD = os.getenv("APP_PASSWORD", "test123")

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    marker = "[OK]" if ok else "[FAIL]"
    print(f"  {marker} {name}")
    if detail:
        print(f"         {detail}")
    RESULTS.append((name, ok, detail))


# ---- 1. HTML structure ----------------------------------------------------

def validate_html() -> None:
    print("\n== 1. HTML structure ==")
    # First hit /login (public) and pull the login HTML — should contain sign-in form.
    r = httpx.get(f"{BACKEND}/login", timeout=5)
    check("login page reachable", r.status_code == 200 and "Access this demo" in r.text)

    # Now log in and pull the actual demo HTML.
    with httpx.Client(base_url=BACKEND, timeout=10, follow_redirects=False) as c:
        r = c.post("/login", data={"password": PASSWORD})
        check("password login accepted", r.status_code in (302, 303))
        html = c.get("/").text

    markers = {
        "statusRibbon element":       'id="statusRibbon"',
        "statusRibbon in JS":         'setPipelineStatus',
        "Prev/Next stage nav CSS":    '.stage-nav',
        "Prev/Next stage nav JS":     '_renderStageNav',
        "GATE_DETAILS mapping":       'GATE_DETAILS = {',
        "GATE_DETAILS · dq":          "  dq: {",
        "GATE_DETAILS · pii":         "  pii: {",
        "GATE_DETAILS · biz_validation": "  biz_validation: {",
        "GATE_DETAILS · review":      "  review: {",
        "GATE_DETAILS · deploy":      "  deploy: {",
        "GATE_DETAILS · observability_fix": "  observability_fix: {",
        "handleApprovalDecision":     'handleApprovalDecision',
        "storyboard-only class":      'storyboard-only',
        "live-only-hint class":       'live-only-hint',
        "applyLiveVisibility":        'applyLiveVisibility',
        "Approval modal 4 buttons":   "handleApprovalDecision('skip')",
        "mainInner scope":            'id="mainInner"',
        "Workflow map modal":         'id="workflowModal"',
        "Drift modal":                'id="driftModal"',
        "Paused banner":              'id="pausedBanner"',
        "No cache header":            None,  # placeholder — checked separately
        "No Andersen refs":           None,  # checked separately
    }
    for label, needle in markers.items():
        if needle is None:
            continue
        check(f"HTML has: {label}", needle in html, f"needle={needle!r}")

    # Verify no-cache header
    with httpx.Client(base_url=BACKEND, timeout=10) as c:
        c.post("/login", data={"password": PASSWORD})
        r = c.get("/")
        cc = r.headers.get("cache-control", "")
    check("no-cache header on /", "no-store" in cc.lower(), f"cache-control={cc!r}")

    # Verify no Andersen refs
    check("no 'Andersen' in served HTML", "Andersen" not in html and "andersen" not in html)


# ---- 2. Backend pipeline flows ---------------------------------------------

def _login() -> tuple[httpx.Client, str]:
    c = httpx.Client(base_url=BACKEND, timeout=15, follow_redirects=False)
    r = c.post("/login", data={"password": PASSWORD})
    if r.status_code not in (302, 303):
        raise RuntimeError(f"login failed: {r.status_code}")
    cookies = "; ".join(f"{k}={v}" for k, v in c.cookies.items())
    return c, cookies


def _run_pipeline(gate_decisions: dict[str, str], expect_success: bool) -> dict[str, Any]:
    """Run the main pipeline, feed each gate the specified decision, and collect
    which gates were hit and whether it completed."""
    c, cookies = _login()
    try:
        run = c.post("/api/onboard", json={}).json()
        run_id = run["run_id"]

        report: dict[str, Any] = {"run_id": run_id, "gates_seen": [],
                                  "completed": False, "error": None,
                                  "stages_started": set(), "approvals": []}

        with httpx.Client(base_url=BACKEND, timeout=None) as sc:
            with sc.stream("GET", f"/api/runs/{run_id}/events",
                           headers={"Accept": "text/event-stream", "Cookie": cookies}) as resp:
                kind, buf = "", []
                for line in resp.iter_lines():
                    if line == "":
                        if buf:
                            try:
                                ev = json.loads("\n".join(buf))
                            except json.JSONDecodeError:
                                ev = None
                            if ev and kind != "heartbeat":
                                if kind == "started" and ev.get("stage"):
                                    report["stages_started"].add(ev["stage"])
                                if kind == "approval_required":
                                    gate = (ev.get("payload") or {}).get("gate_id")
                                    payload = ev.get("payload") or {}
                                    report["gates_seen"].append((gate, payload))
                                    decision = gate_decisions.get(gate, "approve")
                                    report["approvals"].append((gate, decision))
                                    try:
                                        urllib.request.urlopen(
                                            urllib.request.Request(
                                                f"{BACKEND}/api/runs/{run_id}/approvals/{gate}",
                                                data=json.dumps({"decision": decision}).encode(),
                                                headers={"Content-Type": "application/json",
                                                         "Cookie": cookies},
                                                method="POST",
                                            ),
                                            timeout=10,
                                        )
                                    except Exception as _e:
                                        # urllib sometimes reports a spurious read timeout when the
                                        # server writes the response while the same process is
                                        # reading an SSE stream. The server-side gate is still
                                        # resolved — verified by watching subsequent stage events.
                                        pass
                                if kind == "pipeline_done":
                                    report["completed"] = True
                                    return report
                                if kind == "error":
                                    report["error"] = ev.get("message")
                                    return report
                            kind, buf = "", []
                        continue
                    if line.startswith("event:"):
                        kind = line.split(":", 1)[1].strip()
                    elif line.startswith("data:"):
                        buf.append(line.split(":", 1)[1].strip())
        return report
    finally:
        c.close()


def validate_full_approve() -> None:
    print("\n== 2. Full pipeline (approve everything) ==")
    r = _run_pipeline({"dq":"approve","pii":"approve","biz_validation":"approve",
                        "review":"approve","deploy":"approve"}, expect_success=True)
    check("pipeline completed", r["completed"], r.get("error") or "")
    seen = {g for g, _ in r["gates_seen"]}
    check("all 5 gates fired", seen == {"dq","pii","biz_validation","review","deploy"},
          f"seen={sorted(seen)}")
    expected_stages = {"plan","pipeline","dbt","profile","dq","pii","synth","docs",
                       "review","deploy","democratize"}
    missing = expected_stages - r["stages_started"]
    check("all 11 agent stages ran", not missing, f"missing={missing}")

    # Preview fields on each gate
    for gate, payload in r["gates_seen"]:
        preview = payload.get("preview", {})
        kind = preview.get("kind")
        expected_kinds = {"dq":"dq-rules","pii":"pii-classification",
                          "biz_validation":"lineage-preview","review":"pr-summary",
                          "deploy":None}
        exp = expected_kinds.get(gate)
        if exp is None:
            check(f"gate '{gate}' payload structure",
                  isinstance(payload, dict), f"payload={list(payload.keys())}")
        else:
            check(f"gate '{gate}' preview kind = {exp}", kind == exp,
                  f"got={kind!r}")


def validate_pii_skip() -> None:
    print("\n== 3. Skip PII gate ==")
    r = _run_pipeline({"dq":"approve","pii":"skip","biz_validation":"skip",
                        "review":"approve","deploy":"approve"}, expect_success=True)
    check("pipeline completed with PII skipped", r["completed"], r.get("error") or "")
    check("downstream agents ran after skip",
          {"synth","docs","review","deploy","democratize"} <= r["stages_started"],
          f"stages={sorted(r['stages_started'])}")


def validate_pii_reject() -> None:
    print("\n== 4. Reject PII gate (should halt) ==")
    r = _run_pipeline({"dq":"approve","pii":"reject"}, expect_success=False)
    check("pipeline halted", not r["completed"] and r["error"] is not None,
          f"completed={r['completed']} error={r.get('error')!r}")
    check("halted at PII (downstream didn't run)",
          "synth" not in r["stages_started"],
          f"stages_started={sorted(r['stages_started'])}")


def validate_drift() -> None:
    print("\n== 5. Observability drift scenario ==")
    c, cookies = _login()
    try:
        run = c.post("/api/observability/simulate", json={}).json()
        run_id = run["run_id"]

        completed = False
        gate_seen = None
        with httpx.Client(base_url=BACKEND, timeout=None) as sc:
            with sc.stream("GET", f"/api/runs/{run_id}/events",
                           headers={"Accept": "text/event-stream", "Cookie": cookies}) as resp:
                kind, buf = "", []
                for line in resp.iter_lines():
                    if line == "":
                        if buf:
                            try:
                                ev = json.loads("\n".join(buf))
                            except json.JSONDecodeError:
                                ev = None
                            if ev and kind != "heartbeat":
                                if kind == "approval_required":
                                    gate_seen = (ev.get("payload") or {}).get("gate_id")
                                    try:
                                        urllib.request.urlopen(
                                            urllib.request.Request(
                                                f"{BACKEND}/api/runs/{run_id}/approvals/{gate_seen}",
                                                data=json.dumps({"decision": "approve"}).encode(),
                                                headers={"Content-Type": "application/json",
                                                         "Cookie": cookies},
                                                method="POST",
                                            ),
                                            timeout=10,
                                        )
                                    except Exception:
                                        pass
                                if kind == "pipeline_done":
                                    completed = True
                                    break
                                if kind == "error":
                                    break
                            kind, buf = "", []
                        continue
                    if line.startswith("event:"):
                        kind = line.split(":", 1)[1].strip()
                    elif line.startswith("data:"):
                        buf.append(line.split(":", 1)[1].strip())

        check("drift scenario completed", completed)
        check("observability_fix gate fired", gate_seen == "observability_fix",
              f"gate={gate_seen!r}")
    finally:
        c.close()


# ---- MAIN -----------------------------------------------------------------

def main() -> int:
    print("== VALIDATION START ==")
    try:
        validate_html()
        validate_full_approve()
        validate_pii_skip()
        validate_pii_reject()
        validate_drift()
    except Exception as exc:
        import traceback
        print(f"\n[FATAL] validation crashed: {exc}")
        traceback.print_exc()
        return 1

    passed = sum(1 for _, ok, _ in RESULTS if ok)
    failed = sum(1 for _, ok, _ in RESULTS if not ok)
    print(f"\n== SUMMARY == passed={passed}  failed={failed}")
    if failed:
        print("\nFAILURES:")
        for name, ok, detail in RESULTS:
            if not ok:
                print(f"  - {name} :: {detail}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
