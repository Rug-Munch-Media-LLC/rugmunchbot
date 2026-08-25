# VENDORED 2026-08-17 from rmi-backend/app/rag/quality.py (frozen copy; do not edit).
# Re-vendor: bash scripts/refresh-vendors.sh
# ---
"""Quality filtering for the RAG ingestion pipeline."""

from __future__ import annotations

from typing import TYPE_CHECKING

from rugmunchbot._vendor.logging_shim import get_logger
from rugmunchbot._vendor.rag_pkg.chunking import content_hash as _content_hash
from rugmunchbot._vendor.rag_pkg.config import RAG_MIN_DOC_LENGTH, RAG_SPAM_REPETITION_THRESHOLD

if TYPE_CHECKING:
    from rugmunchbot._vendor.rag_pkg.ingestion_models import Document

log = get_logger(__name__)


def quality_score(text: str, max_chunk: int) -> float:
    """Quick quality score 0-1. Penalise noise (high special-char ratio)."""
    if not text:
        return 0.0
    n = len(text)
    special = sum(1 for c in text if not c.isalnum() and not c.isspace())
    length_score = min(1.0, n / max_chunk)
    special_score = max(0.0, 1.0 - (special / n))
    return round(0.7 * length_score + 0.3 * special_score, 3)


class QualityFilter:
    """Reject low-quality content before it reaches the embedding pipeline.

    Checks:
      - Minimum document length (too-short documents are noise)
      - Spam / repetitive patterns (high n-gram repetition ratio)
      - Exact duplicate detection (via content_hash against Redis)
    """

    def __init__(
        self,
        min_length: int = RAG_MIN_DOC_LENGTH,
        spam_threshold: float = RAG_SPAM_REPETITION_THRESHOLD,
    ) -> None:
        self.min_length = min_length
        self.spam_threshold = spam_threshold

    def check(self, doc: Document) -> tuple[bool, str]:
        """Return (is_accepted, reason) for a document."""
        content = doc.content.strip()
        if len(content) < self.min_length:
            return False, f"too_short:{len(content)}<{self.min_length}"
        if self._is_spam(content):
            return False, "spam_repetitive"
        h = _content_hash(content)
        if self._is_duplicate(h, doc.collection):
            return False, "duplicate"
        return True, ""

    async def check_async(self, doc: Document) -> tuple[bool, str]:
        return self.check(doc)

    def filter_batch(self, docs: list[Document]) -> tuple[list[Document], list[tuple[Document, str]]]:
        """Filter a batch of documents. Returns (accepted, rejected_with_reason)."""
        import collections as _collections

        accepted: list[Document] = []
        rejected: list[tuple[Document, str]] = []
        reasons: dict[str, int] = _collections.Counter()
        samples: dict[str, str] = {}
        for doc in docs:
            ok, reason = self.check(doc)
            if ok:
                accepted.append(doc)
            else:
                base = reason.split(":")[0]
                reasons[base] += 1
                if base not in samples:
                    samples[base] = doc.content[:80]
                rejected.append((doc, reason))
        if reasons:
            hist = " ".join(f"{k}={v}" for k, v in reasons.most_common(5))
            sample = next(iter(samples.items()))
            log.warning("quality_reject_histogram %s | sample[%s]=%r", hist, sample[0], sample[1])
        return accepted, rejected

    @staticmethod
    def _is_spam(text: str, threshold: float = RAG_SPAM_REPETITION_THRESHOLD) -> bool:
        """Detect spam by checking trigram repetition ratio.

        Length-tiered (2026-08-25): TG news stories are SHORT and carry
        channel boilerplate ("join us", "t.me/...", pinned-promo lines)
        whose repeats blew the flat 0.3 threshold — 100% of swept stories
        were rejected as spam_repetitive and RAG learned nothing. Short
        docs get a lenient ratio + boilerplate stripped first; long-form
        keeps the strict web-article threshold.
        """
        if len(text) < 100:
            return False
        words = text.lower().split()
        if len(words) < 10:
            return False
        # Strip TG boilerplate noise before measuring: bare links/handles.
        cleaned = [w for w in words if not (w.startswith("t.me/") or w.startswith("@") or w.startswith("http"))]
        if len(cleaned) < 10:
            return False
        effective_threshold = threshold
        if len(cleaned) < 120:
            # short TG-scale docs: boilerplate repeats are normal speech
            effective_threshold = max(threshold, 0.55)
        trigrams = [" ".join(cleaned[i : i + 3]) for i in range(len(cleaned) - 2)]
        if not trigrams:
            return False
        unique = len(set(trigrams))
        ratio = 1.0 - (unique / len(trigrams))
        return ratio > effective_threshold

    @staticmethod
    def _is_duplicate(content_hash: str, collection: str) -> bool:
        """Has this exact content already been ingested?"""
        try:
            from rugmunchbot._vendor.redis_client import get_redis

            r = get_redis()
            if r is None:
                return False
            return bool(r.sismember(f"rag:hashes:{collection}", content_hash))
        except Exception as e:
            log.warning("dedup_check_failed: %s", e)
            return False
