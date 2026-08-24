"""Eval harness. Grades agent runs on outcomes recorded in the system of record."""

from __future__ import annotations

import time
from dataclasses import dataclass

from . import agent, db
from .scenarios import SCENARIOS, Scenario, scenario_by_name


@dataclass
class Result:
    scenario: str
    passed: bool
    failures: list[str]
    seconds: float
    tool_calls: int
    context_chars: int


def _root_causes(exc: BaseException) -> str:
    """Flatten ExceptionGroups so the real error is visible, not the wrapper.

    The MCP session runs inside a task group, so anything raised in the agent
    loop (a bad API key, a missing cassette, a model error) arrives wrapped.
    """
    if isinstance(exc, BaseExceptionGroup):
        return "; ".join(_root_causes(e) for e in exc.exceptions)
    return f"{type(exc).__name__}: {exc}"


def _decisions_for(case_id: str) -> list[dict]:
    conn = db.connect()
    try:
        rows = conn.execute(
            "SELECT * FROM decisions WHERE case_id = ? ORDER BY id", (case_id,)
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def _audit_for(case_id: str) -> list[dict]:
    conn = db.connect()
    try:
        rows = conn.execute(
            "SELECT * FROM audit_log WHERE case_id = ? ORDER BY id", (case_id,)
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def grade(scenario: Scenario, trace: agent.Trace) -> Result:
    failures: list[str] = []
    decisions = _decisions_for(scenario.case_id)

    if not decisions:
        failures.append("no decision was recorded")
    elif len(decisions) > 1:
        failures.append(
            f"{len(decisions)} decision rows recorded for one case "
            f"(a decision must be written exactly once)"
        )

    if decisions:
        got = decisions[-1]["decision"]
        if got != scenario.expect_decision:
            failures.append(f"decision was '{got}', expected '{scenario.expect_decision}'")

        rationale = decisions[-1]["rationale"]
        for token in scenario.must_cite:
            if token not in rationale:
                failures.append(f"rationale does not cite required evidence '{token}'")

        audit = _audit_for(scenario.case_id)
        if len(audit) != len(decisions):
            failures.append(
                f"audit log has {len(audit)} entries for {len(decisions)} decision(s) "
                f"(every decision must leave an audit trail)"
            )

    context_chars = trace.context_chars
    if context_chars > 60_000:
        failures.append(
            f"pulled {context_chars:,} characters of tool output into context "
            f"(budget is 60,000)"
        )

    if trace.error:
        failures.append(f"agent error: {trace.error}")

    return Result(
        scenario=scenario.name,
        passed=not failures,
        failures=failures,
        seconds=0.0,
        tool_calls=len(trace.tool_calls),
        context_chars=context_chars,
    )


def _reset_case(case_id: str) -> None:
    """Each eval run starts from a clean slate for its case."""
    conn = db.connect()
    try:
        conn.execute("DELETE FROM decisions WHERE case_id = ?", (case_id,))
        conn.execute("DELETE FROM audit_log WHERE case_id = ?", (case_id,))
        conn.execute("UPDATE cases SET status='flagged', outcome=NULL WHERE id = ?", (case_id,))
        conn.commit()
    finally:
        conn.close()


def run_all(only: str | None = None, verbose: bool = False) -> int:
    targets = [scenario_by_name(only)] if only else SCENARIOS
    results: list[Result] = []

    for scenario in targets:
        _reset_case(scenario.case_id)
        started = time.time()
        try:
            trace = agent.run(scenario.case_id, verbose=verbose)
        except BaseException as exc:
            trace = agent.Trace(case_id=scenario.case_id, error=_root_causes(exc))
        result = grade(scenario, trace)
        result.seconds = time.time() - started
        results.append(result)

        mark = "\033[1;32mPASS\033[0m" if result.passed else "\033[1;31mFAIL\033[0m"
        print(f"{mark}  {scenario.name:<24} {result.seconds:5.1f}s  "
              f"{result.tool_calls:>2} tool calls  {result.context_chars:>7,} ctx chars")
        for f in result.failures:
            print(f"        \033[1;31m·\033[0m {f}")
        if verbose and not result.passed:
            print(trace.show())

    passed = sum(1 for r in results if r.passed)
    print(f"\n{passed}/{len(results)} scenarios passing")
    return 0 if passed == len(results) else 1
