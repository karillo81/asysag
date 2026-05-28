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
