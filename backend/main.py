"""FastAPI backend that serves the HTML demo and drives the agent pipeline.

Single-service architecture:
- Serves the HTML at /
- Mounts the mock DealerSalesCRM endpoints under /v1/* (was formerly on port 8001)
- Exposes /api/onboard, SSE stream, and approval endpoints
- Optional session-cookie password gate (env: APP_PASSWORD)

This lets the whole demo deploy as a single Render/Railway/Fly.io web service.
"""
from __future__ import annotations

import asyncio
import os
import secrets
from pathlib import Path
from typing import Any

import json

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request, Form, Response
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from sse_starlette.sse import EventSourceResponse

from .events import bus
from .orchestrator import start_run
from .llm import is_online
from mock_source.main import router as mock_router

# ---- boot -----------------------------------------------------------------

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env", override=False)

APP_PASSWORD = os.getenv("APP_PASSWORD", "").strip()
SESSION_COOKIE = "adef_session"
_SESSION_TOKEN = secrets.token_urlsafe(24)  # rotates on every server restart

app = FastAPI(title="Autonomous Data Engineering Factory")

# Mount the mock DealerSalesCRM source directly on this app. In hosted mode
# the pipeline agents call the mock via localhost on the same port.
app.include_router(mock_router)

# Serve artifacts as static files for click-through inspection
if (ROOT / "artifacts").exists():
    app.mount("/artifacts", StaticFiles(directory=str(ROOT / "artifacts")), name="artifacts")


# ---- password gate --------------------------------------------------------

_PUBLIC_PATHS = {"/login", "/healthz"}


def _valid_session(req: Request) -> bool:
    if not APP_PASSWORD:
        return True  # no password configured -> open access
    return req.cookies.get(SESSION_COOKIE) == _SESSION_TOKEN


_LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost"}


@app.middleware("http")
async def _auth_gate(request: Request, call_next):
    if not APP_PASSWORD:
        return await call_next(request)
    path = request.url.path
    if path in _PUBLIC_PATHS or path.startswith("/static"):
        return await call_next(request)
    # The mock DealerSalesCRM endpoints (/v1/*) enforce their own bearer-token
    # auth. Skip the session gate for loopback callers so the in-process agents
    # can reach them; external callers still get blocked.
    if path.startswith("/v1/"):
        host = (request.client.host if request.client else "") or ""
        if host in _LOOPBACK_HOSTS:
            return await call_next(request)
    if _valid_session(request):
        return await call_next(request)
    if path.startswith("/api/") or path.startswith("/v1/"):
        return JSONResponse({"error": "authentication required"}, status_code=401)
    return RedirectResponse("/login")


LOGIN_PAGE = """<!doctype html>
<html><head><meta charset="utf-8"><title>Cognizant ADEF · Sign in</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
:root{--bg:#070b14;--card:#111b30;--line:#243456;--ink:#f4f7ff;--brand:#3b82f6;--cog:#7c3aed}
*{box-sizing:border-box}
html,body{margin:0;background:radial-gradient(1200px 800px at 15% -10%,rgba(59,130,246,.16),transparent 60%),radial-gradient(1000px 800px at 110% 10%,rgba(124,58,237,.12),transparent 60%),var(--bg);color:var(--ink);font-family:Inter,system-ui,sans-serif;min-height:100vh;display:grid;place-items:center}
.card{background:linear-gradient(180deg,rgba(23,35,60,.85),rgba(17,27,48,.85));border:1px solid var(--line);border-radius:16px;padding:32px 30px;width:380px;box-shadow:0 24px 60px rgba(0,0,0,.5)}
h1{margin:0 0 4px;font-size:20px}
p{margin:0 0 20px;color:#8695b8;font-size:13px}
label{display:block;font-size:11px;letter-spacing:.6px;text-transform:uppercase;color:#8695b8;margin-bottom:6px;font-weight:600}
input[type=password]{width:100%;padding:11px 14px;background:#070b14;border:1px solid var(--line);color:var(--ink);border-radius:10px;font-size:14px;font-family:inherit}
input[type=password]:focus{outline:2px solid var(--brand);border-color:var(--brand)}
button{width:100%;margin-top:14px;padding:11px;background:linear-gradient(135deg,var(--brand),var(--cog));border:0;color:#fff;border-radius:10px;font-weight:600;cursor:pointer;font-size:14px;box-shadow:0 6px 18px rgba(59,130,246,.35)}
.err{color:#fca5a5;font-size:12px;margin-top:10px}
.brand{display:flex;align-items:center;gap:10px;margin-bottom:22px}
.logo{width:34px;height:34px;border-radius:9px;background:linear-gradient(135deg,var(--brand),var(--cog));display:grid;place-items:center;font-weight:800}
.foot{margin-top:18px;font-size:11px;color:#5c6a8c;text-align:center}
</style></head>
<body>
<form class="card" method="post" action="/login">
  <div class="brand"><div class="logo">C</div><div><strong>Cognizant ADEF</strong><br><span style="color:#8695b8;font-size:11px">Autonomous Data Engineering Factory</span></div></div>
  <h1>Access this demo</h1>
  <p>Enter the demo password to continue. Ask the person who shared this link.</p>
  <label>Password</label>
  <input type="password" name="password" autofocus autocomplete="current-password"/>
  __ERROR__
  <button type="submit">Sign in</button>
  <div class="foot">Cognizant Agentic Engineering Excellence Platform</div>
</form>
</body></html>"""


@app.get("/login", response_class=HTMLResponse)
async def login_get(request: Request):
    if _valid_session(request):
        return RedirectResponse("/")
    error = ""
    if request.query_params.get("bad"):
        error = '<div class="err">Wrong password. Try again.</div>'
    return HTMLResponse(LOGIN_PAGE.replace("__ERROR__", error))


@app.post("/login")
async def login_post(password: str = Form(...)):
    if not APP_PASSWORD or not secrets.compare_digest(password.strip(), APP_PASSWORD):
        return RedirectResponse("/login?bad=1", status_code=302)
    resp = RedirectResponse("/", status_code=302)
    resp.set_cookie(SESSION_COOKIE, _SESSION_TOKEN, httponly=True, samesite="lax", max_age=8 * 3600)
    return resp


# ---- app routes -----------------------------------------------------------

@app.get("/healthz")
async def healthz() -> dict:
    return {"status": "ok"}


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(str(ROOT / "index.html"))


@app.get("/api/status")
async def status() -> dict[str, Any]:
    return {
        "llm_online": is_online(),
        "backend": "adef",
        "hosted": bool(os.getenv("RENDER") or os.getenv("PORT")),
    }


@app.post("/api/onboard")
async def onboard(req: Request) -> dict[str, Any]:
    body: dict = {}
    try:
        body = await req.json()
    except Exception:  # noqa: BLE001
        pass
    run_id = await start_run(body)
    return {"run_id": run_id}


@app.get("/api/runs/{run_id}/events")
async def stream(run_id: str, request: Request) -> EventSourceResponse:
    if run_id not in bus._subs and run_id not in bus._history:  # noqa: SLF001
        raise HTTPException(404, "run not found")

    async def gen():
        q = await bus.subscribe(run_id)
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    ev = await asyncio.wait_for(q.get(), timeout=15.0)
                    yield {"event": ev.kind, "data": ev.to_json()}
                except asyncio.TimeoutError:
                    yield {"event": "heartbeat", "data": "{}"}
        finally:
            bus.unsubscribe(run_id, q)

    return EventSourceResponse(gen())


@app.post("/api/runs/{run_id}/approvals/{gate_id}")
async def approve(run_id: str, gate_id: str, req: Request) -> dict[str, Any]:
    body: dict[str, Any] = {}
    try:
        raw = await req.body()
        if raw:
            body = json.loads(raw)
    except Exception:  # noqa: BLE001
        body = {}
    # Accept either the new `decision` field ("approve"|"skip"|"reject") or the
    # legacy `approved` boolean for backwards compatibility.
    decision = str(body.get("decision", "")).strip().lower()
    if not decision:
        decision = "approve" if body.get("approved", True) else "reject"
    if decision not in {"approve", "skip", "reject"}:
        raise HTTPException(400, f"invalid decision '{decision}'")
    ok = bus.resolve_approval(run_id, gate_id, decision)
    if not ok:
        raise HTTPException(404, "unknown gate for run")
    return {"ok": True, "decision": decision}


async def _has_body(req: Request) -> bool:
    try:
        raw = await req.body()
        return bool(raw)
    except Exception:  # noqa: BLE001
        return False


if __name__ == "__main__":  # pragma: no cover
    import uvicorn
    port = int(os.getenv("PORT") or os.getenv("BACKEND_PORT", "8000"))
    uvicorn.run("backend.main:app", host="0.0.0.0", port=port, reload=False)
