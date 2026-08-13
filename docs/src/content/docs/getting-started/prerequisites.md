---
title: Prerequisites
description: Set up the few tools brnrd needs before you install it.
---

You do not need to be a terminal expert. You need a terminal, a GitHub login,
and one coding agent that brnrd can drive. Set these up once, in order.

## 1. A terminal

On macOS, open **Terminal** from Applications → Utilities. On Linux, use the
terminal app that came with your desktop.

On Windows, first [install WSL 2](https://learn.microsoft.com/windows/wsl/install),
then do the rest of this guide inside its Ubuntu terminal. Everything below is
plain Linux from there — though we have not traced the WSL path end to end yet,
so treat it as adventurous and tell us what you hit. Native Windows (without
WSL) is not a supported path.

## 2. Git and GitHub CLI

Install both tools for your system:

- **macOS:** [Git](https://git-scm.com/install/mac) and [GitHub CLI (`gh`)](https://github.com/cli/cli#installation)
- **Linux or WSL:** [Git](https://git-scm.com/install/linux) and [GitHub CLI (`gh`)](https://github.com/cli/cli/blob/trunk/docs/install_linux.md)

If you do not have a GitHub account, create one at
[github.com/signup](https://github.com/signup).

Then connect `gh` to that account:

```bash
gh auth login
```

Choose **GitHub.com**, then **HTTPS**, and let `gh` authenticate Git for you.
You do **not** need to create or configure an SSH key; this HTTPS login covers
the Git operations brnrd performs.

## 3. One coding agent

Choose one. brnrd runs the CLI on your machine using its own subscription login;
you do not need a separate brnrd model key.

- **Claude Code:** install it using [Anthropic's setup guide](https://docs.anthropic.com/en/docs/claude-code/getting-started), then sign in with a Claude Pro or Max subscription.
- **Codex CLI:** install it using [OpenAI's CLI guide](https://developers.openai.com/codex/cli/), then sign in with ChatGPT. [ChatGPT Plus](https://chatgpt.com/pricing/)—the US $20/month plan—is enough to start; availability and limits vary by plan and region.

## 4. The install runtime

Live in Node already? You're home: `npm install -g brnrd` on the next page is
the recommended path, and it provisions a private Python for you when the
machine has none. Just have a current [Node.js LTS](https://nodejs.org/en/download)
installed (on a Mac, `brew install node` does it).

More of a Python person? The install page also offers
[uv](https://docs.astral.sh/uv/getting-started/installation/) and
[pipx](https://pipx.pypa.io/stable/installation/) — brnrd itself runs on
Python 3.10+.

That is the whole toolbox. You are ready to [install brnrd](../install/).
