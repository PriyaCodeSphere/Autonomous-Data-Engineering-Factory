"""dbt Macro Factory Agent — deterministic templates + optional LLM descriptions."""
from __future__ import annotations

from .base import Agent, RunContext
from .. import llm


SOURCES_YML = """# dbt/models/dealer_sales/sources.yml
version: 2
sources:
  - name: dealer_sales_crm
    database: DP_RAW
    schema: RAW_DEALER_SALES
    loader: fivetran
    loaded_at_field: _fivetran_synced
    freshness:
      warn_after:  {count: 45, period: minute}
      error_after: {count: 90, period: minute}
    tables:
      - name: customer
        columns:
          - name: customer_id
            tests: [unique, not_null]
          - name: email_address
            meta: {pii: true, classification: restricted}
      - name: order
        columns:
          - name: order_id
            tests: [unique, not_null]
          - name: customer_id
            tests:
              - relationships:
                  to:    source('dealer_sales_crm','customer')
                  field: customer_id
      - name: product
        columns:
          - name: product_id
            tests: [unique, not_null]
"""

STG_CUSTOMER = """-- dbt/models/dealer_sales/staging/stg_dealer_sales__customer.sql
{{ config(
    materialized='incremental',
    unique_key='customer_id',
    on_schema_change='append_new_columns',
    tags=['dealer_sales','staging','pii']
) }}

with src as (
    select * from {{ source('dealer_sales_crm','customer') }}
    {% if is_incremental() %}
      where modified_timestamp > (select coalesce(max(modified_timestamp),'1900-01-01') from {{ this }})
    {% endif %}
),
renamed as (
    select
        customer_id                       as customer_key,
        {{ hash_pii('first_name') }}      as first_name_hash,
        {{ hash_pii('last_name') }}       as last_name_hash,
        {{ hash_pii('email_address') }}   as email_hash,
        {{ hash_pii('phone_number') }}    as phone_hash,
        {{ hash_pii('street_address') }}  as street_hash,
        city, state, postal_code, customer_segment,
        created_timestamp, modified_timestamp,
        current_timestamp() as _stg_loaded_at
    from src
)
select * from renamed
"""

STG_ORDER = """-- dbt/models/dealer_sales/staging/stg_dealer_sales__order.sql
{{ config(materialized='incremental', unique_key='order_id',
          tags=['dealer_sales','staging']) }}

with src as (
    select * from {{ source('dealer_sales_crm','order') }}
    {% if is_incremental() %}
      where modified_timestamp > (select coalesce(max(modified_timestamp),'1900-01-01') from {{ this }})
    {% endif %}
)
select
    order_id, customer_id, dealer_id, product_id,
    order_date, order_status,
    order_amount, discount_amount,
    order_amount - discount_amount as net_amount,
    modified_timestamp
from src
"""

STG_PRODUCT = """-- dbt/models/dealer_sales/staging/stg_dealer_sales__product.sql
{{ config(materialized='table', tags=['dealer_sales','staging']) }}

select
    product_id      as product_key,
    product_name,
    product_category,
    product_family,
    list_price,
    active_indicator,
    modified_timestamp
from {{ source('dealer_sales_crm','product') }}
"""

DIM_CUSTOMER = """-- dbt/models/dealer_sales/marts/dim_customer.sql
{{ config(materialized='table', cluster_by=['customer_key'], tags=['dealer_sales','marts']) }}

with s as (select * from {{ ref('stg_dealer_sales__customer') }})
select
    s.customer_key, s.first_name_hash, s.last_name_hash, s.email_hash,
    s.city, s.state, s.postal_code, s.customer_segment,
    s.created_timestamp, s.modified_timestamp
from s
"""

DIM_PRODUCT = """-- dbt/models/dealer_sales/marts/dim_product.sql
{{ config(materialized='table', tags=['dealer_sales','marts']) }}
select * from {{ ref('stg_dealer_sales__product') }}
"""

FCT_ORDER = """-- dbt/models/dealer_sales/marts/fct_order.sql
{{ config(materialized='incremental', unique_key='order_key',
          tags=['dealer_sales','marts','fact']) }}

with o as (select * from {{ ref('stg_dealer_sales__order') }}),
     c as (select customer_key from {{ ref('dim_customer') }}),
     p as (select product_key   from {{ ref('dim_product') }})
select
    o.order_id     as order_key,
    o.customer_id  as customer_key,
    o.dealer_id    as dealer_key,
    o.product_id   as product_key,
    o.order_date, o.order_status,
    o.order_amount, o.discount_amount, o.net_amount,
    o.modified_timestamp
from o
join c on o.customer_id = c.customer_key
join p on o.product_id  = p.product_key
"""

MACRO_HASH = """-- dbt/macros/hash_pii.sql (Cognizant reusable macro)
{% macro hash_pii(col, salt_ref='enterprise_pii_salt') %}
  case
    when {{ col }} is null then null
    else sha2( concat({{ col }}, {{ var(salt_ref) }}), 256 )
  end
{% endmacro %}
"""


SYSTEM_PROMPT = """You are the Documentation half of the dbt Macro Factory Agent.
Given the source entities, produce a single JSON object where each key is a
dbt model name (dim_customer / dim_product / fct_order / stg_dealer_sales__customer
/ stg_dealer_sales__order / stg_dealer_sales__product) and each value is a
1–2-sentence business-friendly description. Return only JSON."""


class DbtFactoryAgent(Agent):
    id = "dbt"
    name = "dbt Macro Factory Agent"
    stage = "dbt"

    async def run(self, ctx: RunContext) -> dict:
        self.started(ctx)

        # Files we always write from templates
        files = [
            (("dbt", "sources.yml"),                                                                     SOURCES_YML),
            (("dbt", "staging", "stg_dealer_sales__customer.sql"),                                       STG_CUSTOMER),
            (("dbt", "staging", "stg_dealer_sales__order.sql"),                                          STG_ORDER),
            (("dbt", "staging", "stg_dealer_sales__product.sql"),                                        STG_PRODUCT),
            (("dbt", "marts",  "dim_customer.sql"),                                                      DIM_CUSTOMER),
            (("dbt", "marts",  "dim_product.sql"),                                                       DIM_PRODUCT),
            (("dbt", "marts",  "fct_order.sql"),                                                         FCT_ORDER),
            (("dbt", "macros", "hash_pii.sql"),                                                          MACRO_HASH),
        ]
        for parts, content in files:
            p = ctx.write_text(parts, content)
            self.emit(ctx, f"generated {'/'.join(parts)}", level="ok")
            self.artifact(ctx, parts[-1], p, preview=content)

        # LLM-authored descriptions
        self.emit(ctx, "Requesting business-friendly model descriptions from LLM…")
        req = ctx.request
        descs = llm.complete_json(
            SYSTEM_PROMPT,
            "Entities: " + ", ".join(e["name"] for e in req["entities"]) +
            f"\nDomain: dealer sales at {req.get('business_owner','the enterprise')}.",
            temperature=0.3,
        )
        if not descs:
            descs = {
                "dim_customer": "Cleansed customer dimension with PII hashed. One row per customer.",
                "dim_product":  "Product catalog dimension with active flag and list price.",
                "fct_order":    "Order fact at order_id grain, joined to conformed customer and product dimensions.",
            }
        schema_yml = _render_schema_yml(descs)
        p = ctx.write_text(("dbt", "schema.yml"), schema_yml)
        self.artifact(ctx, "schema.yml", p, preview=schema_yml)

        ctx.outputs["dbt"] = {
            "models": ["stg_dealer_sales__customer", "stg_dealer_sales__order", "stg_dealer_sales__product",
                       "dim_customer", "dim_product", "fct_order"],
            "descriptions": descs,
        }
        self.done(ctx, "dbt project ready · 8 files generated")
        return ctx.outputs["dbt"]


def _render_schema_yml(descs: dict) -> str:
    def desc(name: str) -> str:
        return descs.get(name, "").replace('"', "'")

    return f"""# dbt/models/dealer_sales/schema.yml (LLM-authored descriptions)
version: 2
models:
  - name: dim_customer
    description: "{desc('dim_customer')}"
    columns:
      - name: customer_key
        tests: [unique, not_null]
      - name: email_hash
        meta: {{pii: true, classification: restricted}}
        tests: [not_null]

  - name: dim_product
    description: "{desc('dim_product')}"
    columns:
      - name: product_key
        tests: [unique, not_null]

  - name: fct_order
    description: "{desc('fct_order')}"
    tests:
      - dbt_utils.expression_is_true:
          expression: "order_amount >= 0"
      - dbt_utils.expression_is_true:
          expression: "discount_amount <= order_amount"
    columns:
      - name: order_key
        tests: [unique, not_null]
      - name: customer_key
        tests:
          - relationships:
              to: ref('dim_customer')
              field: customer_key
"""
