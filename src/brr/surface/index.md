# Release-push — live surface

_Last touched: run-260724-0157-7wd0 (2026-07-24 ~01:58Z). Sticky state a wake
should start from, not reconstruct. Prune lines as they resolve._

## ⚠ Codex pool is exhausted until 2026-07-28 — dispatch claude workers only

Both codex workers in the 01:32 dispatch cycle (S2, S3) died at 01:21:30 before
executing: `You've hit your usage limit … try again at Jul 28th`, plus a
skills-dir permission error. Until Jul 28, **any `spawn:` to a codex profile
(`codex`, `codex-mini`, `codex-terra`, `codex-full`, `codex-gpt-5.*`) will die
identically** and generate a misleading `contract-mismatch` completion event.

- The catalog still lists all seven codex profiles as `available: true` with
  `quota_source: codex-local` — the pool's *name*, never its *level*. A chooser
  cannot see the exhaustion. This is #632 (per-Shell quota level in the catalog).
- #631 (credits-as-quota, merged `39e1d46c`) makes the number *exist*; it does
  not yet reach the block the resident chooses from.
- **Until #632 lands: S4-next-tick and every dispatch dispatches to `claude`.**

## Ranked release work (from #413 §7)

- **S4 next** — shipped correctness bug, not hardening: `brnrd init` writes
  `environment`/`docker.image` via repo-domain `write_config` while
  `is_security_key` classifies both as security keys → fresh Docker install has
  a dead choice, env falls back to `worktree`, `untrusted` → refuse. Everything
  after S4 depends on `docker.image` being readable.
  - **Dedup before spec:** `brr/init-*` branches exist locally
    (`init-wake-spec`, `init-playbook-is-live`, `init-wake-impl`,
    `init-wake-runner-guidance`) + `origin/brr/init-wake-spec`,
    `origin/brr/init-playbook-is-live`. Confirm none already cover S4 before
    writing a spec (the #294 / stale-open check).

## Maintainer-owned, waiting on the human

- **#636** OPEN — prompt-contract: `gh api user -q .login` identity probe
  hard-fails (`403`) on the GitHub-App happy path; carries the codex rule.
- **#630** OPEN (draft) — meter-not-watchdog.
- **#627** OPEN — seed-guard-fires-for-nothing.
- **Host chore:** `~/.codex/skills/.system` is `root:root` (a May-6 `sudo npm
  -g`) → every codex start logs `Permission denied (os error 13)`. Non-fatal but
  it leads the failure text and buries the real cause. `sudo rm -rf
  ~/.codex/skills/.system` next time at the machine.

## Open filed fixes tracking the codex-blindness family

- #631 credits-as-quota — **merged** `39e1d46c`.
- #632 per-Shell quota level in the injected catalog — open.
- #633 `contract-mismatch` should require evidence a worker actually ran —
  open. See the event-layer recurrence below.

## Known noise loop (candidate for a fix)

A doomed dispatch to an exhausted pool produces a `contract-mismatch` completion
event per dead worker, and those events stay `pending` on
`schedule:release-push-dispatch-tick` even after a sibling run published+merged
the same `spec_branch`. Result: superseded spawns wake a strong core to reap
zero work. Reaping-side gap, not yet filed; #633 covers only the label.
