# brr runner image — bundles claude, codex, and gemini CLIs alongside a
# practical baseline toolbox for daemon-launched development work. node:22-slim
# is the base because all three runner CLIs are distributed via npm.
#
# This is still a runner image, not a project image: it preinstalls brr's own
# CLI/runtime deps and the runner toolbox, but not arbitrary repo dependencies,
# databases, cloud CLIs, or language SDKs beyond Node and Python. Repos that
# need those should layer on top with their own Dockerfile FROM this image, or
# use the `devcontainer` env which reuses the repo's own .devcontainer recipe.
#
# The image is designed to run as the *host* user's UID (passed via
# ``docker run -u``) so files written into the bind-mounted repo are owned
# by the host user, not by root. /brr-home is a writable HOME any UID can
# use; the daemon mounts ~/.codex, ~/.claude, ~/.gemini, and ~/.gitconfig
# into it at runtime. GitHub auth is wired separately via an injected
# ``GITHUB_TOKEN`` env var rather than a bind mount, because gh's keyring
# backend isn't reachable from inside the container.
#
# ``brr init -i`` copies ``pyproject.toml``, ``README.md``, and ``src/`` into
# the build context and installs this checkout with ``pip install /opt/brr``.
# Do **not** ``pip install brr`` from PyPI here — that name is an unrelated
# terminal image renderer.

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
    && pip install --no-cache-dir \
        'pytest>=7.0' \
        'requests>=2.31,<3' \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md /opt/brr/
COPY src /opt/brr/src
RUN pip install --no-cache-dir /opt/brr \
    && python3 -m brr review --help >/dev/null

# GitHub CLI via the official upstream APT source. Debian's own ``gh``
# package is sometimes years out of date (e.g. 2.46.x), and ``gh pr
# create`` adds flags often. Keeping the upstream feed gets us the
# current release without pinning. Authentication is supplied at run
# time by an injected ``GITHUB_TOKEN`` env var (see DockerEnv.prepare
# in src/brr/envs/__init__.py for resolution order).
RUN install -m 0755 -d /etc/apt/keyrings \
    && curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg \
        -o /etc/apt/keyrings/githubcli-archive-keyring.gpg \
    && chmod a+r /etc/apt/keyrings/githubcli-archive-keyring.gpg \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" \
        > /etc/apt/sources.list.d/github-cli.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends gh \
    && rm -rf /var/lib/apt/lists/*

# Git refuses to operate on repos owned by a different user
# (CVE-2022-24765). The daemon mounts the host repo regardless of host
# ownership, so mark every path safe inside the container. ``/etc/gitconfig``
# is read by any UID, so this works whether the runtime UID has a passwd
# entry or not. The daemon also exports the same setting via env vars
# (``GIT_CONFIG_*``) as belt-and-braces for images that skip this line.
RUN git config --system --add safe.directory '*'

RUN npm install -g \
        @anthropic-ai/claude-code \
        @openai/codex \
        @google/gemini-cli \
    && npm cache clean --force

# Writable HOME for whichever UID the daemon hands us. Mode 1777 is the
# same shape /tmp uses — any UID can create files inside, but only the
# owner can delete them. The CLIs (codex, claude, gemini) and git
# discover credentials through ``$HOME``; the daemon mounts the host's
# real credential dirs and ``.gitconfig`` here at runtime.
RUN mkdir -p /brr-home \
    && chmod 1777 /brr-home
ENV HOME=/brr-home

# Make user-mode pip installs reachable. Inside the container the daemon
# runs as the host's UID, which has no write access to the system Python
# site-packages, so any ``pip install`` an agent runs falls back to a
# ``--user`` install that lands scripts in ``$HOME/.local/bin``. Without
# this PATH prepend the freshly-installed entry points (``pytest``,
# ``brr``, ``ruff``, project console scripts, etc.) are invisible and
# agents have to fall back to ``python -m <module>`` invocations.
ENV PATH=/brr-home/.local/bin:$PATH

WORKDIR /work

CMD ["bash"]
