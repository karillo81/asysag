import json

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from adapters import JobNotFound, MockAdapter, get_adapter
from agent import build_agent, run_turn, stream_turn
from agent.memory import Memory
from config import settings
from rag import KnowledgeBase

app = FastAPI(title="AutoSys Agent", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

adapter = get_adapter(settings.autosys_mode, settings.mock_data_dir)
memory = Memory(
    db_path=settings.state_dir / "agent.sqlite",
    seed_incidents_path=settings.mock_data_dir / "incidents" / "incident_history.json",
)
knowledge_base = KnowledgeBase(
    persist_dir=settings.state_dir / "chromadb",
    source_dirs=[settings.docs_dir, settings.mock_data_dir / "runbooks"],
)
knowledge_base.ingest()
agent = build_agent(adapter, memory, knowledge_base)


class ChatTurn(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=4000)
    history: list[ChatTurn] = Field(default_factory=list)


class ChatResponse(BaseModel):
    reply: str
    tool_calls: list[dict]


@app.get("/health")
def health():
    return {
        "status": "ok",
        "mode": settings.autosys_mode,
        "model": settings.litellm_model,
    }


@app.get("/jobs")
def list_jobs(filter: str | None = Query(default=None, description="Substring match on job name")):
    try:
        return adapter.list_jobs(name_filter=filter)
    except NotImplementedError as e:
        raise HTTPException(status_code=501, detail=str(e))


@app.get("/jobs/{job_name}")
def get_job(job_name: str):
    try:
        return adapter.get_job_status(job_name)
    except JobNotFound as e:
        raise HTTPException(status_code=404, detail=str(e))
    except NotImplementedError as e:
        raise HTTPException(status_code=501, detail=str(e))


@app.get("/jobs/{job_name}/history")
def get_history(job_name: str, days: int = Query(default=7, ge=1, le=90)):
    try:
        return adapter.get_job_history(job_name, days=days)
    except JobNotFound as e:
        raise HTTPException(status_code=404, detail=str(e))
    except NotImplementedError as e:
        raise HTTPException(status_code=501, detail=str(e))


@app.get("/jobs/{job_name}/dependencies")
def get_dependencies(job_name: str):
    try:
        return adapter.get_dependencies(job_name)
    except JobNotFound as e:
        raise HTTPException(status_code=404, detail=str(e))
    except NotImplementedError as e:
        raise HTTPException(status_code=501, detail=str(e))


@app.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest):
    history = [t.model_dump() for t in req.history]
    result = run_turn(agent, req.message, history=history)
    return ChatResponse(**result)


@app.post("/chat/stream")
async def chat_stream(req: ChatRequest):
    history = [t.model_dump() for t in req.history]

    async def event_gen():
        try:
            async for event in stream_turn(agent, req.message, history=history):
                etype = event.pop("type")
                yield f"event: {etype}\ndata: {json.dumps(event)}\n\n"
        except Exception as exc:
            yield f"event: error\ndata: {json.dumps({'message': str(exc)})}\n\n"

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _load_scenarios() -> dict[str, dict]:
    scenarios_dir = settings.mock_data_dir / "scenarios"
    if not scenarios_dir.exists():
        return {}
    out: dict[str, dict] = {}
    for path in sorted(scenarios_dir.glob("*.json")):
        try:
            with path.open(encoding="utf-8") as f:
                manifest = json.load(f)
        except (OSError, json.JSONDecodeError):
            continue
        name = manifest.get("name") or path.stem
        out[name] = manifest
    return out


@app.get("/scenarios")
def list_scenarios():
    active = adapter.scenario if isinstance(adapter, MockAdapter) else None
    return {
        "active": active,
        "scenarios": list(_load_scenarios().values()),
    }


@app.post("/scenarios/{name}/reset")
def reset_scenario(name: str):
    scenarios = _load_scenarios()
    if name not in scenarios:
        raise HTTPException(status_code=404, detail=f"unknown scenario: {name}")
    if not isinstance(adapter, MockAdapter):
        raise HTTPException(
            status_code=400,
            detail="scenarios can only be loaded in mock mode",
        )
    try:
        adapter.load_scenario(name)
    except FileNotFoundError as e:
        raise HTTPException(status_code=500, detail=str(e))
    return {"active": adapter.scenario, "manifest": scenarios[name]}
