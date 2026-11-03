<p align="center">
  <img src="https://raw.githubusercontent.com/hugimuni-labs/brnrd/main/media/brnrd-boot.gif" width="420" alt="brnrd boot sequence: underscore, b_d, br_rd, brnrd">
</p>

<h1 align="center">brnrd</h1>
<p align="center"><strong>Local agents go brr. From anywhere.</strong></p>

<p align="center">
  <strong>A resident for the machine you leave on: Claude Code or Codex, kept at work 24/7, reachable from your phone.</strong><br>
  brnrd runs the loom, a small service that wakes your coding agent with its own memory, hands it the letters you send from Telegram, WhatsApp or brnrd.dev, and keeps every piece of work in a thread until it lands. No API key: it runs the CLI subscriptions you already have. It leaves receipts: commits, PRs, a README you can read.
</p>

<p align="center">
  <strong>3,147 commits on main · 2,391 by the resident · 1,734 merged PRs · since March 2026 · as of 2026-11-03.</strong><br>
  <sub>This repo is built with brnrd, by brnrd; the git log is the demo.</sub>
</p>

https://github.com/user-attachments/assets/2357758c-fa35-47ba-92b1-f02d51c338e3

<p align="center">
  <img src="https://img.shields.io/badge/status-alpha-f59e0b" alt="alpha">
  <img src="https://img.shields.io/badge/python-3.10+-3776ab" alt="python 3.10+">
  <a href="LICENSE-OVERVIEW.md"><img src="https://img.shields.io/badge/license-MIT%20%2F%20AGPLv3-2ea44f" alt="license: MIT / AGPLv3"></a>
  <a href="https://brnrd.dev"><img src="https://img.shields.io/badge/cloud-brnrd.dev-6d28d9" alt="brnrd.dev"></a>
</p>

---

Your CLI agent can already work in your repos and your shell. What it lacks is
a place to come back to. A session ends and takes everything with it.

**brnrd gives it a seat: a memory in git, a queue of work, and a line to you.**

Write from your phone. The resident reads it, opens a thread, does the work,
and answers in the same chat with a branch, a PR or a sourced answer. Close the
laptop lid on the desk, not the one in your bag.

brnrd is **not another coding agent.** It runs the one you chose, on your
machine, under your accounts.

**Reach it from Telegram, WhatsApp, brnrd.dev or a page on this machine.** Or
run your own Telegram bot, with more locally managed connectors coming.

## ✦ Install

```bash
curl -fsSL https://brnrd.dev/install | sh
brnrd setup
```

The first line puts `brnrd` on your PATH through `uv` (fetching `uv` if it's
missing), installs the loom as a login service (launchd or systemd --user) and
shows its status. `brnrd setup` picks the AI and the doors, pairs them, and
waits for your first message to reach the resident.

To follow main instead of the PyPI release, pipe it to `sh -s -- --dev`
(`curl -fsSL https://brnrd.dev/install | sh -s -- --dev`): it clones main to
`~/.brnrd/src`, and the loom fast-forwards it whenever CI is green.

You need git, a signed-in `claude` or `codex` on PATH, and `gh` signed in if
the resident should back up its memory or open PRs. The full walk is at
[Install](https://hugimuni-labs.github.io/brnrd/getting-started/install/#run-the-loom).

## ✦ How it works

```text
            you, from anywhere
 Telegram · WhatsApp · brnrd.dev · a local page
                     │
                     ▼
           the loom · your machine
                     │
            ┌────────┼────────┐
            ▼        ▼        ▼
         Claude    Codex    Grok
            │        │        │
            └────────┼────────┘
                     ▼
         progress · replies · git
```

Each thread gets its own body, on whichever AI has fuel left; the loom paces
the work to each quota's week.

- **The self** is the resident's memory: plain markdown in git, read at every
  wake and landed back into. You read it the same way.
- **A thread** is one piece of work: a README with the spec, the plan, the
  receipts. A message that fits none goes to the inbox.
- **A wish** is what you want from a thread, in one line, open until you accept
  the result or drop it.
- **A body** is one agent session on one thread. When its context fills, it
  writes down what matters and hands over.
- **Letters** reach a body between tool calls, PR comments included, so a
  correction lands without killing the thought in flight.
- **Waiting is parked, not polled:** the loom wakes the body when CI, a PR or
  you answer.

`brnrd loom where` prints where the home, the self and the logs are.
`brnrd loom logs` shows the loom working.

## ✦ Trust & privacy

No "military-grade" paragraph. The resident runs coding agents with their
approval prompts off (`--dangerously-skip-permissions`,
`--dangerously-bypass-approvals-and-sandbox`), on purpose. So the honest
posture *is* the product:

- **The agent has your authority.** A body can do what you can do in that
  shell. Any text it reads (a message, an issue, a PR comment) may become
  instruction.
- **Only your people are you.** A chat counts as a person once it's paired
  through `brnrd setup`. Anyone else arrives labelled a stranger, and their
  letter is marked tainted, not obeyed as yours.
- **Local stays local, with one stated exception.** Your repos, rooms and
  self live on your machine; the self's backup goes to a *private* repo only
  (the loom refuses to push it anywhere the forge doesn't call private). On
  the brnrd cloud path, your messages transit brnrd.dev, and the loom pushes
  brnrd.dev a snapshot of thread READMEs and ledger heads for the web board.
  Never people's pages, never a body's session.
- **Never paste credentials into a message.** Sign the CLIs in yourself.

The threat model is in **[SECURITY.md](SECURITY.md)**.

## ✦ Docs

The guide (prerequisites, install, troubleshooting, CLI reference) lives at
**[hugimuni-labs.github.io/brnrd](https://hugimuni-labs.github.io/brnrd/)**.
For the loom's own seams:

| | |
|---|---|
| [Connectors](src/brr/loom/runtime/connectors/README.md) | how a chat becomes a door: one folder, an in and an out |
| [Bodies](src/brr/loom/runtime/bodies.md) | how an agent CLI becomes a body |
| `brnrd loom --help` | the service: install, start, stop, status, logs, config, check |
| `brnrd runtime --help` | a body's verbs: send, wish, ask, settle, publish, … |

The daemon, brnrd's earlier runtime (`brnrd up`, `brnrd account connect`),
still ships for existing installs; new installs need only the loom.

## ✦ Current posture

**Alpha, already building itself** (the numbers at the top are `git log` on
this repo, re-measured by `scripts/receipt_line.py`). The loom, its threads
and wishes, Telegram and brnrd.dev doors, quota pacing and follow-main are
what this repo runs on every day. The web board, multi-machine failover and a
setup a non-developer finishes unaided are still being built, in the open.

If you want a quiet appliance, wait. If you want a coworker that lives on your
machine and answers your phone, and you'll report the sharp edges: welcome in.

<details>
<summary>Build it yourself</summary>

Python 3.10+ and git are required.

```bash
git clone https://github.com/hugimuni-labs/brnrd
cd brnrd
pip install -e ".[dev]"
pytest
```

To run a loom from your checkout, use the `--dev` install above: `brnrd loom
install` refuses to run its service from a bare source checkout.

</details>

## License

The local runtime in `src/brr/` is **MIT**. The managed backend and dashboard in
`src/brnrd/` and `src/frontend/` are **AGPLv3**. You can run the complete stack
yourself; the split protects the hosted surface without closing the part that lives
on your machine. See [LICENSE-OVERVIEW.md](LICENSE-OVERVIEW.md).
