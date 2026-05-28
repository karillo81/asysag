# AutoSys Agent – Technology Stack Proposal

## Guiding Principles

- Agent and customer data live on the customer's infrastructure; the LLM endpoint is customer-provided (cloud or on-prem, their contract, their choice of vendor)
- Mock mode and live mode share identical code paths – only the data adapter changes
- Minimal moving parts – fewer services = fewer things to break in a demo
- Every component runs in Docker on a single server (your lab VM)
- UI audience is technicians, not managers – operational density over visual polish
- GDPR: PII (emails, person names, phone numbers) is redacted before any prompt leaves the agent; operational data (job names, hosts, error text) flows freely
- Minimum supported AutoSys: any version exposing the REST API (r11.3.5 / r11.3.6 and newer per current knowledge — verify the exact floor before customer commitments). Older CLI-only / SOAP-only installs are out of scope.

---

## The Core Idea: Data Adapter Pattern

```
┌─────────────────────────────────────────────────┐
│              Agent Application                  │
│                                                 │
│  UI → Agent Core → Tools → [ DATA ADAPTER ]     │
│                                 │               │
│                    ┌────────────┴────────────┐  │
│                    │                         │  │
│              MOCK ADAPTER             LIVE ADAPTER│
│              (JSON files)            (AutoSys      │
│                                       REST API)   │
└─────────────────────────────────────────────────┘
```

One environment variable switches between them:

```
AUTOSYS_MODE=mock    # demo / lab
AUTOSYS_MODE=live    # real AutoSys
```

No code changes required when switching. The agent never knows the difference.

---

## Proposed Stack

### Backend – Python (FastAPI)

| Component | Technology | Why |
|---|---|---|
| API framework | FastAPI | Lightweight, async, auto-generates docs |
| Agent orchestration | LangGraph | Simple graph-based agent loops, no magic |
| LLM gateway | LiteLLM | Single interface for any LLM the customer brings |
| Vector / RAG | ChromaDB (embedded) | Runs in-process inside FastAPI – no extra container |
| Short-term memory | Python in-memory dict | No Redis needed for demo stage |
| Long-term memory | SQLite | Zero-config, single file, enough for demo |
| Mock data | JSON files | Easy to edit, version-controlled, readable |

**Why not Redis, Postgres, or a full message broker at this stage?**
For a demo and lab environment, SQLite and in-memory state are sufficient. Adding Redis or Postgres before you need them creates operational complexity without demo value. The adapter pattern means you can swap them in later without touching business logic.

### Frontend – React

| Component | Technology | Why |
|---|---|---|
| Framework | React + Vite | Fast dev server, simple build |
| UI components | shadcn/ui | Clean, unstyled by default, easy to customise |
| Styling | Tailwind CSS | No CSS files to maintain |
| Chat interface | Custom component | Simple message list + input, no library needed |
| Mode switcher | React state + env flag | Small `MOCK` / `LIVE` badge in header, no fancy UI |
| Job / log output | Monospace text + scrollback | Technicians read JIL, job IDs, stderr – sans-serif hurts scannability |
| Dependency view | Indented text tree (Phase 1) | Adequate for first demo; add a real graph lib (React Flow / Cytoscape) only when a scenario needs it |
| Keyboard shortcuts | Native handlers | `↑` recalls last prompt, `Ctrl+L` clears – cheap, technicians expect it |

### LLM – Customer-Provided

We do not host or run an LLM. The customer brings the endpoint and credentials; LiteLLM routes calls to whichever provider they choose.

| Scenario | LiteLLM provider |
|---|---|
| Internal dev / testing | Anthropic Claude API (our account, cheap, reliable tool calling) |
| Customer (Anthropic) | Anthropic Claude API |
| Customer (Google) | Gemini API |
| Customer (OpenAI) | OpenAI API |
| Customer (Microsoft) | Azure OpenAI (the only "Copilot" surface with a usable inference API) |

Switching providers is a config change in `.env` – no code touches.

**GDPR handling:** a `redactor` module sits between tool outputs and the LLM prompt. It strips emails, phone numbers, and a configurable list of PII-bearing fields (e.g. `owner_email`, `assignee_name`). The technician's UI still shows unredacted data — only the LLM call is sanitised. Redactions are counted and logged per call for verification.

### Infrastructure – Single Server (your lab VM)

```
docker-compose.yml
│
├── agent-backend      (FastAPI + LangGraph + embedded ChromaDB)
└── agent-frontend     (React, served via Nginx)
```

Everything runs on the same VM you already have:
- 4 vCPU / 8 GB RAM (as agreed with your consultant)
- No Kubernetes, no service mesh, no cloud dependencies
- Single `docker-compose up` to start everything

---

## Mock Data Structure

```
/mock-data
  /jobs
    job_definitions.json      # JIL-equivalent job configs
    job_status.json           # current run states
    job_history.json          # last 90 days of runs
    job_dependencies.json     # dependency graph
  /incidents
    incident_history.json     # past failures + resolutions
  /runbooks
    etl_runbook.md
    batch_runbook.md
  /scenarios
    scenario_etl_failure.json      # pre-built demo story
    scenario_cascading_failure.json
    scenario_sla_breach.json
```

Scenarios let you trigger a specific failure story during a demo with one click – the mock adapter serves the right data sequence to make the agent respond as if a real incident is happening.

---

## Live Adapter — REST API Reality

Researched from Broadcom TechDocs + community articles on 2026-05-28. The AutoSys REST API has more nuance than a naïve "one endpoint per tool" mapping.

**Base URL:** `https://{host}:9443/AEWS/` (HTTPS only; production typically has a real CA cert, dev installs are self-signed)

**Auth:** Basic Auth (`Authorization: Basic <base64(user:pass)>`) is universal. The Swagger spec also mentions `X-AUTH-TOKEN` and enterprise JAAS+CA EEM, but every concrete example uses Basic. Default install user is `ejmcommander`.

**Two surfaces in the same Web Services component:**

| Surface | Purpose | Examples |
|---|---|---|
| `/AEWS/job/...`, `/AEWS/jil/...` (older, pre-Swagger) | **Reads** — job details, JIL definitions, filtered listing | `GET /AEWS/job/etl_load_facts`, `GET /AEWS/job?filter=boxname==etl_box_daily`, `GET /AEWS/jil/job?name=etl_load_facts` |
| `/AEWS/api/...` (Swagger-documented since 12.x) | **Writes** — event triggers (start, hold, kill, etc.), calendar mgmt, global vars, `command/run` | `POST /AEWS/api/event/force-start-job` with `{"jobName":"X","comment":"..."}` |

**Status codes are numeric** in REST responses. Typical mapping (verify against the customer's exact AutoSys version before shipping — values have shifted between releases):

```
1=RUNNING   4=SUCCESS    5=FAILURE   7=TERMINATED
8=STARTING  9=INACTIVE   11=QUE_WAIT 12=ON_NOEXEC
13=ON_HOLD  14=ON_ICE
```

The live adapter must translate these to the same string statuses the mock adapter emits, so the agent prompt/system behaviour is identical.

### Tool-to-endpoint mapping

| Agent tool | Live endpoint | Notes |
|---|---|---|
| `get_job_status(name)` | `GET /AEWS/job/{name}` | Translate numeric `status` field |
| `list_jobs(filter)` | `GET /AEWS/job` (optional `?filter=...`) | Supports filter expressions; numeric statuses again |
| `get_dependencies(name)` | `GET /AEWS/jil/job?name={name}` | Returns JIL text; parse `condition:` expression (`s(...)`, `f(...)`, `AND`/`OR`/`NOT`) into upstream. Downstream requires walking other jobs' conditions. Small parser needed. |
| `get_job_history(name)` | **No native endpoint.** `POST /AEWS/api/command/run` with `autorep -j {name} -w` and parse stdout | Brittle; text-parsing risk. Acceptable for M7 v1. |
| `get_job_log(name)` | **Not in the REST API at all.** | See "Log gap" below. |

### Log gap — biggest open issue

Job stdout/stderr files (`std_err_file`, `std_out_file` from JIL) live on the **remote agent host** where the job ran, not on the scheduler. The AutoSys REST API has no endpoint that returns log content.

Realistic options for a customer install:

| Strategy | Trade-off |
|---|---|
| (a) Customer points us at a log forwarder (Splunk / ELK / S3) — adapter queries that | Clean; depends entirely on the customer's logging stack |
| (b) Indirect via `/AEWS/api/command/run` invoking `cat /var/log/...` on the scheduler | Only works if the scheduler has the log mount, and it usually doesn't (logs are on agent hosts) |
| (c) Configured SSH jump per machine (`ssh agent01 cat /var/log/autosys/{job}.err`) | Privileged, fragile, security-sensitive |
| (d) Disable `get_job_log` in live mode; return the JIL `std_err_file` path so operator can fetch themselves | Degraded but safe. Default for M7 v1. |

Default for M7 v1: **(d)** — the tool returns the *path* with a clear "fetch this yourself" note. Strategy (a) becomes a per-customer integration.

---

## What Is NOT in the Stack (deliberately)

| Excluded | Reason |
|---|---|
| Redis | Not needed until multi-user / production |
| PostgreSQL | SQLite is sufficient for demo + early customers |
| Kubernetes | Single Docker Compose is easier to manage and demo |
| Message queue (Kafka, RabbitMQ) | No async event streaming needed yet |
| Separate auth service | API key in config is enough for demo |
| Elasticsearch | ChromaDB covers search needs at this scale |

These can all be added incrementally when a real customer requires them. The adapter pattern protects the business logic from these infrastructure changes.

---

## File / Project Structure

```
autosys-agent/
├── backend/
│   ├── main.py                  # FastAPI entrypoint
│   ├── agent/
│   │   ├── graph.py             # LangGraph agent definition
│   │   ├── tools.py             # All tool definitions
│   │   ├── redactor.py          # PII redaction before LLM calls
│   │   └── memory.py            # SQLite memory layer
│   ├── adapters/
│   │   ├── base.py              # Abstract adapter interface
│   │   ├── mock_adapter.py      # Reads from /mock-data
│   │   └── live_adapter.py      # Calls AutoSys REST API
│   ├── rag/
│   │   └── knowledge_base.py    # ChromaDB + doc ingestion
│   └── config.py                # AUTOSYS_MODE + LLM config
├── frontend/
│   ├── src/
│   │   ├── components/
│   │   │   ├── Chat.jsx
│   │   │   ├── JobStatusPanel.jsx
│   │   │   ├── ModeBadge.jsx    # Small MOCK / LIVE indicator
│   │   │   └── EventLog.jsx     # Monospace scrollback of agent + job events
│   │   └── App.jsx
│   └── vite.config.js
├── mock-data/                   # All mock JSON + scenarios
├── docs/                        # Broadcom docs for RAG
├── docker-compose.yml
└── .env.example                 # AUTOSYS_MODE, LLM config
```

---

## Development Phases vs Stack Complexity

| Phase | New components added |
|---|---|
| Phase 1 – Demo | FastAPI + LangGraph + Mock adapter + React chat + Claude API + PII redactor |
| Phase 2 – First customer | Live adapter + ChromaDB docs + SQLite memory |
| Phase 3 – Multi-customer | PostgreSQL replaces SQLite + Redis for sessions |
| Phase 4 – Scale | Kubernetes + message queue if needed |

No phase requires rewriting the previous one. Each adds a layer.

---

## Open Questions to Decide Before Build

1. **LLM strategy** – Resolved (2026-05-28): Claude API for internal dev; customer brings their own endpoint in production (Claude / Gemini / OpenAI / Azure OpenAI) via LiteLLM.
2. **Frontend language** – React confirmed (2026-05-28) to keep room for dynamic pages: streaming chat tokens, interactive job graphs, live status updates.
3. **AutoSys version in live mode** – API shape verified for 12.x and 24.x (see "Live Adapter — REST API Reality" section). Specific customer version still useful for verifying the numeric status-code mapping; not blocking until first install.
4. ~~Demo scenarios~~ – Resolved during M6 (2026-05-28): etl_failure, cascading_failure, sla_breach selectable from the header menu.
5. **Authentication for live mode** – Basic Auth (`ejmcommander`-style service account) is the universal default. Customer install will need: (a) service account credentials, (b) decision on cert trust (real CA cert vs `verify=False`), (c) whether `X-AUTH-TOKEN` or JAAS+CA EEM is required by their security team instead of Basic.
6. ~~PII redaction scope~~ – Resolved (2026-05-28): customer environments anonymise person names at source as a gold rule. Regex (email + phone) + field denylist is sufficient. No NER layer required.
7. **Log retrieval strategy in live mode** – AutoSys REST API does not expose stdout/stderr content. Default plan is (d) return the JIL path and let the operator fetch themselves. Customers with Splunk/ELK/S3 log forwarding can opt into (a) by configuring `LOG_FORWARDER_URL_TEMPLATE` in env. **Confirm per customer before promising the `get_job_log` tool works in live mode.**

---

*Review this stack and confirm before starting build. Once agreed, next step is project scaffolding and mock data design.*
