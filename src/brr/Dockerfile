# brr runner image — bundles claude, codex, and gemini CLIs alongside a
# practical baseline toolbox for daemon-launched development work. node:22-slim
# is the base because all three runner CLIs are distributed via npm.
#
# This is still a runner image, not a project image: it does not preinstall
# repo dependencies, databases, cloud CLIs, or language SDKs beyond Node and
# Python. Repos that need those should layer on top with their own Dockerfile
# FROM this image, or use the `devcontainer` env which reuses the repo's own
# .devcontainer recipe.
#
# Container runs as root so the cred-dir mounts emitted by brr's docker
# env (e.g. ~/.codex -> /root/.codex) land where the CLIs look for
# tokens. Don't override USER unless you also remap the mounts.

FROM node:22-slim

ENV PIP_BREAK_SYSTEM_PACKAGES=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        bash \
        build-essential \
        ca-certificates \
        curl \
        file \
        git \
        jq \
        openssh-client \
        pkg-config \
        python-is-python3 \
        python3 \
        python3-pip \
        python3-venv \
        ripgrep \
        rsync \
        unzip \
        wget \
        zip \
    && ln -sf /usr/bin/pip3 /usr/local/bin/pip \
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
