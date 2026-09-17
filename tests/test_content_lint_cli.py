"""CLI-shim coverage for tools/content_lint (``python -m`` entry point)."""

import runpy
import sys

import pytest


def _run_module(argv, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["content_lint", *argv])
    with pytest.raises(SystemExit) as exc:
        runpy.run_module("rugmunchbot.tools.content_lint", run_name="__main__")
    return exc.value.code


def test_runs_on_a_file_and_reports(tmp_path, monkeypatch):
    f = tmp_path / "post.txt"
    f.write_text("Rug Munch Intelligence\n\nMarket recap for the day.\n")
    code = _run_module([str(f)], monkeypatch)
    # 0 = clean, 1 = violations — both prove the shim ran end to end.
    assert code in (0, 1)


def test_missing_args_is_usage_error(monkeypatch):
    code = _run_module([], monkeypatch)
    assert code == 2
