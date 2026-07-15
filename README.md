# brnrd

<p align="center">
  <img src="media/brnrd-boot.gif" width="720" alt="brnrd boot sequence: underscore, b_d, br_rd, brnrd">
</p>

<p align="center">
  <strong>Local agents go brr. From anywhere.</strong><br>
  Claude Code, Codex, and Gemini CLI on your machine; Telegram, Slack, GitHub, and the web in your pocket.
</p>

Your coding agent already lives where the work is: your repo, shell, credentials,
odd test setup, and all the context nobody put in the ticket. brnrd gives it a
doorbell, a memory, and a live line back to you.

Send the task from your phone. Watch the plan and progress card change while it
works. Correct course without interrupting the run. Get a branch, a PR, or an
answer back in the same thread.

brnrd is not another coding model. It runs the CLI agents you already chose —
locally, under your rules — and turns them into a repo-knowing coworker you can
reach when you are away from the terminal.

## The loop

```text
you · Telegram / Slack / GitHub / dashboard
                         │
                         ▼
                  a small gate
                         │
                         ▼
        brnrd daemon · your machine · your repo
                         │
          ┌──────────────┼──────────────┐
          ▼              ▼              ▼
     Claude Code        Codex       Gemini CLI
          │              │              │
          └──────────────┼──────────────┘
                         ▼
             progress · replies · git
```

The daemon does the boring, load-bearing work around the model: it assembles the
current repo context, selects an execution environment, keeps conversation
continuity, exposes live control surfaces, preserves the work in git, and routes
the result back through the gate.

## Why it feels different

### It stays in the room

Each repo gets a resident with working memory, project knowledge, recent history,
and a playbook. A new run is another thought from the same coworker, not an
amnesiac subprocess wearing yesterday's name tag.

### The conversation stays live

Most agent automation makes you choose between waiting silently and barging into
the process. brnrd keeps an append-only reply channel and a live progress card
beside the run. Follow-ups arrive at runner boundaries, so you can add a fact or
change direction without killing the thought in flight.

### The model is a medium, not an identity

Pin Claude, Codex, or Gemini; choose a stronger core for a hard pass; delegate a
bounded job to a cheaper one; see quota posture before it becomes a surprise.
The resident keeps the thread while the runner changes underneath it.

### Local means local

The checkout, shell, runner process, and normal execution stay on your machine.
Use `host`, isolated `worktree`, or Docker execution per project. Managed gates
relay remote messages and status; they do not move your repo into a hosted IDE.

### The seams are files

Gates and live controls use a small file protocol. Telegram, Slack, GitHub, and
the managed cloud gate ship today; another transport does not require teaching
the daemon a new religion. The same seam carries progress notes, cross-thread
replies, runner handoffs, and PR publication.

## Try it

The shortest path from an npm-shaped world:

```bash
npx brnrd init -i
```

`npx brnrd` is a bootstrapper for the Python package, not a JavaScript port. It
keeps its own environment and leaves your system Python alone.

Or install the command directly:

```bash
uv tool install brnrd        # recommended when uv is already present
# or: pipx install brnrd
```

Then choose your door.

Managed gates, one account across repos:

```bash
brnrd connect                # pair this machine with brnrd.dev
brnrd add .                  # add the current repo
brnrd daemon install         # keep the local daemon alive with systemd/launchd
```

Or bring your own gate and keep the whole route self-hosted:

```bash
brnrd setup telegram         # auth + bind the current repo
brnrd daemon install
```

Now send a message from the other side:

```text
review PR #84 for the auth regression; show me the risky bit before changing it
```

The first useful demo belongs here. It is being recorded against the real
product, not mocked into a terminal — follow [#28](https://github.com/Gurio/brr/issues/28).

## What arrives in a wake

The resident does not begin with “please inspect the repo.” brnrd mounts a compact
orientation layer before the task:

- the repo contract and current run facts;
- the resident's own working memory;
- recent project activity and relevant known pitfalls;
- live queue, quota, delivery, and branch posture;
- the original request and the conversation that led to it.

The rest stays pull-based. Project knowledge can live in a private account home,
a repo-owned knowledge base, or ordinary docs; the injected slice points the
resident at the longer tail when it needs it.

That split is the trick: enough continuity to wake up somewhere, not so much
prompt that the agent spends the morning rereading its diary.

## Trust, without the “military-grade” paragraph

brnrd runs coding agents. They can execute commands and edit files with the
authority you give them. `host` mode has the same trust boundary as launching the
CLI yourself; Docker adds dependency and network isolation, but it is not a
containment boundary for a hostile agent when you mount credentials and a writable
repo into it.

Remote messages necessarily travel through the transport you choose. In managed
mode they also transit brnrd.dev before reaching your daemon; normal code execution
and repo contents stay local. Use self-hosted gates when that route is not
appropriate. Never paste credentials into a task — configure them through the
runner or gate instead.

The release-readiness security and privacy review is tracked under
[#23](https://github.com/Gurio/brr/issues/23). Two ingress gaps found in that
review are explicit release blockers: GitHub trigger-author authorization
([#408](https://github.com/Gurio/brr/issues/408)) and per-sender authorization
for Telegram groups ([#409](https://github.com/Gurio/brr/issues/409)). Until
they land, do not connect a public-repo GitHub gate or trust a paired group chat;
the managed one-to-one Telegram path is the dogfooded route. The execution and
environment contracts are inspectable in
[the execution map](src/brr/docs/execution-map.md) and
[environment guide](src/brr/docs/envs.md).

## Current posture

brnrd is alpha software, already used to build itself. The resident loop, local
daemon, managed Telegram path, live dashboard, runner switching, worktree/Docker
execution, and git handoff are real. The public docs, multi-project proving,
managed billing/failover, and some operational polish are still release work.

If you want a quiet appliance, wait. If you want a local agent coworker with a
remote door and you are willing to report the sharp edges, welcome in.

Useful internals:

- [Portals](src/brr/docs/portals.md) — live interaction and handoff surfaces
- [Conversations](src/brr/docs/conversations.md) — how continuity is recovered
- [Environments](src/brr/docs/envs.md) — host, worktree, and Docker semantics
- [Account daemon](src/brr/docs/account-daemon.md) — multi-repo/account topology
- `brnrd docs` — the docs that ship inside the tool

## Build it

Python 3.10+ and git are required. For a local checkout:

```bash
git clone https://github.com/Gurio/brr
cd brr
pip install -e ".[dev]"
pytest
```

The repo dogfoods brnrd. Run `brnrd up --dev-reload` while changing the daemon so
the next task picks up the new code without a process ritual.

## License

The local runtime in `src/brr/` is MIT. The managed backend and dashboard in
`src/brnrd/` and `src/brnrd_web/` are AGPLv3. You can run the complete stack
yourself; the split protects the hosted surface without closing the part that
lives on your machine. See [LICENSE-OVERVIEW.md](LICENSE-OVERVIEW.md).
