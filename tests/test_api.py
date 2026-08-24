"""Invariant tests for the triage API.

Fast, and no model calls. These assert properties the ledger must hold; they
deliberately do not assert *how* the API should achieve them.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from triage import api, db


@pytest.fixture(autouse=True)
def fresh_db(tmp_path, monkeypatch):
    path = tmp_path / "test.db"
    db.seed(path)
    monkeypatch.setattr(db, "DB_PATH", path)
    yield


@pytest.fixture
def client():
    return TestClient(api.app, raise_server_exceptions=False)


def _decide(client, case_id, decision="approve", rationale="txn_1101 matches baseline"):
    return client.post(
        "/decisions",
        json={"case_id": case_id, "decision": decision, "rationale": rationale},
    )


def test_decision_is_recorded(client):
    assert _decide(client, "case_501").status_code == 201
    assert len(client.get("/decisions/case_501").json()["decisions"]) == 1


def test_transactions_respect_the_lookback_window(client):
    wide = client.get("/accounts/acct_12/transactions", params={"days": 3650, "limit": 500}).json()
    narrow = client.get("/accounts/acct_12/transactions", params={"days": 30, "limit": 50}).json()
    assert narrow["count"] < wide["count"]
    assert narrow["count"] <= 50


def test_ledger_reconciles_after_a_day_of_traffic(client):
    """Replays a day of write traffic and reconciles the ledger.

    This is the check the nightly compliance export runs.
    """
    _decide(client, "case_501")

    _decide(client, "case_504")
    _decide(client, "case_504")

    _decide(client, "case_506", decision="approve")
    _decide(client, "case_does_not_exist")

    conn = db.connect()
    try:
        duplicated = conn.execute(
            "SELECT case_id, COUNT(*) c FROM decisions GROUP BY case_id HAVING c > 1"
        ).fetchall()
        orphaned = conn.execute(
            "SELECT case_id FROM decisions WHERE case_id NOT IN (SELECT id FROM cases)"
        ).fetchall()
        unaudited = conn.execute(
            """SELECT c.case_id FROM
                 (SELECT case_id, COUNT(*) n FROM decisions GROUP BY case_id) c
                 LEFT JOIN (SELECT case_id, COUNT(*) n FROM audit_log
                            WHERE action = 'decision.recorded' GROUP BY case_id) a
                 ON a.case_id = c.case_id
               WHERE COALESCE(a.n, 0) <> c.n"""
        ).fetchall()
        overwritten = conn.execute(
            "SELECT COUNT(*) FROM decisions WHERE case_id = 'case_506'"
        ).fetchone()[0]
    finally:
        conn.close()

    breaks = []
    if duplicated:
        breaks.append(f"{len(duplicated)} case(s) carry more than one decision: "
                      f"{[r['case_id'] for r in duplicated]}")
    if orphaned:
        breaks.append(f"{len(orphaned)} decision(s) reference no case: "
                      f"{[r['case_id'] for r in orphaned]}")
    if unaudited:
        breaks.append(f"{len(unaudited)} case(s) have decisions and audit entries out of step: "
                      f"{[r['case_id'] for r in unaudited]}")
    if overwritten != 1:
        breaks.append(f"case_506 carries {overwritten} decisions; the analyst's "
                      f"outcome is no longer the only one on file")

    assert not breaks, "ledger does not reconcile:\n  - " + "\n  - ".join(breaks)
