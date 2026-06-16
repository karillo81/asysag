import json

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, LLMResult

from agent.capture import (
    TrainingCaptureHandler,
    dataset_stats,
    reset_capture_context,
    set_capture_context,
)


def _emit_call(handler, run_id, input_messages, output_message):
    handler.on_chat_model_start(
        {"kwargs": {"model": "test/model"}},
        [input_messages],
        run_id=run_id,
        invocation_params={"model": "test/model"},
    )
    result = LLMResult(generations=[[ChatGeneration(message=output_message)]])
    handler.on_llm_end(result, run_id=run_id)


def _read_records(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_capture_writes_one_record_per_call(tmp_path):
    path = tmp_path / "llm_calls.jsonl"
    handler = TrainingCaptureHandler(path)
    _emit_call(
        handler,
        "run-1",
        [SystemMessage(content="sys"), HumanMessage(content="is test9 ok?")],
        AIMessage(content="test9 is FAILURE"),
    )
    records = _read_records(path)
    assert len(records) == 1
    rec = records[0]
    assert rec["model"] == "test/model"
    assert rec["messages"][0] == {"role": "system", "content": "sys"}
    assert rec["messages"][1] == {"role": "user", "content": "is test9 ok?"}
    assert rec["output"]["role"] == "assistant"
    assert rec["output"]["content"] == "test9 is FAILURE"
    assert "latency_ms" in rec


def test_capture_records_tool_calls_and_results(tmp_path):
    path = tmp_path / "llm_calls.jsonl"
    handler = TrainingCaptureHandler(path)
    # First call: assistant decides to call a tool.
    ai_with_tool = AIMessage(
        content="",
        tool_calls=[{"id": "c1", "name": "get_job_status", "args": {"job_name": "test9"}}],
    )
    _emit_call(handler, "r1", [HumanMessage(content="status of test9")], ai_with_tool)
    # Second call: the tool result is fed back, assistant answers.
    _emit_call(
        handler,
        "r2",
        [
            HumanMessage(content="status of test9"),
            ToolMessage(content="FAILURE", tool_call_id="c1", name="get_job_status"),
        ],
        AIMessage(content="It failed."),
    )
    records = _read_records(path)
    assert len(records) == 2
    # The tool call is captured in the first record's output.
    assert records[0]["output"]["tool_calls"][0]["name"] == "get_job_status"
    # The tool result message is captured as a 'tool' role in the second record.
    tool_msg = records[1]["messages"][1]
    assert tool_msg["role"] == "tool"
    assert tool_msg["tool_call_id"] == "c1"


def test_capture_redacts_pii(tmp_path):
    path = tmp_path / "llm_calls.jsonl"
    handler = TrainingCaptureHandler(path)
    _emit_call(
        handler,
        "r1",
        [HumanMessage(content="email me at alice@example.com about test9")],
        AIMessage(content="ok"),
    )
    raw = path.read_text(encoding="utf-8")
    assert "alice@example.com" not in raw
    assert "REDACTED_EMAIL" in raw


def test_capture_attributes_user_from_context(tmp_path):
    path = tmp_path / "llm_calls.jsonl"
    handler = TrainingCaptureHandler(path)
    token = set_capture_context("operator-1", "conv-42")
    try:
        _emit_call(handler, "r1", [HumanMessage(content="hi")], AIMessage(content="hello"))
    finally:
        reset_capture_context(token)
    rec = _read_records(path)[0]
    assert rec["user"] == "operator-1"
    assert rec["conversation_id"] == "conv-42"


def test_capture_handler_never_raises_on_bad_input(tmp_path):
    path = tmp_path / "llm_calls.jsonl"
    handler = TrainingCaptureHandler(path)
    # on_llm_end with an unknown run_id is a no-op, not an error.
    handler.on_llm_end(LLMResult(generations=[[]]), run_id="never-started")
    assert not path.exists() or _read_records(path) == []


def test_dataset_stats(tmp_path):
    path = tmp_path / "llm_calls.jsonl"
    assert dataset_stats(path) == {"records": 0, "bytes": 0, "exists": False}
    handler = TrainingCaptureHandler(path)
    _emit_call(handler, "r1", [HumanMessage(content="hi")], AIMessage(content="yo"))
    stats = dataset_stats(path)
    assert stats["records"] == 1 and stats["exists"] is True and stats["bytes"] > 0
