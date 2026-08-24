#!/usr/bin/env bash
# One-command setup. Safe to re-run. Logs everything to setup.log.

# --- refuse to be sourced -----------------------------------------------
# `exit` inside a sourced script closes the caller's shell, so bail out
# with `return` instead if someone runs `source setup.sh`.
_sourced=0
if [ -n "${ZSH_EVAL_CONTEXT:-}" ]; then
  case "$ZSH_EVAL_CONTEXT" in *file*) _sourced=1 ;; esac
elif [ -n "${BASH_VERSION:-}" ]; then
  (return 0 2>/dev/null) && _sourced=1
fi
if [ "$_sourced" -eq 1 ]; then
  printf '\033[1;31mRun this as ./setup.sh — not "source setup.sh".\033[0m\n'
  return 1
fi

cd "$(dirname "$0")" || exit 1

LOG="$PWD/setup.log"
: > "$LOG"

say()  { printf "\n\033[1;36m==>\033[0m %s\n" "$*" | tee -a "$LOG"; }
ok()   { printf "  \033[1;32m✓\033[0m %s\n" "$*" | tee -a "$LOG"; }
warn() { printf "  \033[1;33m!\033[0m %s\n" "$*" | tee -a "$LOG"; }
info() { printf "    %s\n" "$*" | tee -a "$LOG"; }

fail() {
  printf "\n  \033[1;31m✗ %s\033[0m\n" "$1" | tee -a "$LOG"
  printf "\n\033[1;31mSetup stopped.\033[0m Full log: %s\n" "$LOG"
  printf "The last command's output is at the end of that file.\n\n"
  exit 1
}

# Run a command, stream it to the log, show it only if it fails.
run() {
  printf '\n$ %s\n' "$*" >> "$LOG"
  if "$@" >>"$LOG" 2>&1; then
    return 0
  fi
  printf "\n  \033[1;31m--- output of: %s ---\033[0m\n" "$*"
  tail -25 "$LOG"
  return 1
}

printf "\033[1mTriage agent setup\033[0m  (log: %s)\n" "$LOG"

# ---------------------------------------------------------------- uv
say "Checking for uv (Python toolchain manager)"
if command -v uv >/dev/null 2>&1; then
  ok "uv already installed ($(uv --version 2>/dev/null))"
else
  warn "uv not found — installing (single static binary, no sudo)"
  if command -v curl >/dev/null 2>&1; then
    run sh -c 'curl -LsSf https://astral.sh/uv/install.sh | sh' || fail "uv install failed"
  elif command -v wget >/dev/null 2>&1; then
    run sh -c 'wget -qO- https://astral.sh/uv/install.sh | sh' || fail "uv install failed"
  else
    fail "Need curl or wget to install uv. See https://docs.astral.sh/uv/"
  fi
  export PATH="$HOME/.local/bin:$PATH"
  command -v uv >/dev/null 2>&1 \
    || fail "uv installed but not on PATH. Open a new terminal and re-run ./setup.sh"
  ok "uv installed ($(uv --version 2>/dev/null))"
fi

# ---------------------------------------------------------------- deps
say "Installing Python 3.12 and dependencies (first run takes ~30s)"
run uv python install 3.12 || info "system Python will be used instead"
run uv sync || fail "dependency install failed — see the output above"
ok "dependencies installed into ./.venv"

# ---------------------------------------------------------------- env
say "Checking configuration"
if [ ! -f .env ]; then
  cp .env.example .env || fail "could not create .env"
  ok "created .env from .env.example"
else
  ok ".env already present"
fi
NEEDS_KEY=0
if grep -q "your-key-here" .env 2>/dev/null; then
  NEEDS_KEY=1
  warn "GEMINI_API_KEY not set yet — the interviewer will give you one"
fi

# ---------------------------------------------------------------- db
say "Seeding the database"
run uv run triage seed || fail "could not seed the database"
ok "SQLite database created at ./triage.db"

# ---------------------------------------------------------------- preflight
say "Running preflight"
uv run triage preflight 2>&1 | tee -a "$LOG"
PREFLIGHT=${PIPESTATUS[0]:-$?}

echo
if [ "$PREFLIGHT" -ne 0 ]; then
  printf "\033[1;31mPreflight found a problem above.\033[0m Full log: %s\n\n" "$LOG"
  exit 1
fi

printf "\033[1;32mSetup complete.\033[0m\n"
if [ "$NEEDS_KEY" -eq 1 ]; then
  printf "One thing left: paste your GEMINI_API_KEY into \033[1m.env\033[0m when you get it.\n"
  printf "Everything else is ready — you can run the tests now:\n"
else
  printf "You're ready. Try:\n"
fi
printf "\n  uv run pytest       # fast, no API key needed\n"
printf "  uv run triage evals # needs the API key\n\n"
