---
name: setup
description: Help a user install and connect the brnrd resident agent daemon on their own machine, with explicit confirmation before changes.
disable-model-invocation: true
---

# Set up brnrd

brnrd is a separately installed, local-first agent runtime. This skill is a guided setup assistant, **not** the daemon, and installing the plugin does not start any background process.

1. Ask what the user wants: a self-hosted gate (such as Telegram) or the optional brnrd.dev managed account. Make the tradeoffs clear before giving commands.
2. Check what is already available with `command -v brnrd` and, if installed, `brnrd --version`. Check service state with `brnrd daemon status`. Do not install or reinstall when a running setup already exists.
3. If installation is needed, offer one documented option appropriate to their environment:
   - `npm install -g brnrd` (Node launcher), or
   - `uv tool install brnrd`, or
   - `pipx install brnrd`.
   Require explicit approval before installing software. `npx brnrd` is also an option, but it does **not** put a standalone `brnrd` command on PATH; spell later commands `npx brnrd ...` when using it.
4. Explain the route and request approval before configuring an account, gate, or persistent service:
   - **Self-hosted Telegram:** from the desired project context, run `brnrd gate setup telegram` (interactive credentials), then `brnrd daemon install`.
   - **Managed account:** from the desired project context, run `brnrd account connect` to pair with brnrd.dev and install/start the user service. Add further repositories with `brnrd account add <repo>` as needed.
5. After the chosen flow, verify with `brnrd daemon status`. Tell the user what to try from their phone or gate and where to find the docs.

**Machine, not repository:** brnrd keeps a resident on the machine. Tasks can target different repositories; a chosen repo for setup does not define the agent's entire identity.

**Boundaries:** Do not request credentials in the chat, echo private tokens, or send logs or project data to external services. Managed mode introduces transport and optional publishing to brnrd.dev; explain this rather than calling it fully offline. The daemon executes CLI agents with the host's authority; it is not a sandbox for untrusted instructions. Never start a service, connect an account, change gate permissions, or install packages without clear user approval.

Docs: https://hugimuni-labs.github.io/brnrd/  
Security: https://github.com/hugimuni-labs/brnrd/blob/main/SECURITY.md
