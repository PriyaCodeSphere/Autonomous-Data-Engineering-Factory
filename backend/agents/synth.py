"""Synthetic Test Data Agent — Faker-based, respects PII classification."""
from __future__ import annotations

import csv
import random
from datetime import timedelta

from faker import Faker

from .base import Agent, RunContext


class SynthAgent(Agent):
    id = "synth"
    name = "Synthetic Test Data Agent"
    stage = "synth"

    async def run(self, ctx: RunContext) -> dict:
        self.started(ctx)
        n_customers, n_orders, n_products = 500, 5_000, 200

        seed = 42
        random.seed(seed)
        fake = Faker("en_US")
        Faker.seed(seed)

        self.emit(ctx, f"Seed={seed} · generating {n_customers} customers · {n_orders} orders · {n_products} products")

        # Customers
        customers_rows = []
        for i in range(1, n_customers + 1):
            customers_rows.append({
                "customer_id": i,
                "first_name": fake.first_name(),
                "last_name":  fake.last_name(),
                "email_address": fake.email(),
                "phone_number":  fake.phone_number(),
                "city":  fake.city(),
                "state": fake.state_abbr(),
                "postal_code": fake.postcode(),
                "customer_segment": random.choice(["Homeowner", "Contractor", "Builder", "Architect"]),
            })
        p = _write_csv(ctx, ("synth", "customer.csv"), customers_rows)
        self.artifact(ctx, "customer.csv", p, preview=_preview_csv(p))

        # Products
        cats = ["Windows", "Patio Doors", "Entry Doors", "Storm Doors", "Accessories"]
        product_rows = []
        for i in range(1, n_products + 1):
            price = round(max(12.5, min(11250.0, random.lognormvariate(6.0, 1.1))), 2)
            product_rows.append({
                "product_id": i,
                "product_name": f"{random.choice(cats)} {fake.word().title()} {fake.random_int(100,9999)}",
                "product_category": random.choice(cats),
                "list_price": price,
                "active_indicator": random.random() > 0.06,
            })
        p = _write_csv(ctx, ("synth", "product.csv"), product_rows)
        self.artifact(ctx, "product.csv", p, preview=_preview_csv(p))

        # Orders
        cids = [c["customer_id"] for c in customers_rows]
        pids = [p["product_id"] for p in product_rows]
        order_rows = []
        for i in range(1, n_orders + 1):
            amount = round(max(5.0, min(98450.0, random.lognormvariate(6.1, 1.3))), 2)
            discount = round(amount * random.uniform(0.02, 0.15), 2) if random.random() < 0.1 else 0.0
            date = fake.date_between(start_date="-1y", end_date="today")
            order_rows.append({
                "order_id": i,
                "customer_id": random.choice(cids),
                "dealer_id":   random.randint(100, 999),
                "product_id":  random.choice(pids),
                "order_date":  date.isoformat(),
                "order_status": random.choices(
                    ["NEW", "PENDING", "SHIPPED", "DELIVERED", "CANCELLED"],
                    weights=[6, 14, 30, 48, 2],
                )[0],
                "order_amount":    amount,
                "discount_amount": discount,
            })
        p = _write_csv(ctx, ("synth", "order.csv"), order_rows)
        self.artifact(ctx, "order.csv", p, preview=_preview_csv(p))

        # Generator config for reproducibility
        cfg = (
            "# testing/synth/dealer_sales.yml\n"
            f"seed: {seed}\n"
            "volume:\n"
            f"  customer: {n_customers}\n"
            f"  order:    {n_orders}\n"
            f"  product:  {n_products}\n"
            "integrity:\n"
            "  order.customer_id: {fk_to: customer.customer_id}\n"
            "  order.product_id:  {fk_to: product.product_id}\n"
            "pii:\n"
            "  strategy: from_faker\n"
            "  locale: en_US\n"
            "  reproducible: true\n"
        )
        p = ctx.write_text(("synth", "generator.yml"), cfg)
        self.artifact(ctx, "generator.yml", p, preview=cfg)

        ctx.outputs["synth"] = {
            "customer_rows": n_customers,
            "order_rows":    n_orders,
            "product_rows":  n_products,
            "seed": seed,
        }
        self.done(ctx, f"generated {n_customers + n_orders + n_products:,} synthetic rows")
        return ctx.outputs["synth"]


def _write_csv(ctx: RunContext, parts: tuple[str, ...], rows: list[dict]):
    p = ctx.artifact_path(*parts)
    if not rows:
        p.write_text("", encoding="utf-8")
        return p
    with p.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    return p


def _preview_csv(path) -> str:
    lines = []
    with path.open("r", encoding="utf-8") as f:
        for i, line in enumerate(f):
            if i >= 6:
                break
            lines.append(line.rstrip())
    return "\n".join(lines)
