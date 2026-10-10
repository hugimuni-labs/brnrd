# brnrd — Claude plugin (pre-release)

**Give your AI agent a life beyond the session.**

brnrd is a local-first resident agent runtime that keeps Claude Code and other supported CLI harnesses reachable on a machine you control. The brnrd daemon handles incoming events and remote messaging, maintains resident continuity across runs, and routes work across projects.

This plugin is a **small companion for Claude Code**, not a copy of brnrd and not the runtime itself. Its initial skills guide setup and diagnose a running daemon:

- `/brnrd:setup` — choose a self-hosted or managed route and walk through installation with consent gates.
- `/brnrd:diagnose` — inspect an existing local installation's status and, with permission, relevant logs.

## Develop and test

This folder is the plugin root. From a local clone of brnrd:

```bash
claude plugin validate --strict ./integrations/claude-plugin
claude --plugin-dir ./integrations/claude-plugin
```

Inside Claude Code, invoke `/brnrd:setup` or `/brnrd:diagnose`.

Installing this plugin **does not** install brnrd, enable a gate, run a daemon, or grant shell access. The user chooses and approves any system changes separately.

## Compatibility and distribution

The setup and diagnosis skills are designed for **Claude Code on a machine with access to the user's shell**. They do not give the browser edition of Claude or Cowork direct control of a local daemon. A proper local/remote MCP integration is a separate design step requiring an explicit tool API, authentication, and permission model.

This is a **staging folder in the brnrd monorepo**. Before submitting to Anthropic's plugin directory, confirm whether a monorepo subdirectory is accepted by the portal; otherwise publish the plugin as the root of a small public GitHub repository. Do not submit the whole brnrd monorepo as the plugin payload.

## Scope and trust

brnrd requires a machine that remains online. It uses the user's provider authentication/subscriptions where supported, subject to provider policies. brnrd agents can execute tools and modify files with permissions granted on the host; local execution is **not** isolation from untrusted input.

Self-hosted and managed configurations have different data flows. See [Security and privacy](https://github.com/hugimuni-labs/brnrd/blob/main/SECURITY.md) before connecting external channels.

Product: https://brnrd.dev  
Source: https://github.com/hugimuni-labs/brnrd  
Docs: https://hugimuni-labs.github.io/brnrd/
