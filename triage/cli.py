"""Command line entrypoints."""

from __future__ import annotations

import argparse
import asyncio
import os
import subprocess
import sys
import time

import httpx

API_BASE = os.environ.get("TRIAGE_API", "http://127.0.0.1:8787")
_server_proc: subprocess.Popen | None = None


def _api_up() -> bool:
    try:
        return httpx.get(f"{API_BASE}/health", timeout=1).status_code == 200
    except Exception:
        return False


def ensure_api() -> None:
    """Start the API in the background if it isn't already running."""
    global _server_proc
    if _api_up():
        return
    _server_proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "triage.api:app",
         "--host", "127.0.0.1", "--port", "8787", "--log-level", "warning"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    for _ in range(60):
        if _api_up():
            return
        time.sleep(0.25)
    raise SystemExit("could not start the API on port 8787")


def stop_api() -> None:
    if _server_proc is not None:
        _server_proc.terminate()


def cmd_seed(_args) -> int:
    from . import db
    print(f"seeded {db.seed()}")
    return 0


def cmd_serve(_args) -> int:
    import uvicorn
    uvicorn.run("triage.api:app", host="127.0.0.1", port=8787, reload=False)
    return 0


def cmd_run(args) -> int:
    from . import agent
    ensure_api()
    try:
        trace = agent.run(args.case_id, verbose=True)
        print()
        print(trace.show())
    finally:
        stop_api()
    return 0


def cmd_evals(args) -> int:
    from . import evals
    ensure_api()
    try:
        return evals.run_all(only=args.only, verbose=args.verbose)
    finally:
        stop_api()


def cmd_preflight(_args) -> int:
    from . import db

    # (label, passed, detail, required)
    checks: list[tuple[str, bool, str, bool]] = []
    checks.append(("python >= 3.11", sys.version_info >= (3, 11), sys.version.split()[0], True))
    checks.append(("database seeded", db.DB_PATH.exists(), str(db.DB_PATH), True))

    try:
        ensure_api()
        checks.append(("api reachable", _api_up(), API_BASE, True))
    except SystemExit as exc:
        checks.append(("api reachable", False, str(exc), True))

    try:
        from .model import ModelClient
        ModelClient()
        checks.append(("gemini api key", True, os.environ.get("TRIAGE_MODEL", "gemini-2.5-flash"), False))
    except Exception:
        checks.append(("gemini api key", False, "not set yet — paste it into .env", False))

    try:
        from mcp import Client
        from .mcp_server import server as mcp_server

        async def _probe() -> int:
            async with Client(mcp_server) as session:
                return len((await session.list_tools()).tools)

        count = asyncio.run(_probe())
        checks.append(("mcp tools discoverable", True, f"{count} tools exposed", True))
    except Exception as exc:
        checks.append(("mcp tools discoverable", False, str(exc)[:70], True))

    stop_api()

    blocked = False
    for name, passed, detail, required in checks:
        if passed:
            mark = "\033[1;32m✓\033[0m"
        elif required:
            mark = "\033[1;31m✗\033[0m"
            blocked = True
        else:
            mark = "\033[1;33m·\033[0m"
        print(f"  {mark} {name:<24} {detail}")

    if blocked:
        print("\n  A required check failed. Re-run ./setup.sh, or see setup.log.")
    return 1 if blocked else 0


def main() -> None:
    parser = argparse.ArgumentParser(prog="triage")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("seed", help="recreate the database").set_defaults(fn=cmd_seed)
    sub.add_parser("serve", help="run the API in the foreground").set_defaults(fn=cmd_serve)
    sub.add_parser("preflight", help="verify the environment").set_defaults(fn=cmd_preflight)

    p_run = sub.add_parser("run", help="run the agent on one case")
    p_run.add_argument("case_id")
    p_run.set_defaults(fn=cmd_run)

    p_ev = sub.add_parser("evals", help="run the eval suite")
    p_ev.add_argument("--only", help="run a single scenario by name")
    p_ev.add_argument("-v", "--verbose", action="store_true", help="print full traces on failure")
    p_ev.set_defaults(fn=cmd_evals)

    args = parser.parse_args()
    raise SystemExit(args.fn(args))
