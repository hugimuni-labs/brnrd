# brr

**Structured AI agent playbook with a persistent knowledge base and remote
execution.**

brr produces `AGENTS.md` — a playbook that encodes your project's
conventions, workflow, and guardrails. Any AI tool that reads it (Claude
Code, Cursor, Codex, Gemini) gets the same behavior. On top of that, brr
adds a remote execution layer: a daemon that accepts tasks from Telegram,
Slack, GitHub (issue labels, PR/issue mentions), or anything that writes a
file, and runs them through whichever AI CLI you have installed.

Two layers of value, and you can stop at either one:

1. **Playbook only.** `AGENTS.md` + a project knowledge base work with any
   AI tool — copy the conventions, use them everywhere, no brr daemon
   required.
2. **Full tool.** The brr daemon handles remote execution, gate I/O,
   knowledge persistence, and git push — so you can hand a task to a
   Telegram message and get a reviewed PR back.

No database, no cloud dependency, no lock-in: everything brr writes is
plain files in your repo (`AGENTS.md`, a knowledge base) or a gitignored
runtime directory (`.brr/`).

## Two ways to run it

|  | Self-hosted (this site) | Hosted — [brnrd.dev](https://brnrd.dev) |
|---|---|---|
| What it is | Run the brr daemon yourself, on your own machine or server | The same software, operated for you |
| Cost | Free, always | Subscription — see [brnrd.dev/pricing](https://brnrd.dev) |
| You provide | An AI CLI + its API key/subscription | Nothing extra — bring your own AI provider or use theirs |
| Full feature parity | Yes | Yes |

Self-hosting is not a crippled trial of the hosted product — it's the
same code, and it stays free. brnrd.dev exists for people who'd rather not
run the always-on daemon themselves.

## Where to go next

- New to brr? Start with [Quickstart](getting-started/quickstart.md).
- Want to understand the moving parts before you install anything? Read
  [Concepts](concepts/agents-and-kb.md).
- Looking for a specific command? Jump to the
  [CLI reference](reference/cli.md).
- Want brr to run unattended on a server without you operating it?
  See [Self-hosting brnrd](self-hosting/index.md).

## Source

brr is open source: [github.com/Gurio/brr](https://github.com/Gurio/brr).
Contributions, issues, and forks are welcome — see the repository's
`AGENTS.md` for the project's own conventions.
