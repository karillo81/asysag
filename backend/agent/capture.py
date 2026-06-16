"""Capture every LLM call to a JSONL dataset for distillation / fine-tuning.

A LangChain callback records each model call — the full input messages (system
prompt + conversation + tool results), the assistant output (content AND tool
calls), model id, token usage and latency — as one OpenAI-format JSON line.
Capturing at the model boundary means we get every intermediate ReAct step,
which is what teaches a student model to use the tools.

PII note: the redactor only scrubs *tool outputs* in-context; a user's typed
message reaches the model unredacted. The persisted dataset has a longer life,
so we run `redact()` over each record before writing it — captured != raw.

Enable with CAPTURE_TRAINING_DATA=true. Off by default.
"""

from __future__ import annotations

import contextvars
import json
import logging
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from langchain_core.callbacks import BaseCallbackHandler

from .redactor import redact

logger = logging.getLogger(__name__)

# Per-request context (who/which conversation), set by the chat endpoints so
# the callback can attribute records without threading args through LangGraph.
_capture_ctx: contextvars.ContextVar[dict[str, Any]] = contextvars.ContextVar(
    "capture_ctx", default={}
)


def set_capture_context(user: str | None, conversation_id: str | None) -> contextvars.Token:
    return _capture_ctx.set({"user": user, "conversation_id": conversation_id})


def reset_capture_context(token: contextvars.Token) -> None:
    _capture_ctx.reset(token)


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _message_to_dict(msg: Any) -> dict[str, Any]:
    """Serialise a LangChain message into an OpenAI-style chat dict."""
    role = {
        "system": "system",
        "human": "user",
        "ai": "assistant",
        "tool": "tool",
    }.get(getattr(msg, "type", ""), getattr(msg, "type", "unknown"))

    out: dict[str, Any] = {"role": role, "content": msg.content}
    tool_calls = getattr(msg, "tool_calls", None)
    if tool_calls:
        out["tool_calls"] = [
            {
                "id": tc.get("id"),
                "name": tc.get("name"),
                "args": tc.get("args", {}),
            }
            for tc in tool_calls
        ]
    tool_call_id = getattr(msg, "tool_call_id", None)
    if tool_call_id:
        out["tool_call_id"] = tool_call_id
    name = getattr(msg, "name", None)
    if name:
        out["name"] = name
    return out


class TrainingCaptureHandler(BaseCallbackHandler):
    """Appends one redacted JSONL record per chat-model call."""

    def __init__(self, dataset_path: Path):
        self._path = dataset_path
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        # Buffer inputs by run_id between on_chat_model_start and on_llm_end.
        self._pending: dict[Any, dict[str, Any]] = {}

    # LangChain calls this for chat models with the resolved message list.
    def on_chat_model_start(self, serialized, messages, *, run_id=None, **kwargs):
        try:
            first = messages[0] if messages else []
            invocation = kwargs.get("invocation_params") or {}
            self._pending[run_id] = {
                "started": time.perf_counter(),
                "messages": [_message_to_dict(m) for m in first],
                "model": invocation.get("model")
                or (serialized or {}).get("kwargs", {}).get("model"),
                "tools": invocation.get("tools") or invocation.get("functions"),
            }
        except Exception:  # never let capture break a live turn
            logger.exception("capture: on_chat_model_start failed")

    def on_llm_end(self, response, *, run_id=None, **kwargs):
        info = self._pending.pop(run_id, None)
        if info is None:
            return
        try:
            gen = response.generations[0][0]
            message = getattr(gen, "message", None)
            output = (
                _message_to_dict(message)
                if message is not None
                else {"role": "assistant", "content": getattr(gen, "text", "")}
            )
            usage = None
            if message is not None:
                usage = getattr(message, "usage_metadata", None)
            ctx = _capture_ctx.get()
            record = {
                "ts": _now_iso(),
                "user": ctx.get("user"),
                "conversation_id": ctx.get("conversation_id"),
                "model": info.get("model"),
                "messages": info["messages"],
                "tools": info.get("tools"),
                "output": output,
                "usage": usage,
                "latency_ms": round((time.perf_counter() - info["started"]) * 1000),
            }
            redacted, _counts = redact(record)
            self._append(redacted)
        except Exception:
            logger.exception("capture: on_llm_end failed")

    def on_llm_error(self, error, *, run_id=None, **kwargs):
        self._pending.pop(run_id, None)

    def _append(self, record: dict[str, Any]) -> None:
        line = json.dumps(record, ensure_ascii=False, default=str)
        with self._lock:
            with self._path.open("a", encoding="utf-8") as f:
                f.write(line + "\n")


def dataset_stats(path: Path) -> dict[str, Any]:
    """Lightweight stats for the admin UI."""
    if not path.exists():
        return {"records": 0, "bytes": 0, "exists": False}
    records = 0
    with path.open("r", encoding="utf-8") as f:
        for _ in f:
            records += 1
    return {"records": records, "bytes": path.stat().st_size, "exists": True}
