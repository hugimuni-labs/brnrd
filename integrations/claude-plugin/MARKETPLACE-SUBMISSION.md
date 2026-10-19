# Claude directory submission — draft for review

Owner: **HugiMuni SAS** · Product: **brnrd** · Listing target: **Claude plugins (Claude Code use case)**

## Listing copy

- **Name:** brnrd
- **Headline:** Give your AI agent a life beyond the session.
- **Short description:** Keep Claude Code reachable on your machine beyond a terminal session. Set up and diagnose a resident agent with persistent context, remote messaging, and cross-project workflows.
- **Developer:** HugiMuni SAS (France)
- **Homepage:** https://brnrd.dev
- **Source:** https://github.com/hugimuni-labs/brnrd
- **Documentation:** https://hugimuni-labs.github.io/brnrd/
- **Support:** https://github.com/hugimuni-labs/brnrd/issues

### Longer description

Claude Code is powerful inside a session. brnrd gives it a continuing place on a machine you control.

The independent brnrd daemon can receive work from messaging gates, route tasks to Claude Code and other supported CLI engines, preserve resident-owned context across runs, and deliver progress and durable work receipts. It works across projects rather than tying the resident to one repository.

This starter plugin helps Claude Code users install, configure, and troubleshoot that runtime. It does not itself run an agent, expose remote MCP tools, or connect to a daemon without user configuration.

### Discovery keywords

persistent Claude Code, coding agent daemon, run Claude Code 24/7, local AI agents, remote agent access, agents across sessions, machine-scoped agent, resident agent, Telegram agent, agent memory

### Positioning / differentiators

- **Complement, not replacement:** Claude Code remains a chosen CLI harness.
- **Independent continuity:** memory and ongoing commitments live outside single model sessions.
- **Machine-first:** daemon works on the user's own host; project context is selected per task.
- **Transparent limits:** an online host, intentional gate access, and CLI agent subscriptions remain necessary.

## Before submitting

- [ ] Confirm the directory portal accepts a GitHub subdirectory; otherwise move this plugin folder to a standalone public GitHub repository root.
- [ ] Verify accurate company/product URLs and brand assets; add a real plugin icon if required.
- [ ] Review listing claims against the current product, especially gate support, security and data publishing.
- [ ] Run `claude plugin validate --strict` on a supported Claude Code version and run both skills in a fresh local session.
- [ ] Test empty-machine setup, already-running daemon, unauthorized operations, npx install, macOS and Linux flows.
- [ ] Check the official directory pre-submission checklist, trust/safety review, and each supported Claude surface. The skill's CLI instructions do not make browser Claude into a daemon client.
- [ ] Confirm an eligible paid Claude account (on Team, an Owner) submits at https://claude.ai/directory/manage.
- [ ] Ask the Claude Startups team about review guidance, catalog visibility, and whether an official marketplace listing is suitable.
- [ ] Submit for review **only after** the above and explicit approval from HugiMuni.

## Note for Anthropic / Startup office hours

> We build brnrd, a local-first runtime that gives Claude Code continuity beyond a terminal session. We'd like to publish a Claude plugin for onboarding and diagnostics and explore a secure MCP interface to the resident daemon. Could you help us validate the best category, user experience and security expectations for directory review, and advise on discoverability for Claude Code users?

## Future phase: actual daemon connectivity

Design an opt-in MCP interface rather than binding model-generated commands directly to the shell. Start with narrowly scoped read-only status and event inspection. Only later consider authenticated task submission, strict recipient permissions, consent, logging, and protection against prompt-injection through gate traffic. Avoid presenting that design as implemented today.

Official references:
- https://code.claude.com/docs/en/plugins/create
- https://code.claude.com/docs/en/plugins/publish
- https://code.claude.com/docs/en/plugins-reference
- https://claude.com/resources/articles/build-plugins-for-claude
