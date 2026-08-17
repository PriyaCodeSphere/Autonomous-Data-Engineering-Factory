# Andersen Autonomous Data Engineering Factory

Interactive prototype demonstrating how multiple AI agents (Cognizant reusable
patterns) collaborate to onboard a new enterprise data source into a modern
Snowflake / dbt / Fivetran / Power BI / Azure DevOps ecosystem.

Scenario: **onboard the `DealerSalesCRM` source** and produce a governed, tested,
documented, deployment-ready data product.

## Quick start (Windows / PowerShell)

```powershell
# 1. Put Azure OpenAI credentials in .env (already populated for the demo laptop)
copy .env.example .env

# 2. Bootstrap venv, install deps, generate fake data, launch both servers
.\start.ps1
```

The script opens the demo at `http://localhost:8000/`. The top-right pill will
turn green and say **LIVE · Azure OpenAI** when the backend and OpenAI are up.

## What runs when you click *Start Agentic Onboarding*

1. Portal `POST /api/onboard` → orchestrator spawns a run and returns a `run_id`.
2. Browser opens an SSE stream on `/api/runs/{run_id}/events`.
3. Ten agents execute in order — the sidebar advances automatically, artifacts
   land under `artifacts/{run_id}/`, and every event streams into the floating
   *Live agent activity* dock:

   | # | Agent (Cognizant reusable pattern)          | LLM? | Real work                                 |
   |---|---------------------------------------------|------|-------------------------------------------|
   | 1 | Solution Planning (Virtual Data Engineer)   | yes  | Builds task graph + agent selection       |
   | 2 | Pipeline Configuration                      | no   | Calls mock source `/v1/schema`, writes YAML |
   | 3 | dbt Macro Factory (Coding & Code Review)    | yes  | Templates + LLM-authored descriptions     |
   | 4 | Data Profiling & Validation                 | no   | pandas over paginated API calls           |
   | 5 | Data Quality Rule Generation                | yes  | LLM proposes rules from profile output    |
   | 6 | Data Governance & Classification            | yes  | LLM classifies each column; **approval gate** |
   | 7 | Testing (Synthetic Data)                    | no   | Faker-based fixtures                      |
   | 8 | Documentation & Metadata                    | yes  | LLM writes README                         |
   | 9 | Coding & Code Review (PR)                   | yes  | LLM PR summary; **approval gate**         |
   | 10| DevOps & Deployment Automation              | no   | Simulated Azure DevOps run; **CAB gate**  |

4. Three human approval modals appear at the governance-critical gates.
   Clicking *Approve & continue* posts to `/api/runs/{run_id}/approvals/{gate}`
   which wakes the paused agent.

Every generated file — Fivetran YAML, Snowflake DDL, dbt models, DQ tests,
masking SQL, synthetic CSVs, README, PR body, Azure DevOps pipeline — is
written to `artifacts/{run_id}/`.

## Project layout

```
Andersen Automation Agents/
├── index.html                     # customer-facing UI (12 stages)
├── start.ps1                      # one-command launcher
├── requirements.txt
├── .env / .env.example            # Azure OpenAI credentials
│
├── mock_source/
│   ├── generate_data.py           # 5k customers, 50k orders, 1.3k products (seed=42)
│   └── main.py                    # FastAPI mock with cursor pagination + bearer auth
│
├── backend/
│   ├── main.py                    # FastAPI: /api/onboard, SSE, approvals
│   ├── orchestrator.py            # composes the 10-agent pipeline
│   ├── llm.py                     # Azure OpenAI wrapper (offline fallback)
│   ├── events.py                  # in-process event bus + approval gates
│   └── agents/
│       ├── base.py                # BaseAgent + RunContext
│       ├── planner.py             # ← LLM
│       ├── pipeline.py            # ← calls the mock source
│       ├── dbt_factory.py         # ← LLM (descriptions)
│       ├── profiler.py            # ← pandas
│       ├── dq.py                  # ← LLM (rule proposals)
│       ├── pii.py                 # ← LLM + governance approval gate
│       ├── synth.py               # ← Faker
│       ├── docs.py                # ← LLM (README)
│       ├── review.py              # ← LLM (PR summary) + reviewer gate
│       └── deploy.py              # ← simulated pipeline + CAB gate
│
├── data/           # generated fake data (git-ignored)
└── artifacts/      # per-run agent outputs (git-ignored)
```

## Positioning (used in the UI copy)

- **Cognizant Agentic Engineering Excellence Platform** — the umbrella solution.
- **Reusable Cognizant agent patterns** used here:
  Virtual Data Engineer · Data Profiling & Validation · Data Quality Rule
  Generation · Coding & Code Review · Testing · Documentation & Metadata ·
  Data Governance & Classification · DevOps & Deployment Automation.
- **Andersen-specific implementations** = configurable extensions built with
  those reusable patterns (dbt style guide v2.1, Andersen naming, catalog,
  Fivetran destination, Azure DevOps templates).

## Configuration

`.env` values understood by the backend:

| Var | Default | Purpose |
|---|---|---|
| `AZURE_OPENAI_ENDPOINT`        | —          | Azure OpenAI resource URL |
| `AZURE_OPENAI_API_KEY`         | —          | Key |
| `AZURE_OPENAI_CHAT_DEPLOYMENT` | `gpt-4o`   | Chat deployment name |
| `AZURE_OPENAI_API_VERSION`     | `2024-08-01-preview` | API version |
| `MOCK_SOURCE_PORT`             | `8001`     | Mock DealerSalesCRM port |
| `BACKEND_PORT`                 | `8000`     | Agent backend port |
| `MOCK_SOURCE_TOKEN`            | `demo-token` | Bearer token for the mock source |
| `OFFLINE_MODE`                 | `0`        | `1` = skip LLM, use deterministic stubs |

If the LLM credentials are missing or `OFFLINE_MODE=1`, every LLM-driven agent
falls back to a hand-authored deterministic response — the pipeline still runs
end-to-end. Useful for offline demos.

## Troubleshooting

- **Backend won't start** — check `backend.err` in the repo root; the most
  common cause is a missing wheel for pandas on very-new Python. Fall back to
  Python 3.12 or 3.13.
- **LLM calls fail** — set `OFFLINE_MODE=1` in `.env` and restart. Everything
  still works; you just lose the reasoning trace / PR summary / README variety.
- **Stuck on an approval modal** — the pipeline waits on human approval at the
  PII, PR, and prod-deploy gates. Click *Approve & continue* to proceed.
- **Regenerate everything from scratch** — delete `data/` and `artifacts/` and
  re-run `.\start.ps1`.

## Notes for the RFP demo

- Runs entirely on the presenter's laptop — no external internet dependency
  beyond Azure OpenAI.
- ~40s–90s wall-time per full run depending on LLM latency.
- Each run is idempotent and deterministic where deterministic (data, synth,
  templates); only the LLM-authored artifacts vary between runs.
