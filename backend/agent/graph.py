"""LangGraph ReAct agent. Single-tool loop, no branching — the prebuilt
create_react_agent gives us exactly the receive → plan → call_tool → respond
shape the development plan calls for. Redaction sits inside each tool, so the
LLM context never sees PII (verified by tests/test_redactor.py)."""

from __future__ import annotations

from typing import AsyncIterator

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_litellm import ChatLiteLLM
from langgraph.prebuilt import create_react_agent

from adapters import AutoSysAdapter
from config import settings
from rag import KnowledgeBase

from .memory import Memory
from .tools import make_tools

SYSTEM_PROMPT = (
    "You are an AutoSys operator's assistant. You help technicians investigate "
    "job failures, cascade impacts, run history, and resolution. Nothing else.\n\n"
    "Rules:\n"
    "- Always call tools to get real data; never guess job names, statuses, "
    "  return codes, error text, timestamps, or resolutions.\n"
    "- If a job name is unfamiliar, call list_jobs first.\n"
    "- Be terse. Prefer short paragraphs and small lists. No filler.\n"
    "- When a job has failed, mention return code and any error string you got "
    "  verbatim — operators copy these.\n"
    "- For failure investigation, the useful flow is usually: get_job_status -> "
    "  get_job_log (for the exact error) -> find_similar_incidents (for past "
    "  resolutions). Quote the prior resolution if one matches.\n"
    "- When recommending a fix, prefer steps that were actually used to resolve "
    "  a similar past incident over generic advice.\n"
    "- For AutoSys concepts (job states, sendevent, conditions, ON_HOLD vs ON_ICE), "
    "  call search_knowledge_base. Cite the source file path from the result so "
    "  the operator can read the full doc if they want more.\n"
    "- If a tool returns {\"error\": \"unknown_job\"}, say so plainly and suggest "
    "  list_jobs to find the right name.\n"
    "- Language is presentation, not topic. If the user asks for your answer in a "
    "  specific language, or to translate your previous AutoSys answer, comply and "
    "  write the AutoSys answer in that language. This is in-scope; do NOT refuse it. "
    "  Keep verbatim tool strings untranslated — error text, return codes, job and "
    "  box names, log lines, and file paths stay exactly as the tools returned them, "
    "  because operators copy these; translate only the surrounding explanation.\n"
    "- Off-topic refusals: if the user asks about anything not related to "
    "  AutoSys jobs, failures, logs, history, dependencies, incidents, or "
    "  runbooks (e.g. general knowledge, geography, opinions, other systems, "
    "  small talk), reply with EXACTLY ONE short sentence stating you only "
    "  help with AutoSys. A request to present or translate an AutoSys answer "
    "  into another language is NOT off-topic — handle it per the language rule "
    "  above. Only genuinely unrelated content triggers this refusal. Do not "
    "  answer the off-topic question. Do not add a follow-up offer, apology, or "
    "  trailing question. The customer pays per token; brevity here is required."
)


def build_agent(
    adapter: AutoSysAdapter,
    memory: Memory,
    knowledge_base: KnowledgeBase,
):
    """Construct the LangGraph ReAct agent bound to the given backends."""
    model = ChatLiteLLM(
        model=settings.litellm_model,
        temperature=0,
        streaming=True,
    )
    tools = make_tools(adapter, memory, knowledge_base)
    return create_react_agent(model, tools)


def _assemble_messages(message: str, history: list[dict] | None) -> list:
    msgs = [SystemMessage(content=SYSTEM_PROMPT)]
    for entry in history or []:
        if entry["role"] == "user":
            msgs.append(HumanMessage(content=entry["content"]))
        elif entry["role"] == "assistant":
            msgs.append(AIMessage(content=entry["content"]))
    msgs.append(HumanMessage(content=message))
    return msgs


def run_turn(agent, message: str, history: list[dict] | None = None) -> dict:
    """Synchronous chat turn. Returns the assistant reply plus tool calls."""
    msgs = _assemble_messages(message, history)
    result = agent.invoke({"messages": msgs})
    all_messages = result["messages"]

    tool_calls: list[dict] = []
    for m in all_messages:
        for call in getattr(m, "tool_calls", []) or []:
            tool_calls.append({"name": call["name"], "args": call.get("args", {})})

    reply = all_messages[-1].content if all_messages else ""
    return {"reply": reply, "tool_calls": tool_calls}


async def stream_turn(
    agent, message: str, history: list[dict] | None = None
) -> AsyncIterator[dict]:
    """Stream chat events as dicts. Yields:
      {"type": "tool_call", "name": str, "args": dict}
      {"type": "token", "text": str}
      {"type": "done"}
    """
    msgs = _assemble_messages(message, history)
    async for event in agent.astream_events({"messages": msgs}, version="v2"):
        kind = event.get("event")
        if kind == "on_chat_model_stream":
            chunk = event["data"].get("chunk")
            if chunk is None:
                continue
            text = getattr(chunk, "content", "")
            if isinstance(text, list):
                # Some providers return content as a list of parts; flatten text parts.
                text = "".join(
                    part.get("text", "") if isinstance(part, dict) else str(part)
                    for part in text
                )
            if text:
                yield {"type": "token", "text": text}
        elif kind == "on_tool_start":
            yield {
                "type": "tool_call",
                "name": event.get("name", "unknown_tool"),
                "args": event["data"].get("input", {}),
            }
    yield {"type": "done"}
