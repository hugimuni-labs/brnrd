# GitHub

way: command `gh`
key: the forge token, fetched by the stand-in at each call; the resident never holds it

| row   | what                                                   | how                                     |
|-------|--------------------------------------------------------|-----------------------------------------|
| look  | view, list, diff, checks, status, api reads            | passes                                  |
| do    | open a PR, comment, edit, close, merge on green        | passes, recorded as before              |
| ask   | merge when the checks are not green · create a release | his word naming the PR or the tag       |
| never | delete a repository                                    | refused: a person does it on github.com |

Whatever the small print does not match is `do`. The loom puts a command `gh` in front of the real one, made from this page: a `never` call stops before the key is fetched, an `ask` call goes to `brnrd runtime guard` for the owner's letter, every other call goes through.

## Small print

What the stand-in matches, one rule per line with its details indented under it. Each `e.g.` line is a test: the loom checks at load that it lands in the row it names, and that every rule decides at least one of them.

```limb
never  gh repo delete
  say: brnrd refuses to delete a repository; a person deletes it on github.com
never  gh api  -X|--method=delete  @repos/*/*
  say: brnrd refuses to delete a repository; a person deletes it on github.com

ask merge-red  gh pr merge  {pr}  -R|--repo={repo}
  skip: -b --body -F --body-file -t --subject -A --author-email --match-head-commit
  take repo from pr: [A-Za-z]+://[^/]+/([^/]+/[^/]+)/pull/
  take pr: (?:.*/pull/)?([^/]*)
  default pr: gh pr view [-R {repo}] --json number -q .number
  unless: gh pr checks {pr} [-R {repo}]
  target: #{pr}
  why: its checks are not all green
ask merge-red  gh api  @repos/{repo}/pulls/{pr}/merge
  unless: gh pr checks {pr} [-R {repo}]
  target: #{pr}
  why: its checks are not all green
ask publish  gh release create  {tag}
  skip: -n --notes -F --notes-file -t --title --target --discussion-category --notes-start-tag -R --repo
  target: {tag}
  why: a release is public

look  gh * view|list|diff|checks|status
look  gh api  !-X|--method|-f|--raw-field|-F|--field|--input

e.g. never  gh repo delete o/r --yes
e.g. never  gh api -X DELETE repos/o/r
e.g. never  gh api --method delete /repos/o/r/
e.g. never  gh api repos/{owner}/{repo} -XDELETE
e.g. never  gh api --method=DELETE repos/o/r?x=1
e.g. never  gh api -X DELETE https://api.github.com/repos/o/r
e.g. do     gh api -X DELETE repos/o/r/git/refs/heads/x
e.g. ask    gh pr merge 2610 --squash
e.g. ask    gh pr merge -R o/r --subject "x y" 2610
e.g. ask    gh pr merge https://github.com/o/r/pull/2610
e.g. ask    gh pr merge https://github.com/o/other/pull/5 --squash
e.g. ask    gh pr merge --squash
e.g. ask    gh api -X PUT repos/o/r/pulls/2610/merge
e.g. ask    gh api -X PUT https://api.github.com/repos/o/r/pulls/2610/merge
e.g. ask    gh release create -t Title v0.9.1
e.g. ask    gh release create --notes-file notes.md --target main v0.9.1
e.g. look   gh pr view 2610
e.g. look   gh api repos/o/r
e.g. do     gh api repos/o/r/issues -f title=x
e.g. do     gh pr create --fill
```
