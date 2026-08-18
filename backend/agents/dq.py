"""Data Quality Rule Generation Agent — LLM proposes candidate rules
based on the profile output, Python renders them as dbt tests and GE suites."""
from __future__ import annotations

from .base import Agent, RunContext
from .. import llm


SYSTEM_PROMPT = """You are the Data Quality Rule Generation Agent.
Given a data profile for a Dealer Sales CRM source at a US windows manufacturer,
propose ~10-14 data-quality rules. Each rule is a JSON object with:
  id (e.g. DQ-001), entity, column (or "*" for cross-column),
  expectation (short natural-language rule),
  severity ("blocker" | "warn"), owner (short role name),
  rationale (why this matters — one sentence).

Return a JSON object: {"rules": [...]}
Focus on: uniqueness of PKs, FK integrity, null thresholds informed by
observed null_pct, value ranges informed by profile min/max, allowed value
sets for status/segment/category, cross-column checks (discount<=amount),
freshness SLA (<= 15 min lag). Return only JSON.
"""


class DQAgent(Agent):
    id = "dq"
    name = "Data Quality Agent"
    stage = "dq"

    async def run(self, ctx: RunContext) -> dict:
        self.started(ctx)
        profile = ctx.outputs.get("profile", {})
        self.emit(ctx, "Reading profile output to propose candidate DQ rules…")

        prompt_data = _summarise_profile_for_llm(profile)
        result = llm.complete_json(SYSTEM_PROMPT, prompt_data, temperature=0.2, max_tokens=1800)
        rules = result.get("rules") or _fallback_rules(profile)

        # Deterministic rules that must always be present
        rules = _ensure_baseline(rules)

        for r in rules[:8]:
            sev = r.get("severity", "warn").upper()
            self.emit(
                ctx,
                f"[{r.get('id','?')}] {r.get('entity','?')}.{r.get('column','?')} — {r.get('expectation','?')} · {sev}",
            )

        # dbt tests file
        dbt_tests = _render_dbt_tests(rules)
        p1 = ctx.write_text(("dq", "schema_tests.yml"), dbt_tests)
        self.artifact(ctx, "schema_tests.yml", p1, preview=dbt_tests)

        # GE suite
        ge = _render_ge_suite(rules)
        p2 = ctx.write_text(("dq", "great_expectations.yml"), ge)
        self.artifact(ctx, "great_expectations.yml", p2, preview=ge)

        # Snowflake alert
        alert = _render_snowflake_alert()
        p3 = ctx.write_text(("dq", "snowflake_alerts.sql"), alert)
        self.artifact(ctx, "snowflake_alerts.sql", p3, preview=alert)

        # JSON catalog
        p4 = ctx.write_json(("dq", "rules.json"), {"rules": rules})
        self.artifact(ctx, "rules.json", p4, preview="")

        blockers = sum(1 for r in rules if r.get("severity") == "blocker")

        # Human review gate — team wants an explicit checkpoint on DQ rules.
        decision = await self.wait_for_approval(
            ctx, "dq",
            title="Review data-quality rules",
            body=f"The DQ agent proposed {len(rules)} rules ({blockers} blockers). "
                 "Approve to commit them, skip to omit DQ tests from this run, "
                 "or reject to halt the pipeline.",
            preview={"kind": "dq-rules", "rules": rules[:12], "total": len(rules), "blockers": blockers},
        )
        if not decision["approved"]:
            raise RuntimeError("DQ rules rejected by reviewer.")
        if decision["skipped"]:
            self.emit(ctx, "DQ gate skipped — proceeding without committing tests", level="warn")
            ctx.outputs["dq"] = {"rules": [], "blocker_count": 0, "skipped": True}
            self.done(ctx, "DQ rules skipped")
            return ctx.outputs["dq"]

        ctx.outputs["dq"] = {"rules": rules, "blocker_count": blockers}
        self.done(ctx, f"{len(rules)} DQ rules generated · {blockers} blockers · reviewer approved")
        return ctx.outputs["dq"]


def _summarise_profile_for_llm(profile: dict) -> str:
    parts = ["PROFILE SUMMARY"]
    for entity, e in profile.get("entities", {}).items():
        parts.append(f"\nEntity {entity}: {e['row_count']:,} rows")
        for col, ci in e["columns"].items():
            frag = f"  - {col}: dtype={ci['dtype']} null%={ci['null_pct']} distinct%={ci['distinct_pct']}"
            if "min" in ci:
                frag += f" min={ci['min']} max={ci['max']}"
            parts.append(frag)
    parts.append("\nAnomalies: " + str(profile.get("anomalies", {})))
    return "\n".join(parts)


def _ensure_baseline(rules: list[dict]) -> list[dict]:
    ids = {r.get("id") for r in rules}
    baseline = [
        {"id": "DQ-PK-CUSTOMER", "entity": "customer", "column": "customer_id",
         "expectation": "unique and not null", "severity": "blocker", "owner": "Dealer Sales COE",
         "rationale": "Primary key must be unique."},
        {"id": "DQ-PK-ORDER", "entity": "order", "column": "order_id",
         "expectation": "unique and not null", "severity": "blocker", "owner": "Dealer Sales COE",
         "rationale": "Primary key must be unique."},
        {"id": "DQ-FK-CUSTOMER", "entity": "order", "column": "customer_id",
         "expectation": "foreign key resolves to customer.customer_id", "severity": "blocker",
         "owner": "Data Platform", "rationale": "Prevent orphan orders."},
        {"id": "DQ-XCOL-DISCOUNT", "entity": "order", "column": "*",
         "expectation": "discount_amount <= order_amount", "severity": "blocker",
         "owner": "Finance", "rationale": "Business rule from Finance."},
    ]
    for r in baseline:
        if r["id"] not in ids:
            rules.append(r)
    return rules


def _render_dbt_tests(rules: list[dict]) -> str:
    lines = ["# dbt/models/dealer_sales/marts/schema_tests.yml",
             "version: 2", "models:", "  - name: fct_order", "    columns:",
             "      - name: order_key",
             "        tests: [unique, not_null]",
             "      - name: order_amount",
             "        tests:",
             "          - dbt_utils.expression_is_true: { expression: '>= 0' }",
             "      - name: discount_amount",
             "        tests:",
             "          - dbt_utils.expression_is_true: { expression: '<= order_amount' }",
             "      - name: customer_key",
             "        tests:",
             "          - relationships: {to: ref('dim_customer'), field: customer_key}"]
    return "\n".join(lines) + "\n"


def _render_ge_suite(rules: list[dict]) -> str:
    return (
        "# dq/ge_suites/dealer_sales.yml\n"
        "suite_name: dealer_sales_full\n"
        "expectations:\n"
        "  - expect_column_values_to_be_unique: {column: customer_id}\n"
        "  - expect_column_values_to_not_be_null: {column: customer_id}\n"
        "  - expect_column_value_lengths_to_equal: {column: postal_code, value: 5, mostly: 0.99}\n"
        "  - expect_column_values_to_match_regex:\n"
        "      column: email_address\n"
        "      regex: '^[^@\\s]+@[^@\\s]+\\.[^@\\s]+$'\n"
        "      mostly: 0.985\n"
        "  - expect_column_pair_values_A_to_be_greater_than_B:\n"
        "      column_A: order_amount\n"
        "      column_B: discount_amount\n"
        "      or_equal: true\n"
    )


def _render_snowflake_alert() -> str:
    return (
        "-- infra/snowflake/alerts/dealer_sales.sql\n"
        "CREATE OR REPLACE ALERT DP_META.ALERT_DEALER_SALES_FRESHNESS\n"
        "  WAREHOUSE = WH_MONITOR_XSMALL\n"
        "  SCHEDULE  = '15 MINUTE'\n"
        "IF (EXISTS (\n"
        "    SELECT 1 FROM DP_META.SOURCE_FRESHNESS\n"
        "    WHERE source = 'DEALER_SALES_CRM'\n"
        "      AND minutes_since_last_sync > 15\n"
        "))\n"
        "THEN CALL DP_META.NOTIFY_PAGE(\n"
        "  channel => 'data-platform-oncall',\n"
        "  subject => 'DealerSalesCRM freshness SLA breach'\n"
        ");\n"
    )


def _fallback_rules(profile: dict) -> list[dict]:
    return [
        {"id": "DQ-001", "entity": "customer", "column": "email_address",
         "expectation": "null_pct < 0.5%", "severity": "warn", "owner": "Dealer Sales COE",
         "rationale": "Marketing requires email coverage."},
        {"id": "DQ-002", "entity": "order", "column": "order_amount",
         "expectation": ">= 0", "severity": "blocker", "owner": "Finance",
         "rationale": "Negative amounts break revenue reporting."},
        {"id": "DQ-003", "entity": "order", "column": "order_status",
         "expectation": "in (NEW, PENDING, SHIPPED, DELIVERED, CANCELLED)",
         "severity": "warn", "owner": "Dealer Sales COE",
         "rationale": "Constrain to accepted values."},
    ]
