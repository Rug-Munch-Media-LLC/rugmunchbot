"""Content-source registry tests (2026-10-17).

Guards the second half of the content plan: every canonical content type must
have a DECLARED source (ingest → enrich → rerank → render). A content type
with no source is a channel nobody can feed — the defect class that left
@rmialpha / @rmiscans quiet.

Covers:
1. Every canonical content type has a declared source (no orphans).
2. Every source row is complete (all four data-flow stages named).
3. No source row is declared for a non-canonical type (no drift).
4. Every chartered channel's content types are all sourced.
"""

from __future__ import annotations

import pytest

from rugmunchbot.channels.content_charter import (
    CANONICAL_CONTENT_TYPES,
    CHANNEL_CHARTER,
)
from rugmunchbot.channels.content_sources import (
    CONTENT_SOURCES,
    source_for,
    undeclared_content_types,
)


def test_every_content_type_has_a_declared_source():
    """The core contract: no canonical content type is left unwired."""
    assert undeclared_content_types() == frozenset(), (
        f"content types with no declared source: {sorted(undeclared_content_types())}"
    )


@pytest.mark.parametrize("ctype", sorted(CANONICAL_CONTENT_TYPES))
def test_source_row_is_complete(ctype):
    src = source_for(ctype)
    assert src is not None, f"{ctype} has no source row"
    assert src.content_type == ctype
    for field in ("ingest", "enrich", "rerank", "render"):
        value = getattr(src, field)
        assert value, f"{ctype}.{field} is empty"
        assert len(value) > 10, f"{ctype}.{field} too thin: {value!r}"


def test_no_source_for_non_canonical_type():
    """A source row for a type not in the vocabulary is drift."""
    extra = set(CONTENT_SOURCES) - CANONICAL_CONTENT_TYPES
    assert not extra, f"source rows for non-canonical types: {sorted(extra)}"


def test_every_chartered_type_is_sourced():
    """Every type a channel is chartered to post must have a source."""
    for role, charter in CHANNEL_CHARTER.items():
        for ctype in charter.content_types:
            assert source_for(ctype) is not None, f"{role} charters unsourced {ctype}"


def test_source_for_is_case_insensitive_and_fails_soft():
    assert source_for("NEWS") is CONTENT_SOURCES["news"]
    assert source_for("") is None
    assert source_for("unknown") is None
