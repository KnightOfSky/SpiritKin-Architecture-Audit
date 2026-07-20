from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.knowledge.embedding import get_embedding_service
from backend.knowledge.embedding_eval import (
    DEFAULT_DATASET_PATH,
    DEFAULT_REPORT_PATH,
    evaluate_embedding_provider,
    load_embedding_eval_dataset,
    write_embedding_eval_report,
)
from backend.knowledge.reranker import build_reranker


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Evaluate the configured embedding provider on the SpiritKin retrieval baseline.")
    parser.add_argument("--dataset", default=str(DEFAULT_DATASET_PATH))
    parser.add_argument("--output", default=str(DEFAULT_REPORT_PATH))
    parser.add_argument("--top-k", type=int, default=3)
    parser.add_argument("--allow-degraded", action="store_true")
    parser.add_argument("--embedding-only", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    dataset = load_embedding_eval_dataset(args.dataset)
    provider = get_embedding_service(refresh=True)
    reranker = None if args.embedding_only else build_reranker("auto")
    report = evaluate_embedding_provider(
        provider,
        dataset,
        top_k=args.top_k,
        allow_degraded=bool(args.allow_degraded),
        reranker=reranker,
    )
    output = write_embedding_eval_report(report, args.output)
    print(
        json.dumps(
            {
                "passed": report["passed"],
                "metrics": report["metrics"],
                "embedding_metrics": report["embedding_metrics"],
                "reranker": report["reranker"],
                "output": str(output),
            },
            ensure_ascii=False,
        )
    )
    return 0 if report["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
