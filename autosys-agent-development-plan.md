# AutoSys Agent – Development Plan

Companion to `autosys-agent-tech-stack.md`. Sequences the build from empty repo to first customer demo.

## Guiding Principles

- Ship the demo path first – every milestone must produce something you can show
- Mock-first – live AutoSys integration is the last step, not the first
- One scenario end-to-end before three half-done – depth over breadth
- No premature infrastructure – SQLite, in-memory state, embedded ChromaDB until a customer forces otherwise

---

## Milestones at a Glance

| # | Milestone | Outcome you can demo |
|---|---|---|
| 0 | Scaffolding | `docker-compose up` boots empty backend + frontend |
| 1 | Backend core + mock adapter | `curl` returns mock job status |
| 2 | Agent loop with tools | Agent answers "what's the status of job X" via LLM |
| 3 | Frontend MVP | Chat UI talks to agent, streams tokens |
| 4 | First scenario end-to-end | ETL failure demo runs start to finish |
| 5 | RAG + persistent memory | Agent cites Broadcom docs, remembers prior incidents |
| 6 | Two more scenarios + UI polish | Cascading failure + SLA breach demos work |
| 7 | Live adapter | Same agent runs against real AutoSys |

Each milestone leaves the system in a demonstrable state. No half-built features carried forward.

---

## Milestone 0 – Scaffolding

**Goal:** empty but runnable shell.

- Create repo structure per tech-stack doc
- `docker-compose.yml` with two services: `agent-backend`, `agent-frontend`
- `.env.example` with `AUTOSYS_MODE`, `LITELLM_MODEL` (default `anthropic/claude-sonnet-4-6`), `ANTHROPIC_API_KEY`
- FastAPI `/health` endpoint returning `{"status": "ok", "mode": "mock"}`
- Vite + React + Tailwind + shadcn/ui initialised, shows "AutoSys Agent" placeholder
- Smoke test: backend can complete one Claude API call on boot and logs the model name

**Done when:** `docker-compose up` from a clean clone produces a green health check and a visible frontend.

---

## Milestone 1 – Backend Core + Mock Adapter

**Goal:** data adapter pattern proven, mock data flowing.

- `adapters/base.py` – abstract interface (`get_job_status`, `list_jobs`, `get_job_history`, `get_dependencies`)
- `adapters/mock_adapter.py` – reads from `/mock-data/*.json`
- Adapter selector in `config.py` driven by `AUTOSYS_MODE`
- Minimal mock data:
  - 8–12 jobs across 2 boxes
  - Status snapshot (mix of `SUCCESS`, `RUNNING`, `FAILURE`)
  - 7 days of history for those jobs
  - One dependency chain
- REST endpoints expose adapter methods for verification

**Done when:** `curl /jobs/etl_load_daily` returns realistic mock data, and flipping `AUTOSYS_MODE=live` raises a clean "live adapter not yet implemented" error (proves the wiring).

---

## Milestone 2 – Agent Loop with Tools

**Goal:** LLM can answer factual questions about jobs via tool calls.

- LangGraph graph: `receive → plan → call_tool → redact → respond` (single-tool loop, no branching yet)
- LiteLLM configured against Anthropic Claude API
- `agent/redactor.py` – sits between tool result and prompt assembly. First pass:
  - Regex: email addresses, phone numbers (E.164 + common national formats)
  - Field denylist: `owner_email`, `owner_name`, `assignee_email`, `assignee_name` stripped from any dict before serialising
  - Counts redactions per LLM call; logs aggregate count (not contents)
- Tools registered (all backed by adapter, all run through redactor before prompt assembly):
  - `get_job_status(job_name)`
  - `list_jobs(filter)`
  - `get_job_history(job_name, days)`
  - `get_dependencies(job_name)`
- `/chat` endpoint, non-streaming first, then add SSE streaming
- Lightweight prompt: "You are an AutoSys operator's assistant. Use tools to answer. Be terse."

**Done when:** chatting "is etl_load_daily healthy?" returns an answer that uses tool output (not hallucination), and a unit test confirms an email in mock data never appears in the assembled LLM prompt.

---

## Milestone 3 – Frontend MVP

**Goal:** a technician can drive the agent from the browser, no curl.

- `Chat.jsx` – message list, input, SSE streaming, monospace for tool output blocks
- `ModeBadge.jsx` – reads `/health`, displays `MOCK` / `LIVE`
- `JobStatusPanel.jsx` – right-side panel, monospace, shows last referenced job(s)
- Keyboard: `↑` recalls last prompt, `Ctrl+L` clears history
- No router yet – single page

**Done when:** demo flow "ask about a failing job → see status panel update → ask follow-up" works without touching a terminal.

---

## Milestone 4 – First Scenario End-to-End

**Goal:** one complete failure story playable on demand.

- Pick one scenario for first cut (recommendation: **ETL failure** – simplest to narrate)
- `mock-data/scenarios/scenario_etl_failure.json` defines the data sequence
- Scenario trigger: POST `/scenarios/{name}/start` swaps mock-adapter data source to that scenario's timeline
- Mock data adds: failure log excerpt, prior incident with resolution, runbook reference
- Tools added if needed: `get_job_log(job_name)`, `find_similar_incidents(job_name)`
- Rehearse the demo narrative – does the agent's response actually help a technician?

**Done when:** you can run a 5-minute scripted demo: trigger scenario → ask agent → agent explains → agent recommends fix → close out.

---

## Milestone 5 – RAG + Persistent Memory

**Goal:** agent gets smarter across conversations.

- Embedded ChromaDB initialised on backend startup
- Doc ingestion script: chunks `/docs/*.md` (Broadcom docs) into ChromaDB
- Tool: `search_knowledge_base(query)` – returns top 3 chunks with source
- SQLite memory: `conversations` table (turn-by-turn), `incidents` table (resolved cases)
- `EventLog.jsx` – monospace scrollback of agent reasoning + tool calls (for technicians who want to see what it did)

**Done when:** asking "have we seen this error before?" surfaces a prior incident from SQLite, and "what does Broadcom say about JOB_ON_HOLD?" cites a doc chunk.

---

## Milestone 6 – Two More Scenarios + UI Polish

**Goal:** demo library covers the three failure modes the doc lists.

- `scenario_cascading_failure.json` – upstream failure propagates
- `scenario_sla_breach.json` – job ran but late
- Indented text dependency tree in `JobStatusPanel`
- Small UI cleanups based on actually using the tool for an hour
- Pre-flight check: every scenario rehearsed twice without surprises

**Done when:** any of three scenarios can be demoed cold, and the operator-style UI feels usable not pretty.

---

## Milestone 7 – Live Adapter

**Goal:** same agent, real AutoSys. REST API only. CLI and SOAP paths are explicitly out of scope.

Reference: the tech-stack doc's "Live Adapter — REST API Reality" section captures the actual API surface (research completed 2026-05-28). Highlights:

- Base URL `https://{host}:9443/AEWS/`, Basic Auth, two surfaces (`/AEWS/job/...` for reads, `/AEWS/api/...` for writes/events).
- Status field is numeric in responses — adapter translates to the same strings the mock emits.
- **`get_job_log` cannot be implemented straight-through** because logs are not in the REST API. See sub-task 6 below.

### Sub-tasks (in build order)

1. **HTTP client + auth.** Add `httpx` to deps. New module `adapters/autosys_client.py` with: configurable base URL, Basic Auth, optional `verify=False` for self-signed certs, env-driven timeout, response logging (request id + status only, never body to avoid PII leak in logs).
2. **Status code translation.** Module-local mapping table (`1=RUNNING`, `4=SUCCESS`, `5=FAILURE`, `7=TERMINATED`, etc.). Single function `translate_status(numeric) -> str` used by every read endpoint.
3. **`get_job_status`** — `GET /AEWS/job/{name}` → translate. Return 404 / `JobNotFound` mapping when AutoSys says no such job.
4. **`list_jobs`** — `GET /AEWS/job` (with optional `?filter=` clause for `name_filter`). Cap result size; document the pagination behavior we observe.
5. **`get_dependencies`** — `GET /AEWS/jil/job?name={name}` → parse the `condition:` expression. Small recursive-descent parser for `s(...)`, `f(...)`, `done(...)`, `AND`, `OR`, `NOT`, parentheses. Downstream side requires walking `list_jobs` and inspecting each one's condition — cache result of that walk per `list_jobs` snapshot.
6. **`get_job_log`** — first cut returns `{"path": "<std_err_file>", "note": "logs live on the agent host; fetch via your own log system"}`. Optional second cut: if `LOG_FORWARDER_URL_TEMPLATE` env is set, substitute `{job}`/`{stream}` and fetch that URL. Document both behaviors in the adapter's docstring.
7. **`get_job_history`** — `POST /AEWS/api/command/run` with body invoking `autorep -j {name} -w` (last 7 days). Parse the text output into the same shape the mock emits. Note: this is the brittlest tool; if customer wants more reliability, recommend exporting `autorep` output to a scheduled file we read instead.
8. **Replay the Milestone 4 demo script in live mode.** Same conversation flow, same expected agent quality. Any tool that needs prompt changes between mock and live is a design regression — fix it.

### Done when

- Flipping `AUTOSYS_MODE=live` (with `AUTOSYS_HOST`, `AUTOSYS_USER`, `AUTOSYS_PASS` env vars) against a real AutoSys instance produces the same agent quality as mock mode for the etl_failure scenario.
- `get_job_log` either returns useful content (if forwarder configured) or returns the JIL path with a clear note — the agent's reply explains the limitation in one sentence rather than failing silently.
- README has a `Live mode` section documenting the env vars and the log-strategy choices.

### Pre-work (block on customer)

- Confirm customer's AutoSys version — primarily to verify the numeric status-code mapping for any non-standard values.
- Confirm auth flavor (Basic vs X-AUTH-TOKEN vs JAAS+EEM). Basic is the default; ask if their security team requires otherwise.
- Confirm cert posture — real CA, internal CA we can mount, or `verify=False` only.
- Confirm log strategy choice — Splunk/ELK/S3 (a), or accept the path-only fallback (d).
- Get a non-production AutoSys instance to test against. No production access during M7.

---

## Sequencing & Dependencies

```
M0 → M1 → M2 → M3 → M4 → M5 → M6 → M7
              ↑
              Frontend can start in parallel with M2
              once the /chat endpoint contract is fixed
```

M3 can begin as soon as M2's API shape is locked, even if the agent itself is still being tuned. Everything else is strictly sequential – M5 needs M4's tool ecosystem; M7 needs M4's demo script to verify against.

---

## Definition of Done (every milestone)

1. Runs from a clean `docker-compose up` – no manual setup steps (needs `ANTHROPIC_API_KEY` in `.env` for LLM calls)
2. Mock mode works without any AutoSys connection
3. README updated with what's new and how to demo it
4. At least one scripted demo path verified end-to-end

---

## Risks to Watch

| Risk | Mitigation |
|---|---|
| Claude API outage during a demo | Cache one known-good response per scenario; LiteLLM provider swap is one config line |
| PII redactor leaks (regex misses an unusual format) | Snapshot test: every tool fixture asserts redacted output; document known limits (e.g. free-text names inside error logs may need NER later) |
| Cross-provider behaviour drift (Claude vs Gemini vs Azure OpenAI tool-calling differences) | Defer real verification until first customer's provider is known; LiteLLM smooths most of it, but tool schemas should stay JSON-Schema-strict |
| Mock data drifts from real AutoSys semantics | Pair mock data design with someone who has seen prod JIL |
| Scenario demos feel scripted / fragile | M6 explicitly rehearses each twice; treat the "rehearsal" as part of done |
| Frontend creeps toward dashboard polish | Keep the "technicians not managers" principle visible in PR review |
| Live adapter reveals tool-shape mismatches | M7 reuses M4 demo as the acceptance test, not a new script |
| AutoSys log content not in REST API | Documented in tech-stack "Live Adapter — REST API Reality"; M7 ships with the path-only fallback (d) by default and a `LOG_FORWARDER_URL_TEMPLATE` extension point for customers with Splunk/ELK |
| `autorep -w` text-parsing for history breaks across AutoSys versions | Snapshot test the parser against captured `autorep -w` outputs from 12.x and 24.x; if a third format appears, fail with a clear error and ask the customer to share their `autorep -w` output |
| Numeric status-code mapping wrong for a customer's AutoSys version | Mapping table is module-local and one-line-override-able via env (`STATUS_CODE_OVERRIDES`); flag any unknown numeric code in the response with a warning log |

---

## Open Decisions Blocking the Plan

(Mirrors tech-stack doc open questions – flagged here in priority order for the build.)

1. ~~LLM for lab development~~ – Resolved (2026-05-28): Claude API for dev, customer-provided in production.
2. ~~Which scenario for M4~~ – Resolved during M4 (2026-05-28): etl_failure (ORA-12541) shipped. Cascading + SLA breach added in M6.
3. ~~AutoSys version for M7 (REST surface shape)~~ – Resolved (2026-05-28): API surface researched and documented in tech-stack. **Open sub-questions for the first live install:** customer's specific AutoSys version (for numeric-code edge cases), auth flavor, cert posture, log strategy. None block work that happens before an actual live target.
4. **Auth for customer delivery** – not blocking demo work; revisit before first customer install. Basic is the default; some security teams may require X-AUTH-TOKEN or JAAS+EEM.
5. ~~PII scope beyond regex~~ – Resolved (2026-05-28): customer environments anonymise person names at source. Regex + denylist is sufficient.
6. **Log retrieval strategy in live mode** – AutoSys REST has no log content endpoint. Default fallback is path-only; opt-in `LOG_FORWARDER_URL_TEMPLATE` for customers with Splunk/ELK/S3. Confirm per customer; not blocking until first live install.

---

## Rough Time Shape (working solo, focused)

| Milestone | Effort estimate |
|---|---|
| M0 | 0.5 day |
| M1 | 1 day |
| M2 | 2–3 days |
| M3 | 2 days |
| M4 | 2 days |
| M5 | 2–3 days |
| M6 | 2 days |
| M7 | depends on AutoSys access – 3–7 days (was 2–5 before; revised after API research surfaced the JIL parser + history text-parsing + log-strategy work) |

Total to first scripted demo (M4): roughly **1 week of focused work**. Total to full demo library (M6): **~2.5 weeks**.

These are deliberately not calendar dates – they assume uninterrupted focus, which never happens. Multiply by your interruption factor.

---

*Review and adjust scope/sequencing. Once agreed, M0 can start immediately – it has no open dependencies.*
