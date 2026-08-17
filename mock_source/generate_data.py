"""Generate deterministic DealerSalesCRM fake data.

Produces data/customers.json, data/orders.json, data/products.json with
realistic distributions and referential integrity. Deterministic seed=42.

Run:  python mock_source/generate_data.py
"""
from __future__ import annotations

import json
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

from faker import Faker

SEED = 42
N_CUSTOMERS = 5_000
N_ORDERS = 50_000
N_PRODUCTS = 1_284

STATUSES = ["NEW", "PENDING", "SHIPPED", "DELIVERED", "CANCELLED"]
STATUS_WEIGHTS = [0.06, 0.14, 0.30, 0.48, 0.02]
SEGMENTS = ["Homeowner", "Contractor", "Builder", "Architect"]
CATEGORIES = ["Windows", "Patio Doors", "Entry Doors", "Storm Doors", "Accessories"]
FAMILIES = {
    "Windows": ["400 Series", "A-Series", "E-Series", "100 Series"],
    "Patio Doors": ["Frenchwood", "Perma-Shield"],
    "Entry Doors": ["Signature", "Traditional"],
    "Storm Doors": ["3000 Series", "4000 Series"],
    "Accessories": ["Hardware", "Grilles", "Screens"],
}


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _pick_weighted(items, weights):
    return random.choices(items, weights=weights, k=1)[0]


def build_customers(fake: Faker) -> list[dict]:
    customers: list[dict] = []
    for i in range(1, N_CUSTOMERS + 1):
        created = fake.date_time_between(start_date="-3y", end_date="-6M", tzinfo=timezone.utc)
        modified = created + timedelta(days=random.randint(0, 400))
        email = fake.email() if random.random() > 0.014 else None  # ~1.4% nulls
        customers.append({
            "customer_id": i,
            "first_name": fake.first_name(),
            "last_name": fake.last_name(),
            "email_address": email,
            "phone_number": fake.phone_number(),
            "street_address": fake.street_address(),
            "city": fake.city(),
            "state": fake.state_abbr(),
            "postal_code": fake.postcode() if random.random() > 0.005 else None,
            "customer_segment": _pick_weighted(SEGMENTS, [0.55, 0.28, 0.12, 0.05]),
            "created_timestamp": _iso(created),
            "modified_timestamp": _iso(modified),
        })
    return customers


def build_products(fake: Faker) -> list[dict]:
    products: list[dict] = []
    for i in range(1, N_PRODUCTS + 1):
        cat = _pick_weighted(CATEGORIES, [0.42, 0.18, 0.20, 0.10, 0.10])
        fam = random.choice(FAMILIES[cat])
        # lognormal-ish prices
        price = round(max(12.5, min(11250.0, random.lognormvariate(6.0, 1.1))), 2)
        products.append({
            "product_id": i,
            "product_name": f"{fam} {fake.word().title()} {fake.random_int(100, 9999)}",
            "product_category": cat,
            "product_family": fam,
            "list_price": price,
            "active_indicator": random.random() > 0.06,
            "modified_timestamp": _iso(fake.date_time_between(start_date="-2y", end_date="now", tzinfo=timezone.utc)),
        })
    return products


def build_orders(fake: Faker, customer_ids: list[int], product_ids: list[int]) -> list[dict]:
    orders: list[dict] = []
    for i in range(1, N_ORDERS + 1):
        customer_id = random.choice(customer_ids)
        product_id = random.choice(product_ids)
        order_date = fake.date_between(start_date="-2y", end_date="today")
        amount = round(max(5.0, min(98450.0, random.lognormvariate(6.1, 1.3))), 2)
        # 90% no discount, 10% up to 15%
        discount = round(amount * random.uniform(0.02, 0.15), 2) if random.random() < 0.1 else 0.0
        # Inject a tiny fraction of anomalies: discount > amount (matches the demo profile finding)
        if random.random() < 0.0006:
            discount = round(amount * random.uniform(1.01, 1.5), 2)
        orders.append({
            "order_id": i,
            "customer_id": customer_id,
            "dealer_id": random.randint(100, 999),
            "product_id": product_id,
            "order_date": order_date.isoformat(),
            "order_status": _pick_weighted(STATUSES, STATUS_WEIGHTS),
            "order_amount": amount,
            "discount_amount": discount,
            "modified_timestamp": _iso(fake.date_time_between(start_date=order_date, end_date="now", tzinfo=timezone.utc)),
        })
    return orders


def main() -> None:
    random.seed(SEED)
    fake = Faker("en_US")
    Faker.seed(SEED)

    out = Path(__file__).parent.parent / "data"
    out.mkdir(parents=True, exist_ok=True)

    print("[generate] customers …")
    customers = build_customers(fake)
    (out / "customers.json").write_text(json.dumps(customers), encoding="utf-8")

    print("[generate] products …")
    products = build_products(fake)
    (out / "products.json").write_text(json.dumps(products), encoding="utf-8")

    print("[generate] orders …")
    orders = build_orders(fake, [c["customer_id"] for c in customers], [p["product_id"] for p in products])
    (out / "orders.json").write_text(json.dumps(orders), encoding="utf-8")

    print(f"[done] customers={len(customers):,}  products={len(products):,}  orders={len(orders):,}")


if __name__ == "__main__":
    main()
