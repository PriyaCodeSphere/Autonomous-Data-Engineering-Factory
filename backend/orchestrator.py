"""Orchestrator: composes the agent pipeline for a single onboarding run."""
from __future__ import annotations

import asyncio
import os
import shutil
import time
import uuid
from pathlib import Path
from typing import Any

from .agents.base import RunContext, ARTIFACTS
from .agents.planner import PlannerAgent
from .agents.pipeline import PipelineConfigAgent
from .agents.dbt_factory import DbtFactoryAgent
from .agents.profiler import ProfilerAgent
from .agents.dq import DQAgent
from .agents.pii import PIIAgent
from .agents.synth import SynthAgent
from .agents.docs import DocsAgent
from .agents.review import ReviewAgent
from .agents.deploy import DeployAgent
from .events import Event, bus


DEFAULT_REQUEST: dict[str, Any] = {
    "business_ask": (
        "Onboard the DealerSalesCRM source into the Andersen data platform. "
        "Create ingestion configurations, Snowflake structures, dbt models, "
        "data quality controls, PII classifications, documentation, lineage, "
        "test data, and deployment artifacts."
    ),
    "source_name": "DealerSalesCRM",
    "source_type": "REST API",
    "refresh":     "30 minutes",
    "target":      "Snowflake",
    "transform":   "dbt",
    "ingest":      "Fivetran",
    "bi":          "Power BI",
    "cicd":        "Azure DevOps",
    "business_owner": "Dealer Sales COE",
    "entities": [
        {"name": "customer"}, {"name": "order"}, {"name": "product"},
    ],
}


def _new_run_dir(run_id: str) -> Path:
    d = ARTIFACTS / run_id
    if d.exists():
        shutil.rmtree(d)
    d.mkdir(parents=True, exist_ok=True)
    return d


async def start_run(request_override: dict[str, Any] | None = None) -> str:
    run_id = "run-" + uuid.uuid4().hex[:10]
    bus.new_run(run_id)
    request = {**DEFAULT_REQUEST, **(request_override or {})}
    # In hosted mode the mock source is mounted on the same FastAPI app,
    # so we point at localhost on Render's assigned $PORT. Locally we default
    # to the standalone mock on :8001 (from start.ps1). Override via SOURCE_URL.
    default_source = (
        f"http://localhost:{os.getenv('PORT')}" if os.getenv("PORT")
        else f"http://localhost:{os.getenv('MOCK_SOURCE_PORT', '8001')}"
    )
    ctx = RunContext(
        run_id=run_id,
        source_url=os.getenv("SOURCE_URL", default_source),
        source_token=os.getenv("MOCK_SOURCE_TOKEN", "demo-token"),
        request=request,
        artifacts_dir=_new_run_dir(run_id),
    )
    ctx.outputs["plan"] = {"entities": [e["name"] for e in request["entities"]]}

    bus.emit(Event(run_id=run_id, stage="intake", agent="Portal",
                   kind="started", level="info",
                   message=f"Onboarding request received · source={request['source_name']}"))

    asyncio.create_task(_run_pipeline(ctx))
    return run_id


async def _run_pipeline(ctx: RunContext) -> None:
    t0 = time.time()
    agents = [
        PlannerAgent(),
        PipelineConfigAgent(),
        DbtFactoryAgent(),
        ProfilerAgent(),
        DQAgent(),
        PIIAgent(),
        SynthAgent(),
        DocsAgent(),
        ReviewAgent(),
        DeployAgent(),
    ]
    try:
        for agent in agents:
            await agent.run(ctx)
    except Exception as exc:  # noqa: BLE001
        import traceback
        tb = traceback.format_exc()
        print("[orchestrator] pipeline failed:\n" + tb, flush=True)
        bus.emit(Event(run_id=ctx.run_id, stage="error", agent="Orchestrator",
                       kind="error", level="err",
                       message=f"Pipeline failed: {exc}",
                       payload={"traceback": tb.splitlines()[-6:]}))
        return

    dur = round(time.time() - t0, 1)
    bus.emit(Event(run_id=ctx.run_id, stage="product", agent="Orchestrator",
                   kind="pipeline_done", level="ok",
                   message=f"Data product certified in {dur}s",
                   payload={"duration_seconds": dur}))
