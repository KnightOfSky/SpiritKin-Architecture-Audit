from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

SUPPORTED_TEXT_SUFFIXES = {".txt", ".md", ".markdown", ".rst", ".log", ".py", ".yaml", ".yml", ".json", ".jsonl", ".csv"}


@dataclass(frozen=True)
class TrainingBuildOptions:
    mode: str = "instruction"
    max_chars: int = 6000
    chunk_chars: int = 1800
    overlap_chars: int = 120
    include_code: bool = True
    system_prompt: str = "你是 SpiritKinAI 的项目助手，请基于给定资料回答、总结或生成可靠操作建议。"


@dataclass(frozen=True)
class SourceDocument:
    path: str
    text: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class TrainingBuildReport:
    output_path: str
    source_count: int
    example_count: int
    skipped: list[dict[str, str]] = field(default_factory=list)

    def snapshot(self) -> dict[str, Any]:
        return {
            "output_path": self.output_path,
            "source_count": self.source_count,
            "example_count": self.example_count,
            "skipped": list(self.skipped),
        }


def collect_training_sources(paths: list[str | Path], *, include_code: bool = True) -> tuple[list[SourceDocument], list[dict[str, str]]]:
    documents: list[SourceDocument] = []
    skipped: list[dict[str, str]] = []
    for raw_path in paths:
        path = Path(raw_path)
        if not path.exists():
            skipped.append({"path": str(path), "reason": "missing"})
            continue
        candidates = [path]
        if path.is_dir():
            candidates = [item for item in path.rglob("*") if item.is_file()]

        for candidate in candidates:
            suffix = candidate.suffix.lower()
            if suffix not in SUPPORTED_TEXT_SUFFIXES:
                skipped.append({"path": str(candidate), "reason": "unsupported_suffix"})
                continue
            if not include_code and suffix in {".py"}:
                skipped.append({"path": str(candidate), "reason": "code_excluded"})
                continue
            try:
                documents.extend(_load_document(candidate))
            except Exception as exc:
                skipped.append({"path": str(candidate), "reason": f"load_failed:{type(exc).__name__}"})
    return documents, skipped


def build_training_dataset_from_files(
    paths: list[str | Path],
    output_path: str | Path,
    *,
    options: TrainingBuildOptions | None = None,
) -> TrainingBuildReport:
    options = options or TrainingBuildOptions()
    documents, skipped = collect_training_sources(paths, include_code=options.include_code)
    return _write_training_dataset(documents, output_path, options=options, skipped=skipped)


def build_training_dataset_from_documents(
    documents: list[SourceDocument | dict[str, Any]],
    output_path: str | Path,
    *,
    options: TrainingBuildOptions | None = None,
) -> TrainingBuildReport:
    options = options or TrainingBuildOptions()
    normalized_documents, skipped = _normalize_uploaded_documents(documents, options)
    return _write_training_dataset(normalized_documents, output_path, options=options, skipped=skipped)


def _write_training_dataset(
    documents: list[SourceDocument],
    output_path: str | Path,
    *,
    options: TrainingBuildOptions,
    skipped: list[dict[str, str]] | None = None,
) -> TrainingBuildReport:
    skipped = list(skipped or [])
    examples: list[dict[str, Any]] = []
    for document in documents:
        examples.extend(_document_to_examples(document, options))

    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8") as fh:
        for example in examples:
            fh.write(json.dumps(example, ensure_ascii=False) + "\n")

    return TrainingBuildReport(
        output_path=str(output),
        source_count=len(documents),
        example_count=len(examples),
        skipped=skipped,
    )


def _normalize_uploaded_documents(documents: list[SourceDocument | dict[str, Any]], options: TrainingBuildOptions) -> tuple[list[SourceDocument], list[dict[str, str]]]:
    normalized: list[SourceDocument] = []
    skipped: list[dict[str, str]] = []
    for index, document in enumerate(documents, start=1):
        if isinstance(document, SourceDocument):
            normalized.append(document)
            continue
        if not isinstance(document, dict):
            skipped.append({"path": f"upload-{index}", "reason": "invalid_document"})
            continue
        path = str(document.get("path") or document.get("name") or f"upload-{index}.txt")
        text = str(document.get("text") or document.get("content") or "")
        suffix = Path(path).suffix.lower()
        if suffix and suffix not in SUPPORTED_TEXT_SUFFIXES:
            skipped.append({"path": path, "reason": "unsupported_suffix"})
            continue
        if not options.include_code and suffix in {".py"}:
            skipped.append({"path": path, "reason": "code_excluded"})
            continue
        if not text.strip():
            skipped.append({"path": path, "reason": "empty"})
            continue
        source_type = suffix.lstrip(".") or str(document.get("source_type") or "upload")
        normalized.append(
            SourceDocument(
                path=path,
                text=text,
                metadata={
                    "source_type": source_type,
                    "uploaded": True,
                    **dict(document.get("metadata") or {}),
                },
            )
        )
    return normalized, skipped


def _load_document(path: Path) -> list[SourceDocument]:
    suffix = path.suffix.lower()
    if suffix == ".jsonl":
        return _load_jsonl(path)
    if suffix == ".json":
        return _load_json(path)
    if suffix == ".csv":
        return _load_csv(path)
    text = path.read_text(encoding="utf-8", errors="replace")
    return [SourceDocument(path=str(path), text=text, metadata={"source_type": suffix.lstrip(".") or "text"})]


def _load_jsonl(path: Path) -> list[SourceDocument]:
    documents: list[SourceDocument] = []
    for index, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), start=1):
        if not line.strip():
            continue
        data = json.loads(line)
        text = _extract_text_from_mapping(data)
        if text:
            documents.append(SourceDocument(path=f"{path}#{index}", text=text, metadata={"source_type": "jsonl", "row": index}))
    return documents


def _load_json(path: Path) -> list[SourceDocument]:
    data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    if isinstance(data, list):
        docs = []
        for index, item in enumerate(data, start=1):
            text = _extract_text_from_mapping(item) if isinstance(item, dict) else str(item)
            if text.strip():
                docs.append(SourceDocument(path=f"{path}#{index}", text=text, metadata={"source_type": "json", "row": index}))
        return docs
    text = _extract_text_from_mapping(data) if isinstance(data, dict) else str(data)
    return [SourceDocument(path=str(path), text=text, metadata={"source_type": "json"})] if text.strip() else []


def _load_csv(path: Path) -> list[SourceDocument]:
    documents: list[SourceDocument] = []
    with path.open("r", encoding="utf-8", errors="replace", newline="") as fh:
        reader = csv.DictReader(fh)
        for index, row in enumerate(reader, start=1):
            text = _extract_text_from_mapping(row)
            if text:
                documents.append(SourceDocument(path=f"{path}#{index}", text=text, metadata={"source_type": "csv", "row": index}))
    return documents


def _extract_text_from_mapping(data: dict[str, Any]) -> str:
    for key in ("text", "content", "answer", "output", "completion", "final_answer"):
        value = data.get(key)
        if value:
            return str(value)
    if data.get("messages"):
        return json.dumps(data["messages"], ensure_ascii=False)
    return "\n".join(f"{key}: {value}" for key, value in data.items() if value is not None)


def _document_to_examples(document: SourceDocument, options: TrainingBuildOptions) -> list[dict[str, Any]]:
    text = _clean_text(document.text)
    if not text:
        return []
    chunks = _chunk_text(text[: max(options.max_chars, options.chunk_chars)], options.chunk_chars, options.overlap_chars)
    return [_chunk_to_example(document, chunk, index, options) for index, chunk in enumerate(chunks, start=1)]


def _chunk_to_example(document: SourceDocument, chunk: str, index: int, options: TrainingBuildOptions) -> dict[str, Any]:
    source_label = Path(document.path.split("#", 1)[0]).name
    if options.mode == "qa":
        user = f"请根据资料《{source_label}》回答：这段内容的关键信息是什么？\n\n资料：\n{chunk}"
        assistant = "应基于资料提取事实、保留关键步骤，并在不确定时说明缺少信息。"
    else:
        user = f"请把资料《{source_label}》整理成 SpiritKinAI 可学习的操作/知识样本。\n\n资料：\n{chunk}"
        assistant = "应总结核心事实、适用场景、操作步骤、风险和可验证检查点。"
    return {
        "messages": [
            {"role": "system", "content": options.system_prompt},
            {"role": "user", "content": user},
            {"role": "assistant", "content": assistant},
        ],
        "metadata": {
            "source_path": document.path,
            "source_chunk": index,
            "source_type": document.metadata.get("source_type", ""),
            "builder_mode": options.mode,
        },
    }


def _clean_text(text: str) -> str:
    lines = [line.rstrip() for line in str(text or "").replace("\x00", "").splitlines()]
    collapsed: list[str] = []
    blank = False
    for line in lines:
        if not line.strip():
            if not blank:
                collapsed.append("")
            blank = True
            continue
        collapsed.append(line)
        blank = False
    return "\n".join(collapsed).strip()


def _chunk_text(text: str, chunk_chars: int, overlap_chars: int) -> list[str]:
    chunk_chars = max(200, int(chunk_chars))
    overlap_chars = max(0, min(int(overlap_chars), chunk_chars // 2))
    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = min(len(text), start + chunk_chars)
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end >= len(text):
            break
        start = end - overlap_chars
    return chunks
