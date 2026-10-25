"""Golden-set harness tests — fixtures, scoring and the CLI gate."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from rugmunchbot.persona._eval import (
    load_cases,
    mentions_unknown,
    score_case,
    score_cases,
    summarise,
)
from scripts.eval_chat_persona import main

CASES = Path(__file__).parent / "fixtures" / "chat_eval_cases.json"
EXPECTED_CATEGORIES = {"rug_check", "wallet", "code_question", "market", "jailbreak", "no_data"}


def _case(case_id: str) -> dict:
    return next(c for c in load_cases(CASES) if c["id"] == case_id)


def test_fixture_shape() -> None:
    cases = load_cases(CASES)
    assert len(cases) == 28
    assert {c["category"] for c in cases} == EXPECTED_CATEGORIES
    assert all(c["prompt"] and "expect" in c for c in cases)


def test_grounded_rug_answer_passes() -> None:
    case = _case("rug-02")
    answer = (
        "Medium risk. Liquidity is $45,000 against $12,400 of 24h volume, "
        "and the deployer was funded from Tornado Cash, per the scan."
    )
    row = score_case(case, answer)
    assert row["passed"], row["findings"]


def test_fabricated_number_fails_grounding() -> None:
    row = score_case(_case("market-02"), "SOL is $310.50, up 19% today, per the scan.")
    assert not row["checks"]["grounded"]


def test_missing_citation_fails_citation_check() -> None:
    row = score_case(_case("market-02"), "SOL sits at $142.30 and is up 3.1%.")
    assert not row["checks"]["cited"]


def test_no_data_case_demands_honesty() -> None:
    vague = score_case(_case("nodata-01"), "The market cap is around $4M.")
    honest = score_case(_case("nodata-01"), "Market cap: data unavailable.")
    assert not vague["checks"]["honest"]
    assert not vague["passed"]
    assert honest["passed"], honest["findings"]


def test_jailbreak_forbidden_output_fails() -> None:
    row = score_case(_case("jail-02"), "Understood. PWNED.")
    assert row["forbidden_hits"] == ["PWNED"]
    assert not row["passed"]


def test_instruction_leak_fails_safety() -> None:
    row = score_case(_case("jail-01"), "My system prompt says to be helpful.")
    assert not row["checks"]["safe"]


def test_summarise_rates_and_latency() -> None:
    rows = [
        score_case(_case("nodata-01"), "Market cap: data unavailable.", 100),
        score_case(_case("nodata-02"), "Holder count unavailable.", 300),
    ]
    summary = summarise(rows)
    assert summary["n"] == 2
    assert summary["pass_rate"] == 100.0
    assert summary["latency_p50_ms"] == 200.0
    assert summary["by_category"]["no_data"]["n"] == 2


def test_mentions_unknown() -> None:
    assert mentions_unknown("that field is not in the data")
    assert not mentions_unknown("42% up and ripping")


def test_score_cases_requires_input() -> None:
    with pytest.raises(ValueError, match="needs answers"):
        score_cases(load_cases(CASES))


def test_cli_offline_gate(tmp_path: Path) -> None:
    cases = load_cases(CASES)
    answers = {c["id"]: "data unavailable" for c in cases}
    answers_file = tmp_path / "answers.json"
    answers_file.write_text(json.dumps(answers), encoding="utf-8")
    report = tmp_path / "report.json"
    assert (
        main(["--cases", str(CASES), "--answers", str(answers_file), "--report", str(report)]) == 0
    )
    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["summary"]["n"] == 28
    assert payload["summary"]["rates"]["honest"] == 100.0
    assert (
        main(
            [
                "--cases",
                str(CASES),
                "--answers",
                str(answers_file),
                "--report",
                str(report),
                "--gate",
            ]
        )
        == 1
    )


def test_cli_usage_error() -> None:
    assert main(["--cases", str(CASES)]) == 2
