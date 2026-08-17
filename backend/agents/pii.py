"""Data Governance & Classification Agent — LLM classifies columns; Python writes SQL."""
from __future__ import annotations

from .base import Agent, RunContext
from .. import llm


SYSTEM_PROMPT = """You are the Data Governance & Classification Agent.
Given a list of columns from a Dealer Sales CRM source, classify each column
into one of: "restricted" (direct PII), "confidential" (financial or otherwise
sensitive), "quasi" (quasi-identifier — city, postal_code fragments, etc.),
or "public" (safe to share widely).

For each column also propose a masking_policy (e.g. "hash_sha256", "redact",
"truncate_to_3_digits", "retain", "mask_in_nonprod") and set powerbi_safe
to true only when the value can safely appear in a broadly-shared BI dataset.

Return strict JSON: {"columns": [ {entity, column, classification, sensitivity,
masking_policy, powerbi_safe, rationale}, ... ]}.
"""


class PIIAgent(Agent):
    id = "pii"
    name = "PII Classification Agent"
    stage = "pii"

    async def run(self, ctx: RunContext) -> dict:
        self.started(ctx)
        entities = ctx.outputs.get("profile", {}).get("entities", {})

        cols_list: list[dict] = []
        for entity, e in entities.items():
            for col in e["columns"].keys():
                cols_list.append({"entity": entity, "column": col})

        self.emit(ctx, f"Classifying {len(cols_list)} columns with LLM…")
        result = llm.complete_json(
            SYSTEM_PROMPT,
            "COLUMNS:\n" + "\n".join(f"- {c['entity']}.{c['column']}" for c in cols_list),
            temperature=0.1,
            max_tokens=4500,
        )
        columns = result.get("columns")
        if not columns:
            self.emit(ctx, "LLM offline — using deterministic classifier", level="warn")
            columns = _fallback_classify(cols_list)

        # Emit a short preview to the stream
        by_class: dict[str, int] = {}
        for c in columns:
            k = c.get("classification", "public")
            by_class[k] = by_class.get(k, 0) + 1
        self.emit(ctx, "classification summary: " + ", ".join(f"{k}={v}" for k, v in by_class.items()), level="ok")

        # Governance approval gate
        approved = await self.wait_for_approval(
            ctx, "pii",
            title="Data steward approval required",
            body="Approve PII classification and masking policies for DealerSalesCRM. "
                 "This applies Snowflake column masking policies and registers catalog tags.",
        )
        if not approved:
            raise RuntimeError("PII classification not approved by steward.")

        # Persist artifacts
        classification = {"columns": columns, "approved": True}
        p1 = ctx.write_json(("pii", "classification.json"), classification)
        self.artifact(ctx, "classification.json", p1, preview="")

        masking_sql = _render_masking_sql(columns)
        p2 = ctx.write_text(("pii", "masking.sql"), masking_sql)
        self.artifact(ctx, "masking.sql", p2, preview=masking_sql)

        catalog = _render_catalog_entry(columns)
        p3 = ctx.write_json(("pii", "catalog.json"), catalog)
        self.artifact(ctx, "catalog.json", p3, preview="")

        ctx.outputs["pii"] = {"columns": columns, "by_class": by_class}
        self.done(ctx, f"{len(columns)} columns classified · steward approved")
        return ctx.outputs["pii"]


def _fallback_classify(cols_list: list[dict]) -> list[dict]:
    known_pii = {"first_name", "last_name", "email_address", "phone_number", "street_address"}
    known_quasi = {"city", "postal_code"}
    known_financial = {"order_amount", "discount_amount", "list_price"}

    def classify(col: str) -> tuple[str, str, str, bool]:
        if col in known_pii:
            return ("restricted", "PII", "hash_sha256", False)
        if col in known_quasi:
            return ("quasi", "Internal", "truncate_to_3_digits" if col == "postal_code" else "retain", False)
        if col in known_financial:
            return ("confidential", "Confidential", "mask_in_nonprod", False)
        return ("public", "Public", "retain", True)

    out = []
    for c in cols_list:
        cls, sens, mask, safe = classify(c["column"])
        out.append({
            "entity": c["entity"], "column": c["column"],
            "classification": cls, "sensitivity": sens,
            "masking_policy": mask, "powerbi_safe": safe,
            "rationale": "Deterministic fallback classifier.",
        })
    return out


def _render_masking_sql(cols: list[dict]) -> str:
    parts = [
        "-- infra/snowflake/masking/dealer_sales.sql",
        "CREATE MASKING POLICY IF NOT EXISTS gov.mask_restricted_hash AS (val string) RETURNS string ->",
        "  CASE",
        "    WHEN CURRENT_ROLE() IN ('R_PII_STEWARD','R_APP_DEALER_SALES') THEN val",
        "    ELSE SHA2(val || SYSTEM$GET_TAG('gov.pii_salt','DEALER_SALES','SCHEMA'), 256)",
        "  END;",
        "",
        "CREATE MASKING POLICY IF NOT EXISTS gov.mask_financial AS (val number) RETURNS number ->",
        "  CASE",
        "    WHEN CURRENT_ROLE() IN ('R_FINANCE','R_APP_DEALER_SALES') THEN val",
        "    WHEN CURRENT_DATABASE() ILIKE 'DP_PROD%' THEN val",
        "    ELSE NULL",
        "  END;",
        "",
    ]
    for c in cols:
        if c["classification"] == "restricted":
            parts.append(
                f"ALTER TABLE DP_RAW.RAW_DEALER_SALES.{c['entity'].upper()} "
                f"MODIFY COLUMN {c['column']} SET MASKING POLICY gov.mask_restricted_hash;"
            )
        elif c["classification"] == "confidential":
            parts.append(
                f"ALTER TABLE DP_RAW.RAW_DEALER_SALES.{c['entity'].upper()} "
                f"MODIFY COLUMN {c['column']} SET MASKING POLICY gov.mask_financial;"
            )
    return "\n".join(parts) + "\n"


def _render_catalog_entry(cols: list[dict]) -> dict:
    by_class: dict[str, list[str]] = {}
    for c in cols:
        by_class.setdefault(c["classification"], []).append(f"{c['entity']}.{c['column']}")
    return {
        "asset": "snowflake://DP_RAW.RAW_DEALER_SALES",
        "domain": "dealer_sales",
        "owner": "dealer_sales_coe@enterprise.example.com",
        "steward": "s.kandasamy@cognizant.com",
        "classifications": by_class,
        "retention": "P7Y",
    }
