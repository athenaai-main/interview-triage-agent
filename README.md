# Transaction Triage Agent

An LLM agent that triages transactions our rules engine has flagged. It
investigates a case using tools, then records one of three decisions:
`approve`, `block`, or `needs_review`.

This is going to production next week. **It isn't ready.**

---

## Setup

```bash
./setup.sh
```

That checks for and installs `uv`, installs Python and dependencies, seeds the
database, and runs a preflight check. Re-run it any time. Then paste the
`GEMINI_API_KEY` you were given into `.env`.

Verify:

```bash
uv run triage preflight
```

## Architecture

```
  agent loop  ──▶  MCP server  ──▶  Triage API  ──▶  SQLite
 (Gemini Flash)     (tools)        (system of record)
```

| File | What it is |
|---|---|
| `triage/agent.py` | The agent loop: discovers tools, calls the model, dispatches tool calls |
| `triage/mcp_server.py` | The MCP layer — exposes the API as agent tools |
| `triage/api.py` | The core API. System of record for decisions and the audit log |
| `triage/db.py` | Schema and fixture data |
| `triage/policy.md` | The triage policy. This is the agent's system prompt |
| `triage/scenarios.py` | Eval scenarios: expected decision + required evidence |
| `triage/evals.py` | Eval harness and graders |
| `tests/test_api.py` | Fast API invariant tests (no model calls) |

## Commands

```bash
uv run pytest                        # API invariants — fast, no model calls
uv run triage evals                  # full eval suite (calls the model)
uv run triage evals --only NAME      # one scenario
uv run triage evals -v               # print full traces on failure
uv run triage run case_501           # run the agent on one case, verbose
uv run triage seed                   # reset the database
```

`uv run triage run <case>` prints the whole conversation — every tool call, every
tool result, and how much context each one consumed. Start there.

---

## Before you start

Work on a branch, and commit as you go:

```bash
git checkout -b <your-name>
# ... work ...
git add -A && git commit -m "what and why"
```

Small, frequent commits are better than one big one. We read the diff
afterwards, so commit messages that say *why* are worth more than ones that
say *what changed*.

## Part 1 — make it work (~20 min)

The suite is red. `uv run pytest` fails, and several eval scenarios fail.

**You will not fix everything in the time we have, and you aren't expected to.**
Triage it like you would on a real on-call rotation:

1. Work out what's actually wrong — root causes, not symptoms.
2. Decide what order to fix things in, and say why.
3. Fix what you can.
4. For whatever's left, tell us what you'd do about it.

Use whatever AI tooling you normally use — Claude Code, Cursor, Codex,
whatever. We're interested in how you actually work, not in watching you type
from memory.

**If you don't have access to the tooling you'd normally reach for, tell us
before the session and we'll provide it.** Don't spend interview time on
setup or logins.

Think out loud as you go. If something in this repo looks wrong to you, say so —
including anything in this README, the policy, or the tests.

## Part 2 — architecture (~10 min)

**Do this part even if Part 1 is unfinished.** Create `DESIGN.md`, write your
answers there, and commit it. Bullet points are fine; we care about the
position and the reasoning, not the prose.

Three questions. Pick the two you have the most to say about.

**1. Where does downstream orchestration belong?**
Today `record_decision` writes a row and closes the case. In production a
`block` also has to freeze the account, notify the customer, and file a
regulatory report — and a `needs_review` has to route to a human queue with an
SLA. None of that exists yet. Does that sequencing belong in the API, in the
agent loop, or in the policy the model reads? What breaks under each choice?

**2. Where is the line between code and prompt?**
The API enforces invariants in code. The policy states decision criteria in
prose that a model interprets. Some rules could live in either — "a block
requires affirmative evidence", "never decide a case a human already closed".
What is your rule for which side something goes on?

**3. What would you have to build to trust this in production?**
The evals grade the final decision against an expected value. What does that
fail to catch, and what would you add? Consider what happens when this runs on
10,000 cases a day and you cannot read the transcripts.
