# Report — vendor-cloud execution as a brnrd environment kind

Dispatched by run run-260910-1206-buo7, event evt-1789041893960505000-tbo1.
Strand run: run-260910-2151-rd7z · branch `brr/the-hands-that-work-elsewhere`.
All dates 2026-09-10 unless stated.

## Verdict

_PROVISIONAL — docs/ToS/billing evidence still landing._

Leaning: **no, as an environment kind.** The proposal keeps the seat (the
daemon) local, so when the machine is asleep the thing that would dispatch
to the vendor cloud is asleep too. It does not answer "what if my machine is
asleep?"; brnrd's own docs already concede that: *"If your laptop sleeps
when you close the lid, the daemon sleeps with it — there is no 'always on'
without a machine that stays on."* (`docs/src/content/docs/guides/vps-install.md`).

## Receipts so far (driven, not read)

| probe | result |
| --- | --- |
| `claude --help` (2.1.266) | `--cloud [description\|session_id\|url]  Create a cloud session with the given description, or attach to an existing one by session ID or claude.ai/code URL` · `--environment <environment_id>  Create a new cloud session that runs on the given self-hosted environment (ccpool_...)` · `--teleport [session]` · `--remote-control [name]` · `ultrareview` = "cloud-hosted multi-agent code review" |
| `codex --help` / `codex cloud --help` (0.153.4) | `cloud [EXPERIMENTAL] Browse tasks from Codex Cloud and apply changes locally` · `exec` "Submit a new Codex Cloud task without launching the TUI" · `status` · `list [--json]` · `diff` · `apply` |
| `codex cloud exec --help` | `Usage: codex cloud exec [OPTIONS] --env <ENV_ID> [QUERY]` · `--branch` "defaults to current branch" · `--attempts` best-of-N |
| `codex cloud list` (read-only, existing ChatGPT login) | `No tasks found.` — the lane answers on the user's existing codex credential |
| Claude Code `RemoteTrigger list` (read-only, in-process subscription OAuth) | `HTTP 200 {"data":[],"has_more":false}` — `GET /v1/code/triggers` on claude.ai answers on the user's existing subscription login |

Not driven, on purpose: creating a cloud session/task. It writes to the
user's vendor account and draws their plan; the task forbids spending, and
that is the experiment to propose, not to run.

## 1. Does the capability exist and is it reachable by a third party?

_(pending vendor docs + ToS + billing evidence)_

## 2. What would break

_(drafting)_

## 3. Where the seam already is

`src/brr/envs/__init__.py` (a package now, not `envs.py`). Five kinds ship,
not four: `_BUILTINS = {docker, host, sandbox, solitary, worktree}` and
`get_env()` raises `UnsupportedEnvironmentError` for anything else.
`EnvBackend` is a Protocol with `prepare → RunContext`,
`invoke → runner.RunnerResult` (synchronous), `finalize`, `session_seed_home`.

_(analysis pending)_

## 4. Cost

_(pending)_

## Smallest experiment

_(pending)_
