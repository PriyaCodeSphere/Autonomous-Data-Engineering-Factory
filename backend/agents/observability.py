"""Observability & Change-Impact Agent — Cognizant reusable pattern.

Once a data product is in production, this agent watches for schema drift,
DQ regressions, and freshness SLA breaches. When a change is detected it
runs a blast-radius analysis, notifies affected owners, and proposes a
remediation PR.

This module implements a `run_scenario_drift(ctx, column, new_type)` entry
point used by /api/observability/simulate. It streams the same event shape
as the main pipeline so the UI can reuse the SSE consumer.
"""
from __future__ import annotations

import asyncio
from typing import Any

from .base import Agent, RunContext
from .. import llm


ANALYSIS_SYSTEM = """You are the Observability & Change-Impact Agent. A column
in a source system has just changed type. Your job is to write a concise
impact summary — what breaks, what to fix, in what order — for the on-call
data engineer. Keep the tone factual, no marketing. Return strict JSON:

{
  "severity": "low" | "medium" | "high",
  "summary": "one paragraph",
  "affected_dbt_models": ["stg_dealer_sales__customer", ...],
  "affected_bi_reports": [{"name": "...", "reason": "..."}],
  "affected_reverse_etl": ["crm_scoring_v2", ...],
  "recommended_fix": ["step 1", "step 2", ...],
  "auto_fix_available": true | false
}"""


NOTIFY_SYSTEM = """You are the Observability Agent. Draft a Slack message to
the affected on-call channel about a schema-drift incident. Keep it short —
5-8 lines max, markdown formatted for Slack, with a clear action ask.
Return just the message body."""


class ObservabilityAgent(Agent):
    id = "observability"
    name = "Observability Agent"
    stage = "observability"

    async def run_scenario_drift(self, ctx: RunContext, entity: str, column: str,
                                 old_type: str, new_type: str) -> dict:
        self.started(ctx)
        self.emit(ctx, f"scanning source /v1/schema for drift…")
        await asyncio.sleep(0.4)

        # --- 1. Detect drift ---
        self.emit(
            ctx,
            f"DRIFT DETECTED · {entity}.{column} type changed: "
            f"{old_type} -> {new_type}",
            level="warn",
        )
        drift = {
            "entity": entity, "column": column,
            "old_type": old_type, "new_type": new_type,
            "detected_at": _now_iso(),
        }

        # --- 2. LLM impact analysis ---
        self.emit(ctx, "Running blast-radius analysis with LLM…")
        prompt = (
            f"Source column changed: {entity}.{column} "
            f"({old_type} -> {new_type}).\n"
            "Downstream: dbt models in staging + marts, Power BI 'Dealer Sales — Certified', "
            "reverse-ETL 'crm_scoring_v2', semantic-layer metrics for revenue.\n"
            "Analyse the impact now."
        )
        analysis = llm.complete_json(ANALYSIS_SYSTEM, prompt, temperature=0.2, max_tokens=1200)
        if not analysis:
            analysis = _fallback_analysis(entity, column, new_type)

        self.emit(ctx, f"severity: {analysis.get('severity','?').upper()} · "
                       f"{len(analysis.get('affected_dbt_models', []))} dbt models · "
                       f"{len(analysis.get('affected_bi_reports', []))} BI reports affected",
                  level="warn")

        p = ctx.write_json(("observability", "impact.json"),
                           {"drift": drift, "analysis": analysis})
        self.artifact(ctx, "impact.json", p, preview="")

        # --- 3. Notifications ---
        self.emit(ctx, "Drafting notification for #dealer-sales-oncall…")
        slack_msg = llm.complete(
            NOTIFY_SYSTEM,
            f"Column: {entity}.{column} · Old: {old_type} · New: {new_type}\n"
            f"Severity: {analysis.get('severity')}\n"
            f"Affected: {len(analysis.get('affected_dbt_models', []))} models, "
            f"{len(analysis.get('affected_bi_reports', []))} reports.",
            temperature=0.4, max_tokens=400,
        )
        if not slack_msg:
            slack_msg = _fallback_slack(entity, column, new_type, analysis)
        p = ctx.write_text(("observability", "slack.md"), slack_msg)
        self.artifact(ctx, "slack.md", p, preview=slack_msg)
        self.emit(ctx, "notifications sent to owners: Dealer Sales COE, Data Platform, Finance", level="ok")

        # --- 4. Proposed fix PR ---
        self.emit(ctx, "Drafting remediation PR…")
        fix = _render_fix_patch(entity, column, old_type, new_type)
        p = ctx.write_text(("observability", "fix.patch"), fix)
        self.artifact(ctx, "fix.patch", p, preview=fix)

        # Approval gate — human confirms the fix should ship
        decision = await self.wait_for_approval(
            ctx, "observability_fix",
            title="Approve the proposed fix?",
            body=f"The Observability Agent proposes a fix PR for the "
                 f"{entity}.{column} type change. Approve to auto-merge, "
                 f"reject to file a manual ticket instead.",
            optional=False,
            preview={"kind": "drift-fix",
                     "entity": entity, "column": column,
                     "old_type": old_type, "new_type": new_type,
                     "severity": analysis.get("severity", "medium"),
                     "affected_models": analysis.get("affected_dbt_models", []),
                     "affected_bi": analysis.get("affected_bi_reports", [])},
        )
        if not decision["approved"]:
            self.emit(ctx, "Fix rejected — filing manual ticket JIRA-DP-4931 instead", level="warn")
            ctx.outputs["observability"] = {"drift": drift, "analysis": analysis,
                                            "outcome": "manual_ticket_filed"}
            self.done(ctx, "Drift handled · manual ticket filed")
            return ctx.outputs["observability"]
        if decision["skipped"]:
            self.emit(ctx, "Fix deferred — will re-notify at next observability sweep", level="warn")
            ctx.outputs["observability"] = {"drift": drift, "analysis": analysis,
                                            "outcome": "deferred"}
            self.done(ctx, "Drift deferred")
            return ctx.outputs["observability"]

        # --- 5. Merge + redeploy ---
        self.emit(ctx, "Merging fix PR · re-running dbt · updating catalog…")
        await asyncio.sleep(0.6)
        self.emit(ctx, "fix PR merged (SHA e9a2f1)", level="ok")
        self.emit(ctx, "dbt run --select stg_dealer_sales__customer+ → OK", level="ok")
        self.emit(ctx, "Power BI dataset schema refreshed", level="ok")
        self.emit(ctx, "catalog entry updated · glossary link preserved", level="ok")

        ctx.outputs["observability"] = {"drift": drift, "analysis": analysis,
                                        "outcome": "auto_fixed"}
        self.done(ctx, f"drift resolved · {entity}.{column} handled end-to-end")
        return ctx.outputs["observability"]


def _now_iso() -> str:
    import datetime
    return datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


def _fallback_analysis(entity: str, column: str, new_type: str) -> dict:
    return {
        "severity": "medium",
        "summary": f"{entity}.{column} type change to {new_type} affects staging + mart models and BI dataset.",
        "affected_dbt_models": [f"stg_dealer_sales__{entity}",
                                f"dim_{entity}" if entity != "order" else "fct_order"],
        "affected_bi_reports": [
            {"name": "Dealer Sales — Certified", "reason": "column exposed in dataset schema"},
        ],
        "affected_reverse_etl": ["crm_scoring_v2"] if column == "phone_number" else [],
        "recommended_fix": [
            f"Add safe-cast in stg_dealer_sales__{entity}",
            "Update column type in downstream mart",
            "Extend not_null/range tests for the new type",
            "Regenerate Power BI dataset schema",
        ],
        "auto_fix_available": True,
    }


def _fallback_slack(entity: str, column: str, new_type: str, analysis: dict) -> str:
    return f"""*[Observability]* schema drift detected

*Column:* `{entity}.{column}` -> `{new_type}`
*Severity:* {analysis.get('severity','medium').upper()}
*Affected:* {len(analysis.get('affected_dbt_models', []))} dbt models · {len(analysis.get('affected_bi_reports', []))} BI reports

The Observability Agent has drafted a fix PR. A steward needs to approve
before we auto-merge. See the ADEF UI to review.

/cc @dealer-sales-oncall @data-platform-oncall"""


def _render_fix_patch(entity: str, column: str, old_type: str, new_type: str) -> str:
    return f"""diff --git a/dbt/models/dealer_sales/staging/stg_dealer_sales__{entity}.sql b/dbt/models/dealer_sales/staging/stg_dealer_sales__{entity}.sql
index a1b2c3d..e9a2f1f 100644
--- a/dbt/models/dealer_sales/staging/stg_dealer_sales__{entity}.sql
+++ b/dbt/models/dealer_sales/staging/stg_dealer_sales__{entity}.sql
@@ -12,7 +12,7 @@
 renamed as (
     select
         customer_id                       as customer_key,
-        {column},
+        try_cast({column} as {new_type})  as {column},
         city,
         state,
         postal_code,

diff --git a/dbt/models/dealer_sales/schema.yml b/dbt/models/dealer_sales/schema.yml
index 4a5b6c7..b8c9d0e 100644
--- a/dbt/models/dealer_sales/schema.yml
+++ b/dbt/models/dealer_sales/schema.yml
@@ -8,6 +8,10 @@ models:
     columns:
       - name: {column}
         tests: [not_null]
+        meta:
+          type_pre_drift:  "{old_type}"
+          type_post_drift: "{new_type}"
+          drift_detected_at: "{{{{ run_started_at }}}}"
"""
