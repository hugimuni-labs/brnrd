# Threat model: what an injected instruction can reach

Status: first draft, 2026-10-09. Written because security work was being judged by feel.
Every "built" claim below was read in the code on `main` at `e57abf3b`; paths and names only,
never values. Companion pages: `SECURITY.md` (the operator-facing trust model, which this page
leans on) and `design-the-self-is-a-repository.md` (the designed controls).

**The attack worth modelling** is a prompt injection that quietly *steals a key* or *deletes or
encrypts files* while the run looks healthy. A crashing run is cheap and loud. **Content
detection is not a defence we rely on**: an instruction hidden in a web page, issue or README
cannot be reliably told from a task. So the question for every control is structural: *if the
model fully obeys the attacker, what can it still not do?*

Base fact (`SECURITY.md:12-22`): runners start with approvals bypassed
(`--dangerously-skip-permissions`, `loom/runtime/adapters.py:54`), so a run does anything its
UID can do. The only boundaries are the ones the environment draws around that UID.

## 1. Assets

| Asset | Where it lives (names only) | Worth |
|---|---|---|
| GitHub write access | token dir behind `GH_CONFIG_DIR` (managed pointer, `runner.py:420`), `.brr/credentials/github/token`; or an operator `GH_TOKEN`; the operator's own `gh` keyring | push to branches, open PRs, read private repos |
| Public-account creds | `account/x-brnrd-resident.env` (`x_Access_Token=`), `account/x-browser-profile/` (logged-in browser), Google creds under `account/*.env` | post as us, read mail/calendar |
| SSH keys | `~/.ssh` | push anywhere the operator can, log in to other machines |
| Shell auth | `~/.claude`, `~/.claude.json`, `~/.codex`, `~/.gemini`, model API keys in the daemon env | spend the operator's quota; read the conversation history |
| The self / dominion | the dominion repo and `.brnrd-kb/` (account home), `thread-of-record.md`, pitfalls, identity core | the thing that makes the next wake *me*; a poisoned line here persists |
| The maintainer's files | the host checkout, sibling repos, `~/` | irreplaceable work, other credentials |
| Public accounts | X, GitHub identity `brnrd-bot` | reputation; a post is irreversible |
| Quota and money | provider subscriptions, spawn allowances | burn, or starvation of real work |
| Dashboard mirror | brnrd.dev lanes (`SECURITY.md:141-`) | not an asset, an *exit*: see 3b |

## 2. Entry points for untrusted words

Tier is set at ingress by the gate and read in `trust.py:102` (`resolve_tier`); unknown
ingress fails to `untrusted`. Tier picks the *environment*, not the content's trustworthiness.

| Entry | Who can write it | Reaches the model as | Tier today |
|---|---|---|---|
| Chat letters (Telegram, Slack) | paired user; Slack: anyone in the channel (`SECURITY.md:366`) | the task | owner (Slack: owner by channel membership) |
| GitHub issues / PRs / comments | strangers on a public repo | the task, or text the run reads with `gh` | collaborator or `untrusted` per sender; `untrusted` → solitary |
| Web fetch / search | anyone who can rank a page or buy a result | tool result inside an owner-tier run | **inherits the run's tier**: no re-tiering |
| Files in repos | any contributor, any dependency, any PR branch the run checks out | tool result | same as above |
| Peer brnrds (design §Sharing) | another install's model, reading its own inputs | designed: a letter in a shared repo | not built; designed as tainted by default |
| MCP tools / MCP face | the tool's author; design §MCP face for the inbound face | tool result | inherits the run's tier |
| Our own memory | kb pages, dominion, pitfalls, earlier replies | wake prompt, every boot | fully trusted; **a prior injection that got written down is a standing one** |

The hole this table exposes: tier protects against *who sent the task*. An owner-tier run that
*reads* a hostile page is owner-tier with a hostile page in context. That is the realistic case.

## 3. Adversary goals → paths on today's host

"Today's host" = `environment=host` or `worktree` for the owner's runs (the default), `solitary`
for untrusted ingress. Same UID as the maintainer, full network, full filesystem.

**a. Exfiltrate a key.** Path: hostile page or issue text → run reads a credential file
(`~/.ssh/*`, the `account/*.env` files, the token dir; plain `open()`, as `envoy_x.py:132` does
for the X token, no broker in between) → sends it out by any network call (`curl`, `gh gist`,
a `git push` to a repo the attacker can read, or a DNS lookup). Nothing on the host stops any
step: egress is open, the files are same-UID readable, and the permission prompts are off.
Second route, no network needed: write the value into a card, kb page or run body and the
dashboard mirror ships it to brnrd.dev within ~3 s (`SECURITY.md:141-160`).

**b. Destroy or encrypt files.** Path: same entry → `rm -rf` / `openssl enc` over the host
checkout, sibling repos, `~/`. A `worktree` run sits in `.brr/worktrees/<id>`, but nothing stops
a command from leaving it (`SECURITY.md:68-70`: "not a security boundary"). The `.git` object
store is shared for resident worktree runs (strands get their own clone: `envs/__init__.py:170`, `worktree.py:263`), so a branch can be force-deleted and gc'd. Recovery depends on backups we have
not verified exist.

**c. Post as us.** Path: read the X token file (3a) → call the API directly, or drive
`x-browser-profile/` (a logged-in browser) → irreversible public post. Or `gh` with the managed
token → comment, open PRs, edit issues under `brnrd-bot`. The managed GitHub token is
repo-scoped and 1 h (`SECURITY.md` §Credential scope, `runner.py:441-447`), which bounds *where*, not *what*.

**d. Poison the self.** Path: injected run writes into the dominion, kb, `pitfalls.md` or
`schedule.md` ("always run X", "trust Y") → capture commits it (`SECURITY.md:352-354`: the agent
writes to kb and dominion, which may be pushed) → every later wake reads it as our own memory.
Persistent, survives the original injection, and a `schedule.md` entry also *fires on its own*.
The identity core is guarded by review (`core/` change notices, designed), not by a taint check.

**e. Burn quota.** Path: instruct a run to spawn strands or loop; each strand carries
`spawn.allowance_tokens` and `max_concurrent` is a width gate, so the loss is bounded per
dispatch but not per *injection*; a schedule entry (3d) makes it repeating. Cost, not
catastrophe; the cheapest to tolerate.

## 4. Layers, built vs designed

"Built" = I found the code and read it. "Claimed, not found" = a doc or intuition says so and I
could not locate the enforcement.

| # | Control | What it stops | Status | Evidence |
|---|---|---|---|---|
| 1 | Tiered env by sender | stranger's *task* running with owner authority | **built** | `trust.py:102,122`; untrusted → `solitary` or refuse (default solitary) |
| 2 | `clean_runner_environ` | parent-agent session ids and safe-mode flag leaking in; **not** a secret filter | **built, narrower than the name** | `runner.py:232-244` strips nine `CLAUDE*`/`AI_AGENT` vars; everything else in the daemon env passes through (`runner.py:286-288`) |
| 3 | GitHub fail-closed identity | run acting as the operator's `gh`/git identity when no token is configured | **built** | `runner.py:345-347` null `GH_CONFIG_DIR` + empty credential helper list; applies to *any* run on that branch, not just strands |
| 4 | Managed token pointer | long-lived GitHub secret in the runner env | **built** | `runner.py:420-447`; App token, repo-scoped, 1 h (self-hosted PAT fallback stays broad: `SECURITY.md:372`) |
| 5 | Strand gets its own clone | strand editing the resident's `.git`, stash, index | **built** | `envs/__init__.py:170-215`, `worktree.py:263` |
| 6 | Worktree isolation (resident runs) | edits landing on the maintainer's working tree | **built, not a boundary** | `envs/__init__.py:188`; shares credentials, network, filesystem (`SECURITY.md:68-70`) |
| 7 | `solitary` | exfil to third parties, push from inside, host CLI-state edits, `.ssh` theft | **built; untrusted ingress only** | `envs/__init__.py:1856`, creds `1841-1845` (no `.ssh`), proxy allowlist; model provider still sees everything; repo mount rw (`SECURITY.md:84-89`, #80) |
| 8 | `docker` env | little: forwards `.ssh` rw, `.gitconfig`, tokens | **built, widens exposure** | `envs/__init__.py:606-613` |
| 9 | Hook rails (closeout, rooted-write neutral) | run ending without its obligations; flow mistakes | **built, not a security control** | `hooks.py`; grep for deny/block found closeout guards only, no read/exec guard |
| 10 | Local `immune` pre-receive on the self | a commit that drops `core/immune`, or breaks README/core notice | **built (loom step 4a), host-local** | `loom/runtime/merge.py:35-87`; checks live in `seed/core/immune.d/` (readme, core-notice) only. On the host the strand can edit the hook (design §Merging "how much this enforces") |
| 11 | Taint / audience labels | write of tainted words into the self | **designed only** | design §Labels; grep for `taint` in `src/` finds no label code (only unrelated "uncertainty" matches) |
| 12 | `immune` off-host (protected `main`, required CI, admin-less token) | a subverted strand merging into the self | **designed only** | design §Merging; whether `main` here has branch protection is unverified |
| 13 | Per-room sandbox (bubblewrap/Seatbelt) | file destruction and exfil outside the room | **designed only** | design §Merging last paragraph; `SandboxEnv` (`envs/__init__.py:1563`) is docker `sbx`, not this |
| 14 | Content detection of injections | n/a | **deliberately absent** | not a defence we rely on |
| 15 | Secret redaction on dashboard mirror | secret in published pages | **claimed, not found** as a filter: `SECURITY.md:141-` describes pages mirrored verbatim; `hooks.py:263` `redact_detail` covers hook detail text only |
| 16 | Credential files mode 0600 / broker | same-UID read of X/Google creds | **claimed, not found**: only the operator checklist (`SECURITY.md:356`) asks for 0600 on gate files; `envoy_x.py` reads the env file directly |

Not verified: the mode and ownership of `account/*.env`; whether the host has Time Machine or
other backups; branch protection on `hugimuni-labs/brnrd`; the exact solitary proxy allowlist
at runtime (code reads fine, not driven).

## 5. Ranked gaps (risk closed per unit of work)

1. **Pass an env allowlist, not a denylist, to runners.** Replace `runner.py:286-288`'s "copy
   everything minus nine" with an explicit set (PATH, HOME, locale, the Shell's own keys, the
   managed GitHub pointer). Cost: small (one function, plus the docker/solitary key lists that
   already exist). Closes: any secret that merely sits in the daemon's environment reaching
   every run. Does not close files on disk.
2. **Move public-account creds behind a broker the run cannot read.** The X token, Google creds
   and browser profile are same-UID files today (`envoy_x.py:132`). Have the daemon perform
   post/read on request and keep `account/*.env` at 0600 and out of the runner's reach (a
   different UID or a Seatbelt deny rule). Cost: medium (the X lane is already one module;
   Google is the longer tail). Closes 3c and the key half of 3a for the creds that matter most.
3. **Run owner-tier runs that fetch hostile content in `solitary`, or at least route the
   *fetching* limb there.** Re-tier on the capability, not the sender: a web fetch or a
   stranger's issue body is a taint event. Cost: medium (needs the run to be split, which strands
   already allow: a fetch strand with no GitHub token and provider-only egress returns a
   clean-by-schema summary). Closes the main hole in section 2 and most of 3a/3b for web-borne
   injections.
4. **A per-run filesystem sandbox for host-tier runs.** Seatbelt on macOS, bubblewrap on Linux:
   writes only to the run's worktree and `.brr/`, reads that exclude `~/.ssh` and `account/`.
   Cost: medium-high (profile design, tests per Shell, breakage hunting), but it is the one
   control that closes 3b and gives gap 2 its teeth. Pairs with a verified backup.
5. **Redact before the mirror and before capture commits.** Scan outbound pages for known
   secret shapes and for the literal values we hold (the daemon knows its own tokens) and refuse
   to publish. This is exact-match on our own secrets, so it is not content detection of
   injections. Cost: small-medium. Closes the dashboard-as-exit route in 3a.
6. **Take the self's guard off the host.** Protect `main` of the self repo, require `immune`
   in CI, and give strands a non-admin token (design §Merging). Add the taint check (design
   §Labels) once labels exist; until then add the cheap rule that a run which read an untrusted
   entry may not write to `schedule.md` or `core/`. Cost: low for the protection setting,
   high for labels. Closes 3d and the repeating half of 3e.

Order: 1 and 5 first (hours each, no design risk), then 2, then 3 and 4 together, 6 alongside
the loom work already in flight.

## How to use this page

When a security change is proposed, find its row in section 4 and its goal in section 3. If it
closes no path in section 3 on today's host, say what it does close. If it only helps against a
model that disobeys, it is a different tier of argument. Update the table when a control moves
from "designed" to "built"; the evidence column is the contract.
