"""Eval scenarios.

Each scenario states the case to triage, the decision the policy requires,
and the evidence the rationale must cite. Graded on outcomes, not transcripts.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Scenario:
    name: str
    case_id: str
    expect_decision: str
    must_cite: list[str] = field(default_factory=list)


SCENARIOS: list[Scenario] = [
    Scenario(
        name="duplicate_posting",
        case_id="case_501",
        expect_decision="needs_review",
        must_cite=["txn_1102"],
    ),
    Scenario(
        name="card_testing_cashout",
        case_id="case_502",
        expect_decision="block",
        must_cite=["mrc_giftmax"],
    ),
    Scenario(
        name="established_traveller",
        case_id="case_503",
        expect_decision="approve",
        must_cite=["mrc_hotel"],
    ),
    Scenario(
        name="high_volume_account",
        case_id="case_504",
        expect_decision="approve",
        must_cite=[],
    ),
    Scenario(
        name="conflicting_signals",
        case_id="case_505",
        expect_decision="block",
        must_cite=["mrc_cryptox"],
    ),
]


def scenario_by_name(name: str) -> Scenario:
    for s in SCENARIOS:
        if s.name == name:
            return s
    raise SystemExit(f"unknown scenario '{name}'. Known: {', '.join(s.name for s in SCENARIOS)}")
