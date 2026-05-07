# Notes: Fleet & Steering — open pondering

Capture-only. Decisions in here are **not committed to**; this page
exists so the thinking doesn't evaporate while we focus on
`design-env-interface.md` and ship the worktree-PR.

When any of these crystallise into something actionable, promote them
into a deck or a `design-*.md` page and link from `kb/index.md`.

Companion to `deck-brr-fleet-steering.md` — the deck holds the strategic
"shape." This page holds the open questions and current-best-guess
answers.

---

## 1. Drop `brr eject`?

> **Promoted — see `plan-overlays.md` → "`brr eject` retirement".**
> The staged retirement path (ship overlays → deprecation notice → remove
> one release later) now lives there as part of the overlays plan. The
> thinking below is kept for provenance.

**Likely yes.** The verb made sense when per-repo override (`.brr/prompts/`)
was the only customisation path. Once overlays exist as the
"workflow-wide" path, `eject` is just a copy-paste shortcut.

Replacement: ship a `src/brr/docs/customising.md` page that documents
the lookup chain (bundled → overlay → per-repo) and explains how to
copy a bundled prompt for editing using `cp $(python -c 'import brr;
print(brr.prompts_dir())') ./.brr/prompts/`. The user can `cp` themselves
or alias it. No need for a verb.

**Pre-overlay:** keep `brr eject`, mark it `--legacy` once overlays
ship, drop in a follow-up.

**Open:** does this mean per-repo `.brr/prompts/` is also de-emphasised?
Unsure. Probably stays as the "this one weird repo" escape hatch but
loses CLI discoverability. Fine.

---

## 2. Single overlay file vs multi-file overlay

> **Promoted — see `plan-overlays.md` → "Research gate (blocking)".**
> The single-vs-multi choice is now a blocking research step before the
> overlays implementation can start; the deliverable is
> `kb/research-overlay-shape.md`. The two-option analysis below feeds
> directly into that page and is kept here for provenance.

User leans toward **single file**. That's a real simplification worth
exploring.

Two reads of "single overlay file":

### A. Single delta appended to every prompt

```
~/.config/brr/overlay.md           # one file, always read, appended to every prompt
```

Treat the overlay as **personal steering injected into every agent
prompt** — like `AGENTS.md`, but at the user level rather than repo
level. The agent reads it as additional context. No file-by-file
override; the overlay never *replaces* a bundled prompt, only augments
it.

This is dramatically simpler than the four-layer chain in the deck:

```
bundled prompt + ~/.config/brr/overlay.md (if present) + .brr/prompts/<x>.md (if present)
```

Pros:
- One file. One concept. Spinal-brain-easy.
- Composable: the overlay says "always commit with conventional commit
  format" once, and every prompt picks it up.
- Maps to user mental model ("my personal AGENTS.md").

Cons:
- Can't fully replace a bundled prompt (only add to it). If a user
  hates the bundled `triage.md`, they still need per-repo override.
- Prompt size grows; overlay content is paid for on every invocation.

### B. Single file with sections that target specific prompts

```yaml
# ~/.config/brr/overlay.md
---
applies_to: [run, triage]      # sections targeted to specific prompts
---

# Personal steering
- always use conventional commits
- prefer prose-style log entries

---
prompt: triage
---

# Triage hint
- treat anything with the word "spike" as branch=auto, env=worktree
```

Slightly more powerful, much more complex parsing. Probably not worth
it.

### Likely landing point

Ship **A** (single appended delta) as the overlay primitive. Keep
`.brr/prompts/<x>.md` as the per-repo full-replacement escape hatch.
Multi-file profile overlays (the deck's `~/.config/brr/profiles/<name>/`
shape) become a v-next concern — let one file demonstrate the
overlay-thoughtless promise first.

If this lands, the overlay slide in the deck needs a rewrite. **Note
to self:** revisit when picking overlays back up.

---

## 3. Where does overlay belong?

`~/.config/brr/` (XDG-respecting, override via `BRR_CONFIG_HOME`) is
fine. Self-hosted-friendly because the user's machine *is* the source
of truth. Git-cloning that dir for remote-edit is the obvious nice-to-have
(see deck slide).

Real question: should overlay belong to the **machine** (one overlay per
user-machine) or the **user** (synced across all their machines, one
overlay)? The git-clone trick collapses them — one repo, N machines,
each cloned into `~/.config/brr/`. Use git branches per machine if
divergence is needed; default = main everywhere.

---

## 4. Setup for a spinal-brain user

Goal: zero config to get started, one optional command to opt into overlay.

Sketch of the happy path:

```
$ brr init
[brr] no .brr/ — running setup
[brr] runner: codex (auto-detected)
[brr] no overlay configured (~/.config/brr/overlay.md absent)
[brr] init complete

$ brr overlay init                 # optional, idempotent
[brr] created ~/.config/brr/overlay.md (template)
[brr] edit it to steer all your brrs

$ brr overlay init --git=git@gitlab:me/brr-overlay.git
[brr] cloned ~/.config/brr/  (will sync on next run)
```

`brr overlay init` is the *only* extra knob. Without it, brr behaves
exactly as today.

Ties to brnrd (later): `brnrd adopt` could `brr overlay init` on each
fleet member if they don't already have one.

---

## 5. Discovery without scanning home

Registry file is right; the unreliability comes from **manual
maintenance**. Fix: brr maintains its own registry.

```
~/.local/state/brr/repos.json      # XDG state dir
```

`brr init` appends `{path, created_at}`.
`brr down` (or a future `brr forget`) removes its entry.
`brnrd ls` reads this file; prunes entries whose path no longer exists.

Self-maintaining. No scan. Failure mode = stale entries → easy to
detect, easy to prune. brr never overwrites: it's append + delete-by-path.

For multi-machine fleets (brnrd remote), each machine has its own
local registry; brnrd aggregates by syncing them via whatever transport
it uses (see brnrd notes below). The brr-side groundwork is just:
"maintain a JSON file in a known location."

**Brr-side TODO when this lands:**
- `adopt.init_repo` appends to `~/.local/state/brr/repos.json` after success.
- New `brr forget` (or `brr init --remove`) command for explicit removal.
- `brr status --json` for machine-readable status (brnrd needs this anyway).

---

## 6. brnrd identity — agentic project manager

User's framing: brnrd is **not a CLI**. It's an agentic system with its
own brain. Manages brrs, has tools, has APIs, talks to the user.

Closer model: brnrd is **the operator-as-agent**. Today the human
operator decides "which brr to nudge, when, how, with what context."
brnrd is the agent that takes over that role.

What brnrd *uses* (existing brr surface, mostly):
- `~/.local/state/brr/repos.json` — fleet inventory.
- `brr status --json` per repo — health, recent tasks, pending events.
- Writing to each brr's `.brr/inbox/` (brnrd is essentially a "meta gate" — could even register itself as one).
- Reading each brr's `.brr/responses/` and `kb/`.
- Triggering `brr run` for one-shot tasks.

What brnrd *adds* (its own work, separate codebase):
- LLM brain ("which brrs do I poke for this user request?").
- Tools (web search, github API, calendar, etc.).
- Channel for the user (telegram/slack/web UI).
- Memory across user conversations (its own kb/).
- Scheduling / prioritisation across the fleet.

It runs *somewhere persistent* (cloud VM, home server, hosted service).
It is not a per-machine local process.

### What this implies for brr (groundwork now)

Three small things, none of which are urgent but all of which are cheap:

1. **`brr status --json`** — machine-readable status. Existing `brr status`
   stays human-formatted; `--json` flag adds the structured form.
2. **Self-maintaining registry** (see #5).
3. **A "remote gate" stub idea** — document that anything writing to
   `.brr/inbox/` over any transport is a valid gate. brnrd will write a
   gate (an HTTP/SSH/whatever bridge) for itself. brr doesn't need to
   ship that gate; the protocol already supports it.

That's all the brr-side groundwork brnrd needs. Defer the rest.

### Surface-vs-substance with envs

The user noticed brnrd-deployment ergonomics overlap with brr-env
ergonomics (containers, ssh, kube). Genuine surface overlap — both
need "ship a Python program to run somewhere persistent" — but the
substance is different:

- brr envs answer "where does *one task* execute?"
- brnrd deployment answers "where does *the manager itself* live?"

Different lifetimes (per-task vs. always-on), different concurrency
needs (env = N parallel jobs; brnrd = 1 long-running process), different
trust boundaries (env runs untrusted-ish agent code; brnrd is the
operator's most trusted process).

Conclusion: **don't collapse them.** Reuse implementation tricks
(Dockerfile patterns, ssh provisioning) but keep the abstractions
separate.

---

## 7. Cross-platform supervisor for the brr daemon

systemd is great where it exists. To go cross-platform without rewriting:

| Platform     | Supervisor                                    | Notes                              |
|--------------|-----------------------------------------------|------------------------------------|
| Linux        | systemd unit                                  | preferred; brr ships a template    |
| macOS        | launchd plist                                 | brr ships a template               |
| Windows      | Task Scheduler / NSSM                         | community-supported (low priority) |
| Anywhere     | docker container (`brr/daemon:latest`)        | uniform; the productisation lever  |
| Anywhere     | tmux/screen (manual)                          | what most users do today           |

`brr install-service` (future verb) generates the right thing for the
detected platform. Probably stays out of v1.

For productisation: a **hosted brnrd** is the obvious commercial play
("we run the operator agent for you, you connect your repos"). The
daemons themselves probably stay self-hosted because they need git
credentials. brnrd-as-a-service + brr-on-your-box is the natural
split.

---

## 8. Decentralised merge — concrete examples

User wanted specific use cases to ground the "do we need a coordinator?"
question. Worked through:

| Task type        | branch    | Output                              | Conflict risk      | Coordinator needed? |
|------------------|-----------|--------------------------------------|--------------------|---------------------|
| Q&A              | `current` | response file only                   | none               | no                  |
| Research note    | `auto`    | one new file in `kb/`                | very low (new file)| no — ff-merge       |
| Bug fix          | `auto`    | one or two file edits                | low to medium      | no — ff-merge or `conflict` status |
| kb maintenance   | `auto`    | edits to `kb/index.md`, `kb/log.md`  | high if parallel   | mutex on `auto` finalize |
| Feature work     | named     | full PR                              | n/a (human merges) | no                  |
| Refactor         | `auto`    | many file edits                      | high               | likely `conflict`; human handles |

The conclusion in `design-env-interface.md` stands: there is **no
"coordinator" component**, only a `git merge --ff-only` attempt with
`conflict` fallback, plus a future host-HEAD mutex when N>1 workers
land.

The CRDT-flavoured framing is real: branches in git already have
well-defined merge semantics; `conflict` is the brr equivalent of
"the CRDT can't auto-resolve, escalate to a human." This naturally
maps to a distributed-actors model where each task is an actor
producing a branch, and the merge step is a single state-machine
transition that either succeeds or yields to manual intervention.

---

## 9. Re-promotion guide

When picking any of these up:

- **#1 (drop eject)** → **promoted** — see `plan-overlays.md` → "`brr eject` retirement".
- **#2 (single overlay file)** → **promoted** — see `plan-overlays.md` → "Research gate (blocking)"; blocks the overlays implementation until resolved.
- **#5 (registry)** → trivial PR; do it now if convenient since brnrd will need it.
- **#6 (brnrd)** → separate project; start when the env work is shipped and the overlay is proven.
- **#7 (cross-platform supervisor)** → defer until a non-Linux user complains.
- **#8 (decentralised merge)** → already absorbed into `design-env-interface.md`; this section is just provenance.
- **#10 (plugin candidates)** → first dogfood plugin (Daytona) after the env PR merges; each other candidate promotes when demand appears.

Until one of these is promoted, **no code changes here**. The point of
this page is to keep the side-channel from getting lost while the env
work ships.

---

## 10. Plugin candidates for `brr.envs`

Once the env PR merges, `brr.envs` is an open plugin point. Everything
below is a candidate for a **third-party plugin package**, not a
built-in. Keeping brr core zero-dep and self-hosted-first means these
ship as separate pip packages (or script envs), never in the main
repo.

### Daytona — the planned dogfood target

Daytona is a natural first real-world plugin because it exercises the
remote-env shape end-to-end (workspace lifecycle, remote filesystem,
CLI-driven control plane). If the plugin mechanism can host Daytona
cleanly, we designed it right.

- **Why a plugin, not a built-in.** Daytona is SaaS-adjacent — making it
  core would pull brr toward "we integrate with services," which cuts
  against the self-hosted ideology. Ship it as `brr-env-daytona` in its
  own repo; document it in the brr docs as an example plugin.
- **Sketch — roughly the `ssh` env shape, backed by the Daytona CLI**:
  - `validate` — `daytona` CLI on PATH, auth configured.
  - `prepare` — `daytona create --image=<img> --from-repo=<url>` (or
    `--devcontainer` when the repo has one); stash the workspace id in
    `ctx.env_state`.
  - `invoke` — `daytona exec <ws-id> -- <runner-cmd>`; stdout/stderr
    streamed back to the host; trace is host-side as usual.
  - `finalize` — `git bundle` the branch inside the workspace, fetch
    back locally; `scp` or `daytona cp` the response file; delete the
    workspace only when `status=done` and `debug=False` (honours the
    env salvage rule).
- **Response-path split.** `response_path_env` lives inside the Daytona
  workspace; `response_path_host` stays at `.brr/responses/<id>.md`.
  The finalize transfer closes the gap — exactly the `ssh` pattern.
- **Open questions for the plugin.** Auth model (personal token vs.
  org-level?); image choice ergonomics (default to the repo's
  devcontainer when one exists?); cost visibility (per-workspace
  pricing should surface in `brr inspect`).

### Neighbouring candidates

Each of these tests a slightly different slice of the plugin surface.
Kept at one-line notes until someone actually wants to build one.

- **E2B** — sandbox-as-a-service with a Python SDK; tests whether a
  plugin can stay entirely API-driven with no CLI dependency.
- **Modal** — function-style ephemeral compute; tests whether the
  prepare/invoke/finalize rhythm survives a non-workspace-shaped
  backend.
- **Gitpod** — devcontainer-native remote env; overlaps with the
  built-in `devcontainer` but remote; tests the "remote devcontainer"
  shape.
- **GitHub Codespaces** — similar to Gitpod; proves the plugin story on
  a major GitHub-integrated surface; `gh codespace ssh` makes the
  invoke step trivial.
- **Fly Machines** — cheap, global, per-task VM; tests whether a plugin
  can take over scheduling without brr caring.
- **Runpod** — GPU-first sandbox; tests the "GPU box on demand" use
  case that `ssh` currently covers only if you already have a box.

### Rule of thumb

**Built-in** means "works with zero extra install, for the common
case." **Plugin** means "works when the user opts in and installs
something." Daytona, E2B, Modal, Gitpod, Codespaces, Fly, Runpod all
fail the first test: they each require an account, a CLI, or an SDK
install. They belong in plugins, not core.
