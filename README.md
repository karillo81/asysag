# AutoSys Agent

On-premise LLM agent for AutoSys operations. Customer brings the LLM endpoint (Claude / Gemini / OpenAI / Azure OpenAI); agent + customer data stay on customer infrastructure.

See [autosys-agent-tech-stack.md](autosys-agent-tech-stack.md) and [autosys-agent-development-plan.md](autosys-agent-development-plan.md).

---

## Status

**M0 – Scaffolding** complete. Backend `/health` responds; frontend dev server renders placeholder; Vite proxy forwards `/api/*` to backend.

Not yet implemented: data adapter, mock data, LangGraph agent, redactor, real chat UI. See the development plan for milestone sequencing.

---

## Run natively (today's path)

Prereqs: Python 3.11+, Node 20+.

### Backend

```bash
cd backend
python -m venv .venv
.venv/Scripts/python.exe -m pip install -e .          # Windows
# source .venv/bin/activate && pip install -e .       # Linux/macOS
uvicorn main:app --reload
```

Backend listens on `http://127.0.0.1:8000`. Verify: `curl http://127.0.0.1:8000/health`.

### Frontend

```bash
cd frontend
npm install
npm run dev
```

Frontend on `http://localhost:5173`. The dev server proxies `/api/*` to the backend on `:8000`.

### Configure

Copy `.env.example` to `.env` and fill in the credentials matching your `LITELLM_MODEL`. `.env` is gitignored.

**Login gate.** The frontend won't render until you sign in. Default `AUTH_USERNAME=root` and `AUTH_PASSWORD=changeme` — change the password in `.env` before exposing the app. Session is an HttpOnly cookie signed with `SESSION_SECRET`; leave that unset for dev (auto-generated on each backend start) or set it for stable sessions across restarts.

Live-mode-only knobs worth noting:

- `LOG_FORWARDER_URL_TEMPLATE` — point at Splunk/ELK/S3 if you want real log content; otherwise `get_job_log` falls back to the local-mount path and then to a path-only message.
- `AUTOSYS_LOG_MOUNT_ROOT` — local directory where the AutoSys agent's `job_logs/` is mounted (NFS/SMB/sshfs/bind). When set, `get_job_log` reads `{mount_root}/{filename}` directly. Cheapest way to get real log content when the agent host is reachable.
- `AUTOSYS_LOG_SSH_CONFIG` — path to a JSON host map (AutoSys `machine` name → SSH endpoint). When set, `get_job_log` SFTPs the configured `std_*_file` off the host that ran the job — tried after the local mount, before the path-only fallback. Requires the optional `paramiko` dependency (`pip install '.[ssh]'`). Host-key verification is on by default (`AUTOSYS_LOG_SSH_INSECURE_SKIP_HOST_KEY=true` to disable); only the last `AUTOSYS_LOG_SSH_MAX_BYTES` of each log are read, cached for `AUTOSYS_LOG_CACHE_TTL_SECONDS`. See [`backend/adapters/log_orchestrator.py`](backend/adapters/log_orchestrator.py) for the host-map shape.
- `STATUS_CODE_OVERRIDES` — patch the numeric status-code table (`"4=SUCCESS,5=FAILURE,99=CUSTOM"`) without a code change.
- `AUTOSYS_AUTOREP_HISTORY_STRATEGY` — how `get_job_history` fetches runs. Default `walk-runs` iterates `autorep -j NAME -w -r N` for N=0,1,... until autorep reports no more runs; one HTTP call per historical run. `days-flag` does a single `autorep -j NAME -w -d {days}` instead — faster, but field-tested AutoSys instances return only the latest run summary that way.

---

## Run via Docker (tomorrow's path)

Requires Docker + Docker Compose. On Windows we use WSL2 (Ubuntu distro) with native Docker.

```bash
docker compose up --build
```

Frontend at `http://localhost:5173`, backend at `http://localhost:8000`. The frontend nginx config proxies `/api/*` to the backend container.

---

## Repository layout

```
autosys-agent/
├── backend/                      # FastAPI app
│   ├── main.py                   # /health and (later) /chat
│   ├── config.py                 # env-driven settings
│   ├── agent/                    # LangGraph graph, tools, redactor, memory  (M2+)
│   ├── adapters/                 # mock_adapter, live_adapter             (M1, M7)
│   ├── rag/                      # ChromaDB + ingestion                   (M5)
│   ├── pyproject.toml
│   └── Dockerfile
├── frontend/                     # Vite + React + Tailwind
│   ├── src/
│   ├── vite.config.js
│   ├── nginx.conf                # used by the Docker build only
│   └── Dockerfile
├── mock-data/                    # JSON fixtures (M1+) + scenarios (M4+)
├── docs/                         # Broadcom docs for RAG (M5)
├── docker-compose.yml
├── .env.example
└── autosys-agent-*.md            # stack + plan
```
