# GitHub

way: command `gh`
key: the forge token, fetched by the stand-in at each call; the resident never holds it
unlisted: do

| row   | what                                                   | how                                     |
|-------|--------------------------------------------------------|-----------------------------------------|
| look  | view, list, diff, checks, status, api reads            | passes                                  |
| do    | open a PR, comment, edit, close, merge on green        | passes, recorded as before              |
| ask   | merge when the checks are not green · create or delete a release · change a repository's visibility · set or delete a secret · add or remove a collaborator · start a workflow | his word naming the PR, the tag, the repository, the secret, the person or the workflow |
| never | delete a repository · print the forge token            | refused: a person does it on github.com |

Whatever the small print does not match is `do` (the `unlisted:` line): GitHub's other acts are reversible or seen in review. The loom puts a command `gh` in front of the real one, made from this page: a `never` call stops before the key is fetched, an `ask` call goes to `brnrd runtime guard` for the owner's letter, every other call goes through.

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
ask forge-delete  gh release delete  {tag}
  skip: -R --repo
  target: {tag}
  why: a release others may have fetched is gone, and its tag with --cleanup-tag
ask forge-delete  gh api  -X|--method=delete  @repos/*/*/releases/{release}
  target: {release}
  why: a release others may have fetched is gone

never  gh auth token
  say: brnrd does not hand out the forge token; its stand-in fetches the key at each call
never  gh auth status  +-t|--show-token
  say: brnrd does not hand out the forge token; its stand-in fetches the key at each call

ask visibility  gh repo edit  {repo}  +--visibility
  skip: -d --description -h --homepage --default-branch --add-topic --remove-topic --visibility
  default repo: gh repo view --json nameWithOwner -q .nameWithOwner
  target: {repo}
  why: it changes who can see the repository
ask visibility  gh api  -f|-F|--field|--raw-field=visibility=*  @repos/{repo=*/*}
  target: {repo}
  why: it changes who can see the repository
ask visibility  gh api  -f|-F|--field|--raw-field=private=*  @repos/{repo=*/*}
  target: {repo}
  why: it changes who can see the repository
ask visibility  gh api  -X|--method=patch  +--input  @repos/{repo=*/*}
  target: {repo}
  why: its body is unread, and may change who can see the repository

ask secret  gh secret set  +-f|--env-file  -f|--env-file={file}
  target: {file}
  why: each secret in the file is what the workflows act with
ask secret  gh secret set|delete|remove  {name}
  skip: -R --repo -e --env -o --org -a --app -b --body -f --env-file -r --repos -v --visibility
  target: {name}
  why: a secret is what the workflows act with
ask secret  gh api  -X|--method=[pd]*  @{scope}/secrets/{name}
  target: {name}
  why: a secret is what the workflows act with

ask access  gh api  -X|--method=[pd]*  @repos/*/*/collaborators/{user}
  target: {user}
  why: it changes who can reach the repository

ask run  gh workflow run  {workflow}
  skip: -R --repo -r --ref -f --raw-field -F --field
  target: {workflow}
  why: a workflow acts with the repository's secrets
ask run  gh api  @repos/*/*/actions/workflows/{workflow}/dispatches
  target: {workflow}
  why: a workflow acts with the repository's secrets
ask run  gh api  @repos/{repo=*/*}/dispatches
  target: {repo}
  why: a repository_dispatch starts its workflows, which act with its secrets

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
e.g. ask    gh release delete v0.9.1 --yes --cleanup-tag
e.g. ask    gh api -X DELETE repos/o/r/releases/123
e.g. never  gh auth token
e.g. never  gh auth status --show-token
e.g. never  gh auth status -t
e.g. look   gh auth status
e.g. ask    gh repo edit --visibility public --accept-visibility-change-consequences
e.g. ask    gh repo edit o/r --description "x y" --visibility private
e.g. do     gh repo edit --description "x y"
e.g. ask    gh api -X PATCH repos/o/r -f visibility=public
e.g. ask    gh api -X PATCH repos/o/r -F private=false
e.g. ask    gh api -X PATCH repos/o/r --input body.json
e.g. do     gh api -X PATCH repos/o/r/issues/1 --input body.json
e.g. ask    gh secret set TOKEN --body x
e.g. ask    gh secret set -f .env
e.g. ask    gh secret delete TOKEN -R o/r
e.g. look   gh secret list
e.g. ask    gh api -X PUT repos/o/r/actions/secrets/TOKEN -f encrypted_value=x -f key_id=1
e.g. look   gh api repos/o/r/actions/secrets/TOKEN
e.g. ask    gh api -X PUT repos/o/r/collaborators/someone
e.g. ask    gh api --method DELETE repos/o/r/collaborators/someone
e.g. look   gh api repos/o/r/collaborators
e.g. ask    gh workflow run deploy.yml --ref main -f env=prod
e.g. ask    gh api -X POST repos/o/r/actions/workflows/deploy.yml/dispatches -f ref=main
e.g. ask    gh api -X POST repos/o/r/dispatches -f event_type=deploy
e.g. look   gh workflow list
e.g. look   gh pr view 2610
e.g. look   gh api repos/o/r
e.g. do     gh api repos/o/r/issues -f title=x
e.g. do     gh pr create --fill
```
