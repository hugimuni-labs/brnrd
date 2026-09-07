#!/usr/bin/env bash
# Contenders 2, 4, 5, 6 — the exact commands this bench ran for each, so the
# install-path findings in the-continuity-bench.md are reproducible.
#
# Unlike adapters/claude_plain.py and adapters/brnrd_terminal.py, these did
# NOT complete a full 3-session chain (time-boxed, see report §Results) —
# this script reproduces the install + first-wall findings only.
#
# Always: env -u GIT_DIR -u GIT_WORK_TREE before any git-capable tool touches
# a scratch dir — see make_scratch_repo.py's docstring for why.
set -euo pipefail

BENCH=/tmp/continuity-bench
mkdir -p "$BENCH"

# --- contender 2: claude --bg / claude agents -------------------------------
rm -rf "$BENCH/claude-bg"
env -u GIT_DIR -u GIT_WORK_TREE python3 "$(dirname "$0")/make_scratch_repo.py" "$BENCH/claude-bg"
cd "$BENCH/claude-bg"
# First attempt, --dangerously-skip-permissions, refused non-interactively:
#   "--bg with bypassPermissions requires accepting the disclaimer first.
#    Run `claude --dangerously-skip-permissions` once interactively."
# Fallback used instead:
env -u GIT_DIR -u GIT_WORK_TREE claude --bg \
  "Add a --json flag to the CLI; we're standardising on snake_case JSON keys and we never print secrets." \
  --permission-mode acceptEdits
# Poll: `claude agents --json` (needs --json outside a TTY) until status != "waiting".
# Observed in this bench: stuck at {"status":"waiting","waitingFor":"permission prompt","state":"blocked"}
# indefinitely — stopped by hand with `claude stop <id>` rather than attach-and-approve,
# which would defeat the point of testing unattended continuity.

# --- contender 4: OpenClaw ---------------------------------------------------
curl -fsSL https://openclaw.ai/install.sh -o "$BENCH/openclaw-install.sh"
# OPENCLAW_HOME / OPENCLAW_WORKSPACE_DIR scope *runtime state* only — the
# installer still published a global npm package + /opt/homebrew/bin/openclaw
# symlink on this bench's own machine. Clean up after every run:
#   npm uninstall -g openclaw
OPENCLAW_HOME="$BENCH/openclaw-home" OPENCLAW_WORKSPACE_DIR="$BENCH/openclaw-workspace" \
  env -u GIT_DIR -u GIT_WORK_TREE bash "$BENCH/openclaw-install.sh" --no-onboard
npm uninstall -g openclaw   # mandatory cleanup — see report §4

# --- contender 5: Hermes Agent ----------------------------------------------
curl -fsSL https://hermes-agent.nousresearch.com/install.sh -o "$BENCH/hermes-install.sh"
HERMES_HOME="$BENCH/hermes-home" env -u GIT_DIR -u GIT_WORK_TREE \
  bash "$BENCH/hermes-install.sh" --skip-browser --skip-computer-use --skip-setup
# Codex-credential path (no new API key): `hermes auth add openai-codex`
# imports ~/.codex/auth.json. Claude path needs Max + purchased overage
# credits per Hermes' own docs — not this account's tier, not attempted.

# --- contender 6: claude-code-telegram ---------------------------------------
python3 -m venv "$BENCH/telegram-bridge-venv"
"$BENCH/telegram-bridge-venv/bin/pip" install -q \
  "git+https://github.com/RichardAtCT/claude-code-telegram@v1.3.0"
# Cold start, no config supplied, to surface the real wall:
"$BENCH/telegram-bridge-venv/bin/claude-telegram-bot" || true
# -> pydantic "Field required": telegram_bot_token, telegram_bot_username,
#    approved_directory. Mandatory fresh @BotFather token — no headless
#    fallback. Bench stops here per the dispatch's own "needs API key" rule.
