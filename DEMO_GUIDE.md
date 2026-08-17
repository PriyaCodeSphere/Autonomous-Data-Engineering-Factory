# Autonomous Data Engineering Factory — Demo & Test Guide

> A team walkthrough for setting up, running, and demonstrating the
> **Autonomous Data Engineering Factory** — an interactive prototype
> that shows how the *Cognizant Agentic Engineering Excellence Platform* uses
> a fleet of AI agents to onboard a new enterprise data source into a
> Snowflake / dbt / Fivetran / Power BI / Azure DevOps ecosystem.

---

## 1. What this demo shows

**Scenario:** A data owner submits a request:

> *"Onboard the DealerSalesCRM source into the enterprise data platform. Create
> ingestion configurations, Snowflake structures, dbt models, data quality
> controls, PII classifications, documentation, lineage, test data, and
> deployment artifacts."*

The demo takes that single ask through a **10-agent collaborative workflow**,
producing 28+ engineering artifacts (SQL, YAML, Python, CSV, Markdown) and
requiring **three human governance approvals** — the same shape an enterprise
change-advisory process would follow. Everything runs live on the presenter's
laptop against a mock source and Azure OpenAI. Total wall time per run:
~90-100 seconds.

---

## 2. The 10 agents

Each agent maps to a **reusable Cognizant agent pattern**. The customer
implementation is a configurable extension of that pattern (naming, catalog,
policies, target platforms).

| # | Agent | Cognizant reusable pattern | Purpose | LLM? |
|---|---|---|---|---|
| 1 | **Solution Planning Agent** | Virtual Data Engineer | Reads the intake, verifies metadata sufficiency, decomposes the request into a task graph, picks the specialist agents, attaches enterprise policies (dbt style guide, retention, cost guardrails). Emits a reasoning trace + plan JSON. | ✅ |
| 2 | **Pipeline Configuration Agent** | Virtual Data Engineer | Discovers the source schema (calls `/v1/schema`), generates the Fivetran custom REST connector, the Snowflake landing DDL, and a Python paginator. Validates its own output before hand-off. | — |
| 3 | **dbt Macro Factory Agent** | Coding & Code Review Agent | Emits `sources.yml`, 3 staging models, 3 mart models, and a reusable `hash_pii` macro following the enterprise dbt style guide. Asks the LLM to write business-friendly model descriptions for `schema.yml`. | ✅ |
| 4 | **Data Profiling Agent** | Data Profiling & Validation Agent | Pulls the actual data (paginated HTTP calls) and computes column-level stats: null %, distinct %, min/max/mean/p95, distribution histograms, referential-integrity checks, cross-column anomaly detection. | — |
| 5 | **Data Quality Rule Generation Agent** | Data Quality Rule Generation Agent | Takes the profile output and asks the LLM to propose ~12-18 candidate DQ rules with severity and ownership. Baseline blocker rules are always enforced. Emits dbt tests, a Great Expectations suite, and a Snowflake freshness alert. | ✅ |
| 6 | **PII Classification Agent** | Data Governance & Classification Agent | LLM classifies every column into `restricted / confidential / quasi / public`, proposes a masking policy, and flags Power BI safety. Writes Snowflake masking SQL and the Enterprise Data Catalog entry. **Pauses for data steward approval.** | ✅ |
| 7 | **Synthetic Test Data Agent** | Testing Agent | Uses the PII classification to generate realistic Faker-driven fixtures for DEV/QA. Preserves referential integrity and statistical distributions from the profile. Reproducible via a fixed seed. | — |
| 8 | **Documentation & Lineage Agent** | Documentation & Metadata Agent | LLM writes the README (overview, ownership, SLA, runbook). Emits `exposures.yml` for the Power BI dashboard and a column-level lineage graph published to the catalog. | ✅ |
| 9 | **PR Review & Policy Agent** | Coding & Code Review Agent | Runs 6 policy checks (naming, secrets, PII hashing, DQ coverage, SBOM, cost guardrails). Asks the LLM to write a proper PR body summarising all upstream work. **Pauses for human reviewer approval.** | ✅ |
| 10 | **Deployment Orchestrator** | DevOps & Deployment Automation Agent | Simulates the Azure DevOps pipeline: build → static → DQ → deploy DEV → integration → promote QA → **CAB approval** → prod. Enables Fivetran, certifies the Power BI dataset. | — |

**Governance approval gates** (human-in-the-loop):
- **Gate 1 — PII (Agent 6)** — steward approves the classification and masking policy.
- **Gate 2 — PR (Agent 9)** — human reviewer approves the pull request.
- **Gate 3 — Deploy (Agent 10)** — change-advisory board approves the production release.

---

## 3. Prerequisites

Before your teammates start, they need:

| Requirement | Version | Notes |
|---|---|---|
| Windows | 10 / 11 | The launcher script is PowerShell |
| Python | 3.11 or later | Verified on 3.14 |
| Azure OpenAI resource | any region with a chat deployment | Endpoint URL + API key + deployment name |
| Internet access | to `*.openai.azure.com` | Only during the LLM calls; the rest runs offline |
| ~500 MB disk | for the venv and generated data | |
| A browser | Chrome/Edge/Firefox | For the demo UI |

The demo listens on **`localhost:8000`** (UI + agent backend) and
**`localhost:8001`** (mock DealerSalesCRM REST API). Make sure both ports are
free.

---

## 4. First-time setup

```powershell
# 1. Copy the project folder to your machine, e.g.:
#    C:\Users\<you>\Autonomous-Data-Engineering-Factory\

# 2. Add Azure OpenAI credentials to .env
copy .env.example .env
notepad .env
```

Populate the four Azure OpenAI keys:

```
AZURE_OPENAI_ENDPOINT="https://<your-resource>.openai.azure.com/"
AZURE_OPENAI_API_KEY="<your-key>"
AZURE_OPENAI_CHAT_DEPLOYMENT="gpt-4.1"       # or your chat deployment name
AZURE_OPENAI_API_VERSION="2025-01-01-preview"
```

If you don't have Azure OpenAI available, set `OFFLINE_MODE=1` in `.env`. The
pipeline still runs end-to-end with deterministic stubs — you just lose the
LLM-authored variety.

Then:

```powershell
# 3. Launch (creates venv, installs deps, generates fake data, starts both servers)
.\start.ps1
```

The first run takes 3-5 minutes for `pip install`. Subsequent runs are ~5 seconds:

```powershell
# Skip pip install / data regen on subsequent launches
.\start.ps1 -SkipInstall -SkipData
```

When the browser opens at `http://localhost:8000/`, the top-right pill should
read **`● LIVE · Azure OpenAI`** in green. That's the signal that both the
backend and the LLM are reachable.

---

## 5. How to run a demo (5-7 minutes)

Recommended flow for a stakeholder walkthrough:

### Step 1 · Frame the ask (30 s)
Open the demo at `http://localhost:8000/`.

*"This is the Engineering Excellence Portal a data owner would use to request
onboarding of a new source. On the right you can see the pre-populated request
for **DealerSalesCRM** — a REST API with 3 entities and 30 fields."*

Scroll down to the *Source entities* card to show Customer / Order / Product
with pre-assigned classifications, then to *Expected agent workflow* to show
the 10 reusable Cognizant patterns that will run.

### Step 2 · Kick off the pipeline (10 s)
Click **▶ Start agentic onboarding** (amber button, bottom-right of the intake
card).

*"That single click starts the orchestrator. In a real deployment this would
be a chat/portal/Slack request going into the fabric."*

### Step 3 · Watch it run (2-3 min)
- **Floating dock** at bottom-right streams every agent event in real time.
- **Sidebar** advances automatically as each agent activates.
- Each stage view populates with the artifacts as they're produced.

Talk track while it runs:

| Stage arriving | What to say |
|---|---|
| Solution Planning | *"The planner is calling GPT-4.1 to decompose the request. Notice the reasoning trace — it identified PII, financial columns, and 3 governance gates."* |
| Pipeline Configuration | *"This agent just called the source's `/v1/schema` endpoint for real and generated the Fivetran YAML + Snowflake DDL."* |
| dbt Macro Factory | *"Eight dbt files generated from templates. The LLM wrote the business-friendly descriptions in `schema.yml`."* |
| Data Profiling | *"Real pandas profiling running over 56,284 rows pulled through the paginated API. Look — it caught 30 rows where discount exceeds order amount."* |
| Data Quality | *"The DQ agent used the profile output to ask the LLM for candidate rules — with ownership and severity."* |

### Step 4 · The first approval gate (30 s)
The PII agent will pause and pop the *Data steward approval* modal.

*"This is a governance-critical checkpoint. In a real deployment this would
route to the domain steward via ServiceNow or an approval workflow. For the
demo we'll approve it here."*

Click **Approve & continue**.

### Step 5 · Watch through PR + CAB gates (2 min)
Two more modals will appear:
- **PR review** — human reviewer sign-off (after the LLM writes the PR body).
- **Change-advisory** — production deployment approval (after DEV/QA succeed).

Approve both.

### Step 6 · The finished data product (1 min)
The workflow lands on the *Deployment-Ready Data Product* view. Walk through:
- The KPIs at the top (files generated, tests passing, time-to-prod).
- The consumption endpoints (Snowflake, Power BI, semantic layer, reverse-ETL).
- The run summary showing all 10 agents with duration and status.

*"Every artifact you just watched us generate is in the `artifacts/` folder,
under this run's ID. It's ready to `git push` — this is not a mock."*

### Step 7 · Show real artifacts (2 min)
Open the `artifacts/run-<id>/` folder from the last run in File Explorer and
open a few files to show they're production-grade:
- [`dbt/marts/fct_order.sql`](artifacts/) — real dbt model
- [`pii/masking.sql`](artifacts/) — real Snowflake masking policies
- [`review/pr_body.md`](artifacts/) — LLM-authored PR summary
- [`docs/README.md`](artifacts/) — LLM-authored README
- [`deploy/azure-pipelines.yml`](artifacts/) — real Azure DevOps pipeline

---

## 6. Test cases your team should run

| # | Scenario | Steps | Expected outcome |
|---|---|---|---|
| 1 | **Happy path (live LLM)** | `.\start.ps1`, click Start, approve all 3 gates | Pipeline finishes in ~90-100 s. All 28 artifacts generated. Top-right pill stays green. |
| 2 | **Offline mode** | Set `OFFLINE_MODE=1` in `.env`, restart, run | Pipeline still completes. Fallback (deterministic) responses used for LLM agents. Pill turns amber. |
| 3 | **Missing credentials** | Blank `AZURE_OPENAI_API_KEY`, restart, run | Same as offline mode. `[llm] Azure OpenAI credentials not set` warning in `backend.log`. |
| 4 | **Decline an approval** | On the PII gate modal, click *Request changes* | Pipeline halts with a clear error message. No orphan artifacts. |
| 5 | **Manual navigation** | While a run is executing, click a previous stage in the sidebar | The stage-view loads instantly for review; the live dock keeps streaming; the pipeline keeps progressing regardless. |
| 6 | **Re-run** | Click *Restart demo* on the final Data Product page | New `run-<id>` is created; old artifacts are preserved on disk for comparison. |
| 7 | **Inspect a specific artifact** | Open `artifacts/run-<id>/dbt/marts/fct_order.sql` | Valid dbt SQL with `{{ ref() }}` and `{{ config() }}` calls; passes `dbt parse`. |
| 8 | **Cost/latency check** | `.\start.ps1`, time the run with a stopwatch | LLM tokens (5 calls × ~1-2k tokens ≈ 10k in + 10k out). ~$0.05-$0.10 per run at gpt-4.1 rates. |
| 9 | **Both servers healthy** | `curl http://localhost:8000/api/status` and `curl http://localhost:8001/v1/health` | Both return `200 OK` with the expected payload. |
| 10 | **Mock source pagination** | `curl -H "Authorization: Bearer demo-token" http://localhost:8001/v1/customers?offset=0` | Returns 500 rows and a `next` cursor. |

---

## 7. What you should see (checklist)

At the end of a full run, verify:

- [ ] Top-right pill is green: **LIVE · Azure OpenAI**
- [ ] All 12 stages in the sidebar have a green check
- [ ] The floating *Live agent activity* dock scrolled ~80+ lines
- [ ] Modal appeared 3 times (PII, PR, CAB) and was approved
- [ ] The final *Data Product* page shows "Time from request to prod" ≈ 90-100 s
- [ ] `artifacts/run-<id>/` contains: `plan/`, `pipeline/`, `dbt/`, `profile/`, `dq/`, `pii/`, `synth/`, `docs/`, `review/`, `deploy/`
- [ ] `artifacts/run-<id>/dbt/schema.yml` has LLM-authored model descriptions (not the template placeholder)
- [ ] `artifacts/run-<id>/review/pr_body.md` is a proper multi-section markdown PR summary
- [ ] `artifacts/run-<id>/docs/README.md` is 40-60 lines of coherent markdown
- [ ] `artifacts/run-<id>/pii/classification.json` shows a mix of `restricted`, `confidential`, `quasi`, `public`

If any of those fail, see **Troubleshooting** below.

---

## 8. Troubleshooting

| Symptom | Cause & fix |
|---|---|
| Top-right pill is amber (`offline stubs`) | Backend can't reach Azure OpenAI. Check `.env` values, VPN, and firewall. Verify with the smoke test in section 9. |
| Top-right pill missing entirely | The HTML isn't being served by the backend — you're viewing `index.html` directly as a `file://`. Open `http://localhost:8000/` instead. |
| `Port 8000/8001 already in use` | Something is bound. `netstat -ano \| findstr :8000`, then `taskkill /F /PID <pid>`. |
| `.\start.ps1 : cannot be loaded` | PowerShell execution policy. Run once: `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` |
| Pipeline halts mid-run with error | Look at `backend.err` in the repo root. The event stream also shows the last 6 traceback lines under the `error` event. |
| First run is very slow | `pip install` on cold cache pulls ~200 MB of wheels (pandas, numpy). Subsequent `.\start.ps1 -SkipInstall` runs skip this. |
| PII agent uses deterministic fallback | LLM response was truncated (28 columns × ~140 tokens each). Bump `max_tokens` in [backend/agents/pii.py](backend/agents/pii.py) if the fallback happens repeatedly. |
| Modal doesn't advance the pipeline | The backend didn't receive the approval POST. Check the network tab; the URL is `/api/runs/<run_id>/approvals/<gate_id>`. |
| Stages "run" instantly, no LLM latency | You're in offline mode or the OFFLINE_MODE flag is set. Check `.env` and top-right pill. |

---

## 9. Verifying the LLM connection

If in doubt, run this one-liner from the project root:

```powershell
.venv\Scripts\python.exe -c @"
from dotenv import load_dotenv; load_dotenv()
from backend import llm
print('online:', llm.is_online())
print('response:', llm.complete('You are helpful.', 'Say PING OK', max_tokens=10))
"@
```

Expected: `online: True` and `response: PING OK`.

---

## 10. Handy paths

| Path | What's in it |
|---|---|
| `index.html` | The single-page demo UI |
| `backend/main.py` | FastAPI backend — `/api/onboard`, SSE stream, approvals |
| `backend/orchestrator.py` | Composes the 10-agent pipeline |
| `backend/agents/*.py` | Individual agents (one file each) |
| `mock_source/main.py` | Mock DealerSalesCRM REST API (bearer auth, cursor pagination) |
| `mock_source/generate_data.py` | Deterministic Faker generator (seed=42) |
| `data/` | Generated fake source data (~50 MB) |
| `artifacts/run-<id>/` | Everything a run produced |
| `backend.log` / `backend.err` | Backend stdout/stderr |
| `mock_source.log` / `.err` | Mock source stdout/stderr |

---

## 11. Talking points for stakeholder Q&A

**Q: Are these real production agents?**
A: They implement **reusable Cognizant agent patterns** — the same patterns
we deploy in production. For this customer we've assembled them into a working
prototype configured to the target platform stack (Snowflake, dbt, Fivetran,
Power BI, Azure DevOps). Some agents (planner, DQ, PII, docs, code review)
use GPT-4.1 for reasoning and writing; the rest are deterministic Python that
would run against real Fivetran / dbt / Snowflake in a live deployment.

**Q: How would this connect to our real systems?**
A: Replace the mock source with a Fivetran connector call, swap the
deterministic pipeline agent to write into `<customer-org>/data-platform-repo`,
point Snowflake at the real accounts. The agent logic is unchanged —
only the target adapters swap.

**Q: How does human oversight work?**
A: Three explicit gates: PII classification (steward), PR review
(engineer), production deployment (change advisory board). Each gate
halts the entire pipeline until approved. All approvals are audited.
Additional gates can be added declaratively in [backend/orchestrator.py](backend/orchestrator.py).

**Q: What about cost?**
A: A full run today uses ~5 LLM calls totalling ~15-20 k tokens each
direction. At gpt-4.1 rates that's roughly **$0.05-$0.10 per onboarded
source**. Deterministic agents are free. Compare to the ~4-6 weeks of
engineer time this replaces.

**Q: What if the LLM is down?**
A: Every LLM-driven agent has a deterministic fallback. The pipeline
completes end-to-end even offline — the artifacts are just less rich.

**Q: Can we add / remove agents?**
A: Yes. Each agent is a self-contained file in [backend/agents/](backend/agents/)
with a fixed contract (input from `ctx.outputs`, output back to `ctx.outputs`,
emits events). The orchestrator composition is one edit in
[backend/orchestrator.py](backend/orchestrator.py).

---

## 12. Feedback + next steps

Please try the demo and file any issues or improvement ideas back to
`shanmugapriya.kandasamy@cognizant.com`. Suggested experiments:

1. Onboard a **second source** by swapping the request payload in the intake card.
2. Wire the deployment agent to a **real Azure DevOps pipeline** (change `_render_pipeline_yaml` + add a POST to the ADO REST API).
3. Add a **Cost Optimisation Agent** that inspects the generated Snowflake DDL and warehouse choices.
4. Add a **Semantic Model Agent** that emits Power BI dataset definitions.
5. Point the profiler at a **real Snowflake sample** table instead of the mock source.
