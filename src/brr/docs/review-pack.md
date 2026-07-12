# Review pack — publishing a diffense pack as a PR

This is the *inspected* half of the diffense review-pack flow. The wake
bundle injects a compact review-pack block (when diffense emit is on)
that tells you to emit, shape, and `--check` a pack; this doc holds the
heavier **publish** procedure so it is summoned, not paid for on every
diffense wake. Read it when you have a checked pack and the branch is
already getting a PR that should carry the diffense review surface.

## When to publish

Publish from a pack only when this run produced a **review-worthy
committed change** and the branch should be opened or refreshed as a PR.
A chat-only reply, a read-only run, or a one-line trivial fix is not
review-worthy — skip the pack and the PR ceremony. An honest absence
beats a hollow pack.

`diffense.emit_pack` defaults off. Turn it on per repo when the review
surface is worth the prompt and pack work. The forge PR send itself is
not diffense-owned: `gate: forge` is the explicit PR handoff for any
pushed branch, and a checked pack is one high-context way to generate
its title and body.

## Procedure

1. **Validate first.** `brnrd review --check <Review pack path>` must pass
   — a dead locator, a dangling card edge, or a missing axis means the
   pack is not done. Fix every error it reports.

2. **Project the PR body and title from the pack.**

   ```
   brnrd review <Review pack path> --pr-body --relay
   brnrd review <Review pack path> --pr-title --fallback-title <branch>
   ```

   `--relay` makes a **rich review link**: it first writes the pack JSON
   to a secret gist owned by the user's GitHub account and links brnrd's
   `/r?pack=<raw gist url>` renderer shell. If gist publication is
   unavailable, it falls back to the transient brnrd RAM relay.

3. **Open or refresh the PR via the forge gate.** Write a `gate: forge`
   outbox file whose frontmatter names `head`, `base`, and `title`; the
   body is the projected PR body. The GitHub gate opens or refreshes the
   PR idempotently for that head branch.

The pack you emit and project *is* the PR a reviewer reads, so the
quality bar is the pack's, not the diff's.

## See also

- `kb/design-diffense.md` — the card model and the six clamps.
- `kb/diffense-prototype-pr64-pack.json` — a worked pack to shape after.
- `brnrd docs portals` — the outbox / `gate:` control-file protocol the
  publish step rides on.
