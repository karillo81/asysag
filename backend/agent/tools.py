"""LangChain tools wrapping the adapter, persistent memory, and the RAG
knowledge base. Every tool result is run through the redactor before being
returned, so the LLM context never sees PII."""

from __future__ import annotations

import json
from typing import Annotated, Any

from langchain_core.tools import BaseTool, tool

from adapters import AutoSysAdapter, JobNotFound
from adapters.job_actions import (
    ACTIONS,
    get_action,
    is_action_allowed,
    parse_allowlist,
)
from config import settings
from rag import KnowledgeBase

from .actor_context import current_actor
from .memory import Memory
from .redactor import redact


def _serialise(payload: Any) -> str:
    """Convert tool output to a stable JSON string for the LLM context.

    Always runs through the redactor first.
    """
    cleaned, _ = redact(payload)
    try:
        return json.dumps(cleaned, default=str, ensure_ascii=False)
    except (TypeError, ValueError):
        return repr(cleaned)


def make_tools(
    adapter: AutoSysAdapter,
    memory: Memory,
    knowledge_base: KnowledgeBase,
    action_store: Any | None = None,
) -> list[BaseTool]:
    """Build the LangChain tool list bound to specific dependencies.

    The write-action tool (propose_job_action) is only included when
    settings.writes_enabled AND an action_store is supplied — read-only
    deployments never even advertise the capability.
    """

    @tool
    def get_job_status(job_name: Annotated[str, "AutoSys job or box name, e.g. etl_load_facts"]) -> str:
        """Return the current run status for a single AutoSys job.

        Use this when the user asks whether a specific job is healthy,
        running, failed, or what its last run did. Job names are
        case-sensitive and must match exactly.
        """
        try:
            return _serialise(adapter.get_job_status(job_name))
        except JobNotFound:
            return _serialise({"error": "unknown_job", "job_name": job_name})

    @tool
    def list_jobs(name_filter: Annotated[str | None, "Optional substring filter, e.g. 'etl'"] = None) -> str:
        """List AutoSys jobs and their current statuses.

        Use this to find candidate jobs by name, or to scan for failures.
        Pass a substring as name_filter to narrow the list — for example
        'etl' returns every job whose name contains 'etl'.
        """
        return _serialise(adapter.list_jobs(name_filter=name_filter))

    @tool
    def get_job_history(
        job_name: Annotated[str, "AutoSys job name"],
        days: Annotated[int, "Trailing window in days, 1-90"] = 7,
    ) -> str:
        """Return historical runs of a job within the trailing window.

        Use this to investigate whether a failure is recurring, or to
        compare current run characteristics (duration, return code)
        against the recent baseline.
        """
        try:
            return _serialise(adapter.get_job_history(job_name, days=days))
        except JobNotFound:
            return _serialise({"error": "unknown_job", "job_name": job_name})

    @tool
    def get_dependencies(job_name: Annotated[str, "AutoSys job name"]) -> str:
        """Return upstream and downstream job names for an AutoSys job.

        Use this to understand cascade impact — what failed upstream that
        caused this job to fail, or what downstream jobs are blocked.
        """
        try:
            return _serialise(adapter.get_dependencies(job_name))
        except JobNotFound:
            return _serialise({"error": "unknown_job", "job_name": job_name})

    @tool
    def get_job_log(
        job_name: Annotated[str, "AutoSys job name"],
        stream: Annotated[str, "'err' for stderr, 'out' for stdout"] = "err",
    ) -> str:
        """Return the stderr or stdout excerpt from the most recent run of a job.

        Use this when investigating a failure — the log usually contains the
        exact error text and stack trace. Returns an empty string if no log
        is available for that stream.
        """
        try:
            log = adapter.get_job_log(job_name, stream=stream)
            return _serialise({"job_name": job_name, "stream": stream, "log": log})
        except JobNotFound:
            return _serialise({"error": "unknown_job", "job_name": job_name})
        except ValueError as e:
            return _serialise({"error": str(e)})

    @tool
    def find_similar_incidents(
        job_name: Annotated[str | None, "Optional job name filter"] = None,
        error_pattern: Annotated[
            str | None,
            "Optional substring to match against error signature or excerpt (e.g. 'ORA-12541')",
        ] = None,
    ) -> str:
        """Search the persistent incident history (SQLite) for past failures.

        Use this when a job has failed to find out whether the same problem
        has occurred before and how it was resolved. Each result includes
        the date, error excerpt, full resolution steps, and resolved_by team.
        """
        try:
            results = memory.find_similar_incidents(
                job_name=job_name, error_pattern=error_pattern
            )
            return _serialise({"matches": results, "count": len(results)})
        except ValueError as e:
            return _serialise({"error": str(e)})

    @tool
    def search_knowledge_base(
        query: Annotated[str, "Natural-language query"],
        k: Annotated[int, "Number of chunks to return, 1-10"] = 3,
    ) -> str:
        """Search Broadcom AutoSys documentation and operational runbooks.

        Use this when the user asks about AutoSys concepts (job states,
        sendevent, conditions), or when investigating a failure and you
        need procedural guidance. Returns the top-k most relevant chunks
        with their source file paths so you can cite them.
        """
        try:
            chunks = knowledge_base.search(query, k=k)
            return _serialise({"chunks": chunks, "count": len(chunks)})
        except Exception as e:
            return _serialise({"error": str(e)})

    @tool
    def propose_job_action(
        action: Annotated[str, "Action key, e.g. 'on_hold', 'force_start_job', 'cancel_job'"],
        target: Annotated[str, "Job name (or machine name for machine_* actions)"],
        params: Annotated[
            dict | None,
            "Extra params some actions need, e.g. {'status':'SUCCESS'} for change_status, "
            "{'comment':'...'} for comment, {'priority':5} for change_priority",
        ] = None,
    ) -> str:
        """Propose a WRITE action on an AutoSys job for human approval.

        IMPORTANT: this does NOT execute anything. It only stages the action; a
        human must explicitly confirm it in the UI before it runs. Use it when
        the user clearly asks to change a job (start, hold, ice, kill, restart,
        change status, etc.). Never claim the action was performed — report that
        it has been proposed and is awaiting confirmation. Always confirm the
        target job exists first (get_job_status) if you're unsure.
        """
        act = get_action(action)
        if act is None:
            return _serialise({
                "error": "unknown_action", "action": action,
                "available_actions": sorted(ACTIONS),
            })
        allowlist = parse_allowlist(settings.writes_allowlist)
        if not is_action_allowed(action, allowlist):
            return _serialise({
                "error": "action_not_allowed", "action": action,
                "message": "This action is not enabled by the current allowlist.",
            })
        if act.target == "job":
            try:
                adapter.get_job_status(target)
            except JobNotFound:
                return _serialise({"error": "unknown_job", "job_name": target})
        proposal = action_store.propose(
            action, target,
            tier=act.tier, destructive=act.destructive,
            params=params, proposed_by=current_actor(),
        )
        return _serialise({
            "status": "proposed",
            "action_id": proposal["id"],
            "action": action,
            "label": act.label,
            "target": target,
            "tier": act.tier,
            "destructive": act.destructive,
            "summary": act.summary,
            "requires_role": act.min_role,
            "message": (
                f"Proposed '{act.label}' on '{target}'. Nothing has been executed — "
                f"a human must confirm it in the UI."
            ),
        })

    tools = [
        get_job_status,
        list_jobs,
        get_job_history,
        get_dependencies,
        get_job_log,
        find_similar_incidents,
        search_knowledge_base,
    ]
    if settings.writes_enabled and action_store is not None:
        tools.append(propose_job_action)
    return tools
