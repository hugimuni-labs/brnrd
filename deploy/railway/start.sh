#!/usr/bin/env bash
# Railway entrypoint. Idempotent: every redeploy re-enters here.
set -euo pipefail

if [ "$(id -u)" = "0" ]; then
  mkdir -p /data/home /data/state /data/repo
  chown -R brnrd:brnrd /data
  exec setpriv --reuid=brnrd --regid=brnrd --init-groups "$0" "$@"
fi

: "${BRNRD_REPO_URL:?set BRNRD_REPO_URL to the https clone URL of the repo the resident should work in}"

git config --global user.name  "${GIT_AUTHOR_NAME:-brnrd}"
git config --global user.email "${GIT_AUTHOR_EMAIL:-brnrd@users.noreply.github.com}"
if [ -n "${GH_TOKEN:-}" ]; then
  git config --global credential.helper \
    '!f() { echo username=x-access-token; echo "password=$GH_TOKEN"; }; f'
fi

if [ ! -d "$BRNRD_REPO_DIR/.git" ]; then
  git clone "$BRNRD_REPO_URL" "$BRNRD_REPO_DIR"
fi
cd "$BRNRD_REPO_DIR"

# Pair once. Same command as docs getting-started/connect.md; the approval
# link is printed in the deploy log. --no-service: this process IS the daemon.
if [ ! -f "$XDG_STATE_HOME/.railway-paired" ]; then
  brnrd account connect --no-service --defaults
  touch "$XDG_STATE_HOME/.railway-paired"
fi

exec brnrd up --foreground
