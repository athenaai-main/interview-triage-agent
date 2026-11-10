"""Core banking / case-management API.

This is the system of record. The MCP server is a client of this API; so are
the ops console and the batch reconciliation jobs (not in this repo).
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Literal

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from . import db

app = FastAPI(title="Triage API", version="0.1.0")

# Accounts pinned to the degraded write shard. See INFRA-2291.
DEGRADED_SHARD = {"case_504"}


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0, tzinfo=None).strftime("%Y-%m-%d %H:%M:%S")


class DecisionIn(BaseModel):
    case_id: str = Field(..., description="Case being decided")
    decision: Literal["approve", "block", "needs_review"] = Field(
        ..., description="approve | block | needs_review")
    rationale: str = Field(..., description="Evidence-backed explanation")


@app.get("/health")
def health():
    return {"ok": True}


@app.get("/cases/{case_id}")
def get_case(case_id: str):
    conn = db.connect()
    try:
        row = conn.execute(
            """SELECT c.*, t.amount_cents, t.ts AS txn_ts, t.merchant_id, t.description
               FROM cases c JOIN transactions t ON t.id = c.txn_id
               WHERE c.id = ?""",
            (case_id,),
        ).fetchone()
        if row is None:
            raise HTTPException(404, f"case {case_id} not found")
        return dict(row)
    finally:
        conn.close()


@app.get("/accounts/{account_id}")
def get_account(account_id: str):
    conn = db.connect()
    try:
        row = conn.execute("SELECT * FROM accounts WHERE id = ?", (account_id,)).fetchone()
        if row is None:
            raise HTTPException(404, f"account {account_id} not found")
        return dict(row)
    finally:
        conn.close()


@app.get("/accounts/{account_id}/transactions")
def list_transactions(
    account_id: str,
    days: int = Query(30, ge=1, le=3650),
    limit: int = Query(50, ge=1, le=500),
):
    conn = db.connect()
    try:
        rows = conn.execute(
            """SELECT id, ts, amount_cents, merchant_id, description
               FROM transactions
               WHERE account_id = ?
                 AND ts >= datetime('now', ?)
               ORDER BY ts DESC
               LIMIT ?""",
            (account_id, f"-{days} days", limit),
        ).fetchall()
        return {"account_id": account_id, "days": days, "count": len(rows),
                "transactions": [dict(r) for r in rows]}
    finally:
        conn.close()


@app.get("/accounts/{account_id}/prior-cases")
def list_prior_cases(account_id: str):
    conn = db.connect()
    try:
        rows = conn.execute(
            """SELECT id, txn_id, status, flagged_reason, outcome, created_at
               FROM cases WHERE account_id = ? AND status = 'closed'
               ORDER BY created_at DESC""",
            (account_id,),
        ).fetchall()
        return {"account_id": account_id, "prior_cases": [dict(r) for r in rows]}
    finally:
        conn.close()


@app.get("/merchants/{merchant_id}")
def get_merchant(merchant_id: str):
    conn = db.connect()
    try:
        row = conn.execute("SELECT * FROM merchants WHERE id = ?", (merchant_id,)).fetchone()
        if row is None:
            raise HTTPException(404, f"merchant {merchant_id} not found")
        return dict(row)
    finally:
        conn.close()


@app.post("/decisions", status_code=201)
def record_decision(body: DecisionIn):
    """Record the one decision a case gets, and its audit entry, atomically.

    - Unknown case -> 404. Nothing is written.
    - Case already decided with the same outcome -> 200 with the existing
      decision. A client retrying after a lost response gets this, so the
      retry does not write a second decision.
    - Case already decided with a different outcome (for example, an analyst
      already closed it) -> 409. The existing decision stays as it is.
    """
    conn = db.connect()
    try:
        # Take the write lock before reading, so two concurrent requests for
        # one case cannot both pass the "not yet decided" check.
        conn.execute("BEGIN IMMEDIATE")
        case = conn.execute(
            "SELECT status, outcome FROM cases WHERE id = ?", (body.case_id,)
        ).fetchone()
        if case is None:
            raise HTTPException(404, f"case {body.case_id} not found")

        existing = conn.execute(
            "SELECT id, decision FROM decisions WHERE case_id = ? ORDER BY id LIMIT 1",
            (body.case_id,),
        ).fetchone()
        if existing is not None or case["status"] != "flagged":
            prior = existing["decision"] if existing is not None else case["outcome"]
            if existing is not None and prior == body.decision:
                return JSONResponse(status_code=200, content={
                    "decision_id": existing["id"], "case_id": body.case_id,
                    "decision": prior, "already_recorded": True,
                })
            raise HTTPException(
                409, f"case {body.case_id} is already closed with outcome {prior!r}")

        try:
            cur = conn.execute(
                "INSERT INTO decisions (case_id, decision, rationale, created_at) VALUES (?,?,?,?)",
                (body.case_id, body.decision, body.rationale, _now()),
            )
        except sqlite3.IntegrityError:
            raise HTTPException(409, f"case {body.case_id} already has a decision")
        conn.execute(
            "UPDATE cases SET status = 'closed', outcome = ? WHERE id = ? AND status = 'flagged'",
            (body.decision, body.case_id),
        )
        # Same transaction as the decision, so the two cannot get out of step.
        write_audit(conn, actor="agent:triage", action="decision.recorded",
                    case_id=body.case_id, detail=body.decision)
        conn.commit()
        decision_id = cur.lastrowid
        written = conn.execute(
            "SELECT COUNT(*) FROM decisions WHERE case_id = ?", (body.case_id,)
        ).fetchone()[0]
    finally:
        conn.rollback()  # no-op after commit; releases the lock on error paths
        conn.close()

    # Simulated INFRA-2291 fault: the write commits but the response is lost.
    # Because the commit above already includes the audit entry, the ledger
    # stays consistent and the client's retry is handled as a replay.
    if body.case_id in DEGRADED_SHARD and written == 1:
        raise HTTPException(503, "upstream timeout")

    return {"decision_id": decision_id, "case_id": body.case_id, "decision": body.decision}


def write_audit(conn: sqlite3.Connection, *, actor: str, action: str,
                case_id: str | None, detail: str) -> None:
    """Append an audit entry on the caller's connection. The caller commits,
    so the entry is written together with the change it records."""
    conn.execute(
        "INSERT INTO audit_log (ts, actor, action, case_id, detail) VALUES (?,?,?,?,?)",
        (_now(), actor, action, case_id, detail),
    )


@app.get("/decisions/{case_id}")
def get_decisions(case_id: str):
    conn = db.connect()
    try:
        rows = conn.execute(
            "SELECT * FROM decisions WHERE case_id = ? ORDER BY id", (case_id,)
        ).fetchall()
        return {"case_id": case_id, "decisions": [dict(r) for r in rows]}
    finally:
        conn.close()


@app.get("/audit")
def get_audit(case_id: str | None = None):
    conn = db.connect()
    try:
        if case_id:
            rows = conn.execute(
                "SELECT * FROM audit_log WHERE case_id = ? ORDER BY id", (case_id,)
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM audit_log ORDER BY id").fetchall()
        return {"entries": [dict(r) for r in rows]}
    finally:
        conn.close()
