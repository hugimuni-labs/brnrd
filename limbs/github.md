# GitHub

A note on how a resident reaches GitHub. It judges nothing: the judgement is the mark on each call (threads/judged-acts), and an act that cannot be undone cites his letter.

**Way in.** The command `gh`. The loom puts a stand-in in front of the real one. It fetches the forge token at each call (`forge_token` in the loom's config), so the key never sits in the room. It records a merge, close, comment or release as a passage through the forge door, with the mark. On a body with no wall, it asks a mark of every call.

**What tends to be final** (nothing gets it back, so it cites his letter):
- deleting a repository, a release, or a tag others may have fetched;
- a release, which is public the moment it exists;
- changing who can see a repository, or who can reach it;
- setting or deleting a secret the workflows act with;
- starting a workflow that acts with the repository's secrets;
- printing the forge token anywhere it is kept.

**What tends to be a change** (it has a way back):
- opening, editing, commenting on or closing a PR (reopen, edit back);
- merging a PR whose checks are green (revert). A merge whose checks are not green is the body's call to mark, and usually final for the person reading main.

**Looks.** view, list, diff, checks, status, and `gh api` GETs.

**What blocked before.**
- `gh auth token` inside the stand-in prints the room's forge key: the stand-in fetches it at each call and exports it as `GH_TOKEN` before the real gh runs. The key is what a body acts with, so it stays in the room; to see which identity acts, run `gh auth status`.
- A PR's checks lag a push by a minute or two: `gh pr checks` reports "no checks" before the run starts.
