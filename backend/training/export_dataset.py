"""Convert captured LLM calls into an SFT-ready conversational dataset.

Input: state/training/llm_calls.jsonl — one record per LLM call, written by
agent/capture.py:
    {"messages": [...openai-style...], "tools": [...], "output": {...}, ...}

Output: train.jsonl / val.jsonl in HuggingFace "conversational" format:
    {"messages": [ ...inputs..., <assistant target> ], "tools": [...]}

Each record becomes one training example: the model learns to produce the
captured `output` (content AND tool calls) given the inputs. Because capture
records every intermediate ReAct step, the dataset teaches tool-use too.

DISTILLATION REMINDER: only export data captured while the STRONG teacher
model was serving (e.g. gemini-3). Exporting Gemma's own outputs to fine-tune
Gemma is self-training (collapse), not distillation.

Run on any machine (no GPU needed):
    python export_dataset.py --input llm_calls.jsonl --out-dir ./data
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def _to_hf_tool_calls(tool_calls: list[dict]) -> list[dict]:
    """capture.py shape {id,name,args} -> OpenAI/HF {id,type,function{...}}."""
    out = []
    for tc in tool_calls or []:
        out.append(
            {
                "id": tc.get("id") or "",
                "type": "function",
                "function": {
                    "name": tc.get("name"),
                    "arguments": json.dumps(tc.get("args", {}), ensure_ascii=False),
                },
            }
        )
    return out


def _normalise_message(msg: dict) -> dict:
    """Normalise a stored message into the shape chat templates expect."""
    role = msg.get("role", "user")
    out: dict = {"role": role, "content": msg.get("content") or ""}
    if msg.get("tool_calls"):
        out["tool_calls"] = _to_hf_tool_calls(msg["tool_calls"])
        # Assistant tool-call turns conventionally carry empty content.
        out["content"] = out["content"] or ""
    if role == "tool" and msg.get("tool_call_id"):
        out["tool_call_id"] = msg["tool_call_id"]
    if msg.get("name"):
        out["name"] = msg["name"]
    return out


def _build_example(record: dict) -> dict | None:
    messages = record.get("messages") or []
    output = record.get("output")
    if not messages or not output:
        return None
    convo = [_normalise_message(m) for m in messages]
    convo.append(_normalise_message(output))
    example = {"messages": convo}
    if record.get("tools"):
        example["tools"] = record["tools"]
    return example


def _dedupe_key(example: dict) -> str:
    return json.dumps(example, sort_keys=True, ensure_ascii=False)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True, help="llm_calls.jsonl from capture")
    ap.add_argument("--out-dir", default="./data")
    ap.add_argument("--val-frac", type=float, default=0.1)
    ap.add_argument("--min-messages", type=int, default=2,
                    help="drop trivially short conversations")
    args = ap.parse_args()

    src = Path(args.input)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    seen: set[str] = set()
    examples: list[dict] = []
    total = 0
    with src.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            total += 1
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            ex = _build_example(record)
            if ex is None or len(ex["messages"]) < args.min_messages:
                continue
            key = _dedupe_key(ex)
            if key in seen:
                continue
            seen.add(key)
            examples.append(ex)

    # Deterministic split (no RNG) — every Nth example to validation.
    n_val = max(1, int(len(examples) * args.val_frac)) if examples else 0
    step = max(1, len(examples) // n_val) if n_val else len(examples) + 1
    train, val = [], []
    for i, ex in enumerate(examples):
        (val if (n_val and i % step == 0 and len(val) < n_val) else train).append(ex)

    def _write(path: Path, rows: list[dict]) -> None:
        with path.open("w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")

    _write(out_dir / "train.jsonl", train)
    _write(out_dir / "val.jsonl", val)
    print(f"read {total} calls -> {len(examples)} unique examples "
          f"({len(train)} train / {len(val)} val) in {out_dir}")


if __name__ == "__main__":
    main()
