# Distillation: capture → fine-tune Gemma 4 → serve

Turn the agent's live traffic into a fine-tuned Gemma 4 that imitates the
strong model — including tool use. Three stages.

> **Distill from the teacher, not from Gemma.** Capture while a *strong* model
> (e.g. `gemini/gemini-3-flash-preview`) is serving. Fine-tuning Gemma on
> Gemma's own outputs is self-training (model collapse), not improvement.

## 1. Capture (on the app server)

Turn capture on — Settings UI → **Capture LLM calls for training = true**, then
**restart** (or set `CAPTURE_TRAINING_DATA=true`). Make sure the live model is
the **teacher**, not Gemma, for this phase. Every LLM call (incl. each ReAct
tool step) is appended, redacted, to:

    state/training/llm_calls.jsonl     # inside the agent-state volume

Pull it down (admin):

    curl -sk https://<host>/api/admin/training-data            # stats
    curl -sk https://<host>/api/admin/training-data/download -o llm_calls.jsonl

Aim for a few thousand good examples before training.

## 2. Export → SFT dataset (any machine, no GPU)

    python export_dataset.py --input llm_calls.jsonl --out-dir ./data

Produces `data/train.jsonl` + `data/val.jsonl` in HuggingFace conversational
format (messages + tools), deduped, with the captured `output` as the target.

## 3. Fine-tune Gemma 4 (GPU box — NOT the app server, which has no GPU)

    pip install "transformers>=4.45" "trl>=0.11" "peft>=0.13" \
                "bitsandbytes>=0.43" "accelerate>=0.34" datasets
    huggingface-cli login            # Gemma is gated
    python finetune_gemma.py --model google/gemma-4-12b-it \
                             --data-dir ./data --out ./gemma4-autosys-lora

QLoRA (4-bit + LoRA) fits a 12B on a single 24 GB GPU. Tune `--lora-r`,
`--lr`, `--epochs`, `--max-seq-len` for your data/GPU.

## 4. Serve the adapter and point the agent at it

Serve base + LoRA with vLLM (OpenAI-compatible, and the correct Gemma-4 tool
parser — default TGI returns empty tool calls):

    vllm serve google/gemma-4-12b-it \
      --enable-lora --lora-modules autosys=./gemma4-autosys-lora \
      --enable-auto-tool-choice --tool-call-parser gemma4 \
      --reasoning-parser gemma4 --chat-template tool_chat_template_gemma4.jinja

Then in the app's **Settings**:

    LITELLM_MODEL    = openai/autosys           # the served LoRA name
    LITELLM_API_BASE = http://<gpu-host>:8000/v1
    LITELLM_API_KEY  = <vllm key or any dummy>

Restart. **Evaluate** against held-out turns (does it call the right tools?
quote return codes verbatim?). Iterate: capture more, re-export, re-train.

## Notes & gotchas

- **Tool calling is the make-or-break.** Gemma 4 emits a custom (non-JSON) tool
  format; only vLLM's `gemma4` parser reliably fills the OpenAI `tool_calls`
  field. Hosted **Google AI Studio** (`gemini/gemma-4-31b-it`) also parses it
  correctly if you'd rather skip self-hosting for evaluation.
- `assistant_only_loss=True` needs a chat template with a `{% generation %}`
  marker; if Gemma's template lacks it, drop that flag (full-sequence loss).
- Privacy: the dataset is redacted at capture, but review a sample before
  sharing or uploading anywhere.
