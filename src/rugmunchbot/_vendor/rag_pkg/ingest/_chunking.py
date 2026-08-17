# VENDORED 2026-08-17 from rmi-backend/app/rag/ingest/_chunking.py (frozen copy; do not edit).
# Re-vendor: bash scripts/refresh-vendors.sh
# ---
"""Heading-aware doc chunking (markdown / reST / AsciiDoc).

Extracted verbatim from ``app.rag.ingest_crypto_knowledge`` during the
de-godding split (2026-08-14). Converts reST underline + AsciiDoc ``=``
headings into markdown ``#`` form so a single splitter handles all three
formats. Section boundaries preserve the heading line as context; over-long
sections are recursively split at paragraph/sentence/word boundaries
(langchain-style greedy packing). Pure — no network, no I/O.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any

from rugmunchbot._vendor.rag_pkg.config import RAG_CHUNK_OVERLAP, RAG_CHUNK_SIZE, RAG_MIN_CHUNK_CHARS

_DOC_HEADING_RE = re.compile(r"^(#{1,6})[ \t]+([^\n]*?)[ \t]*$", re.MULTILINE)
_RST_UNDERLINE_RE = re.compile(r"^([^\n].*?)\n([=\-~^\"'`:.+*#<>]{3,})[ \t]*$", re.MULTILINE)
_ADOC_HEADING_RE = re.compile(r"^(=+)[ \t]+([^\n]*?)[ \t]*$", re.MULTILINE)


def _normalize_doc_headings(text: str) -> str:
    """Convert reST underline + AsciiDoc ``=`` headings to markdown ``#`` form.

    Only used to make the heading-aware split format-agnostic; the body text is
    otherwise preserved verbatim. Markdown docs pass through unchanged.
    """
    text = _ADOC_HEADING_RE.sub(lambda m: f"{'#' * min(len(m.group(1)), 6)} {m.group(2)}", text)
    text = _RST_UNDERLINE_RE.sub(lambda m: f"# {m.group(1)}", text)
    return text


def _split_at_headings(text: str) -> list[tuple[str, str]]:
    """Split normalized markdown into ``(heading, body)`` sections."""
    matches = list(_DOC_HEADING_RE.finditer(text))
    if not matches:
        return [("", text)]
    sections: list[tuple[str, str]] = []
    for i, m in enumerate(matches):
        heading = m.group(0).strip()
        body_start = m.end()
        body_end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        sections.append((heading, text[body_start:body_end].strip()))
    return sections


def _recursive_char_split(text: str, chunk_size: int, chunk_overlap: int) -> list[str]:
    """Greedy character split preferring paragraph → sentence → word breaks."""
    if not text or not text.strip():
        return []
    text = text.strip()
    if len(text) <= chunk_size:
        return [text]
    chunks: list[str] = []
    start = 0
    n = len(text)
    while start < n:
        end = min(start + chunk_size, n)
        split_at = -1
        for sep in ("\n\n", "\n", ". ", " "):
            pos = text.rfind(sep, start + chunk_size // 2, end)
            if pos != -1:
                split_at = pos + len(sep)
                break
        if split_at == -1 or split_at <= start:
            split_at = end
        piece = text[start:split_at].strip()
        if piece:
            chunks.append(piece)
        if split_at >= n:
            break
        next_start = split_at - chunk_overlap if chunk_overlap > 0 else split_at
        if next_start <= start:
            next_start = split_at
        start = next_start
    return chunks


def split_doc_into_chunks(
    text: str,
    chunk_size: int = RAG_CHUNK_SIZE,
    chunk_overlap: int = RAG_CHUNK_OVERLAP,
    min_chunk_chars: int = RAG_MIN_CHUNK_CHARS,
) -> list[str]:
    """Heading-aware split of raw markdown/reST/AsciiDoc into chunk strings.

    Sections are cut at heading boundaries; each chunk keeps its heading line
    as retrieval context. Over-long sections are recursively split at
    paragraph/sentence/word boundaries. Noise fragments below
    ``min_chunk_chars`` and exact duplicate chunks are dropped. Pure — no
    network, no I/O.
    """
    if not text or not text.strip():
        return []
    normalized = _normalize_doc_headings(text)
    out: list[str] = []
    for heading, body in _split_at_headings(normalized):
        section = f"{heading}\n{body}" if heading else body
        section = section.strip()
        if not section:
            continue
        if len(section) <= chunk_size:
            out.append(section)
            continue
        for piece in _recursive_char_split(section, chunk_size, chunk_overlap):
            if heading and heading not in piece:
                out.append(f"{heading}\n{piece}")
            else:
                out.append(piece)
    seen: set[str] = set()
    final: list[str] = []
    for c in out:
        stripped = c.strip()
        if not stripped or len(stripped) < min_chunk_chars:
            continue
        if stripped in seen:
            continue
        seen.add(stripped)
        final.append(stripped)
    return final


def _doc_payload(source: str, url: str, chunk: str, index: int, category: str = "education") -> dict[str, Any]:
    """Build the bulk-ingest item for one doc chunk."""
    digest = hashlib.sha256(url.encode("utf-8", errors="ignore")).hexdigest()[:12]
    metadata = {
        "source": source,
        "source_url": url,
        "doc_url": url,
        "category": category,
        "content_type": "doc_chunk",
        "chunk_index": index,
        "ingested_via": "doc_loader",
    }
    return {"doc_id": f"doc:{digest}:{index}", "content": chunk, "metadata": metadata}
