## Review pack (diffense)

When this run produced a **review-worthy committed change** — code, kb,
or tests headed for a branch/PR, not a chat-only reply or a one-line
trivial fix — emit a diffense *review pack* as the last step before you
finish, so the change can be reviewed as a graph of cards rather than a
raw diff.

- **Write it to the `Review pack path`** named in the Run Context Bundle
  — an absolute path in the shared runtime dir. Use that exact path: a
  cwd-relative `.brr/diffense/...` would land in the worktree and be torn
  down before the pack can be read. It is a gitignored runtime path —
  don't commit it. (No `Review pack path` in the bundle? Then pack
  emission isn't wanted for this run — skip it.)
- **Shape it** after the worked example at
  `kb/diffense-prototype-pr64-pack.json` and the card model in
  `kb/design-diffense.md`. Every card carries the always-present axes: a
  namespaced `id` (`item:` / `unc:` / `walk:` / `summary:`), a `kind`, an
  `identity`, a one-sentence gloss (`lore.descriptive`), and
  `provenance`. Any card that names code or a kb page also carries a
  resolvable `locator.local` of `path:line`. Emit conditional axes
  (possibility lore, lateral edges, demos, stat blocks) only when they
  are honest and load-bearing.
- **Open with a `summary` card**, then **surface `uncertainty` cards**
  (assumption / concern / dilemma / out-of-scope-flag / follow-up) for
  anything you were genuinely unsure about during the run — they read
  first and are the highest-value part of the review. Ground usage demos
  in real test values, never invented ones.
- **Keep cards sharp** under the six clamps (see `kb/design-diffense.md`):
  skimmable, load-bearing, honest, non-prescriptive, emit-iff-honest.
- **Validate before finishing**: run `brnrd review --check <Review pack
  path>` and fix every error it reports — a dead locator, a dangling card
  edge, a missing axis. A pack that fails `--check` is not done.
- **Publish from the pack** when the branch is already getting a PR and the
  pack should be the review surface: validate, project the PR body/title from
  the checked pack, and open or refresh the PR with a `gate: forge` outbox
  send. The full publish procedure — the `brnrd review --pr-body --relay` /
  `--pr-title` invocations, what `--relay` does (gist + brnrd renderer,
  RAM-relay fallback), and the `gate: forge` frontmatter — lives at
  `brnrd docs review-pack`. Summon it when you have a checked pack; it is
  not re-narrated here every wake.

If the change isn't review-worthy, skip the pack: an honest absence beats
a hollow pack. When you do publish, the pack you emit and project *is* the
PR a reviewer reads.
