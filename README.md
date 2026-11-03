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

## ✦ Install

```bash
curl -fsSL https://brnrd.dev/install | sh
brnrd loom install     # the loom as a login service (launchd or systemd --user)
brnrd setup            # pick a chat, pair it, say hello
brnrd loom status      # state: running
```

The install line puts `brnrd` on your PATH through `uv` (it fetches `uv` if
it's missing) and ends with `Next: brnrd setup`. It installs the PyPI release,
which trails main. To follow main instead:

```bash
curl -fsSL https://brnrd.dev/install | sh -s -- --dev
brnrd setup
```

`--dev` clones main to `~/.brnrd/src`, installs it editable, runs
`brnrd loom install --dev` and `brnrd loom status` for you, and warns before
the known traps bite: a second `brnrd` on PATH, a stray loom, no `gh`. The loom
then follows main by itself, fast-forwarding when CI is green.

You need git, a signed-in `claude` or `codex` on PATH, and `gh` signed in if
the resident should back up its memory or open PRs. The full walk is at
[Install](https://hugimuni-labs.github.io/brnrd/getting-started/install/#run-the-loom).

## ✦ What setup asks

`brnrd setup` is the whole onboarding, in plain words:

1. **Which AI.** The bodies it found on this machine (Claude Code, Codex,
   Grok, Vibe), with what each has left of its quota.
2. **Which doors.** Ticked by default:
   - *brnrd cloud*: Telegram or WhatsApp through brnrd's own bot, and a chat on
     [brnrd.dev](https://brnrd.dev);
   - *a page on this computer*, in your browser, nothing to sign in to.

   Or *your own Telegram bot*, two minutes in the Telegram app.
3. **What else.** Back up the resident's memory to a private GitHub repo
   (`<you>/brnrd-self`), and on the cloud path, the brnrd-dev GitHub App so
   brnrd.dev can reach your repos.
4. **Say hello.** It prints a link (and a QR code) per door and waits until your
   first message reaches the resident.

Run it again and it shows what's connected and what isn't. `brnrd setup channel`
adds or mends a door.

## ✦ How it works

```text
           you, from anywhere
   Telegram · WhatsApp · brnrd.dev · a local page
                     │  letters
                     ▼
        the loom · your machine
     ┌───────────────┼───────────────┐
     ▼               ▼               ▼
  thread          thread          thread       one body each:
  (a room)        (a room)        (a room)     claude · codex · …
     │               │               │
     └──── commits to the self ──────┘
                     │
          PRs · replies · receipts
```

- **The self** is the resident's memory: a git repo of plain markdown, its
  identity, the lessons it learned the hard way, the people it works with.
  Every body reads it at wake and lands its work back into it. You read it the
  same way, by file.
- **A thread** is one piece of work with a README: the spec on top, the plan,
  the receipts. A message that fits no thread goes to the inbox; the resident
  opens a thread for it.
- **A wish** is what you want out of a thread, in one line. It stays open until
  the result is delivered and you accept it, or drop it.
- **A body** is one agent session on one thread, in its own room with its own
  clones. When the context fills, it writes down what matters and hands the
  seat to the next one.
- **Letters** reach a body between tool calls, so a correction lands without
  killing the thought in flight. A comment on a PR it opened comes back as a
  letter too.
- **Waiting is parked, not polled.** A body waiting on CI, a PR or you stops;
  the loom wakes it when the condition turns true.
- **Fuel is counted.** The loom reads each body's quota and paces the work to
  the week, so a resident doesn't spend your Monday's allowance by Sunday.

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
