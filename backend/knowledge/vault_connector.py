from __future__ import annotations

import re
from pathlib import Path

from backend.knowledge.base import KnowledgeDocument

_WIKILINK_PATTERN = re.compile(r"\[\[([^\]|#]+)(?:[#|][^\]]+)?\]\]")
_FRONTMATTER_PATTERN = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


class ObsidianVaultConnector:

    def __init__(self, vault_root: str | Path):
        self._root = Path(vault_root).resolve()

    def load_vault(
        self,
        *,
        recursive: bool = True,
        skip_frontmatter: bool = True,
    ) -> list[KnowledgeDocument]:
        pattern = "**/*.md" if recursive else "*.md"
        documents: list[KnowledgeDocument] = []
        for path in sorted(self._root.glob(pattern)):
            if not path.is_file():
                continue
            content = path.read_text(encoding="utf-8", errors="ignore")
            body = content
            frontmatter: dict[str, str] = {}
            if skip_frontmatter:
                fm_match = _FRONTMATTER_PATTERN.match(content)
                if fm_match:
                    frontmatter = self._parse_frontmatter(fm_match.group(1))
                    body = content[fm_match.end():].strip()
            rel_path = path.relative_to(self._root).as_posix()
            doc_id = f"vault:{rel_path}"
            title = frontmatter.get("title", path.stem)
            metadata = {
                "source_vault": self._root.as_posix(),
                "source_path": path.as_posix(),
                "relative_path": rel_path,
                "frontmatter": frontmatter,
                "tags": _parse_tags(frontmatter.get("tags", "")),
            }
            documents.append(KnowledgeDocument(document_id=doc_id, title=title, content=body, metadata=metadata))
        return documents

    def resolve_backlinks(self, documents: list[KnowledgeDocument]) -> dict[str, list[str]]:
        backlinks: dict[str, list[str]] = {}
        name_to_id: dict[str, str] = {}
        for doc in documents:
            name = Path(doc.metadata.get("relative_path", doc.document_id)).stem
            name_to_id[name] = doc.document_id
            name_to_id[name.lower()] = doc.document_id

        for doc in documents:
            links = _WIKILINK_PATTERN.findall(doc.content)
            for link_name in links:
                target_id = name_to_id.get(link_name) or name_to_id.get(link_name.lower())
                if target_id is None:
                    target_id = f"vault:{link_name}.md"
                backlinks.setdefault(target_id, []).append(doc.document_id)

        return backlinks

    @staticmethod
    def _parse_frontmatter(text: str) -> dict[str, str]:
        result: dict[str, str] = {}
        for line in text.strip().splitlines():
            line = line.strip()
            if ":" in line:
                key, _, value = line.partition(":")
                result[key.strip()] = value.strip()
        return result


def _parse_tags(raw: str) -> list[str]:
    if not raw:
        return []
    items = re.split(r"[\s,]+", raw)
    return sorted(set(t.strip().lstrip("#") for t in items if t.strip()))
