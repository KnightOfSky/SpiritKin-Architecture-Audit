from __future__ import annotations

import argparse
from pathlib import Path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Fine-tune an instruction model with Unsloth LoRA/QLoRA.")
    parser.add_argument("--model", required=True)
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--load-in-4bit", action="store_true")
    parser.add_argument("--no-4bit", action="store_true")
    parser.add_argument("--max-seq-length", type=int, default=2048)
    parser.add_argument("--batch-size", type=int, default=1)
    parser.add_argument("--gradient-accumulation-steps", type=int, default=8)
    parser.add_argument("--max-steps", type=int, default=60)
    parser.add_argument("--learning-rate", type=float, default=2e-4)
    return parser


def run_training(args: argparse.Namespace) -> None:
    dataset_path = Path(args.dataset)
    if not dataset_path.exists():
        raise FileNotFoundError(f"dataset not found: {dataset_path}")

    try:
        from datasets import load_dataset
        from trl import SFTConfig, SFTTrainer
        from unsloth import FastLanguageModel
    except Exception as exc:
        raise RuntimeError(
            "缺少训练依赖。请在专门训练环境安装 unsloth、trl、datasets 后重试。"
        ) from exc

    model, tokenizer = FastLanguageModel.from_pretrained(
        model_name=args.model,
        max_seq_length=args.max_seq_length,
        load_in_4bit=bool(args.load_in_4bit and not args.no_4bit),
    )
    model = FastLanguageModel.get_peft_model(
        model,
        r=16,
        lora_alpha=16,
        lora_dropout=0,
        target_modules=["q_proj", "k_proj", "v_proj", "o_proj", "up_proj", "down_proj", "gate_proj"],
        use_gradient_checkpointing="unsloth",
        random_state=3407,
    )

    dataset = load_dataset("json", data_files=str(dataset_path), split="train")

    def formatting_prompts_func(batch):
        texts = []
        for messages in batch["messages"]:
            texts.append(tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=False))
        return {"text": texts}

    dataset = dataset.map(formatting_prompts_func, batched=True)
    trainer = SFTTrainer(
        model=model,
        tokenizer=tokenizer,
        train_dataset=dataset,
        dataset_text_field="text",
        args=SFTConfig(
            output_dir=args.output,
            per_device_train_batch_size=args.batch_size,
            gradient_accumulation_steps=args.gradient_accumulation_steps,
            max_steps=args.max_steps,
            learning_rate=args.learning_rate,
            logging_steps=5,
            save_steps=max(10, args.max_steps),
            report_to="none",
        ),
    )
    trainer.train()
    model.save_pretrained(args.output)
    tokenizer.save_pretrained(args.output)


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    run_training(args)


if __name__ == "__main__":
    main()
