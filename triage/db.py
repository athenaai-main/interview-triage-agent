"""SQLite storage for the triage service."""

from __future__ import annotations

import os
import random
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

DB_PATH = Path(os.environ.get("TRIAGE_DB", Path(__file__).resolve().parent.parent / "triage.db"))

SCHEMA = """
CREATE TABLE accounts (
    id           TEXT PRIMARY KEY,
    holder_name  TEXT NOT NULL,
    opened_at    TEXT NOT NULL,
    status       TEXT NOT NULL,
    risk_tier    TEXT NOT NULL
);

CREATE TABLE merchants (
    id         TEXT PRIMARY KEY,
    name       TEXT NOT NULL,
    category   TEXT NOT NULL,
    risk_notes TEXT NOT NULL DEFAULT ''
);

CREATE TABLE transactions (
    id           TEXT PRIMARY KEY,
    account_id   TEXT NOT NULL REFERENCES accounts(id),
    ts           TEXT NOT NULL,
    amount_cents INTEGER NOT NULL,
    merchant_id  TEXT NOT NULL REFERENCES merchants(id),
    description  TEXT NOT NULL
);
CREATE INDEX idx_txn_account_ts ON transactions(account_id, ts DESC);

CREATE TABLE cases (
    id             TEXT PRIMARY KEY,
    account_id     TEXT NOT NULL REFERENCES accounts(id),
    txn_id         TEXT NOT NULL REFERENCES transactions(id),
    status         TEXT NOT NULL,           -- flagged | closed
    flagged_reason TEXT NOT NULL,
    outcome        TEXT,                    -- set when status = closed
    created_at     TEXT NOT NULL
);

CREATE TABLE decisions (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id    TEXT NOT NULL,
    decision   TEXT NOT NULL,               -- approve | block | needs_review
    rationale  TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE audit_log (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    ts         TEXT NOT NULL,
    actor      TEXT NOT NULL,
    action     TEXT NOT NULL,
    case_id    TEXT,
    detail     TEXT NOT NULL
);
"""


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _iso(dt: datetime) -> str:
    # SQLite-comparable format: "YYYY-MM-DD HH:MM:SS"
    return dt.replace(microsecond=0, tzinfo=None).strftime("%Y-%m-%d %H:%M:%S")


def seed(path: Path | None = None) -> Path:
    """Drop and recreate the database with deterministic fixture data."""
    target = path or DB_PATH
    if target.exists():
        target.unlink()

    conn = sqlite3.connect(target)
    conn.executescript(SCHEMA)
    rng = random.Random(20260821)
    now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)

    merchants = [
        ("mrc_delta", "Delta Air Lines", "travel", ""),
        ("mrc_wholefds", "Whole Foods Market", "grocery", ""),
        ("mrc_shell", "Shell", "fuel", ""),
        ("mrc_netflix", "Netflix", "subscription", ""),
        ("mrc_lux", "LuxeGoods Direct", "retail", "High chargeback rate; frequent card-testing target."),
        ("mrc_cryptox", "CryptoXchange", "crypto", "High-risk category. Elevated fraud rate on new accounts."),
        ("mrc_giftmax", "GiftMax Online", "gift_cards", "Gift-card resale; common in account-takeover cashout."),
        ("mrc_util", "Metro Water Utility", "utilities", ""),
        ("mrc_cafe", "Blue Bottle Coffee", "dining", ""),
        ("mrc_hotel", "Marriott Hotels", "travel", ""),
    ]
    conn.executemany("INSERT INTO merchants VALUES (?,?,?,?)", merchants)

    accounts = [
        ("acct_11", "Dana Whitfield", _iso(now - timedelta(days=2190)), "active", "low"),
        ("acct_12", "Priya Raman", _iso(now - timedelta(days=1460)), "active", "low"),
        ("acct_13", "Marcus Oyelaran", _iso(now - timedelta(days=21)), "active", "high"),
        ("acct_14", "Sofia Lindqvist", _iso(now - timedelta(days=900)), "active", "medium"),
        ("acct_15", "Theo Brandt", _iso(now - timedelta(days=3200)), "active", "low"),
        ("acct_16", "Amara Nwosu", _iso(now - timedelta(days=140)), "active", "medium"),
    ]
    conn.executemany("INSERT INTO accounts VALUES (?,?,?,?,?)", accounts)

    txns: list[tuple] = []

    def add(txn_id, acct, minutes_ago, cents, merchant, desc):
        txns.append((txn_id, acct, _iso(now - timedelta(minutes=minutes_ago)), cents, merchant, desc))

    # --- acct_11 ---
    add("txn_1101", "acct_11", 2, 84200, "mrc_delta", "DELTA AIR 0062314455")
    add("txn_1102", "acct_11", 4, 84200, "mrc_delta", "DELTA AIR 0062314455")
    add("txn_1103", "acct_11", 3000, 6512, "mrc_wholefds", "WHOLEFDS #221")
    add("txn_1104", "acct_11", 7000, 4210, "mrc_shell", "SHELL OIL 9932")
    add("txn_1105", "acct_11", 12000, 1599, "mrc_netflix", "NETFLIX.COM")

    # --- acct_13 ---
    for i in range(6):
        add(f"txn_13{i:02d}", "acct_13", 90 - i * 3, 100 + i, "mrc_lux", "LUXEGOODS AUTH")
    add("txn_1310", "acct_13", 40, 240000, "mrc_giftmax", "GIFTMAX ONLINE BULK")
    add("txn_1311", "acct_13", 2800, 3200, "mrc_cafe", "BLUE BOTTLE")

    # --- acct_15 ---
    add("txn_1501", "acct_15", 15, 312800, "mrc_hotel", "MARRIOTT RESORT MAUI")
    add("txn_1504", "acct_15", 11520, 198600, "mrc_delta", "DELTA AIR 0061301442")
    for i in range(12):
        add(f"txn_155{i:02d}", "acct_15", 4000 + i * 900, 2000 + i * 311, "mrc_cafe", "BLUE BOTTLE")
    add("txn_1502", "acct_15", 25920, 289400, "mrc_hotel", "MARRIOTT LONDON")
    add("txn_1503", "acct_15", 86400, 194500, "mrc_delta", "DELTA AIR 0061299871")

    # --- acct_12 ---
    add("txn_1201", "acct_12", 30, 4990, "mrc_cafe", "BLUE BOTTLE")
    for i in range(2000):
        add(
            f"txn_12h{i:04d}",
            "acct_12",
            120 + i * 37,
            rng.randint(300, 9000),
            rng.choice(["mrc_cafe", "mrc_wholefds", "mrc_shell", "mrc_netflix", "mrc_util"]),
            "RECURRING PURCHASE",
        )

    # --- acct_14 ---
    add("txn_1401", "acct_14", 25, 129900, "mrc_cryptox", "CRYPTOXCHANGE FUNDING")
    add("txn_1402", "acct_14", 60000, 89900, "mrc_cryptox", "CRYPTOXCHANGE FUNDING")
    add("txn_1403", "acct_14", 5000, 7300, "mrc_wholefds", "WHOLEFDS #118")
    add("txn_1404", "acct_14", 20000, 15400, "mrc_util", "METRO WATER AUTOPAY")

    # --- acct_16 ---
    add("txn_1601", "acct_16", 300, 55000, "mrc_giftmax", "GIFTMAX ONLINE")
    add("txn_1602", "acct_16", 9000, 3400, "mrc_wholefds", "WHOLEFDS #402")

    conn.executemany("INSERT INTO transactions VALUES (?,?,?,?,?,?)", txns)

    cases = [
        ("case_501", "acct_11", "txn_1101", "flagged",
         "Amount exceeds 30-day average by 3x", None, _iso(now - timedelta(minutes=1))),
        ("case_502", "acct_13", "txn_1310", "flagged",
         "Large gift-card purchase on account younger than 30 days", None, _iso(now - timedelta(minutes=35))),
        ("case_503", "acct_15", "txn_1501", "flagged",
         "Amount exceeds 30-day average by 8x", None, _iso(now - timedelta(minutes=10))),
        ("case_504", "acct_12", "txn_1201", "flagged",
         "Velocity rule: 3 transactions within 10 minutes", None, _iso(now - timedelta(minutes=25))),
        ("case_505", "acct_14", "txn_1401", "flagged",
         "High-risk merchant category (crypto)", None, _iso(now - timedelta(minutes=20))),
        ("case_506", "acct_16", "txn_1601", "closed",
         "Gift-card purchase on recently opened account", "block", _iso(now - timedelta(days=4))),
        ("case_401", "acct_13", "txn_1300", "closed",
         "Card-testing pattern detected", "block", _iso(now - timedelta(days=9))),
        ("case_402", "acct_15", "txn_1503", "closed",
         "Amount exceeds 30-day average by 6x", "approve", _iso(now - timedelta(days=60))),
        ("case_403", "acct_14", "txn_1402", "closed",
         "High-risk merchant category (crypto)", "approve", _iso(now - timedelta(days=41))),
    ]
    conn.executemany("INSERT INTO cases VALUES (?,?,?,?,?,?,?)", cases)

    conn.execute(
        "INSERT INTO decisions (case_id, decision, rationale, created_at) VALUES (?,?,?,?)",
        ("case_506", "block", "Confirmed account takeover reported by holder.", _iso(now - timedelta(days=4))),
    )
    conn.execute(
        "INSERT INTO audit_log (ts, actor, action, case_id, detail) VALUES (?,?,?,?,?)",
        (_iso(now - timedelta(days=4)), "analyst:jrivera", "decision.recorded", "case_506", "block"),
    )

    conn.commit()
    conn.close()
    return target


if __name__ == "__main__":
    print(f"seeded {seed()}")
