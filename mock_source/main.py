"""Mock DealerSalesCRM REST API.

Serves the generated fake data with realistic REST behaviour: cursor pagination
via `modified_since`, page tokens, and simple bearer-token auth. This is what
the Pipeline Configuration Agent's custom Fivetran connector would call.

Run:  uvicorn mock_source.main:app --port 8001 --reload
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from fastapi import APIRouter, FastAPI, Header, HTTPException, Query
from fastapi.responses import JSONResponse

DATA_DIR = Path(__file__).parent.parent / "data"
PAGE_SIZE = 500
BEARER_TOKEN = os.getenv("MOCK_SOURCE_TOKEN", "demo-token")

# Router (mountable inside the main backend app) + standalone FastAPI app.
router = APIRouter(tags=["mock-source"])
app = FastAPI(title="DealerSalesCRM (mock source)", version="1.0.0")


def _load(name: str) -> list[dict[str, Any]]:
    path = DATA_DIR / f"{name}.json"
    if not path.exists():
        raise HTTPException(500, f"Missing {path}. Run: python mock_source/generate_data.py")
    return json.loads(path.read_text(encoding="utf-8"))


def _paginate(rows: list[dict], cursor_field: str, modified_since: str | None, offset: int) -> dict:
    filtered = [r for r in rows if (modified_since is None or (r.get(cursor_field) or "") > modified_since)]
    filtered.sort(key=lambda r: r.get(cursor_field) or "")
    page = filtered[offset : offset + PAGE_SIZE]
    max_ts = page[-1].get(cursor_field) if page else modified_since
    next_offset = offset + PAGE_SIZE if offset + PAGE_SIZE < len(filtered) else None
    return {
        "data": page,
        "count": len(page),
        "total_matched": len(filtered),
        "max_modified_ts": max_ts,
        "next": next_offset,
    }


def _check_auth(authorization: str | None) -> None:
    if authorization != f"Bearer {BEARER_TOKEN}":
        raise HTTPException(401, "Missing or invalid bearer token")


@router.get("/v1/")
def mock_root() -> dict:
    """Descriptor for the mock source. Kept off `/` so it doesn't shadow the demo UI."""
    return {
        "name": "DealerSalesCRM (mock)",
        "endpoints": ["/v1/customers", "/v1/orders", "/v1/products", "/v1/health"],
        "auth": "Bearer <token>",
        "pagination": "cursor via `modified_since`, page via `offset`",
    }


@router.get("/v1/health")
def health() -> dict:
    return {"status": "ok"}


@router.get("/v1/customers")
def customers(
    modified_since: str | None = Query(None),
    offset: int = Query(0, ge=0),
    authorization: str | None = Header(None),
):
    _check_auth(authorization)
    return JSONResponse(_paginate(_load("customers"), "modified_timestamp", modified_since, offset))


@router.get("/v1/orders")
def orders(
    modified_since: str | None = Query(None),
    offset: int = Query(0, ge=0),
    authorization: str | None = Header(None),
):
    _check_auth(authorization)
    return JSONResponse(_paginate(_load("orders"), "modified_timestamp", modified_since, offset))


@router.get("/v1/products")
def products(
    modified_since: str | None = Query(None),
    offset: int = Query(0, ge=0),
    authorization: str | None = Header(None),
):
    _check_auth(authorization)
    return JSONResponse(_paginate(_load("products"), "modified_timestamp", modified_since, offset))


@router.get("/v1/schema")
def schema() -> dict:
    """Discovery endpoint used by the Pipeline Configuration Agent."""
    return {
        "entities": [
            {
                "name": "customer",
                "primary_key": ["customer_id"],
                "cursor_field": "modified_timestamp",
                "fields": [
                    ["customer_id", "integer"], ["first_name", "string"], ["last_name", "string"],
                    ["email_address", "string"], ["phone_number", "string"],
                    ["street_address", "string"], ["city", "string"], ["state", "string"],
                    ["postal_code", "string"], ["customer_segment", "string"],
                    ["created_timestamp", "timestamp"], ["modified_timestamp", "timestamp"],
                ],
            },
            {
                "name": "order",
                "primary_key": ["order_id"],
                "cursor_field": "modified_timestamp",
                "fields": [
                    ["order_id", "integer"], ["customer_id", "integer"], ["dealer_id", "integer"],
                    ["product_id", "integer"], ["order_date", "date"], ["order_status", "string"],
                    ["order_amount", "number"], ["discount_amount", "number"],
                    ["modified_timestamp", "timestamp"],
                ],
            },
            {
                "name": "product",
                "primary_key": ["product_id"],
                "cursor_field": "modified_timestamp",
                "fields": [
                    ["product_id", "integer"], ["product_name", "string"],
                    ["product_category", "string"], ["product_family", "string"],
                    ["list_price", "number"], ["active_indicator", "boolean"],
                    ["modified_timestamp", "timestamp"],
                ],
            },
        ]
    }


# Attach the router to the standalone app (used when running as its own process).
app.include_router(router)
