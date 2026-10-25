"""The registry is the single source: every view must round-trip from it."""

from __future__ import annotations

import pytest

from rugmunchbot._command_registry_data import ENTRIES
from rugmunchbot.bot._menu_guard import ADMIN_MENU_COMMANDS
from rugmunchbot.command_catalog import CATALOG
from rugmunchbot.command_menu import (
    ADVERTISED_COMMANDS,
    GROUP_ADMIN_COMMANDS,
    MAX_ADVERTISED,
    menu_rows,
)
from rugmunchbot.command_registry import AUDIENCES, REGISTRY, get, names


def test_no_duplicate_commands() -> None:
    all_names = [entry.name for entry in REGISTRY]
    dupes = sorted({name for name in all_names if all_names.count(name) > 1})
    assert not dupes, f"registry duplicates: {dupes}"


def test_every_entry_has_a_valid_audience_and_section() -> None:
    for entry in REGISTRY:
        assert entry.audience in AUDIENCES, entry.name
        assert entry.section, entry.name


@pytest.mark.parametrize("audience", ["public", "group_admin"])
def test_documented_audiences_have_descriptions(audience: str) -> None:
    missing = [e.name for e in REGISTRY if e.audience == audience and not e.description]
    assert not missing, f"undocumented {audience} commands: {missing}"


def test_menu_is_derived_from_the_registry() -> None:
    from_registry = [entry.name for entry in REGISTRY if entry.menu_group]
    # registry order is catalogue order, menu order is presentation order
    assert sorted(from_registry) == sorted(ADVERTISED_COMMANDS)
    assert len(from_registry) == len(ADVERTISED_COMMANDS) == len(menu_rows())
    assert len(ADVERTISED_COMMANDS) <= MAX_ADVERTISED


def test_no_duplicate_menu_rows() -> None:
    assert len(set(ADVERTISED_COMMANDS)) == len(ADVERTISED_COMMANDS)


def test_catalog_is_exactly_the_public_audience() -> None:
    rows = [row for _k, _t, _i, cmds in CATALOG for row in cmds]
    assert len(rows) == len({name for name, _d in rows})
    assert {name for name, _d in rows} == names("public")
    assert all(desc for _name, desc in rows)


def test_owner_and_group_admin_sets_are_derived() -> None:
    assert names("owner") == ADMIN_MENU_COMMANDS
    assert names("group_admin") == GROUP_ADMIN_COMMANDS
    assert not (ADMIN_MENU_COMMANDS & GROUP_ADMIN_COMMANDS)


def test_owner_commands_are_never_advertised() -> None:
    assert not any(entry.menu_group for entry in REGISTRY if entry.audience == "owner")


def test_lookup_normalises_slashes_and_case() -> None:
    assert get("/SCAN") is not None
    assert get("nope-not-a-command") is None


def test_data_rows_match_the_public_contract() -> None:
    fields = {
        "name",
        "section",
        "description",
        "audience",
        "handler",
        "menu_group",
        "menu_description",
    }
    for row in ENTRIES:
        assert set(row) == fields


def test_audited_surface_numbers() -> None:
    """Pinned after the 2026-10-25 reconciliation: 162 registered, 162 documented."""
    assert len(REGISTRY) == 162
    assert len(names("public")) == 106
    assert len(ADVERTISED_COMMANDS) == 20
    assert len(names("group_admin")) == 34
    assert len(ADMIN_MENU_COMMANDS) == 22
