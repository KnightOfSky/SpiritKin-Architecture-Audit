from __future__ import annotations


def chunk_text(text: str, chunk_size: int = 400, overlap: int = 50) -> list[str]:
    content = (text or "").strip()
    if not content:
        return []

    size = max(1, chunk_size)
    step = max(1, size - max(0, overlap))
    chunks: list[str] = []
    start = 0

    while start < len(content):
        end = min(len(content), start + size)
        chunks.append(content[start:end])
        if end >= len(content):
            break
        start += step

    return chunks


def chunk_text_with_citations(
    text: str, chunk_size: int = 400, overlap: int = 50
) -> list[tuple[str, tuple[int, int]]]:
    content = (text or "").strip()
    if not content:
        return []

    size = max(1, chunk_size)
    step = max(1, size - max(0, overlap))
    chunks: list[tuple[str, tuple[int, int]]] = []
    start = 0

    while start < len(content):
        end = min(len(content), start + size)
        chunk_text_content = content[start:end]
        start_line = content[:start].count("\n") + 1 if start > 0 else 1
        end_line = start_line + chunk_text_content.count("\n")
        chunks.append((chunk_text_content, (start_line, end_line)))
        if end >= len(content):
            break
        start += step

    return chunks