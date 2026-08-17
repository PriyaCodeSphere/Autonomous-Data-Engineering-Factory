"""Data Profiling & Validation Agent — pandas, no LLM.

Reads the actual DealerSalesCRM data via the mock source's paginated API,
computes column statistics, detects anomalies, and publishes the profile
JSON that downstream agents (DQ, PII, Synth) consume.
"""
from __future__ import annotations

from typing import Any

import httpx
import pandas as pd

from .base import Agent, RunContext


PAGE_SIZE = 500


async def _fetch_all(source_url: str, token: str, entity: str) -> pd.DataFrame:
    headers = {"Authorization": f"Bearer {token}"}
    rows: list[dict[str, Any]] = []
    offset = 0
    async with httpx.AsyncClient(timeout=30) as client:
        while True:
            r = await client.get(
                f"{source_url}/v1/{entity}s",
                headers=headers,
                params={"offset": offset},
            )
            r.raise_for_status()
            body = r.json()
            rows.extend(body["data"])
            if body.get("next") is None:
                break
            offset = body["next"]
    return pd.DataFrame(rows)


class ProfilerAgent(Agent):
    id = "profile"
    name = "Data Profiling Agent"
    stage = "profile"

    async def run(self, ctx: RunContext) -> dict:
        self.started(ctx)
        entities = [e["name"] for e in ctx.outputs.get("plan", {}).get("entities", [])] or ["customer", "order", "product"]
        # Prefer the pipeline's actual discovered entities
        entities = ctx.outputs.get("pipeline", {}).get("entities", entities)

        profile: dict[str, Any] = {"entities": {}}

        for entity in entities:
            self.emit(ctx, f"Sampling {entity} via /v1/{entity}s …")
            df = await _fetch_all(ctx.source_url, ctx.source_token, entity)
            self.emit(ctx, f"loaded {len(df):,} rows · {len(df.columns)} columns", level="ok")

            cols: dict[str, Any] = {}
            for col in df.columns:
                s = df[col]
                # pandas 3 + numpy 2 don't allow boolean subtract inside .mean(); use sum() / len().
                null_pct = round((int(s.isna().sum()) / max(len(s), 1)) * 100, 3)
                distinct_pct = round(int(s.nunique(dropna=True)) / max(len(s), 1) * 100, 3)
                col_info: dict[str, Any] = {
                    "dtype": str(s.dtype),
                    "rows": int(len(s)),
                    "null_pct": null_pct,
                    "distinct_pct": distinct_pct,
                }
                ss = s.dropna()
                # Only compute numeric stats for true numeric (int/float), not booleans.
                is_num = pd.api.types.is_numeric_dtype(ss) and not pd.api.types.is_bool_dtype(ss)
                if is_num and len(ss):
                    ss_f = ss.astype(float)
                    col_info.update({
                        "min":    float(ss_f.min()),
                        "max":    float(ss_f.max()),
                        "mean":   float(ss_f.mean()),
                        "median": float(ss_f.median()),
                        "p95":    float(ss_f.quantile(0.95)),
                    })
                else:
                    sample = s.dropna().astype(str).head(5).tolist()
                    col_info["sample"] = sample
                cols[col] = col_info
            profile["entities"][entity] = {
                "row_count": int(len(df)),
                "columns": cols,
            }

        # Cross-column anomaly: discount > amount
        try:
            orders_df = await _fetch_all(ctx.source_url, ctx.source_token, "order")
            anomaly = int((orders_df["discount_amount"] > orders_df["order_amount"]).sum())
            profile["anomalies"] = {"discount_exceeds_amount": anomaly}
            if anomaly:
                self.emit(ctx, f"anomaly · discount_amount > order_amount in {anomaly} rows", level="warn")
        except Exception:  # noqa: BLE001
            pass

        # Histogram of order_amount for the UI
        try:
            bins = [0, 100, 500, 1000, 5000, 10000, 50000, 10**9]
            labels = ["$0–$100", "$100–$500", "$500–$1K", "$1K–$5K", "$5K–$10K", "$10K–$50K", "$50K+"]
            cats = pd.cut(orders_df["order_amount"], bins=bins, labels=labels, right=False)
            hist = cats.value_counts().reindex(labels, fill_value=0)
            profile["order_amount_hist"] = {
                "labels": labels,
                "counts": [int(v) for v in hist.values],
                "pct":    [round(int(v) / max(len(orders_df), 1) * 100, 2) for v in hist.values],
            }
        except Exception:  # noqa: BLE001
            pass

        p = ctx.write_json(("profile", "profile.json"), profile)
        self.artifact(ctx, "profile.json", p, preview="")
        ctx.outputs["profile"] = profile
        total_rows = sum(v["row_count"] for v in profile["entities"].values())
        self.done(ctx, f"Profiled {total_rows:,} rows across {len(profile['entities'])} entities")
        return profile
