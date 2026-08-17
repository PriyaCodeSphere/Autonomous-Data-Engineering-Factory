"""Solution Planning Agent — LLM-driven."""
from __future__ import annotations

from .base import Agent, RunContext
from .. import llm


SYSTEM_PROMPT = """You are the Solution Planning Agent inside the Cognizant Agentic
Engineering Excellence Platform. You are asked to onboard a new data source into
the enterprise data platform.

Return a strict JSON object with keys:
  - "summary": one-sentence executive summary
  - "reasoning": array of short bullet strings capturing the planner's thought steps
  - "tasks": ordered array of {id, title, agent, depends_on: [ids], parallel_group?}
  - "approval_gates": array of gate ids (e.g. ["pii","review","deploy"])
  - "risks": array of short strings describing key risks
  - "estimated_wall_time_seconds": integer

Available agents (Cognizant reusable patterns):
  pipe (Pipeline Configuration), dbt (dbt Macro Factory), profile (Data Profiling),
  dq (Data Quality Rule Generation), pii (Data Governance & Classification),
  synth (Testing / Synthetic Data), docs (Documentation & Metadata),
  review (Coding & Code Review), deploy (DevOps & Deployment).

Prefer real referential integrity (dbt depends on pipe; dq depends on profile;
docs depends on dbt+pii; review depends on docs; deploy depends on review).
Keep the task list under 18 items. Use snake_case ids.
"""


class PlannerAgent(Agent):
    id = "planner"
    name = "Solution Planning Agent"
    stage = "plan"

    async def run(self, ctx: RunContext) -> dict:
        self.started(ctx)
        self.emit(ctx, "Parsing intake payload…")
        req = ctx.request
        self.emit(ctx, f"source={req['source_name']} · type={req['source_type']} · entities={len(req['entities'])}")

        prompt = (
            "REQUEST\n"
            f"{req['business_ask']}\n\n"
            "STRUCTURED METADATA\n"
            f"source_name: {req['source_name']}\n"
            f"source_type: {req['source_type']}\n"
            f"refresh:     {req['refresh']}\n"
            f"target:      {req['target']}\n"
            f"transform:   {req['transform']}\n"
            f"ingest:      {req['ingest']}\n"
            f"bi:          {req['bi']}\n"
            f"cicd:        {req['cicd']}\n"
            f"entities:    {', '.join(e['name'] for e in req['entities'])}\n"
            "Return the JSON plan now."
        )

        plan = llm.complete_json(SYSTEM_PROMPT, prompt, temperature=0.2, max_tokens=1500)

        if not plan:
            self.emit(ctx, "LLM offline — using deterministic fallback plan", level="warn")
            plan = _fallback_plan()

        # Basic normalisation
        plan.setdefault("approval_gates", ["pii", "review", "deploy"])
        plan.setdefault("tasks", [])
        plan.setdefault("risks", [])

        # Stream reasoning
        for line in plan.get("reasoning", [])[:12]:
            self.emit(ctx, f"↳ {line}")

        self.emit(ctx, f"{len(plan.get('tasks', []))} tasks planned across "
                       f"{len({t.get('agent') for t in plan.get('tasks', []) if t.get('agent')})} agents",
                  level="ok")

        p = ctx.write_json(("plan", "plan.json"), plan)
        self.artifact(ctx, "plan.json", p, preview=p.read_text(encoding="utf-8"))

        ctx.outputs["plan"] = plan
        self.done(ctx, "Handing off to Pipeline Configuration Agent")
        return plan


def _fallback_plan() -> dict:
    return {
        "summary": "Onboard DealerSalesCRM into the enterprise data platform end-to-end.",
        "reasoning": [
            "Metadata completeness check: PASS.",
            "Detected PII columns in Customer entity — governance gate required.",
            "Detected financial columns in Order/Product — masking in non-prod.",
            "Selected 10 reusable Cognizant patterns.",
            "Decomposed into ordered task graph with 3 parallel branches.",
            "Attached enterprise policies: dbt style guide v2.1, retention 7y.",
        ],
        "tasks": [
            {"id": "pipe_config",       "title": "Generate Fivetran connector + landing schema",  "agent": "pipe",    "depends_on": []},
            {"id": "profile_sample",    "title": "Profile source columns and detect anomalies",   "agent": "profile", "depends_on": ["pipe_config"]},
            {"id": "dbt_sources",       "title": "Emit dbt sources.yml",                          "agent": "dbt",     "depends_on": ["pipe_config"]},
            {"id": "dbt_staging",       "title": "Emit dbt staging models (3)",                   "agent": "dbt",     "depends_on": ["dbt_sources"]},
            {"id": "dbt_marts",         "title": "Emit dbt mart models + schema tests",           "agent": "dbt",     "depends_on": ["dbt_staging"]},
            {"id": "dq_rules",          "title": "Generate DQ rules from profile output",         "agent": "dq",      "depends_on": ["profile_sample", "dbt_marts"]},
            {"id": "pii_classify",      "title": "Classify PII and propose masking policies",     "agent": "pii",     "depends_on": ["profile_sample"]},
            {"id": "synth_fixtures",    "title": "Generate masked synthetic fixtures",            "agent": "synth",   "depends_on": ["pii_classify"]},
            {"id": "docs_readme",       "title": "Write README + catalog + lineage",              "agent": "docs",    "depends_on": ["dbt_marts", "pii_classify"]},
            {"id": "pr_review",         "title": "Open PR and run policy checks",                 "agent": "review",  "depends_on": ["docs_readme", "dq_rules", "synth_fixtures"]},
            {"id": "deploy_prod",       "title": "Run Azure DevOps pipeline to production",       "agent": "deploy",  "depends_on": ["pr_review"]},
        ],
        "approval_gates": ["pii", "review", "deploy"],
        "risks": [
            "PII columns in Customer require steward approval before publish.",
            "Rate limit for source REST API not declared — will be inferred and monitored.",
            "Historical currency data untyped in source — DQ will add ≥ 0 checks.",
        ],
        "estimated_wall_time_seconds": 460,
    }
