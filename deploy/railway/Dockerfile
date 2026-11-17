# brnrd daemon on a small always-on box (Railway template image).
#
# NOT the repo-root Dockerfile (that is the hosted backend: uvicorn) and NOT
# src/brr/Dockerfile (that is the runner image a strand executes inside).
# This one runs the DAEMON itself: `brnrd up --foreground`, plus git and the
# Claude Code CLI it drives. Execution stays in this container; brnrd.dev only
# relays messages and status (docs: getting-started/connect.md).
FROM node:22-bookworm-slim

ARG BRNRD_VERSION=""

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        bash ca-certificates curl git openssh-client util-linux python3 python3-pip python3-venv \
    && rm -rf /var/lib/apt/lists/* \
    && npm install -g @anthropic-ai/claude-code \
    && useradd --create-home --home-dir /home/brnrd --shell /bin/bash brnrd

# brnrd is a checkout at a release tag (BRNRD_VERSION, e.g. 0.7.1; empty: the newest release), installed
# editable: the one install kind, the same as the curl installer's, pinned the way the image always was.
COPY checkout.sh /usr/local/bin/brnrd-checkout
RUN sh /usr/local/bin/brnrd-checkout /opt/brnrd/checkout "$BRNRD_VERSION" \
    && python3 -m venv /opt/brnrd/env \
    && /opt/brnrd/env/bin/pip install --no-cache-dir -e /opt/brnrd/checkout \
    && ln -s /opt/brnrd/env/bin/brnrd /usr/local/bin/brnrd

# One Railway volume per service, mounted at /data. Everything that must
# survive a redeploy lives under it: the account home (XDG_STATE_HOME), the
# runner's login (HOME/.claude), and the repo checkout.
ENV XDG_STATE_HOME=/data/state \
    HOME=/data/home \
    BRNRD_REPO_DIR=/data/repo

COPY start.sh /usr/local/bin/brnrd-railway-start
RUN chmod +x /usr/local/bin/brnrd-railway-start

# Starts as root only to chown the volume, then drops to `brnrd`: Claude Code
# refuses --dangerously-skip-permissions as root, and brnrd's claude runner
# passes that flag (src/brr/runners.toml).
CMD ["brnrd-railway-start"]
