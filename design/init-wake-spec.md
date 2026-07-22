# Init as a wake — build spec (Layer 3)

Status: **spec, no build** — #507's own sequencing ("spec before build").
Scope: Layer 3 of the design in kb `design-init-as-a-wake.md`. Layers 0–2
(artifact split, versioned blocks, shell bridges) and D2(a) (knowledge shape
asked before the contract is authored) are **already landed** in this tree —
`constitution.py`, `templates/constitution.md`, and the current `adopt.py`
are the shipped form of that design. This spec covers only what remains:
replacing the mechanical interview and the headless one-shot setup call with
**one wake**, and defining what "portal" and "delivery" mean before any gate
exists.

Companion deliverable: `src/brr/prompts/init-playbook.md` (DRAFT) — the
prompt the init wake receives.

---

## 1. Current behavior (verified against this tree)

### 1.1 CLI entry

- `brnrd init [url] [-i|--interactive]` — parser at `cli.py:67-71`;
  `cmd_init` (`cli.py:480-482`) calls `adopt.init_repo(url,
  interactive=...)`. There is **no `--auto` flag today**; non-interactive is
  the default and `-i` opts *in* to questions. #507 inverts this polarity.

### 1.2 `init_repo` sequence (`adopt.py:112-164`)

1. optional clone (`adopt.py:114-118`)
2. `_ensure_repo` (`adopt.py:337`) — git init fallback
3. `_setup_brr_dir` (`adopt.py:347`) — `.brr/` subdirs (inbox, responses,
   gates, prompts, runs, traces, reviews, worktrees), default config written
   only if absent (`adopt.py:362-377`), `.gitignore` marker
4. `_bootstrap_dominion` (`adopt.py:392`) — best-effort, soft-skip
5. `runner.detect_all_runners` (`runner.py:767`); zero runners ⇒ the runner
   doctor in §2.1. Today's two-line `SystemExit` (`adopt.py:125-129`) is not
   enough: it hard-codes only two shells, cannot distinguish "not installed"
   from "installed but outside this process's PATH", and gives no verification
   ladder.
6. interactive only: `_interactive_configure` (`adopt.py:205`) — runner
   choice + docker-vs-worktree (`_configure_environment`, `adopt.py:223`)
7. `_resolve_knowledge_shape` (`adopt.py:184`) — the landed D2(a): repo-kb
   vs home-kb asked *before* the contract is authored; non-interactive
   default `"repo"`
8. `_run_setup` (`adopt.py:412`) — the piece the wake replaces, see §1.3
9. `constitution.write_bridges` for every *detected* shell
   (`adopt.py:156-159`, `constitution.py:293`, detection map
   `adopt.py:171-181`)
10. `_verify` (`adopt.py:497`) — AGENTS.md structure
    (`_agents_structure_problems`, `adopt.py:482`; required section anchors
    `adopt.py:475-479`), soft kb-file notes, per-shell reachability
    (`constitution.verify_reachability`, `constitution.py:353`)
11. interactive only: `_offer_home_link` (`adopt.py:263`) — skipped
    silently when `gh` absent

The interview machinery is Python: `_timed_input` (`adopt.py:43`,
SIGALRM-timed), `_pick_option` (`adopt.py:69`), `_confirm` (`adopt.py:98`).
This is exactly the "form" #24/#507 want replaced by the entity.

### 1.3 The setup session today is a headless one-shot

`_run_setup` builds `prompts.build_init_prompt` (`prompts.py:1646`) =
`setup.md` + `templates/constitution.md` + a knowledge-shape directive, and
calls `runner.invoke_runner` (`runner.py:1468`) with
`RunnerInvocation(kind="init", label="setup", ...)` (`adopt.py:426-435`).
Properties that matter here:

- **No TTY, no dialogue.** The prompt goes in via argv/stdin; stdout/stderr
  are captured to files (`runner.py:1517-1539`). The model cannot ask the
  user anything.
- **No portals.** No outbox dir, no `inbox.json`, no `portal-state.json`,
  no card, no keepalive — those are all daemon-run constructions
  (`daemon.py:1889-1890`, `2073`, `2116-2117`, `2144-2145`, `2179`,
  `2245`).
- **Validation is post-hoc**: required artifact `AGENTS.md`
  (`adopt.py:432-435`), then the structure gate (`adopt.py:456-465`).
- The commit convention lives *inside the prompt* (`setup.md:52-53`).

### 1.4 Gate issuance today is a separate ceremony

`brnrd gate setup|auth|bind <gate>` (`cli.py:105-124`; `GATES =
("telegram", "slack", "github", "cloud")`, `cli.py:20`; `cmd_setup`
`cli.py:1301` = auth + bind). Each `auth()` is an interactive `input()`
walk: telegram token + `getMe` validation (`gates/telegram.py:268-284`),
paired principal in `bind` (`gates/telegram.py:287-320`), GitHub PAT with
gh-CLI/env pickup (`gates/github/wizard.py:28-58`). State lands in
`.brr/gates/<gate>.json` (`gates/runtime.py:32-33`). **`init_repo` never
mentions gates** — the fragmented `init`→`gate setup`→`up` journey #24
complains about is current behavior.

---

## 2. Target shape

```
brnrd init            # TTY + runner present → mechanical bootstrap, then THE INIT WAKE
brnrd init --auto     # today's non-interactive path, verbatim (substrate retained)
```

- Phase 1 — **mechanical substrate** (unchanged, `--auto` and wake path
  share it): steps 1–5 of §1.2. Idempotent already (`mkdir exist_ok`,
  config only-if-absent), which is what makes resume free (§6).
- Phase 2 — **the wake**: one wake-shaped dispatch replaces steps 6–8 and
  11; brnrd keeps steps 9–10 (bridges + verify) as mechanical post-passes,
  exactly as it keeps them today (`setup.md:49-50` already tells the model
  not to write bridges).
- `--auto` keeps `_resolve_knowledge_shape`'s non-interactive `"repo"`
  default and the headless `_run_setup`. On the wake path both questions
  move into the playbook; `_interactive_configure` and
  `_resolve_knowledge_shape`'s TTY branch become dead code to delete.
- No TTY and no `--auto` ⇒ warn once, fall back to `--auto` behavior
  (CI-safe; never hang on stdin).

### 2.1 Zero runners is an onboarding branch, not an exception

The first wake cannot explain how to install its own medium: when detection
finds no supported Runner there is no model process to invoke. Guidance must
therefore live in the mechanical init layer, before the portal loop, and be
shared with the selected-Runner launch-failure path.

Add a small `runner.diagnose_runners(repo_root)` / adopter renderer that uses
the declared runner catalog rather than a duplicated `(claude, codex)` list.
Its terminal result has three parts:

1. **What brnrd checked.** Name the supported shell commands and say that none
   resolved on this process's `PATH`; print the PATH directories, not the
   user's whole environment. This is an observation, not a claim that the
   tools are absent from the machine.
2. **Two recovery lanes.** "Already installed" points to `command -v <shell>`,
   `<shell> --version`, opening a fresh terminal after an installer changed
   PATH, and `brnrd runners list --all`. "Not installed yet" offers the three
   supported Shells (Claude Code, Codex CLI, Gemini CLI) with one sentence each
   and a canonical official install URL. Keep the URLs and any current install
   commands in one runner-owned help table so init, `runners list --all`, and
   troubleshooting cannot drift independently.
3. **The return path.** Authenticate the chosen CLI directly, verify it can
   start, then rerun `brnrd init`. Preserve the substrate already written;
   rerun is resume, not rollback.

The selected-runner failure path uses the same advice, preceded by the exact
profile/binary attempted and the bounded launch error. If another detected
Runner exists, offer rerunning init and choosing it. Do not recommend `--auto`:
that path also needs a Runner and would only move the same failure behind less
guidance.

The init facts block carries the detection report (available profiles plus
missing shell families) once a wake *can* run. The resident may mention an
optional second Runner when it materially improves resilience, but missing
alternatives never block a healthy first wake.

### 2.2 Why not a daemon boot

Per the design's build note: the wake needs **prompt assembly + portals**,
not the daemon lifecycle. `_run_worker` (`daemon.py:1602`) is ~1400 lines of
event weaving, branch planning, retry, presence, ledger, and delivery
threads — a second lifecycle to maintain for one invocation. The init
process itself plays the daemon's part for exactly one run: it writes the
portal files, drains the outbox to the TTY, and feeds typed replies back as
events. Delivery pre-gate *is* the terminal.

---

## 3. The dispatch — new module `src/brr/init_wake.py`

### 3.1 Event synthesis

Create a real inbox event, not an in-memory task: `protocol.create_event`
(`protocol.py:266-334`) with `source="init"`, `status="pending"`, body = a
short contract line ("Initialize this repository — follow the init
playbook."). A real event file means the whole portal grammar (`event:`
addressing, `inbox.json` pending lists via `_pending_events_for_agent`,
outbox frontmatter routing) works unmodified.

### 3.2 Portal surfaces (what exists pre-gate)

Created by `init_wake` before invoking the runner, mirroring
`daemon.py:1889-1890` / `2116-2117`:

| surface | init-wake meaning |
|---|---|
| `.brr/outbox/<eid>/` | wake→user channel; drained to the TTY by init itself (§3.4) |
| `inbox.json` | pending events (the init contract + typed user replies) |
| `portal-state.json` | reduced capsule: pending events, phase, notices; no quota/presence facets needed v1 |
| `.card` | honored: printed once at close as the run summary; captured to `runs/<id>/body.md` |
| `.keepalive` | honored by the init loop as a timeout extension |
| response file | `protocol.response_path(responses_dir, eid)` (`protocol.py:540`); final stdout printed to TTY at close |

**Extraction task:** `_write_live_inbox` (`daemon.py:3336`) is small and
nearly dependency-free — move it (plus a *reduced* portal-state writer) into
a new `src/brr/portals.py`; the daemon delegates. Do **not** try to extract
`_write_live_portal_state` (`daemon.py:3713`) wholesale — it is deeply
`Run`-coupled; init writes its own thin capsule with the same file name and
top-level keys the wake's discipline expects (`events`, `notices`,
`resources` may read `unimplemented`).

### 3.3 Prompt assembly

New `prompts.build_init_wake_prompt(repo_root, *, event_id, response_path,
outbox_path, runner_name, ...)` — a thin wrapper over
`build_daemon_prompt_with_score` (`prompts.py:1473`) so the boot score,
keyed preamble, and bundle assembly are reused, with:

- **Stage parametrized.** `_build_run_context_bundle` hardcodes
  `"- Stage: brnrd daemon run"` (`prompts.py:2057`). Add `stage: str =
  "brnrd daemon run"`; init passes `"brnrd init wake"`. The stage line is
  what licenses the bundle's init-specific deltas (commit target §5,
  delivery meaning §3.4).
- **Task = the playbook.** `read_prompt("init-playbook.md", repo_root)` +
  the adopter template + a facts block (detected shells, detected runners,
  configured gates via `runtime.configured_gates` (`gates/runtime.py:124`),
  `gh` availability, git remotes). `setup.md` stays as the `--auto` prompt;
  the playbook subsumes its mechanics by reference for the wake path.
- **Resident stack, not worker** (`worker=False`) — first contact is the
  resident (fork F3 if contested). Injected resident blocks that presume a
  connected account must degrade gracefully; the account is typically absent
  at init. Build task: test `build_injected_context` (`prompts.py:959`) and
  the continuity facet against a fresh no-account repo; anything that
  raises is a bug this build fixes.
- **Environment: host.** `envs.HostEnv` (`envs/__init__.py:102`), cwd = the
  user's checkout. No worktree: the repo may have zero commits, and init's
  entire point is to mutate the checkout the user is standing in.

### 3.4 The terminal portal (delivery pre-gate)

The init process runs the runner in a thread (simplified
`_invoke_with_heartbeat`, `daemon.py:3090` — no budget escalation; generous
`timeout_seconds` on the invocation, default ~30m) and services portals on
the main thread:

1. **Drain**: poll `.brr/outbox/<eid>/` (~1s; plus the Tier-2 `.flush`
   handshake if hooks are installed — `hooks.py:55`, `install_hook_config`
   `hooks.py:1167` — making drains boundary-driven instead of poll-only).
   Parse via `protocol.parse_outbox_message` (`protocol.py:72`). Print the
   body to the TTY, move the file under `.processed` — the same
   accepted-file discipline as `_drain_outbox` (`daemon.py:4671`), minus
   gates.
2. **Reply**: after each printed message, offer a `you> ` prompt
   (multi-line, blank line ends; empty = no reply). A reply becomes
   `protocol.create_event(source="init", ...)`; refresh `inbox.json` +
   `portal-state.json`. The wake picks it up by the existing linger
   discipline (poll portal-state with backoff) and answers with `event:`
   frontmatter — nothing new to teach.
3. **Control verbs** (the secrets seam, §4): an outbox file whose
   frontmatter carries `control: gate-setup <name>` (or `control:
   home-link`) is *not* printed as chat — it transfers the TTY to brnrd,
   which runs the existing interactive flow, then posts the outcome back as
   an event.
4. **Interrupt**: SIGINT → kill the runner (registered in `_active_procs`,
   `runner.py:1532`), then §6.

Runner env carries `BRR_PORTAL_STATE` / `BRR_OUTBOX_DIR` exactly as the
daemon does (`daemon.py:2144-2145`, `2179`) via `RunnerInvocation.env`
(`runner.py:356`).

---

## 4. The token/gate walk

The wake **orchestrates and explains**; brnrd **collects secrets**. The
playbook forbids asking the user to paste tokens into chat. For each gate
the user opts into, the wake emits `control: gate-setup telegram` (etc.);
the init process runs the *existing* `auth()`/`bind()` functions verbatim
(`gates/telegram.py:268`/`287`, `gates/github/wizard.py:28`/`90`,
`cli.py:1301` composition) against the real TTY, then posts "telegram
authenticated as @<bot>, paired to <id>" (or the failure) as an event the
wake folds into the conversation.

Why this seam: raw tokens never enter the model transcript or the
`.brr/traces/` capture; the gate modules need **zero changes**; failures
surface to the wake as events it can react to (retry, skip, explain).
Alternatives weighed in F2.

`gate list --json` (`cli.py:121-124`, `cmd_gate_list` `cli.py:1312`) is the
wake's read-side: it can verify configuration without parsing state files.

---

## 5. What "done" writes

- `AGENTS.md` — authored by the wake per the template mechanics
  (`setup.md:21-38` rules, referenced by the playbook); structure-gated by
  `_agents_structure_problems` (`adopt.py:482`) at closeout, same bar as
  today.
- kb seeds + `.gitattributes` union rule — only for the repo-kb shape
  (seeds in `setup.md:57-100`).
- Shell bridges — **brnrd writes them**, post-wake (`adopt.py:156-159`);
  unchanged.
- `.brr/gates/<gate>.json` — via the control-verb walk; each save is
  immediate (`runtime.save_state`, `gates/runtime.py:45`), so partial
  progress survives aborts.
- `.brr/config` overrides — runner/environment/docker choices the interview
  produced; written by brnrd from a structured closeout relic (or by the
  wake via `conf.write_config` semantics — build detail).
- A commit on the **current branch**. This deliberately deviates from the
  daemon-substrate receipts pin ("host env ⇒ move off the default branch"):
  init is the bootstrap exception — the user just asked for these files in
  their checkout. The init bundle's stage text says so explicitly, so the
  wake doesn't fight its training.
- Terminal summary: `_verify` output (structure, reachability, kb notes) +
  configured-gates line + "next: `brnrd up`, then message the bot".

## 6. Failure and resume

- **User aborts (Ctrl-C)**: runner killed; brnrd prints what already
  exists (partial AGENTS.md, saved gate auths, config) and "re-run `brnrd
  init` to continue". Nothing is rolled back — every artifact is
  independently useful and idempotently re-derivable.
- **Resume = re-run.** Phase 1 is already idempotent (§2). The playbook's
  first duty is a **state survey**: existing `AGENTS.md` ⇒ merge path
  (today's `setup.md:35-38` analog); configured gates ⇒ skip/confirm;
  interview only fills gaps. Re-runs converge instead of restarting — no
  resume file, no checkpoint format.
- **Runner failure** (not found between detection and launch, nonzero exit,
  auth, quota): report the selected profile and bounded error, then render the
  shared runner doctor from §2.1. Suggest a detected alternative when one
  exists; otherwise give the install/PATH/auth/verify ladder. Exit 1 with the
  partial substrate intact. `--auto` is not an escape hatch — it needs the same
  Runner.
- **Runner never speaks / silent exit**: the drain loop notices the thread
  ended with no outbox and no response file ⇒ treat as runner failure.

## 7. Build checklist (touch points, by file)

1. `src/brr/cli.py:67-71` — add `--auto`; keep `-i` as a deprecated alias
   for the default; flip `cmd_init` (`cli.py:480-482`) accordingly.
2. `src/brr/adopt.py` — split `init_repo` (`adopt.py:112`) into
   `bootstrap()` (steps 1–5) + the two branches; delete
   `_interactive_configure` / `_configure_environment` /
   `_offer_home_link`'s TTY duties on the wake path (they become playbook
   beats + control verbs); keep them for `--auto` only where they never ran
   anyway (non-interactive skipped them all — so this is pure deletion
   pressure, verify with tests); call the shared zero-runner / launch-failure
   diagnosis before either branch can fail opaque.
3. **New** `src/brr/init_wake.py` — §3: event synthesis, portal surfaces,
   runner thread, terminal drain loop, control-verb dispatch, SIGINT,
   closeout (`_verify` + summary).
4. `src/brr/prompts.py` — `build_init_wake_prompt`; `stage` param through
   `build_daemon_prompt` (`prompts.py:1772`) →
   `_build_run_context_bundle` (`prompts.py:2057`); no-account
   degradation test for the injected blocks.
5. **New** `src/brr/prompts/init-playbook.md` — drafted in this branch;
   maintainer review before it becomes a boot surface.
6. **New** `src/brr/portals.py` — `_write_live_inbox` moved from
   `daemon.py:3336` + thin portal-state writer; daemon delegates.
7. `src/brr/gates/` — no changes (the control-verb seam reuses
   `auth`/`bind`/`setup` as-is).
8. `src/brr/runner.py` — add the catalog-derived runner diagnosis/help data;
   `RunnerInvocation.env`, `timeout_seconds`, and artifact specs otherwise
   suffice.
9. Tests — init-wake loop with a scripted fake runner (writes outbox files,
   reads inbox.json); `--auto` regression pin (byte-identical to today's
   non-interactive init); zero-runner output (catalog-derived shell list,
   installed-but-not-on-PATH lane, install lane, resume command); selected
   runner disappearing before launch; no-account prompt assembly; abort/resume
   convergence.

Non-goals: daemon boot inside init; gate *threads*; cloud onboarding; the
config-file-comment overhaul from #24's tail (separate issue); #551's
repo-birth deed (its ceremony *narration* lands as a playbook beat when it
ships, but its artifacts are its own workstream).

## 8. Forks for the maintainer

**F1 — interview channel.**
(a) *Headless wake + terminal portal loop* (this spec): reuses the portal
grammar the resident already knows, keeps traces/capture/validation, one
implementation for all three shells. Cost: brnrd mediates the chat, so the
UX is line-oriented, not the shell's rich REPL.
(b) *Hand the TTY to the shell's own interactive mode* (e.g. `claude
"<playbook>"` with inherited stdio): richest first-contact UX, zero
mediation code. Cost: no outbox/inbox portals (the choreography the rest of
the product teaches), no stdout capture or trace, per-shell divergence
(codex/gemini interactive flags differ), no artifact validation until exit,
and the secrets seam (§4) has no home — the model would have to take tokens
in chat or shell out to `input()`-based flows it can't reach.
**Recommend (a).** (b) can be a later `--repl` experiment without
unwinding (a).

**F2 — secrets seam.**
(a) *Control-verb TTY handback* (this spec): tokens never transit the
model; gate code unchanged. (b) *Non-interactive auth flags* (`brnrd gate
auth telegram --token …` run by the wake): tokens land in the transcript,
the Bash trace, and `.brr/traces/`. (c) *Keep gates out of the wake* (wake
just points at `brnrd gate setup` afterwards): re-opens exactly the
fragmented journey #24 exists to close.
**Recommend (a).**

**F3 — resident vs worker stack for the init wake.** Resident (`run.md`)
matches the product thesis — the user meets the being they'll work with —
but several resident pins (dominion curation, kb governance, schedule)
barely apply at minute zero, and the account is usually absent. Worker
(`worker.md`) is bounded and cheap but literally opens "you are not a
standing resident" — the wrong first sentence for first contact. Options:
(a) resident stack + init-stage carveouts in the bundle text (this spec);
(b) a third slim `init.md` preamble (more surface to maintain, cleaner
fit). **Recommend (a)** until the playbook stabilizes, then revisit (b).

**F4 — commit target.** (a) Commit on the current (often default) branch
(this spec, §5) — honest bootstrap, zero ceremony. (b) Branch +
merge/PR ceremony — consistent with daemon receipts, but absurd for a repo
that may have no remote and a user standing in the checkout.
**Recommend (a).**

**F5 — where the D2 knowledge-shape question lives on the wake path.**
(a) In the playbook, merged with the home-link/durability question into one
interview beat (this spec) — one coherent "where does memory live" moment;
`_resolve_knowledge_shape` (`adopt.py:184`) remains only for `--auto`.
(b) Keep it in Python before dispatch — preserves the landed D2 ordering
guarantee mechanically, but splits the interview across two voices (form,
then entity), which is the exact seam #507 is removing.
**Recommend (a)**; the ordering guarantee survives because the playbook
authors the contract *after* its own interview by construction.
