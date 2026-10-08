# Protecting main

`core/immune` is the self's own check. A push reaches `main` only if this
script, as it exists on the `main` being replaced, says so. Strand branches are
not checked. This file is the part a person does by hand. Nothing here is
applied automatically.

## On a forge

A pull request can edit this workflow file itself, so on GitHub the check is only as strong as where the workflow is read from: make `immune` a required status through a ruleset, and keep strand tokens without admin rights.

1. Copy `ci/immune.yml` to `.github/workflows/immune.yml` in the self (it
   already lives in the seed, under `ci/`, so a self built from the seed
   has the file — it still has to sit where the forge looks).
2. Open the repository's branch protection for `main` and require the
   `immune` status check. Do this after the workflow has run once; a forge
   will not offer a check it has never seen.
3. Give a strand a token that can push and cannot administer the
   repository. A fine-grained personal access token, or a GitHub App
   installation token, with contents write and no administration, cannot
   lift the protection. A strand that holds an admin token can, which is
   why a room never gets one.

The workflow runs `sh core/immune <old> <new>` on pushes to `main`, and the
same script with the pull request's base and head. The first push of a
branch sends an all-zero `<old>`. The script accepts that and runs the
checks against `<new>` alone.

## Where the hook is installed

The loom installs the hook with `install_pre_receive` (see
`brr.loom.runtime.merge`). The hook is a shim: it reads `core/immune` out
of the tip being replaced and runs that. **The incumbent judges the change**:
a push that rewrites `core/immune` to `exit 0`, or deletes a check, is judged
by the rules it is trying to remove. `core/immune` then runs `immune.d/` from
both the old tip (reasons prefixed `old/`) and the new one, so a change must
also pass its own new rules. Only the first push is judged by itself alone. Replacing the file on disk, next to
the repo, does not change what the next push is judged by.

On a bare repo the shim is `hooks/pre-receive`. On a working self — `main`
checked out, which is what a room's `origin` is — the shim is
`.git/hooks/pre-receive`, and the install sets
`receive.denyCurrentBranch` to `updateInstead`. An accepted push then
updates that worktree. A dirty worktree still refuses the push; the
install does not force it.

By hand, the same shape is: a `hooks/pre-receive` that runs

```
git show <new>:core/immune
```

and feeds the result to `sh`, with the hook's `<old> <new> <ref>` line as
its arguments. A deletion of `main` has no new commit, so the shim runs the
script from the tip being deleted. That script refuses the deletion.

## What the checks are

Every executable file in `core/immune.d/` runs, in name order, as
`<check> <old> <new>`. Exit 0 accepts. Exit 1 refuses, and the refusal
lists every failing check, not just the first. A file in that directory
without the executable bit does not run. Copying the seed with a tool that
drops `+x` silently drops the check. `10-readme` (step 2) and
`50-core-notice` (this step) both have to stay executable.

`50-core-notice` never refuses. When the push touches `core/` it prints
one line, `core changed: <paths>`. The loom turns that line into the
person's notice. It is not a block.

Stderr from a failing check is prefixed with the check's file name
(`10-readme: threads/inbox/README.md: why`). A check should print the
reason, not its own name, or the name appears twice.

## What this step does not do

Labels, widening citations, and the README merge driver are step 4b.
Until that driver exists, a README or `memory/stance.md` that both sides
edited stops the rebase on purpose. `merge=union` is already set for
`memory/scars`, `memory/itches`, `memory/moves`, and `threads/*/notes`.
Union is built into git. It needs no driver configuration.
