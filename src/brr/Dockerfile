# brr runner image — bundles claude, codex, and gemini CLIs alongside the
# minimum tooling brr expects: git for commits, bash for shell-out,
# ca-certificates for HTTPS. node:22-slim is the base because all three
# runner CLIs are distributed via npm.
#
# This image is intentionally minimal: it does NOT carry project tooling
# (test runners, language SDKs beyond Node, build tools). Repos that need
# more should either layer on top with their own Dockerfile FROM this
# image, or use the `devcontainer` env which reuses the repo's own
# .devcontainer recipe.
#
# Container runs as root so the cred-dir mounts emitted by brr's docker
# env (e.g. ~/.codex -> /root/.codex) land where the CLIs look for
# tokens. Don't override USER unless you also remap the mounts.

FROM node:22-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        bash \
        ca-certificates \
        git \
        ripgrep \
    && rm -rf /var/lib/apt/lists/*

# The container runs as root while the host repo is bind-mounted read-write
# at the same absolute path it has on the host. Git refuses to operate on
# repos owned by a different user (CVE-2022-24765) and the daemon mounts
# the repo regardless of host ownership, so mark every path safe inside
# the container. This config is image-local and does not leak to the host.
RUN git config --system --add safe.directory '*'

RUN npm install -g \
        @anthropic-ai/claude-code \
        @openai/codex \
        @google/gemini-cli \
    && npm cache clean --force

WORKDIR /work

CMD ["bash"]
