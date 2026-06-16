"""LoRA/QLoRA fine-tune of Gemma 4 on the captured agent dataset (distillation).

This runs on a GPU machine (NOT the 2-vCPU app box). It loads Gemma 4 in
4-bit, attaches LoRA adapters, and trains on the conversational dataset from
export_dataset.py using TRL's SFTTrainer. The tokenizer's chat template renders
each example (tool calls included), so the student learns the agent's tool-use
behaviour, not just final answers.

This is a STARTING SKELETON — tune model id, ranks, LR, and sequence length for
your GPU and data volume. As a rule of thumb you want a few thousand good
examples before fine-tuning earns its keep.

Quickstart (GPU box):
    pip install "transformers>=4.45" "trl>=0.11" "peft>=0.13" \
                "bitsandbytes>=0.43" "accelerate>=0.34" datasets
    huggingface-cli login            # Gemma is a gated model
    python finetune_gemma.py --data-dir ./data --out ./gemma4-autosys-lora
"""

from __future__ import annotations

import argparse


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="google/gemma-4-12b-it",
                    help="base model to distil into (HF id)")
    ap.add_argument("--data-dir", default="./data")
    ap.add_argument("--out", default="./gemma4-autosys-lora")
    ap.add_argument("--epochs", type=float, default=2.0)
    ap.add_argument("--lr", type=float, default=2e-4)
    ap.add_argument("--batch-size", type=int, default=1)
    ap.add_argument("--grad-accum", type=int, default=8)
    ap.add_argument("--max-seq-len", type=int, default=4096)
    ap.add_argument("--lora-r", type=int, default=16)
    ap.add_argument("--lora-alpha", type=int, default=32)
    args = ap.parse_args()

    # Imports are inside main so --help works without the heavy GPU stack.
    import torch
    from datasets import load_dataset
    from peft import LoraConfig
    from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig
    from trl import SFTConfig, SFTTrainer

    tokenizer = AutoTokenizer.from_pretrained(args.model)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token

    quant = BitsAndBytesConfig(
        load_in_4bit=True,
        bnb_4bit_quant_type="nf4",
        bnb_4bit_compute_dtype=torch.bfloat16,
        bnb_4bit_use_double_quant=True,
    )
    model = AutoModelForCausalLM.from_pretrained(
        args.model,
        quantization_config=quant,
        torch_dtype=torch.bfloat16,
        device_map="auto",
        attn_implementation="eager",  # safest default; switch to flash_attention_2 if available
    )

    lora = LoraConfig(
        r=args.lora_r,
        lora_alpha=args.lora_alpha,
        lora_dropout=0.05,
        bias="none",
        task_type="CAUSAL_LM",
        target_modules=["q_proj", "k_proj", "v_proj", "o_proj",
                        "gate_proj", "up_proj", "down_proj"],
    )

    dataset = load_dataset(
        "json",
        data_files={
            "train": f"{args.data_dir}/train.jsonl",
            "validation": f"{args.data_dir}/val.jsonl",
        },
    )

    cfg = SFTConfig(
        output_dir=args.out,
        num_train_epochs=args.epochs,
        per_device_train_batch_size=args.batch_size,
        gradient_accumulation_steps=args.grad_accum,
        learning_rate=args.lr,
        max_seq_length=args.max_seq_len,
        logging_steps=10,
        save_strategy="epoch",
        eval_strategy="epoch",
        bf16=True,
        gradient_checkpointing=True,
        # Train on the assistant turns only (needs a chat template with the
        # {% generation %} marker; if your Gemma template lacks it, drop this
        # and the loss covers the full sequence — still works, just less ideal).
        assistant_only_loss=True,
        packing=False,
        report_to="none",
    )

    # SFTTrainer applies the tokenizer chat template to the "messages" (and
    # "tools") columns automatically for conversational datasets.
    trainer = SFTTrainer(
        model=model,
        args=cfg,
        train_dataset=dataset["train"],
        eval_dataset=dataset["validation"],
        processing_class=tokenizer,
        peft_config=lora,
    )
    trainer.train()
    trainer.save_model(args.out)
    tokenizer.save_pretrained(args.out)
    print(f"saved LoRA adapter to {args.out}")


if __name__ == "__main__":
    main()
