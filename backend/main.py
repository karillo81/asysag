import json

from fastapi import Depends, FastAPI, HTTPException, Query, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from adapters import JobNotFound, MockAdapter, get_adapter
from agent import build_agent, run_turn, stream_turn
from agent.memory import Memory
from auth import (
    SESSION_COOKIE_NAME,
    current_account,
    current_user,
    make_session_token,
    require_admin,
    user_store,
    verify_credentials,
)
from users import (
    InvalidUserInput,
    LastAdminError,
    UserError,
    UserExists,
    UserNotFound,
)
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


class LoginRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=64)
    password: str = Field(..., min_length=1, max_length=256)


class CreateAccountRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=64)
    password: str = Field(..., min_length=1, max_length=256)
    role: str = Field(..., pattern="^(admin|operator)$")


class UpdateAccountRequest(BaseModel):
    password: str | None = Field(default=None, min_length=1, max_length=256)
    role: str | None = Field(default=None, pattern="^(admin|operator)$")
    is_active: bool | None = None


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(..., min_length=1, max_length=256)
    new_password: str = Field(..., min_length=1, max_length=256)


@app.get("/health")
def health():
    return {
        "status": "ok",
        "mode": settings.autosys_mode,
        "model": settings.litellm_model,
    }


@app.post("/login")
def login(req: LoginRequest, response: Response):
    account = verify_credentials(req.username, req.password)
    if account is None:
        raise HTTPException(status_code=401, detail="invalid credentials")
    token = make_session_token(account["username"])
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=token,
        max_age=settings.session_ttl_seconds,
        httponly=True,
        samesite="lax",
        secure=settings.session_cookie_secure,
    )
    return {"username": account["username"], "role": account["role"]}


@app.post("/logout")
def logout():
    response = Response(status_code=204)
    response.delete_cookie(SESSION_COOKIE_NAME)
    return response


@app.get("/me")
def me(account: dict = Depends(current_account)):
    return {"username": account["username"], "role": account["role"]}


@app.get("/jobs")
def list_jobs(
    filter: str | None = Query(default=None, description="Substring match on job name"),
    _user: str = Depends(current_user),
):
    try:
        return adapter.list_jobs(name_filter=filter)
    except NotImplementedError as e:
        raise HTTPException(status_code=501, detail=str(e))


@app.get("/jobs/{job_name}")
def get_job(job_name: str, _user: str = Depends(current_user)):
    try:
        return adapter.get_job_status(job_name)
    except JobNotFound as e:
        raise HTTPException(status_code=404, detail=str(e))
    except NotImplementedError as e:
        raise HTTPException(status_code=501, detail=str(e))


@app.get("/jobs/{job_name}/history")
def get_history(
    job_name: str,
    days: int = Query(default=7, ge=1, le=90),
    _user: str = Depends(current_user),
):
    try:
        return adapter.get_job_history(job_name, days=days)
    except JobNotFound as e:
        raise HTTPException(status_code=404, detail=str(e))
    except NotImplementedError as e:
        raise HTTPException(status_code=501, detail=str(e))


@app.get("/jobs/{job_name}/dependencies")
def get_dependencies(job_name: str, _user: str = Depends(current_user)):
    try:
        return adapter.get_dependencies(job_name)
    except JobNotFound as e:
        raise HTTPException(status_code=404, detail=str(e))
    except NotImplementedError as e:
        raise HTTPException(status_code=501, detail=str(e))


@app.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest, _user: str = Depends(current_user)):
    history = [t.model_dump() for t in req.history]
    result = run_turn(agent, req.message, history=history)
    return ChatResponse(**result)


@app.post("/chat/stream")
async def chat_stream(req: ChatRequest, _user: str = Depends(current_user)):
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
def list_scenarios(_user: str = Depends(current_user)):
    active = adapter.scenario if isinstance(adapter, MockAdapter) else None
    return {
        "active": active,
        "scenarios": list(_load_scenarios().values()),
    }


@app.post("/scenarios/{name}/reset")
def reset_scenario(name: str, _user: str = Depends(current_user)):
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


# --- Account management (admin-only, except own-password change) ---------

def _account_http_error(exc: UserError) -> HTTPException:
    code = {
        UserExists: 409,
        UserNotFound: 404,
        LastAdminError: 409,
        InvalidUserInput: 422,
    }.get(type(exc), 400)
    return HTTPException(status_code=code, detail=str(exc))


@app.get("/accounts")
def list_accounts(_admin: dict = Depends(require_admin)):
    return {"accounts": user_store.list()}


@app.post("/accounts", status_code=201)
def create_account(req: CreateAccountRequest, admin: dict = Depends(require_admin)):
    try:
        return user_store.create(
            req.username, req.password, req.role, created_by=admin["username"]
        )
    except UserError as e:
        raise _account_http_error(e)


@app.patch("/accounts/{username}")
def update_account(
    username: str, req: UpdateAccountRequest, admin: dict = Depends(require_admin)
):
    if user_store.get(username) is None:
        raise HTTPException(status_code=404, detail=f"unknown user: {username}")
    # An admin cannot lock themselves out: role/active changes to your own
    # account must go through a different admin. Password reset is fine.
    if username == admin["username"] and (
        req.role is not None or req.is_active is not None
    ):
        raise HTTPException(
            status_code=400,
            detail="cannot change your own role or active state; use another admin",
        )
    try:
        if req.password is not None:
            user_store.set_password(username, req.password)
        if req.role is not None:
            user_store.set_role(username, req.role)
        if req.is_active is not None:
            user_store.set_active(username, req.is_active)
    except UserError as e:
        raise _account_http_error(e)
    return user_store.get_account(username)


@app.delete("/accounts/{username}", status_code=204)
def delete_account(username: str, admin: dict = Depends(require_admin)):
    if username == admin["username"]:
        raise HTTPException(status_code=400, detail="cannot delete your own account")
    try:
        user_store.delete(username)
    except UserError as e:
        raise _account_http_error(e)
    return Response(status_code=204)


@app.post("/account/password", status_code=204)
def change_own_password(
    req: ChangePasswordRequest, account: dict = Depends(current_account)
):
    if verify_credentials(account["username"], req.current_password) is None:
        raise HTTPException(status_code=403, detail="current password is incorrect")
    try:
        user_store.set_password(account["username"], req.new_password)
    except UserError as e:
        raise _account_http_error(e)
    return Response(status_code=204)
