# Changelog

What changed for you, per release. This is the short, human version; the full PR
list is on each [GitHub release](https://github.com/hugimuni-labs/brnrd/releases).

## Unreleased

- **The repository history was rewritten on 2026-10-02** to take media out of
  it (fresh clone: ~890 MB → ~60 MB once the `shots` branch moves). If you
  cloned or forked before then, re-clone, or replant your own commits onto the
  new `main` with `git cherry-pick`. Never merge the old `main` back in.
- `.gitconfig` at the repo root is gone (nothing read it). The PyPI description
  now matches the README.

## 0.6.24 — 2026-10-02

- **Mistral Vibe is a third Shell.** It runs daemon strands, delivers native
  boundaries through Vibe hooks, honours an explicit model choice, and stays
  selectable when its quota can't be read.
- **Quota walls recover on their own more often.** Exhausted wakes stop before
  launch instead of failing mid-run, a refill hold is probed every heartbeat,
  and the chosen runner is claimed before a starvation bounce.
- **Chat lands in the right room.** A seat hears chat meant for another repo,
  `brnrd account add` no longer moves your default repo, and scheduled wakes
  read the default from disk.
- **The list of asks.** `brnrd asks` shows what you asked for, ordered by last
  touch; replies mint or bind warp items (`--item`, `--new-item`, `--no-ask`);
  items get their own dashboard page.
- **The action ledger.** `brnrd act attempt|confirm|fail` records world-facing
  acts as rows the model can't backfill.
- Dashboard: the run page follows live work before the mirror catches up;
  connecting a cloud repo honours GitHub App grants and reuses machine pairing.
- Groundwork for a Railway template: the daemon on a small always-on box.
