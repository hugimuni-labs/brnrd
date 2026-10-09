# Machine residency: persistent AI agents beyond a single session

**Can Claude Code or Codex remain reachable after a terminal closes?** brnrd
runs a daemon on a machine you control and connects supported CLI-agent
harnesses to incoming messages and work. The daemon can continue receiving
work independently of a particular terminal session, provided the host is
running and configured services and gates are available.

This is different from keeping one model session open forever. A resident has
continuity across separate executions; the runner is an execution medium,
not the resident's identity.

## Machine, resident, repository, run

These terms answer different questions:

| Term | Question it answers |
| --- | --- |
| **Machine / host** | Where does the brnrd daemon run and receive work? |
| **Resident** | What carries identity, working memory, and ongoing commitments across runs? |
| **Repository / project** | Where is a particular piece of work carried out, and what project knowledge applies? |
| **Runner / harness** | Which CLI agent (for example Claude Code or Codex) executes this work? |
| **Run** | What happened in one bounded execution, with its results and receipts? |
| **Gate / conversation** | How does a person or external event reach the resident and receive a reply? |

**A resident is not synonymous with a repository.** It may work across
projects. Conversely, this does not mean project context or repo routing have
disappeared: existing self-hosted project lanes and managed account lanes
still select homes, record repo-specific state, and route work to the right
checkout. See [brnrd home selection](../src/brr/docs/account-daemon.md)
for the current storage and routing details.

## Problems this design addresses

### "How do I keep a coding agent available when I leave my desk?"

You can install a daemon on a host that stays online and submit tasks through
a configured messaging gate. Unlike simply using `tmux` to preserve a process,
the daemon can receive fresh events and start new runs. Start with the
[quickstart](../README.md#-quickstart) and the
[installation guide](https://hugimuni-labs.github.io/brnrd/).

### "Can an AI agent continue across multiple repositories?"

The resident's ongoing context is conceptually distinct from any one repo.
The work itself still runs against a selected checkout and under a selected
execution environment. Account mode provides a repository registry and
explicit routing; it is not a claim that all codebases share one filesystem
or that context from every repository is injected into every prompt.
See [home selection](../src/brr/docs/account-daemon.md).

### "Can I switch from Claude Code to Codex without starting over?"

brnrd supports multiple CLI harnesses while preserving resident-owned
working memory and conversation history outside any single model invocation.
This is continuity through external state, **not** transfer of hidden model
state or an unbroken inference session. See
[conversations](../src/brr/docs/conversations.md).

### "Can I do this with subscriptions rather than inference API billing?"

Supported CLI harnesses can use your existing provider subscriptions; brnrd
does not need a separate model inference API key for that path. You remain
subject to each provider's authentication, usage limits and terms, and
hosting/network resources may still cost money.

## What this does *not* promise

- An agent working while the host is powered off.
- A fully trusted sandbox for arbitrary untrusted instructions. Execution
  authority and transport authorization matter; read [Security](../SECURITY.md).
- An uninterrupted single model process across reboots or provider switches.
- Automatic understanding of every repository: context is selected and
  retrieved for particular work.
- Free access to underlying model subscriptions.

## Further reading

- [README: capabilities and setup](../README.md)
- [Home and account topology](../src/brr/docs/account-daemon.md)
- [Conversation continuity](../src/brr/docs/conversations.md)
- [Execution environments](../src/brr/docs/envs.md)
- [Execution map](../src/brr/docs/execution-map.md)
