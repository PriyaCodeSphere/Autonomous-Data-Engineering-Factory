"""Pipeline Configuration Agent — deterministic (calls the mock source /v1/schema)."""
from __future__ import annotations

import httpx

from .base import Agent, RunContext


class PipelineConfigAgent(Agent):
    id = "pipe"
    name = "Pipeline Configuration Agent"
    stage = "pipeline"

    async def run(self, ctx: RunContext) -> dict:
        self.started(ctx)
        self.emit(ctx, f"Discovering schema from {ctx.source_url}/v1/schema …")

        headers = {"Authorization": f"Bearer {ctx.source_token}"}
        async with httpx.AsyncClient(timeout=10) as client:
            try:
                schema_resp = await client.get(f"{ctx.source_url}/v1/schema", headers=headers)
                schema_resp.raise_for_status()
                schema = schema_resp.json()
            except Exception as exc:  # noqa: BLE001
                self.emit(ctx, f"Source schema discovery failed: {exc}", level="err")
                raise

        entities = schema["entities"]
        self.emit(ctx, f"Discovered {len(entities)} entities: {', '.join(e['name'] for e in entities)}", level="ok")

        # --- Fivetran connector YAML ---
        streams_yaml = ""
        for e in entities:
            streams_yaml += (
                f"    - name: {e['name']}\n"
                f"      endpoint: /v1/{e['name']}s\n"
                f"      primary_key: [{', '.join(e['primary_key'])}]\n"
                f"      cursor_field: {e['cursor_field']}\n"
            )

        fivetran_yaml = (
            "# infra/fivetran/dealer-sales-crm.yaml\n"
            "version: 3\n"
            "schema: RAW_DEALER_SALES\n"
            "destination:\n"
            "  type: snowflake\n"
            "  account: enterprise-prod.us-east-2.aws\n"
            "  warehouse: WH_INGEST_XSMALL\n"
            "  database: DP_RAW\n"
            "  role: R_FIVETRAN_LOADER\n"
            "source:\n"
            "  name: dealer_sales_crm\n"
            "  type: rest_api_custom\n"
            "  paginator: cursor\n"
            "  auth:\n"
            "    type: oauth2_client_credentials\n"
            "    secret_ref: arn:aws:secretsmanager:us-east-2:...:dealer-sales/oauth\n"
            "  streams:\n"
            f"{streams_yaml}"
            "schedule:\n"
            "  frequency: 30m\n"
            "  alert_if_lag_over: 15m\n"
            "tags:\n"
            "  domain: dealer_sales\n"
            "  owner: dealer_sales_coe@enterprise.example.com\n"
            "  data_class: mixed_pii_public\n"
        )
        p_yaml = ctx.write_text(("pipeline", "fivetran.yaml"), fivetran_yaml)
        self.artifact(ctx, "fivetran.yaml", p_yaml, preview=fivetran_yaml)

        # --- Landing schema DDL ---
        ddl = (
            "-- infra/snowflake/landing/dealer_sales_crm.sql\n"
            "CREATE SCHEMA IF NOT EXISTS DP_RAW.RAW_DEALER_SALES\n"
            "  WITH MANAGED ACCESS COMMENT = 'Landing schema for DealerSalesCRM';\n\n"
            "GRANT USAGE ON SCHEMA DP_RAW.RAW_DEALER_SALES TO ROLE R_DBT_TRANSFORMER;\n"
            "GRANT USAGE ON SCHEMA DP_RAW.RAW_DEALER_SALES TO ROLE R_DATA_STEWARD;\n\n"
            "ALTER SCHEMA DP_RAW.RAW_DEALER_SALES\n"
            "  SET TAG governance.retention = '7Y',\n"
            "          governance.pii_masked_nonprod = 'true';\n"
        )
        p_ddl = ctx.write_text(("pipeline", "landing_schema.sql"), ddl)
        self.artifact(ctx, "landing_schema.sql", p_ddl, preview=ddl)

        # --- Custom connector.py ---
        py = (
            '"""DealerSalesCRM REST paginator. Cognizant Virtual Data Engineer pattern."""\n'
            "from fivetran_sdk import Connector, Schema, Sync, Cursor\n\n"
            f"STREAMS = {[e['name'] for e in entities]}\n\n"
            "def schema():\n"
            '    return [Schema.stream(s, primary_key=[f"{s}_id"]) for s in STREAMS]\n\n'
            "def update(config, state):\n"
            "    for stream in STREAMS:\n"
            '        cursor = state.get(stream, "1970-01-01T00:00:00Z")\n'
            "        page_url = f\"{config['base_url']}/v1/{stream}s?modified_since={cursor}\"\n"
            "        while page_url:\n"
            "            r = _get(page_url, config[\"token\"])\n"
            "            for row in r[\"data\"]:\n"
            "                yield Sync.upsert(stream, row)\n"
            "            page_url = r.get(\"next\")\n"
            "            cursor = r.get(\"max_modified_ts\", cursor)\n"
            "        yield Cursor.checkpoint({stream: cursor})\n"
        )
        p_py = ctx.write_text(("pipeline", "connector.py"), py)
        self.artifact(ctx, "connector.py", p_py, preview=py)

        # --- Test the actual source pagination ---
        self.emit(ctx, "Verifying pagination with a live call to /v1/customers …")
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(
                f"{ctx.source_url}/v1/customers",
                headers=headers,
                params={"offset": 0},
            )
            r.raise_for_status()
            first_page = r.json()
        self.emit(
            ctx,
            f"OK · first page = {first_page['count']} rows · total = {first_page['total_matched']:,}",
            level="ok",
        )

        ctx.outputs["pipeline"] = {
            "entities": [e["name"] for e in entities],
            "artifacts": ["fivetran.yaml", "landing_schema.sql", "connector.py"],
        }
        self.done(ctx, "Pipeline config drafted and validated")
        return ctx.outputs["pipeline"]
