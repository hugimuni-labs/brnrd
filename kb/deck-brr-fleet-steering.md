---
marp: true
paginate: true
theme: default
title: brr — fleet & steering
style: |
  section { font-size: 22px; line-height: 1.35; }
  h1 { color: #d14; margin-bottom: 0.2em; }
  h2 { color: #555; font-weight: 500; }
  code { background: #f4f4f4; padding: 1px 4px; border-radius: 3px; font-size: 0.9em; }
  pre code { font-size: 0.78em; }
  table { font-size: 18px; }
  section.lead h1 { font-size: 58px; }
  section.lead h2 { font-size: 28px; }
  section.lead { text-align: center; }
  .small { font-size: 18px; }
---

<!-- _class: lead -->

# brr — fleet & steering

## scaling from 1 brr to N

**overlays** · **brnrd** · **environments**

---

# thesis

> brr wins when **steering is cheap** and the **fleet is visible**.

Today a single brr in a single repo is solid. The next 10× of value is in:

- changing agent behaviour across many repos **without touching each repo**
- seeing / commanding your brrs as a **set**, not a list of tmux panes
- running tasks in **environments beyond a worktree** without asking users to write wrappers

These are three independent axes. Ship them independently.

---

# the three axes, untangled

The idea-page conflates these. Separating them is the whole insight.

|     | Axis              | Question                                          | Shape                 |
|-----|-------------------|---------------------------------------------------|-----------------------|
| A1  | **Overlay**       | how do I change behaviour across many repos?      | lookup chain          |
| A2  | **Fleet**         | how do I see / command all my brrs at once?       | `brnrd` supervisor    |
| A3  | **Environment**   | where does a task actually execute?               | `Env` interface       |

They interact, but none depends on the others for correctness. Pick any subset to ship first.

---

<!-- _class: lead -->

# A1 — Overlays

## thoughtless by default · one file, one line, done

---

# the user journey

> Ana has 20 repos. Ten are work, ten are personal on GitLab.
> She wants her *personal* repos to use a warmer commit-message tone, prose-style kb logs, and auto-bind the `gitlab` gate.

**Today.** Edit `.brr/prompts/agents-template.md` in 10 repos. Reword? Edit 10× again.

**With overlays.**

```
~/.config/brr/profiles/personal/prompts/agents-template.md
~/.config/brr/profiles/personal/config
```

Any repo tagged `profile=personal` picks it up. Edit **once**, ten brrs read it on the next run.

---

# shape: 4-layer lookup chain

```
bundled         →  ~/.config/brr/default/  →  ~/.config/brr/profiles/<name>/  →  .brr/prompts/
(shipped w/pip)    (your machine global)      (your workflow variant)            (this repo)
```

Principles (decisions already taken):

- **single slot.** One `profile=` per repo. No stacking. Debuggable by construction.
- **pull-on-next-run.** Overlay is read live. Edit overlay → next agent run sees it. No copies, no sync.
- **prompts + config defaults.** Docs stay bundled; they describe brr itself.
- **`.brr/prompts/` still wins.** The per-repo escape hatch is preserved.

---

# what belongs in an overlay

Audited by asking: *does this vary per user, not per repo?*

| file / artefact                 | per-user varies | in overlay    |
|---------------------------------|-----------------|---------------|
| `agents-template.md`            | heavily         | **yes**       |
| `kb-index.md` / `kb-log.md`     | yes             | **yes**       |
| `setup.md`                      | yes             | **yes**       |
| `kb-maintenance.md`             | yes             | **yes**       |
| `triage.md`                     | somewhat        | **yes**       |
| `run.md`                        | rarely          | **yes**       |
| `runners.md`                    | yes (PATH/flags)| **yes**       |
| `.brr/config` defaults          | yes             | **yes**       |
| `src/brr/docs/*`                | no              | no            |

**Rule.** If it steers the agent → overlay. If it describes brr itself → stays bundled.

---

# mechanics — what actually changes in code

```python
# src/brr/runner.py — the only resolution site
def _read_prompt(name: str, repo_root: Path | None = None) -> str:
    profile = _profile_for(repo_root)          # from .brr/config
    for root in (
        repo_root / ".brr" / "prompts" if repo_root else None,
        _USER_CFG / "profiles" / profile / "prompts" if profile else None,
        _USER_CFG / "default" / "prompts",
        _BUNDLED_PROMPTS,
    ):
        if root and (root / name).exists():
            return (root / name).read_text(encoding="utf-8")
    return ""
```

```
_USER_CFG = Path(os.environ.get("BRR_CONFIG_HOME", "~/.config/brr")).expanduser()
```

`.brr/config` gains one optional line: `profile=personal`.

**~20 LOC. No new concepts. Back-compat by construction** (absent overlay dirs ⇒ current two-layer behaviour).

---

# overlay CLI (minimal surface)

```
brr eject --global                  # seed ~/.config/brr/default/ from bundled
brr eject --profile=personal        # seed a profile from bundled
brr profile set personal            # write profile= in this repo's .brr/config
brr profile show                    # print resolved lookup chain for this repo
```

Four new sub-verbs on the existing `brr` binary. No new command, no new concept the user has to learn if they don't want overlays.

---

<!-- _class: lead -->

# A2 — brnrd

## the uberbot · one knob for N brrs

---

# why a separate tool

`brr` is **per-repo**. It should never know about other repos — that stays honest.

`brnrd` is **per-user**. It knows about the fleet and nothing else.

```
brr      →  "do the thing in THIS repo"
brnrd    →  "do the thing in some subset of MY repos"
```

Packaging: lives alongside `brr` (`src/brnrd/` in the same repo, separate entry point) or sibling repo. `brnrd` depends on `brr`; never the reverse. The boundary stays clean either way.

---

# what brnrd actually is

Three things in one CLI — each optional.

1. **Registry.** `~/.config/brr/fleet.toml` — flat list of repo paths with tags and profile.
2. **Broadcaster.** `brnrd all [--tag=X | --profile=Y] <cmd>` fans a `brr` subcommand across matching repos.
3. **Supervisor daemon (later).** `brnrd up` — one long-running process that owns N `brr up` subprocesses.

Ship **1 + 2** as v1. Add **3** only when users ask for it. Registry + broadcast are enough to unblock the gitlab story end-to-end.

---

# the registry

```toml
# ~/.config/brr/fleet.toml
default_profile = "default"

[[repo]]
path    = "~/src/work/alpha"
tags    = ["work"]
profile = "work"

[[repo]]
path    = "~/src/personal/blog"
tags    = ["personal", "gitlab"]
profile = "personal"
```

Lifecycle:

- `brnrd adopt ~/src/personal/* --tag=personal --profile=personal`
- `brnrd forget blog`
- `brnrd tag blog --add=public`
- `brnrd ls`

One TOML file is the entire source of truth for "my fleet."

---

# fleet UX — what it feels like

```
$ brnrd ls
path                     daemon   profile     last task   pending
work/alpha               ● up     work        2h ago      0
work/beta                ○ down   work        —           0
personal/blog            ● up     personal    12m ago     1
personal/notes           ● up     personal    —           0

$ brnrd all --profile=personal run "bind gitlab gate"
→ personal/blog          ✓  done in 4.2s
→ personal/notes         ✓  done in 3.1s

$ brnrd all --tag=work up
→ work/alpha             already up (pid 2834)
→ work/beta              started (pid 9091)
```

Selectors: `--tag=`, `--profile=`, `--path=<glob>`, or unset = all.
Commands: any `brr` subcommand. `brnrd` is a fan-out, not a reinvention.

---

# the gitlab scenario end-to-end

```mermaid
flowchart LR
  E["edit<br/>~/.config/brr/profiles/personal/<br/>prompts/agents-template.md"]
  B["brnrd all --profile=personal<br/>run 'refresh AGENTS.md from template'"]
  subgraph Fan["fan-out (parallel brr run)"]
    direction TB
    R1[blog]
    R2[notes]
    R3[sideproj]
  end
  C["git push · telegram ping"]
  E --> B --> Fan --> C
```

One edit. One broadcast. Three repos converge. No per-repo ceremony.

---

# brnrd as supervisor (phase 4)

When the fleet grows past ~5 repos, manually managing `brr up` gets tedious.

```
brnrd up                       ← one long-running process
  ├─ brr up in alpha            (subprocess · restart on crash)
  ├─ brr up in beta
  └─ brr up in blog
```

Adds: systemd-style unit, shared secret cache, unified log stream, health check loop.
Does **not** add: cross-repo task coordination. Each brr stays independent — that's a feature.

**Deferred.** `brnrd ls` + `brnrd all` already cover 80% of the pain.

---

<!-- _class: lead -->

# A3 — Environments

## worktrees are one option · not the only option

---

# why this matters for viability

The first time a prospective user asks *"can I run tasks in a container?"*, your answer shapes adoption.

- "Write your own wrapper" → they leave.
- "`env: docker` is built-in and tested" → they stay.

But you don't need to ship every environment. You need to ship an **interface** that makes adding one cheap — and a couple of credible built-ins.

Environments are a **commercial lever**, not a niche feature. Worth doing once, properly.

---

# the Env interface

The `Task` already carries `env: local | worktree | docker`. Only `local` and `worktree` are implemented, and they're hardcoded inside `daemon._run_worker()`.

Abstract it:

```python
class Env(Protocol):
    name: str

    def prepare(self, task: Task, repo_root: Path) -> RunContext: ...
    def invoke(self, ctx: RunContext, prompt: str) -> RunnerResult: ...
    def finalize(self, ctx: RunContext, task: Task, *, debug: bool) -> None: ...
```

Each environment owns: where the code lives during the run, how the runner is launched, how results come back, how cleanup happens.

Daemon code collapses to: `env = envs[task.env]; env.prepare → env.invoke → env.finalize`.

---

# built-ins and plugins

| env           | prepare                  | invoke                    | finalize          |
|---------------|--------------------------|---------------------------|-------------------|
| `local`       | cwd = repo_root          | subprocess                | nothing           |
| `worktree`    | `git worktree add …`     | subprocess in worktree    | merge + remove    |
| `docker`      | bind-mount + image       | `docker run`              | container rm      |
| `ssh`         | rsync to remote          | `ssh … runner "$prompt"`  | rsync back        |
| `kube`        | `kubectl create job`     | stream logs               | `kubectl delete`  |

Built-ins ship with brr. Third-party envs register via `entry_points = {"brr.envs": [...]}`. Zero runtime deps stays intact because built-ins use only stdlib + the executable on PATH.

---

# what the refactor buys (salvage & unlock)

1. **Merge coordinator has a natural home.** `WorktreeEnv.finalize()`. Not scattered across the daemon.
2. **Concurrency becomes a property of the env, not the daemon.**
   - `docker` runs: trivially parallel (independent containers).
   - `local` runs: mutually exclusive (one cwd).
   - `worktree` runs: serial until a merge coordinator exists.
3. **Drop-in replacements.** Distrust worktrees? `default_env=docker` and move on.
4. **Everything on the unmerged branch is salvageable.** `Task` abstraction, triage, per-task log, trace system, `needs_context` status — all apply verbatim. **Pausing concurrent-worktree costs nothing.**

---

# recommendation on worktrees

You asked me to weigh in. Pointed answer:

- **Keep `env: worktree` as one environment among several.** It's correct for "multiple branches, one box, no container cost."
- **Do not ship the concurrent-worktree pool + merge coordinator in v1.** Half-built; tail is expensive; failure modes are data-corruption-shaped.
- **Make serial the v1 guarantee.** One task at a time, per daemon. Advertised. Boring. Solid.
- **Unlock concurrency through the Env interface.** `env: docker` gives parallelism for free because containers are inherently independent.

This preserves the "future concurrent" story without betting v1 on the hard case.

---

# decisions locked (from the conversation)

| Question                            | Choice                                              |
|-------------------------------------|-----------------------------------------------------|
| Overlay composition                 | single slot (`profile=<name>`)                      |
| Overlay scope                       | prompts + config defaults; **not** docs             |
| Overlay update model                | pull-on-next-run (live read, no copy)               |
| Worktree position                   | one env among several; no concurrent pool in v1     |
| Fleet shape                         | `brnrd` — registry + broadcaster first; daemon later |
| Ownership ideology                  | self-hosted, user-owned `~/.config/brr/`            |

Anything not in this table is still open.

---

# roadmap — ship the smallest useful thing first

**Phase 1 · Overlays** — ~200 LOC, ~1 week

+ `BRR_CONFIG_HOME` + `~/.config/brr/{default,profiles/<name>}/`
+ `profile=` key in `.brr/config`, resolved in `runner._read_prompt`
+ `brr eject --global [--profile=X]`, `brr profile set|show`
+ docs page + tests

**Phase 2 · brnrd registry + broadcast** — ~400 LOC, ~1 week

+ `fleet.toml`, `brnrd ls / adopt / forget / tag / all`
+ Separate entry point, same repo

**Phase 3 · Env interface refactor** — no user-visible change

+ Extract `Env` protocol; reimplement `local` and `worktree` behind it

**Phase 4 · First non-worktree env + optional brnrd up**

+ `env: docker` with a tested image recipe
+ `brnrd up` supervisor if demand is real

---

# the minimum compelling slice

If you want **one week of work that sells the whole thesis**:

1. **Phase 1 shipped** — overlays live under `~/.config/brr/`.
2. **Half of Phase 2** — `brnrd ls`, `brnrd all`.
3. **One demo.** Adopt three repos → edit `~/.config/brr/profiles/personal/prompts/agents-template.md` → broadcast `refresh AGENTS.md from template` → watch three repos converge in the same minute.

That demo **is** the pitch. Everything else compounds on top of it.

---

# still open (worth one more pass)

- Does `brnrd` live in the `brr` repo or its own? *Lean in-repo until proven; separate if it grows past 1k LOC.*
- Are profiles named only, or inferable from tags? *Named in v1; tag-derived profiles are a v-next gimmick.*
- `brnrd all run "<task>"` — direct runner invocation, or synthesised gate event? *Direct (`brr run`) for v1; gate path requires response aggregation.*
- Env interface before or after overlays? *After. Overlays deliver user value; Env is a refactor that pays off only in Phase 4.*
- Does the kb/plan-concurrent-worktrees page get retired or reframed? *Reframe: it becomes "WorktreeEnv.finalize() design" — same problem, clearer scope.*

---

<!-- _class: lead -->

# summary

## three axes · one coherent story

**A1 Overlays** — steering across repos is cheap
**A2 brnrd** — the fleet becomes a first-class object
**A3 Env** — the runtime is pluggable; worktrees demoted to "one of many"

Ship Phase 1 alone and the gitlab scenario already works end-to-end.
