# Plan: Overlays (Phase 1 of the fleet deck)

Concrete plan for user-level steering overlays: change agent behaviour
across many repos without per-repo edits. Absorbs the personal-workflow
variants idea and the `brr eject` retirement.

Strategic context: see `deck-brr-fleet-steering.md` (Axis 1) and
`notes-pondering-fleet.md` (open pondering).

---

## Status

**Not in flight. Blocked** behind the env PR. See
`design-env-interface.md` for the current active work.

Implementation of this plan must not start until:

1. The env PR is merged.
2. The research gate below is resolved and committed as
   `kb/research-overlay-shape.md`.

---

## Goal

Ship the minimum useful overlay: a mechanism that lets a user say
"here is how I want my agents to behave across all my repos" in one
place, and have every brr-managed repo pick it up on the next agent
run.

Success looks like the user story from the deck:

> Edit a single overlay file (or profile directory). `git push` it.
> On the next agent run in any brr-managed repo, the change is
> visible. No per-repo edits.

---

## Research gate (blocking)

Before implementation starts, we have to pick between two overlay
shapes. Both are credible; they optimise for different things and
result in materially different code.

### Option A — Single overlay file appended to every prompt

```
~/.config/brr/overlay.md
```

One file. Always read, always appended to every prompt the runner
receives. Never replaces a bundled prompt — only augments.

- Pros: one file, one concept, spinal-brain-easy; maps to "my personal
  AGENTS.md"; composable (one rule once, lifted into every invocation);
  minimal lookup logic (no name matching, no precedence across prompts).
- Cons: can't fully replace a bundled prompt (e.g. user hates
  `triage.md` → still needs per-repo override); overlay content is paid
  for on every invocation (prompt-size tax).

### Option B — Multi-file lookup chain

```
~/.config/brr/default/prompts/<name>.md
~/.config/brr/profiles/<name>/prompts/<name>.md
```

Lookup chain `bundled → user-default → profile → per-repo`, one file
resolved per prompt name. `profile=` key in `.brr/config` picks the
active profile.

- Pros: can fully replace any bundled prompt; multiple profiles
  (`work`, `personal`) live side-by-side; only the resolved file is
  read (no per-invocation tax).
- Cons: more code paths; resolution order has to be explained; single
  slot enforcement needs guarding; the "edit once, steer everywhere"
  pitch is slightly diluted because the user has to choose which file
  to edit.

### Deliverable

A single page, `kb/research-overlay-shape.md`, that:

- Walks through 3–5 real user flows (commit-style tweak, kb-format
  tweak, runner-flag tweak, per-profile divergence work-vs-personal,
  prompt replacement).
- Evaluates both options against each.
- Picks one, with justification.
- Notes the fallback escape hatch that survives either way (per-repo
  `.brr/prompts/<name>.md` stays).

Implementation **cannot start** until this lands and is agreed on.

---

## Steps once shape is picked

The outline below is written as if both options survive; the research
gate will trim the list.

### Shared (applies to both options)

1. **XDG paths + `BRR_CONFIG_HOME`.** Resolve `_USER_CFG` once at import:
   `Path(os.environ.get("BRR_CONFIG_HOME", "~/.config/brr")).expanduser()`.
   Cached; no surprise I/O.
2. **Git-backed overlay support.** The overlay dir is allowed to be a
   git clone of a user-owned repo. Adds:
   - `.brr/config` key `overlay_sync=auto|always|never` (default `auto`:
     pull if last sync > N minutes ago).
   - `brr overlay sync` command — one-shot `git -C $_USER_CFG pull`.
   - Read-only if not a git clone; no-op for `overlay_sync` values.
3. **`brr overlay init [--git=<url>]`.** One-command setup:
   - No args → seed `~/.config/brr/` with a default template (single
     file or multi-file, per the gate decision).
   - `--git=<url>` → clone it into `~/.config/brr/` (fail if dir already
     exists and is not empty).
4. **`brr overlay show`.** Print the resolved lookup chain (or the
   resolved single-file overlay path) and whether each layer exists.
   Debug aid; no mutation.

### Option A specific

- Single hook in `runner._build_daemon_prompt` / `_build_run_prompt`:
  append the overlay file contents (if present) between the bundled
  preamble and the task text. No new resolution code; no `profile=`
  key.

### Option B specific

- `profile=<name>` key in `.brr/config`, resolved in
  `runner._read_prompt`.
- New lookup chain exactly as sketched in the deck.
- `brr profile set <name>` writes the key; `brr profile show` reads
  current value + resolved chain.

---

## `brr eject` retirement

`brr eject` was the pre-overlay answer to "how do I edit a bundled
prompt?" With overlays, it's mostly redundant: for Option A the user
never touches bundled prompts, and for Option B `brr overlay init` +
`$EDITOR ~/.config/brr/...` covers the same ground.

Retirement path is staged to keep back-compat:

1. **Ship overlays.** `brr eject` stays as-is, un-deprecated, while
   users try overlays.
2. **Print a deprecation notice.** Next release after overlays ship:
   `brr eject` prints "note: overlays at `~/.config/brr/` are the
   preferred way to customise prompts — see `brr docs customising`."
   Still functions.
3. **Remove one release after that.** Drop `cmd_eject` from
   `src/brr/cli.py`; drop the test; link `brr docs customising` from
   the release notes.

Replacement discoverability:

- `src/brr/docs/customising.md` — new doc, explains the resolution
  chain, shows how to set up overlays, shows how to manually copy a
  bundled prompt with a one-liner (`cp $(python -c 'import brr;
  print(brr.prompts_dir())')/<name>.md ~/.config/brr/...`).
- Update `brr-internals.md` "Override model" section to point at this
  doc and the overlay paths.

---

## Tests

Core (no live network or git remote needed):

- **Resolution chain unit tests.** Set up fixture directories matching
  each layer and assert the right file wins.
- **Back-compat.** With no overlay dir present, every existing test
  still passes (absence of `_USER_CFG` is a no-op).
- **`profile=` guard (Option B).** Setting a profile name that has no
  directory logs a warning and falls back to `default`.
- **`overlay_sync=auto` behaviour.** Stub a git repo at
  `$BRR_CONFIG_HOME`; assert `git pull` is invoked when the recorded
  timestamp is stale, not otherwise. Use a local-path remote so no
  network is required.
- **`brr overlay init` idempotency.** Second invocation doesn't clobber
  existing files; reports what exists.

Integration:

- **End-to-end overlay pickup.** A daemon task with a non-empty
  overlay produces a prompt that reflects the overlay content; assert
  via the trace dir.
- **`brr eject` deprecation banner.** Stage 2 release asserts the
  banner is printed to stderr.

---

## Docs

- `src/brr/docs/customising.md` — new, user-facing.
- `src/brr/docs/brr-internals.md` — update Override model and
  KB/Overlay separation sections.
- `src/brr/docs/execution-map.md` — tiny addition noting that overlay
  content is injected in `runner._build_*_prompt`.

---

## Non-goals

- `brnrd` itself and fleet-wide rollout (`brnrd overlay sync`). Separate
  project; the brr-side hooks here are enough for it to plug in.
- Multi-profile composition / stacking. Option B's single slot is
  already the ceiling; stacking is a v-next-next concern.
- Automatic migration of existing `.brr/prompts/` overrides into
  `~/.config/brr/`. Leaving per-repo overrides as-is is deliberate;
  they remain the "this one repo" escape hatch.
- Windows-specific path quirks beyond what `Path.expanduser()` already
  handles.
- Any overlay-content validation or linting — overlay files are
  user-authored markdown and brr should treat them as opaque strings.

---

## Open questions for the research gate

Not decisions, flags for the research doc to address:

- Does Option A's prompt-size tax matter in practice? Bench against a
  realistic overlay size on Claude/Codex/Gemini prompts.
- Does Option B's `profile=` slot really earn its keep vs. a flat
  single-profile model?
- Do we need both? (Likely not, but worth ruling out explicitly.)
- What's the "reasonable template" for the starter overlay in each
  option — bland enough not to steer, useful enough to show the shape?