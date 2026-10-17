---
name: diagnose
description: Diagnose a local brnrd daemon installation and its service logs without changing configuration or starting processes.
disable-model-invocation: true
---

# Diagnose brnrd

This skill helps examine the *existing* local brnrd installation. Do not make repairs without asking first.

1. Determine which machine and user account host brnrd. The CLI may be absent on the current machine even if brnrd runs elsewhere. Never claim to access a remote machine without a configured connection.
2. Check `command -v brnrd` and `brnrd --version` where available. If the user uses the npx launcher, substitute `npx brnrd` consistently.
3. Inspect `brnrd daemon status`. A user service is provided through launchd on macOS and systemd user services on Linux.
4. If more information is required, *ask before reading or displaying logs*, then inspect `brnrd daemon logs --no-follow` and summarize relevant errors. Redact tokens, paths identifying individuals, account data, and private repository contents before sharing.
5. Recommend the smallest reversible next step. Get explicit approval before running commands that install, restart, stop, configure, remove, or update anything.

Avoid treating the plugin as an MCP bridge or claiming a daemon is active just because this skill is installed. brnrd keeps its work and resident memory outside an individual Claude Code session. Project selection and machine residency are different concepts.

CLI reference: https://hugimuni-labs.github.io/brnrd/reference/cli/  
Troubleshooting: https://hugimuni-labs.github.io/brnrd/guides/troubleshooting/
