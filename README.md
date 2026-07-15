<p align="center">
  <img src="media/brnrd-boot.gif" width="720" alt="brnrd boot sequence: underscore, b_d, br_rd, brnrd">
</p>

<h1 align="center">brnrd</h1>

<p align="center">
  <strong>Local agents go brr. From anywhere.</strong><br>
  Claude Code, Codex, and Gemini CLI on your machine — reachable from Telegram, Slack, GitHub, and the web.
</p>

<p align="center">
  <img src="https://img.shields.io/badge/status-alpha-f59e0b" alt="alpha">
  <img src="https://img.shields.io/badge/python-3.10+-3776ab" alt="python 3.10+">
  <a href="LICENSE-OVERVIEW.md"><img src="https://img.shields.io/badge/license-MIT%20%2F%20AGPLv3-2ea44f" alt="license: MIT / AGPLv3"></a>
  <a href="https://brnrd.dev"><img src="https://img.shields.io/badge/managed-brnrd.dev-6d28d9" alt="brnrd.dev"></a>
  <a href="https://github.com/Gurio/brr/issues/23"><img src="https://img.shields.io/badge/release-tracked%20%2323-0969da" alt="release #23"></a>
</p>

---

Your coding agent already lives where the work is: your repo, your shell, your
credentials, the odd test setup, and all the context nobody put in the ticket.
**brnrd gives it a doorbell, a memory, and a live line back to you.**

Send the task from your phone. Watch the plan and progress card change while it
works. Correct course without interrupting the run. Get a branch, a PR, or an
answer back in the same thread.

brnrd is **not another coding model.** It runs the CLI agents you already chose —
locally, under your rules — and turns them into a repo-knowing coworker you can
reach when you are away from the terminal.

## ✦ What you get

| | Capability | What it actually means |
|---|---|---|
| 📟 | **A remote door** | Fire off a task from Telegram, Slack, GitHub, or the dashboard. The agent runs at home; you drive from your pocket. |
| 🧠 | **A resident, not a reset** | Each repo gets a coworker with working memory, project knowledge, and a playbook. A new run is the same mind's next thought — not an amnesiac subprocess wearing yesterday's name tag. |
| 💬 | **Interrupt-free interaction** | Follow the live plan and progress card. Add a fact or change direction at runner boundaries, without killing the thought in flight. |
| 🔀 | **The model is a medium** | Pin Claude, Codex, or Gemini. Escalate a core for a hard pass, downshift for grunt work, and see quota posture before it becomes a surprise. |
| 🏠 | **Local means local** | Checkout, shell, runner process, and execution stay on your machine. Managed gates relay messages and status; your source never leaves home. |
| 🧾 | **Git-native receipts** | Every run ends somewhere durable: a branch, a PR, or an answer in the thread. The diff is the proof. |
| 📁 | **The seams are files** | Gates and live controls speak a small file protocol. A new transport is not a new religion for the daemon. |

## ✦ The loop

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
continuity across runs, exposes live control surfaces, preserves the work in git,
and routes the result back through the gate.

## ✦ Quickstart

Install the command:

```bash
uv tool install brnrd        # recommended when uv is already present
# or: pipx install brnrd
# or: npx brnrd init -i      # bootstraps the Python package; leaves your system Python alone
```

Then pick your door.

<table>
<tr>
<th>Managed — one account across repos</th>
<th>Self-hosted — bring your own gate</th>
</tr>
<tr>
<td>

```bash
brnrd connect          # pair this machine with brnrd.dev
brnrd add .            # add the current repo
brnrd daemon install   # keep the daemon alive (systemd/launchd)
```

</td>
<td>

```bash
brnrd setup telegram   # auth + bind the current repo
brnrd daemon install
```

</td>
</tr>
</table>

Now send a message from the other side:

```text
review PR #84 for the auth regression; show me the risky bit before changing it
```

> The first end-to-end demo is being recorded against the real product, not
> mocked into a terminal — follow [#28](https://github.com/Gurio/brr/issues/28).

<details>
<summary><code>npx brnrd</code> is not a JavaScript port</summary>

It is a bootstrapper for the Python package. It keeps its own environment and
leaves your system Python alone — a convenience for people who live in an
npm-shaped world, nothing more.

</details>

## ✦ What arrives in a wake

The resident does not begin with "please inspect the repo." brnrd mounts a
compact orientation layer *before* the task, so the agent wakes up somewhere
instead of nowhere:

- the repo contract and current run facts;
- the resident's own working memory and playbook;
- recent project activity and the pitfalls relevant to this task;
- live queue, quota, delivery, and branch posture;
- the original request and the conversation that led to it.

The rest stays pull-based. Project knowledge can live in a private account home,
a repo-owned knowledge base, or ordinary docs; the injected slice just points the
resident at the longer tail when it needs it.

That split is the whole trick: **enough continuity to wake up as someone, not so
much prompt that the agent spends the morning rereading its diary.**

## ✦ Where it runs

Every project chooses an execution environment. They are honest about what they
isolate — none of them is a cage for a hostile agent (see [Trust & privacy](#-trust--privacy)):

| Mode | What it isolates | Reach for it when |
|---|---|---|
| `host` | Nothing beyond your own shell. Edits hit your working tree immediately. | you trust the agent and want zero friction — the dogfooded default. |
| `worktree` | A separate worktree and branch, so your working tree stays clean. Shares your `.git`, credentials, network, and filesystem — **not a security boundary.** | you want runs off your working tree without container overhead. |
| `docker` | Dependencies and network, and it narrows the agent's host-filesystem view to the repo plus mounted credential dirs. **Not** a credential or containment boundary: the repo is mounted read-write, your model/GitHub/SSH credentials cross in, and the network is on by default. | you want a clean toolchain or network control (`docker.network=none`) — defense-in-depth over a trusted agent, not a sandbox. |

Full isolation matrix: [SECURITY.md](SECURITY.md) · semantics: [Environments](src/brr/docs/envs.md) · scope tracked in [#80](https://github.com/Gurio/brr/issues/80).

## ✦ Trust & privacy

No "military-grade" paragraph. brnrd runs coding agents that execute commands and
edit files with the authority you grant them — the runners are launched with their
approval prompts bypassed, on purpose. So the honest posture *is* the product:

- **The agent has your authority.** `host` mode is the same trust boundary as
  launching the CLI yourself. Any text brnrd ingests — an issue body, a PR comment, a
  chat message — becomes instruction the agent may act on. Treat every gate you open
  as a door into your shell.
- **Gates authorize the channel, not the person.** A connected GitHub repo runs on
  the *mention*, not the commenter ([#408](https://github.com/Gurio/brr/issues/408));
  a bound chat runs on the *room*, not the sender
  ([#409](https://github.com/Gurio/brr/issues/409)). Both are explicit release
  blockers. Until they land: keep to private repos, and the managed one-to-one
  Telegram path is the dogfooded, safe route — do not connect a public-repo gate or
  trust a group chat.
- **Local stays local — with one honest caveat.** Your checkout, `.git`, and run
  execution never leave the machine. Remote messages travel through the transport you
  choose (and, in managed mode, transit brnrd.dev on the way to your daemon). If you
  run `brnrd connect`, the dashboard also mirrors *derived* knowledge — plans, the
  decision ledger, run summaries, PR titles — to brnrd.dev; your source code does not.
- **Never paste credentials into a task.** Configure them through the runner or gate.

The full threat model, the per-gate authorization table, and the honest environment
isolation matrix live in **[SECURITY.md](SECURITY.md)**. The review is tracked under
[#23](https://github.com/Gurio/brr/issues/23); execution and environment contracts
are inspectable in [the execution map](src/brr/docs/execution-map.md) and
[environment guide](src/brr/docs/envs.md).

## ✦ Docs

| | |
|---|---|
| [Portals](src/brr/docs/portals.md) | live interaction and handoff surfaces |
| [Conversations](src/brr/docs/conversations.md) | how continuity is recovered across runs |
| [Environments](src/brr/docs/envs.md) | host, worktree, and Docker semantics |
| [Execution map](src/brr/docs/execution-map.md) | what happens between message and reply |
| [Account daemon](src/brr/docs/account-daemon.md) | multi-repo / multi-account topology |
| `brnrd docs` | the docs that ship inside the tool |

## ✦ Current posture

brnrd is **alpha software, already used to build itself.** The resident loop, local
daemon, managed Telegram path, live dashboard, runner switching, worktree/Docker
execution, and git handoff are real. The public docs, multi-project proving, managed
billing/failover, and some operational polish are still release work.

If you want a quiet appliance, wait. If you want a local agent coworker with a
remote door — and you are willing to report the sharp edges — welcome in.

<details>
<summary>Build it yourself</summary>

Python 3.10+ and git are required.

```bash
git clone https://github.com/Gurio/brr
cd brr
pip install -e ".[dev]"
pytest
```

The repo dogfoods brnrd. Run `brnrd up --dev-reload` while changing the daemon so
the next task picks up the new code without a process ritual.

</details>

## License

The local runtime in `src/brr/` is **MIT**. The managed backend and dashboard in
`src/brnrd/` and `src/brnrd_web/` are **AGPLv3**. You can run the complete stack
yourself; the split protects the hosted surface without closing the part that lives
on your machine. See [LICENSE-OVERVIEW.md](LICENSE-OVERVIEW.md).
