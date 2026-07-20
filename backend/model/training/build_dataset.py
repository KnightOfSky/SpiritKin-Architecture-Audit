from __future__ import annotations

import argparse
import json

from backend.model.training.data_builder import TrainingBuildOptions, build_training_dataset_from_files


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build SpiritKinAI training JSONL from uploaded files or folders.")
    parser.add_argument("paths", nargs="+", help="Files or directories to convert.")
    parser.add_argument("--output", required=True, help="Output JSONL path.")
    parser.add_argument("--mode", choices=("instruction", "qa"), default="instruction")
    parser.add_argument("--max-chars", type=int, default=6000)
    parser.add_argument("--chunk-chars", type=int, default=1800)
    parser.add_argument("--overlap-chars", type=int, default=120)
    parser.add_argument("--exclude-code", action="store_true")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    report = build_training_dataset_from_files(
        args.paths,
        args.output,
        options=TrainingBuildOptions(
            mode=args.mode,
            max_chars=args.max_chars,
            chunk_chars=args.chunk_chars,
            overlap_chars=args.overlap_chars,
            include_code=not args.exclude_code,
        ),
    )
    print(json.dumps(report.snapshot(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
