# Protecting main

A push to `main` is judged by `core/immune` from the incumbent tip. Only
an all-zero old tip (the first push) uses the incoming script. Missing or
empty scripts refuse. Strand branches bypass the immune checks.

## Installing the gate

`install_pre_receive` in `brr.loom.runtime.merge` writes the shim. On a
bare self it lives at `hooks/pre-receive`; on a working self it lives at
`.git/hooks/pre-receive` and sets `receive.denyCurrentBranch=updateInstead`.
An accepted push updates the checked-out self. A dirty worktree still
refuses the push. Replacing a script beside the repo does not change the
committed script that judges the next push.

By hand, install the same shim: extract `git show <old>:core/immune` into
a temporary file, refuse failed extraction or an empty file, then run
`sh <file> <old> <new> <ref>`. On the first push extract from `<new>`.
Do not pipe extraction directly into `sh`: an empty script exits zero.
Deletion uses the incumbent script, which refuses deletion of `main`.
Non-fast-forward updates to `main` also refuse.

On GitHub, copy `ci/immune.yml` to `.github/workflows/immune.yml`, run it
once, and require the `immune` status for `main` through branch protection
or a ruleset. The workflow uses the push's before/after tips or the PR's
base/head, and extracts the incumbent script with the same fail-closed
rule. The workflow itself must be protected: a PR can edit it. Give
strands contents-write tokens without administration rights, so they
cannot lift protection. The seed does not configure forge protections.

## Checks

`core/immune` runs executable files in `core/immune.d/` at both tips, in
byte-sorted name order, even after a failure. Incumbent failures carry an
`old/` prefix. A change that deletes or rewrites a check must still pass
the incumbent checks as well as the incoming checks. The first push runs
only the incoming set. An absent check directory adds no checks.

Keep `10-readme`, `20-labels` and `50-core-notice` executable. Copying the
seed without their executable bits silently drops the checks. A failed
check's stderr is prefixed with its name; a silent failure is still
named. `LOOM_IMMUNE_SH` selects the shell for the shim and checks when set.

`10-readme` validates thread README frontmatter, heading and folder id.
`50-core-notice` never refuses: changes under `core/` print
`core changed: <paths>` for the person's notice.

## Labels and widening

`20-labels` walks every new commit. No `Loom-Label` refuses unless the
committer's email is in the incumbent `core/loom/people-commit` list
(the incoming list on the first push). Empty lists enroll nobody;
comments and blank lines are ignored. A push cannot enroll itself.
`init` enrolls the person's global git email for hand commits and stamps
the seed with its own clean label. Bodies receive the unenrolled
`brnrd-loom <loom@localhost>` author and committer identity.

A labeled commit is checked regardless of enrollment. `taint=1` requires
a `Widening` citing `people/<name>/agreement.md#<clause-id>`, with a
`{#clause-id}` marker at the incoming tip. Every widening citation is
validated, including on clean or unlabeled commits. Checks prove existence,
not the meaning of the clause. Audience must contain `self`. A taint
refusal names the strand.

`send_to_self` stamps each outgoing commit from the strand's fact fold,
overwrites body-written labels and drops body-written widening citations.
Only a widening supplied to the send call is stamped. Shown letters,
the parent at birth, taint facts, URL sources and non-empty jack error
logs feed the fold; leased strand senders contribute their own fold.

The jack observes `WebFetch` and `WebSearch`. Bash does not taint:
tainting every shell call would erase the label's usefulness. The sandbox
owns shell reach. A failed taint write is logged, and a non-empty jack
error log fails closed on the label while the work stays open.

On this host these controls stop accidents and honest injections. A body
running as the person can override its git identity, forge trailers or
rewrite facts and hooks. Enforcement needs protected forge checks and a
sandbox that fences the push credential and those writable surfaces.

## Merge drivers

`merge=union` applies to `memory/scars`, `memory/itches`, `memory/moves`
and `threads/*/notes`. Git provides it without driver configuration.
`threads/*/README.md` uses `loom-readme`: the configured text merge body
returns a README, and invalid output stops the rebase. `memory/stance.md`
uses git's ordinary merge because it has no README frontmatter. The
strand resolves a stop in its room and retries; the third stop aborts
that send. The driver does not judge the prose's meaning.
