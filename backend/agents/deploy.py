"""DevOps & Deployment Orchestrator — simulates the Azure DevOps pipeline."""
from __future__ import annotations

import asyncio
import time

from .base import Agent, RunContext


PIPELINE_STAGES = [
    ("build",       "npm ci · dbt deps",                0.6),
    ("static",      "sqlfluff · dbt parse",             0.6),
    ("dq",          "dbt test dev · GE checkpoint",     0.9),
    ("deploy_dev",  "dbt run --target dev",             0.9),
    ("integration", "synth data · smoke tests",         0.9),
    ("promote_qa",  "manual approval",                  0.7),
]

PROD_STAGE = ("promote_prod", "change-advisory approval + prod deploy", 1.2)


class DeployAgent(Agent):
    id = "deploy"
    name = "Deployment Orchestrator"
    stage = "deploy"

    async def run(self, ctx: RunContext) -> dict:
        self.started(ctx)
        self.emit(ctx, "Triggering Azure DevOps pipeline `dealer-sales-crm-cd`…")

        results: list[dict] = []
        for stage_id, label, delay in PIPELINE_STAGES:
            self.emit(ctx, f"stage {stage_id} — {label}", payload={"stage": stage_id, "state": "running"})
            await asyncio.sleep(delay)
            self.emit(ctx, f"stage {stage_id} passed", level="ok",
                      payload={"stage": stage_id, "state": "done"})
            results.append({"stage": stage_id, "status": "done"})

        # Prod approval gate
        decision = await self.wait_for_approval(
            ctx, "deploy",
            title="Change-advisory approval required",
            body="Promote DealerSalesCRM to production. On approval, Fivetran connector "
                 "is enabled, dbt models are promoted to DP_PROD, and Power BI dataset "
                 "is certified.",
        )
        if not decision["approved"]:
            raise RuntimeError("Deployment rejected by CAB.")
        if decision["skipped"]:
            self.emit(ctx, "prod deploy skipped — service stays in QA", level="warn")
            ctx.outputs["deploy"] = {"stages": results, "prod_certified": False, "skipped": True}
            self.done(ctx, "Deployment skipped")
            return ctx.outputs["deploy"]

        self.emit(ctx, f"stage {PROD_STAGE[0]} — {PROD_STAGE[1]}",
                  payload={"stage": PROD_STAGE[0], "state": "running"})
        await asyncio.sleep(PROD_STAGE[2])
        self.emit(ctx, "prod deploy: dbt run --target prod → OK", level="ok")
        self.emit(ctx, "fivetran connector enabled", level="ok")
        self.emit(ctx, "power bi dataset certified", level="ok",
                  payload={"stage": PROD_STAGE[0], "state": "done"})
        results.append({"stage": PROD_STAGE[0], "status": "done"})

        pipeline_yaml = _render_pipeline_yaml()
        p = ctx.write_text(("deploy", "azure-pipelines.yml"), pipeline_yaml)
        self.artifact(ctx, "azure-pipelines.yml", p, preview=pipeline_yaml)

        summary = {"stages": results, "prod_certified": True, "finished_at": int(time.time())}
        ctx.write_json(("deploy", "run.json"), summary)
        ctx.outputs["deploy"] = summary
        self.done(ctx, "DealerSalesCRM is certified in production")
        return summary


def _render_pipeline_yaml() -> str:
    return """# pipelines/azdo-dealer-sales.yml
trigger:
  branches: {include: [main]}
  paths:    {include: [dbt/models/dealer_sales/*, infra/fivetran/dealer-sales-crm.yaml]}

stages:
  - stage: Build
    jobs: [{ job: build, steps: [{script: dbt deps}] }]
  - stage: DQ
    dependsOn: Build
    jobs:
      - job: dq
        steps:
          - script: dbt test --select dealer_sales --target dev
          - script: great_expectations checkpoint run dealer_sales_full
  - stage: DeployDev
    dependsOn: DQ
    jobs: [{ job: deploy, steps: [{script: dbt run --target dev --select dealer_sales}] }]
  - stage: PromoteQA
    dependsOn: DeployDev
    jobs:
      - deployment: promoteQA
        environment: dp-qa
        strategy: {runOnce: {deploy: {steps: [{script: dbt run --target qa --select dealer_sales}]}}}
  - stage: PromotePROD
    dependsOn: PromoteQA
    jobs:
      - deployment: promotePROD
        environment: dp-prod
        strategy: {runOnce: {deploy: {steps: [
          {script: dbt run --target prod --select dealer_sales},
          {script: fivetran-cli connector enable dealer_sales_crm},
          {script: powerbi-cli dataset certify --name 'Dealer Sales - Certified'}
        ]}}}
"""
