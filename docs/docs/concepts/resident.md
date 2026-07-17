# The resident

A resident is the repo-aware coworker behind the remote door. Claude Code,
Codex, or Gemini CLI is the medium for a particular run; the resident is the
continuity that survives when that process exits and another one starts.

Each repo gets:

- working memory for the thread it is carrying;
- project knowledge and recent activity;
- a playbook for how work should be done there;
- the live run facts needed to deliver a result safely.

## What arrives in a wake

Before the task, brnrd assembles a compact orientation layer:

- the repo contract and current run facts;
- the resident's working memory and playbook;
- recent project activity and pitfalls relevant to the task;
- queue, quota, delivery, environment, and branch posture;
- the request and the conversation that led to it.

The longer tail stays pull-based. Project knowledge may live in a private
account home, a repo-owned knowledge base, or ordinary project docs. brnrd
injects a useful slice and points the resident to the rest when needed. It does
not assume that a repo-local `kb/` directory is the only or default shape.

That split gives a wake enough continuity to begin somewhere without making
every run reread the project's entire history.

## One resident, different models

Changing the Shell or Core does not create a new coworker. A cheaper Core can
handle routine work, while the same resident can hand a hard pass to a stronger
local Core with its context intact. See [Models & quota](../guides/models.md).
