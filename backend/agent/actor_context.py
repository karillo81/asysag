"""Request-scoped 'who is acting' context.

The chat endpoints set the authenticated username here so in-agent tools (e.g.
propose_job_action) can attribute a proposal to a person without threading the
user through every LangGraph call. Set/reset per request.
"""

from __future__ import annotations

import contextvars

_actor: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "actor", default=None
)


def set_actor(username: str | None) -> contextvars.Token:
    return _actor.set(username)


def reset_actor(token: contextvars.Token) -> None:
    _actor.reset(token)


def current_actor() -> str | None:
    return _actor.get()
