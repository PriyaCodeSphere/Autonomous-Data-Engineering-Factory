"""Data Democratization & Consumption Agent — Cognizant reusable pattern.

After the data product is deployed, this agent makes it consumable by
business users:

- Publishes the certified data product to the Enterprise Data Catalog
  (Atlan-style), including business glossary linkage
- Provisions role-based access packs
- Generates natural-language query examples with LLM
- Emits a business-friendly quickstart guide with LLM
- Registers the semantic layer / metric definitions
"""
from __future__ import annotations

from .base import Agent, RunContext
from .. import llm


QUERY_EXAMPLES_SYSTEM = """You are the Data Democratization Agent. You are asked
to help a business analyst who has never used the Dealer Sales data product
before. Produce 4 realistic natural-language questions they might ask, along
with the SQL each maps to (Snowflake dialect).

Available marts:
  dim_customer(customer_key, first_name_hash, email_hash, city, state, postal_code, customer_segment)
  dim_product(product_key, product_name, product_category, product_family, list_price, active_indicator)
  fct_order(order_key, customer_key, product_key, dealer_key, order_date, order_status,
            order_amount, discount_amount, net_amount, modified_timestamp)

Return strict JSON:
  {"examples": [{"question": "...", "sql": "...", "why": "one-sentence rationale"}]}
Keep SQL under ~6 lines each, readable, no fancy CTEs unless needed.
"""

QUICKSTART_SYSTEM = """You are the Data Democratization Agent. Write a friendly,
one-page 'How to use this data product' quickstart in markdown for a business
analyst who is new to the DealerSalesCRM certified dataset. Cover:
1. What the data represents (2-3 sentences)
2. How to request access (one paragraph)
3. Key metrics available (bulleted list of 4-6 items)
4. Common questions this data can answer (bulleted list of 4-5 items)
5. Who to contact for help
Keep the total under ~35 lines. Return only markdown."""


class DemocratizationAgent(Agent):
    id = "democratize"
    name = "Data Democratization Agent"
    stage = "democratize"

    async def run(self, ctx: RunContext) -> dict:
        self.started(ctx)
        req = ctx.request

        # --- 1. Publish to Enterprise Data Catalog (Atlan) ---
        self.emit(ctx, "Publishing to Enterprise Data Catalog (Atlan)…")
        catalog_entry = _atlan_entry(req)
        p = ctx.write_json(("democratize", "atlan_asset.json"), catalog_entry)
        self.artifact(ctx, "atlan_asset.json", p, preview="")
        self.emit(ctx, "asset published · marked 'Certified' · owner assigned", level="ok")

        # --- 2. Business Glossary linkage ---
        glossary = _glossary_map()
        p = ctx.write_json(("democratize", "glossary_links.json"), glossary)
        self.artifact(ctx, "glossary_links.json", p, preview="")
        self.emit(ctx, f"linked {len(glossary['links'])} columns to business glossary terms", level="ok")

        # --- 3. Access provisioning (role packs) ---
        self.emit(ctx, "Provisioning role-based access packs…")
        access = _access_packs()
        p = ctx.write_json(("democratize", "access_packs.json"), access)
        self.artifact(ctx, "access_packs.json", p, preview="")
        self.emit(ctx, f"{len(access['packs'])} access packs created; auto-approval routing configured", level="ok")

        # --- 4. LLM-authored query examples ---
        self.emit(ctx, "Generating natural-language query examples with LLM…")
        result = llm.complete_json(QUERY_EXAMPLES_SYSTEM,
                                   "Generate query examples now.", temperature=0.3,
                                   max_tokens=1500)
        examples = result.get("examples") or _fallback_examples()
        p = ctx.write_json(("democratize", "query_examples.json"), {"examples": examples})
        self.artifact(ctx, "query_examples.json", p, preview="")

        # --- 5. Semantic layer / metric registry ---
        semantic = _semantic_layer()
        p = ctx.write_text(("democratize", "metrics.yml"), semantic)
        self.artifact(ctx, "metrics.yml", p, preview=semantic)

        # --- 6. Quickstart guide ---
        self.emit(ctx, "Writing 'How to use this data' quickstart…")
        quickstart = llm.complete(QUICKSTART_SYSTEM,
                                  f"Source: {req.get('source_name','DealerSalesCRM')}. "
                                  "Business owner: Dealer Sales COE. Steward: shanmugapriya.kandasamy@cognizant.com.",
                                  temperature=0.4, max_tokens=1000)
        if not quickstart:
            quickstart = _fallback_quickstart()
        p = ctx.write_text(("democratize", "QUICKSTART.md"), quickstart)
        self.artifact(ctx, "QUICKSTART.md", p, preview=quickstart)

        ctx.outputs["democratize"] = {
            "catalog_asset": catalog_entry["asset_id"],
            "glossary_links": len(glossary["links"]),
            "access_packs": len(access["packs"]),
            "query_examples": len(examples),
            "metrics": semantic.count("- name:"),
            "examples": examples,
            "glossary": glossary,
            "access": access,
        }
        self.done(ctx, "Data product democratized · available to business users")
        return ctx.outputs["democratize"]


def _atlan_entry(req: dict) -> dict:
    return {
        "asset_id": "atlan://dealer-sales/certified",
        "name": "Dealer Sales — Certified",
        "type": "Data Product",
        "certification": "Certified",
        "owner": "Dealer Sales COE",
        "steward": "shanmugapriya.kandasamy@cognizant.com",
        "tags": ["dealer-sales", "revenue", "certified", "PII-safe"],
        "sla": {"refresh": req.get("refresh", "30 min"), "lag_alert_minutes": 15},
        "tables": [
            {"name": "dim_customer", "row_count": 5000, "certified": True},
            {"name": "dim_product",  "row_count": 1284, "certified": True},
            {"name": "fct_order",    "row_count": 50000, "certified": True},
        ],
        "connections": {
            "warehouse": "snowflake://DP_PROD.DEALER_SALES.*",
            "bi": "powerbi://Dealer Sales — Certified",
            "reverse_etl": "hightouch://crm_scoring_v2",
        },
        "quality_score": 98,
        "popularity_rank": None,
    }


def _glossary_map() -> dict:
    return {
        "links": [
            {"column": "fct_order.order_amount",    "term": "Gross Order Amount",   "definition": "Total invoiced amount before discounts, in USD."},
            {"column": "fct_order.net_amount",      "term": "Net Order Amount",     "definition": "order_amount minus discount_amount. Basis for revenue reporting."},
            {"column": "fct_order.discount_amount", "term": "Discount",             "definition": "Total dollar discount applied at the order line."},
            {"column": "fct_order.order_status",    "term": "Order Status",         "definition": "Lifecycle state: NEW, PENDING, SHIPPED, DELIVERED, CANCELLED."},
            {"column": "dim_customer.customer_segment", "term": "Customer Segment", "definition": "Homeowner, Contractor, Builder, or Architect."},
            {"column": "dim_product.product_family", "term": "Product Family",      "definition": "Series / family grouping under a Product Category."},
        ],
    }


def _access_packs() -> dict:
    return {
        "packs": [
            {"name": "dealer_sales_read",   "grants": "SELECT on DP_PROD.DEALER_SALES.*",           "audience": "Dealer Sales analysts", "auto_approve": True},
            {"name": "dealer_sales_finance", "grants": "SELECT + financial columns unmasked",       "audience": "Finance team",         "auto_approve": False},
            {"name": "dealer_sales_pii",    "grants": "SELECT + PII columns unmasked",             "audience": "Steward-approved only", "auto_approve": False},
            {"name": "dealer_sales_bi",    "grants": "Power BI dataset access, no direct SQL",     "audience": "Business users",       "auto_approve": True},
        ],
        "request_flow": "self-serve via ServiceNow → auto-approved for _read/_bi; steward-approval for _finance/_pii",
    }


def _semantic_layer() -> str:
    return """# semantic/metrics/dealer_sales.yml
version: 2
metrics:
  - name: total_net_revenue
    label: "Total Net Revenue"
    model: ref('fct_order')
    calculation_method: sum
    expression: net_amount
    filters: [{field: order_status, operator: 'in', value: "'SHIPPED','DELIVERED'"}]

  - name: order_count
    label: "Orders"
    model: ref('fct_order')
    calculation_method: count
    expression: order_key

  - name: average_order_value
    label: "Average Order Value"
    model: ref('fct_order')
    calculation_method: derived
    expression: metric('total_net_revenue') / metric('order_count')

  - name: dealer_count
    label: "Active Dealers"
    model: ref('fct_order')
    calculation_method: count_distinct
    expression: dealer_key
"""


def _fallback_examples() -> list[dict]:
    return [
        {"question": "What were total dealer sales last month by product category?",
         "sql": "SELECT p.product_category, SUM(o.net_amount) AS revenue "
                "FROM fct_order o JOIN dim_product p USING(product_key) "
                "WHERE o.order_date >= DATE_TRUNC('month', CURRENT_DATE) - INTERVAL '1 month' "
                "AND o.order_date < DATE_TRUNC('month', CURRENT_DATE) "
                "GROUP BY 1 ORDER BY revenue DESC;",
         "why": "Category-level sales rollup for the most recent full month."},
        {"question": "Which dealers had the highest cancellation rate this quarter?",
         "sql": "SELECT dealer_key, "
                "SUM(CASE WHEN order_status='CANCELLED' THEN 1 ELSE 0 END)::FLOAT / COUNT(*) AS cancel_rate "
                "FROM fct_order WHERE order_date >= DATE_TRUNC('quarter', CURRENT_DATE) "
                "GROUP BY 1 ORDER BY cancel_rate DESC LIMIT 20;",
         "why": "Quality signal — helps sales ops identify problem dealers."},
        {"question": "Top 10 customers by lifetime net revenue?",
         "sql": "SELECT c.customer_key, SUM(o.net_amount) AS lifetime_net "
                "FROM fct_order o JOIN dim_customer c USING(customer_key) "
                "GROUP BY 1 ORDER BY lifetime_net DESC LIMIT 10;",
         "why": "For account-based marketing and VIP outreach."},
        {"question": "How does average order value differ by customer segment?",
         "sql": "SELECT c.customer_segment, AVG(o.net_amount) AS aov "
                "FROM fct_order o JOIN dim_customer c USING(customer_key) "
                "GROUP BY 1 ORDER BY aov DESC;",
         "why": "Segment-level pricing insight."},
    ]


def _fallback_quickstart() -> str:
    return """# Dealer Sales — Quickstart

This data product contains the certified Dealer Sales dataset — customers,
products, and orders — refreshed every 30 minutes from the source CRM. It's
governed, PII-masked, and safe for broad analyst use.

## Requesting access
Access is self-serve via ServiceNow. Search for **dealer_sales_read** and
click Request. You'll receive Snowflake and Power BI grants automatically —
usually within 5 minutes. For finance or PII-unmasked access, the steward
reviews.

## Key metrics
- **Total Net Revenue** — sum of `net_amount` for shipped/delivered orders
- **Order Count** — count of `order_key`
- **Average Order Value (AOV)** — net revenue ÷ order count
- **Active Dealers** — count of distinct dealers this period
- **Cancellation Rate** — cancelled orders ÷ total orders
- **Customer Lifetime Value** — sum of `net_amount` per customer

## Common questions this data can answer
- Which product categories are growing month-over-month?
- Which dealers have the highest cancellation rate?
- What is my top-10 customers by lifetime revenue?
- How does AOV differ by customer segment (Homeowner / Contractor / Builder / Architect)?
- Is there seasonality in order volume?

## Contact
- Business owner: **Dealer Sales COE** — dealer_sales_coe@enterprise.example.com
- Steward: **shanmugapriya.kandasamy@cognizant.com**
- On-call: **#dealer-sales-oncall** Slack channel
"""
