# Report: vendor-cloud execution as a brnrd environment kind

Dispatched by run run-260910-1206-buo7, event evt-1789041893960505000-tbo1.
Strand run: run-260910-2151-rd7z · branch `brr/the-hands-that-work-elsewhere`.
Every web claim was fetched **2026-09-10** unless marked otherwise.

## Verdict: **no**

**The deciding fact is that the proposal moves only the hands. The seat
that dispatches the work, brokers the portals and publishes the branch stays
on the user's machine. So when the machine is asleep, nothing dispatches and
nothing collects.** The objection this was meant to answer ("what if my
machine is asleep?") stays exactly as open as it is today. brnrd's own docs
already say so: *"If your laptop sleeps when you close the lid, the daemon
sleeps with it — there is no 'always on' without a machine that stays on."*
(`docs/src/content/docs/guides/vps-install.md`).

The capability itself is real, and more open than expected. Anthropic
explicitly permits exactly brnrd's posture: the **unmodified** `claude`
binary, signed in with the user's own subscription. Cloud sessions draw
that subscription with *"no separate compute charge"*. OpenAI sanctions
scripting its binary (`codex exec` *"reuses saved CLI authentication"*), and
`codex cloud exec` draws the plan's shared pool, at roughly 5× a local
message on the legacy rate table.
Legality is not what kills it. The seam is. A brnrd run is a synchronous
invocation over a **shared filesystem**, with hooks at tool boundaries. A
vendor VM shares no filesystem with the daemon. In the one case that
motivated the idea it cannot reach the daemon over the network either. It
returns its work on vendor terms: GitHub only, vendor-named branches or a
diff. On top of that, each vendor's lane works with that vendor's Shell only.
So "vendor cloud" is a property of the **Runner**, not of the
**environment**. The `environment` axis is Shell-agnostic today, and a
fifth kind would split into two unrelated backends.

This is not a maybe. Two narrower things are true, and neither is this
proposal:

- **The asleep objection is answered by an always-on seat** (the VPS guide
  already exists, at $4–6/month), not by moving the hands.
- **One slice of it can be handled with no brnrd code at all.** A resident
  that knows the machine is about to sleep can hand bounded,
  branch-valued work to a vendor **routine** or cloud session through the
  first-party binary it is already running inside. Routines *"keep working
  when your laptop is closed"*. The next wake collects the branch. That is
  a behaviour of the resident, not a new environment kind. See §5.

## Receipts (driven, not read)

| probe | result |
| --- | --- |
| `claude --help` (2.1.266) | `--cloud [description\|session_id\|url]  Create a cloud session with the given description, or attach to an existing one by session ID or claude.ai/code URL` · `--environment <environment_id>  Create a new cloud session that runs on the given self-hosted environment (ccpool_...)` · `--teleport [session]` · `ultrareview` "cloud-hosted multi-agent code review" |
| `codex cloud --help` (0.153.4) | `[EXPERIMENTAL] Browse tasks from Codex Cloud and apply changes locally` · `exec` "Submit a new Codex Cloud task without launching the TUI" · `status` · `list [--json]` · `diff` · `apply` |
| `codex cloud exec --help` | `Usage: codex cloud exec [OPTIONS] --env <ENV_ID> [QUERY]` · `--branch` "(defaults to current branch)" · `--attempts` best-of-N |
| `codex cloud list` (read-only, the user's existing ChatGPT login) | `No tasks found.`: the lane answers on the credential brnrd already runs codex with |
| Claude Code `RemoteTrigger list` (read-only, in-process subscription OAuth, from inside this very run) | `HTTP 200 {"data":[],"has_more":false}` from `GET /v1/code/triggers`: the routines backend answers on the existing subscription login |

**Not driven, on purpose:** creating a cloud session or task. It writes to
the user's vendor account and draws their plan, and the task forbids
spending. That is the experiment to propose (§6), not one to run.

## 1. Does the capability exist, and may a third party drive it?

### 1a. Anthropic: Claude Code on the web, cloud sessions, routines

| question | answer | source |
| --- | --- | --- |
| surface | *"Claude Code on the web runs tasks on Anthropic-managed cloud infrastructure at claude.ai/code, or on your organization's self-hosted environment when routed there. Sessions persist even if you close your browser."* Research preview: *"for Pro, Max, and Team users, and for Enterprise users with premium seats or Chat + Claude Code seats."* | code.claude.com/docs/en/claude-code-on-the-web |
| programmatic entry | **Documented CLI flag:** `claude --cloud "<task>"`. *"The cloud VM clones your current directory's GitHub remote at your current branch, not your local checkout, so push first."* **Documented follow-up:** `claude -p "your message" --cloud <session-id>` *"queues the message into the session and exits"*, with `--output-format json` giving `{ok, session_id, url}`. Whether *creating* a session with `-p` returns JSON headlessly: **the documentation does not say.** | same page |
| routines (the machine-off lane) | *"Routines execute on Anthropic-managed cloud infrastructure … so they keep working when your laptop is closed."* Triggers: schedule (min. 1 h, or a one-off timestamp), GitHub event, API. **Documented API:** `POST https://api.anthropic.com/v1/claude_code/routines/{id}/fire` with a per-routine bearer token and `anthropic-beta: experimental-cc-routine-2026-04-01`. **But** *"API triggers are added to an existing routine from the web. The CLI cannot currently create or revoke tokens."* Routines push to `claude/`-prefixed branches. | code.claude.com/docs/en/routines |
| undocumented | `/v1/code/triggers`, `/v1/code/webhook-triggers`, `/v1/code/sessions` exist (receipt above) and are what the first-party CLI calls with its in-process OAuth token. **They are not publicly documented**, and brnrd calling them directly would mean handling the user's session token (see ToS). | receipt + absence from docs |
| **may a third party drive it** | **Yes, through the unmodified binary. No, through the endpoints.** *"Anthropic does not permit third-party developers … to route requests through Free, Pro, or Max plan credentials on behalf of their users. Moreover, developers may not collect, store, or intermediate Claude.ai credentials or session tokens."* And then: *"Nor does it prevent an end user from signing in to the unmodified Claude Code binary with their own Claude subscription, including where a platform hosts Claude Code."* Also: *"Advertised usage limits for Pro and Max plans assume ordinary, individual usage of Claude Code and the Agent SDK."* | code.claude.com/docs/en/legal-and-compliance (verified first-hand) |
| auth | claude.ai subscription login only: *"`claude --cloud` and `claude --teleport` require sign-in with a claude.ai account"*. API-key, Bedrock and Vertex users cannot use it. The org policy `allow_remote_sessions` must be on. | claude-code-on-the-web |
| **billing** | **The subscription the user already pays for.** *"Claude Code on the web shares rate limits with all other Claude and Claude Code usage within your account … There is no separate compute charge for the cloud VM."* Routines *"draw down subscription usage the same way interactive sessions do"*, plus *"a daily cap on how many runs can start per account"*. Past the cap there is metered overage, but only if the user turned usage credits on. One-off runs don't count toward the cap. | both pages |

The consumer terms back the binary/endpoint line: *"Except when you are
accessing our Services via an Anthropic API Key or where we otherwise
explicitly permit it, [you may not] access the Services through automated
or non-human means"* (anthropic.com/legal/consumer-terms, via the research
subagent, not re-fetched first-hand). brnrd's whole existing posture already
rests on the "explicitly permit" carve-out on the legal-and-compliance page.
A cloud lane through `claude --cloud` sits on the same carve-out, no wider.

### 1b. OpenAI: Codex cloud

**Unverified, in so many words:** every quote in this table was read by a research subagent, not by me, and the help.openai.com and openai.com/policies pages came through a proxy. Only the `codex` CLI receipts at the top of this report were driven first-hand. Per the dispatcher's steer, I did not chase them further. The verdict does not rest on them.

Sources below were fetched by a research subagent on 2026-09-10.
help.openai.com and openai.com/policies sit behind a Cloudflare bot
challenge and were read through the r.jina.ai proxy. These were **not
re-fetched first-hand**, except the CLI receipts at the top of this report.

| question | answer | source |
| --- | --- | --- |
| surface | *"Codex creates a container and checks out your repo at the selected branch or commit SHA. Codex runs your setup script … When the agent finishes, it shows its answer and a diff of any files it changed. You can open a PR or ask follow-up questions."* Agent-phase internet is *"off by default"*. Options are Off, restricted (allowlist; GET/HEAD/OPTIONS only) or all domains. Secrets are *"removed before the agent phase starts."* | learn.chatgpt.com/docs/environments/cloud-environment · /docs/cloud/internet-access |
| forges | *"Connect GitHub or GitLab (Beta)"*. Nothing else. | learn.chatgpt.com/docs/cloud |
| programmatic entry | **Documented CLI:** `codex cloud exec --env ENV_ID` (*"required"*), plus `status`, `list --json`, `diff`, `apply`. *"Authentication follows the same credentials as the main CLI."* **No way to create an environment from a script**: open issue openai/codex#24777 asks for `codex cloud env …` as a feature. So a user must create the environment on chatgpt.com first. **No documented public API.** The binary calls the undocumented ChatGPT backend: `chatgpt_base_url = "https://chatgpt.com/backend-api/"` (`codex-rs/config/defaults.toml`), `format!("{base_url}/wham/tasks/{id}")` (`codex-rs/cloud-tasks-client/src/http.rs`). | learn.chatgpt.com/docs/developer-commands · github.com/openai/codex source |
| results | A diff always. A PR only on a click. No automatic branch push is documented. `codex cloud apply <id>` lands the diff in a local tree, which is the one lane where brnrd's *own* publish survives, provided the daemon is awake to apply it. | same |
| mid-task steer | Web-UI follow-ups only. A `codex cloud message` subcommand is a feature request in #24777, not shipped in 0.153.4. | same |
| **may a third party drive it** | **Yes through the binary. Undetermined through the endpoints.** OpenAI sanctions headless use of the official binary: *"Non-interactive mode lets you run Codex from scripts (for example, continuous integration (CI) jobs) … `codex exec` reuses saved CLI authentication by default."* (developers.openai.com/codex/noninteractive). The Terms forbid *"Automatically or programmatically extract data or Output"* and *"circumvent any rate limits"*, and say *"You may not share your account credentials"* (openai.com/policies/row-terms-of-use). No page says whether a third party may call `/wham/…` directly with a `codex login` token. **The documentation does not say.** brnrd would not go there. | as cited |
| **billing** | **The same plan pool, but the cloud costs more per unit.** *"Codex, ChatGPT Work, ChatGPT for Excel, and Workspace Agents use a shared allowance and credit pool"* (help.openai.com/en/articles/11369540). The rate card covers *"local tasks, cloud tasks, automations …"* charged *"based on the model used and the actual input, cached input, and output tokens"* (help.openai.com/en/articles/20001106-codex-rate-card). Its legacy table shows a cloud message costing about **5×** a local one for the same model (GPT-5.4: ~34 vs ~7 credits). Plus and Pro can buy credits. | as cited (subagent-read, via proxy) |

**Net for both vendors:** the lane exists. It is reachable **only through
each vendor's own binary**, and on the subscription the user already has.
The endpoints underneath are undocumented, and driving them directly is out
of bounds for Anthropic (explicitly) and unaddressed for OpenAI. So the ToS
question comes out *open, on the same footing brnrd already stands on*.
That is why this report does not rest its "no" on the ToS.

## 2. What would break

brnrd's run assumes four things. Here is each one walked against a vendor VM:

| assumption | where it lives | under a vendor VM | verdict |
| --- | --- | --- | --- |
| **The daemon and the run share a filesystem**: response file, outbox, portal-state | `RunContext.response_path_env` / `outbox_env`, commented *"identical under today's envs (the docker mount keeps `.brr/` the same inode inside and out)"* (`envs/__init__.py:47-53`). Paths reach the runner as `BRR_PORTAL_STATE`, `BRR_OUTBOX_DIR` via `RunnerInvocation.env` (`runner.py:728-732`). | No shared inode. `gate:`, `submit:`, `ask:`, `.card`, `menu.json` and every other outbox verb has nowhere to land. The only shim is a network back-channel to the daemon. That means a publicly reachable daemon (or a brnrd.dev relay) plus a Custom network allowlist in the vendor environment, and it is dead in the exact case that motivated this (the daemon is asleep). | **false** |
| **The return value is stdout** | `runner._write_response_file(invocation.response_path, stdout)` (e.g. `envs/__init__.py`, SandboxEnv invoke) | `claude --cloud` prints a session receipt, not the answer. The answer lives on claude.ai. The documented way to retrieve it is `--teleport`, which pulls the conversation and branch into an interactive local session. A headless "fetch the final message": **the documentation does not say**, and the endpoint that has it (`/v1/code/sessions/{id}/events`) is undocumented. | **false**, or needs an undocumented endpoint |
| **Hooks fire at tool boundaries and reach the daemon** | `hooks.py`: `brnrd hook <phase>` reads the daemon-written `portal-state.json` and drops `.flush` in the outbox. It runs where the runner runs. | Repo-committed hooks *do* run in Anthropic's VM (cloud-environments page), but `brnrd` isn't installed there and the portal files aren't there. The daemon's argv/`--settings`-injected hooks don't travel at all. Steers (`to:`) lose their delivery path. **Partial shim, Anthropic only:** `claude -p "…" --cloud <id>` queues a follow-up into a running session. That is a real `to:` analogue, but one-way and asynchronous, with no injection at a boundary. | **false**, partial shim for steers |
| **A git checkout the daemon branches and pushes** | `WorktreeEnv.prepare` makes a local worktree on `brr/<run-id>`. `daemon.publish` (`daemon.py:579`) pushes `run.meta["publish_branch"]`, set by `WorktreeEnv.finalize` from the local tree, with brnrd's managed forge credential (`RunnerInvocation.publishing_brr_dir`). | Claude clones *the GitHub remote at the current branch, not your local checkout*, so the seed must be pushed first. The VM pushes with **the vendor's** GitHub identity (Claude GitHub App or the user's synced `gh` token), not brnrd's bot. Routines force `claude/…` branch names, so a declared `branch:` contract is unmeetable there. *"repository cloning and pull request creation require GitHub"*: non-GitHub forges get a bundle that *"can't push results back"*. Codex returns a **diff** (`codex cloud apply`), which a still-awake daemon could apply into a local worktree and publish as usual. | **needs a shim** (fetch-and-classify in `finalize`). GitHub-only. The identity changes. |
| session resume / transcript forging | `session_seed_home` → `transcript.mount_claude_session`, used for resource-hold resumes | No local session file exists to forge or resume. | **false** |
| heartbeat, single-flight, stop | `daemon._invoke_with_heartbeat` runs `env_backend.invoke` in a thread and ticks the card | Fits as a poll loop, *if* there is a status call. Codex has `codex cloud status <id>`. Claude has no documented headless status (`/tasks` is interactive). A user's `stop:` would have to become a vendor-side cancel; none is documented for either. | **needs a shim**, half-documented |
| trust routing | `environment` is a security key (`config.py:138` `_SECURITY_EXACT_KEYS`), and `trust.untrusted_env` defaults to `solitary` | A vendor VM holds *"any repository the connecting GitHub account can see, not just the repositories the Claude GitHub App is installed on"*. That is wider than brnrd's per-repo token, so an untrusted event must never route there. | **constraint** |

## 3. Where the seam already is

`src/brr/envs/__init__.py` is a package now, not `envs.py`. **Five** kinds
ship, not four. The registry and the gate are:

```python
_BUILTINS: dict[str, type[EnvBackend]] = {
    "docker": DockerEnv,
    "host": HostEnv,
    "sandbox": SandboxEnv,
    "solitary": SolitaryEnv,
    "worktree": WorktreeEnv,
}

def get_env(name: str) -> EnvBackend:
    ...
        raise UnsupportedEnvironmentError(
            f"environment backend '{env_name}' is not available yet "
```

`EnvBackend` is a `Protocol`: `prepare(...) -> RunContext`,
`invoke(ctx, runner_name, invocation, cfg) -> runner.RunnerResult`
(synchronous), `finalize(ctx, task, runs_dir) -> Run`, and
`session_seed_home(ctx)`.

**It fits the registry but not the contract.** A `cloud` class could satisfy
the signatures: submit in `prepare`/`invoke`, poll in `invoke`, fetch in
`finalize`. The binding contract, though, is not in the signatures. It is in
`RunContext`'s fields, which every downstream consumer (hooks, the portal
broker, control-file capture, the card) assumes are real paths. The closest
precedent, `SandboxEnv`, is the backend *"whose runner never sees the host's
HOME"*. It survives only because *"`sbx create` mounts the run's repo
checkout into the VM at the same absolute path"*. A vendor VM mounts
nothing.

**It also needs reshaping on a second axis.** Every existing kind is
Shell-agnostic: host, worktree, docker, sandbox and solitary run any Shell.
`claude --cloud` exists only for the claude Shell, and `codex cloud exec`
only for the codex Shell, each with its own environment registry, identity
and result shape. A `cloud` env would be two unrelated backends keyed on
`runner_shell`, which is a Runner property wearing an env's name. If this
ever gets built, it belongs in the **runner catalog** (a profile like
`claude-cloud` with `class`/`cost_rank`/quota source, next to `claude-opus`),
dispatched as a fire-and-collect **strand**, never as a seat.

**Against the grain.** Both lanes need a visit to the vendor's web app at
setup. Claude needs the GitHub App or `/web-setup`, plus an environment.
Codex needs an environment id created on the web (`--env <ENV_ID>` is
required). Routines' API tokens are web-only. The produce is then reviewed
on claude.ai or chatgpt.com. brnrd would not be shipping an app, but it
would be routing its user into two apps. That cuts against brnrd being a
colleague you reach through channels you already use.

## 4. Cost

If someone built it anyway, here is who pays:

- **The user's pocket:** the subscription they already hold. Anthropic
  cloud sessions share the plan's rate limits with no compute charge.
  Routines add a daily run cap, with metered overage only if the user has
  switched usage credits on. Codex cloud tasks draw the same plan pool as
  local Codex, but at a higher per-message rate: a cloud strand spends the
  user's week faster than the same strand run locally. Plus and Pro can buy
  credits, which is a metered pocket.
- **The hidden price:** Anthropic's cloud lane and routines are both
  *research preview*, and `codex cloud` is marked `[EXPERIMENTAL]`. The
  routines API ships behind a dated beta header. Anthropic's legal page
  prices subscription limits for *"ordinary, individual usage"*: a fleet
  of cloud strands launched by a daemon is the pattern that sentence is
  written to reserve against.
- **brnrd's pocket:** a second publish lane (vendor-pushed branches, or
  applying a diff), a result-collection shim that today would sit on an
  undocumented endpoint for Claude, a status/cancel shim, and a
  GitHub-only carve-out in a forge-agnostic codebase. All of it is to
  serve "slow machine" and "keep it off my machine". `docker`, `sandbox`
  and `solitary` already serve the second of those locally.

## 5. What actually touches "my machine is asleep"

1. **An always-on seat.** `guides/vps-install.md` already tells the user
   how. This is the real answer, and it is already shipped.
2. **A pre-sleep handoff, as resident behaviour.** When the resident knows
   the machine is about to sleep with bounded work outstanding, it can ask
   the first-party binary it is running inside to create a one-off
   **routine** (`/schedule in 2 hours, …`). One-off runs *"do not count
   against the daily routine run cap"*. The routine runs on Anthropic's
   cloud against the pushed branch and pushes a `claude/…` branch, which
   the next wake collects like any strand's branch. Zero brnrd code, no
   new env kind, and the same ToS footing as today. What it costs: the
   routine is claude.ai's run, not brnrd's (no portals, no card), and it is
   GitHub-only. This is worth a line in the playbook, not a subsystem.

## 6. The smallest experiment that settles what's left

It needs the maintainer's yes, because it writes to his claude.ai / ChatGPT
account and draws his plan (a few thousand tokens):

1. From a pushed scratch branch: `claude -p --cloud "append one line to
   PROBE.md, commit, push" --output-format json`. Settles **(a)** whether
   *creating* a session works headlessly with JSON (undocumented), **(b)**
   which branch name the VM pushes and whether it can honour a declared
   one, **(c)** whether any headless, documented call returns the final
   message, and **(d)** the weighted usage drawn, read from the quota feed
   before and after.
2. `codex cloud exec --env <id> "…"`, then `status` / `diff` / `apply` on
   one trivial task. Settles whether the diff-return lane plus a local
   `apply` gives brnrd a clean fire-and-collect strand. It needs one
   environment id created on chatgpt.com.

A yes on 1(a) and 1(c) would upgrade §3's "runner-catalog strand profile"
from *possible* to *cheap*. It still would not answer the asleep objection,
because only §5 does.
