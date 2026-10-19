# Give Claude Code a self

This unreleased spike installs from its review branch. Use a fresh virtual
environment; the library runs without starting or configuring the daemon:

```sh
pip install 'git+https://github.com/hugimuni-labs/brnrd.git@brr/self-library-spike'
cd your-project
self init --home ~/.local/share/my-agent
claude
```

Once this reaches a release, `pip install brnrd` supplies the same CLI.
`python -m brr.self` is equivalent to `self`.
Python 3.10+ and Claude Code are required. The stdio MCP adapter uses stdlib,
with no model, embedding service or MCP SDK dependency.

The home has `identity.md`, `notebook.md`, `playbook.md`, `pitfalls.md`,
`now.md`, `self-inject`, and `kb/`, `obligations/`, `body/`, `bench/`, `journal/`.
Seeds are copied once: rerunning init preserves your edits. The home may be
shared between projects; choose a separate home when you want separate selves.
Home selection is explicit `--home`, then `BRNRD_SELF_HOME`, then the current
project's `.claude/.self.json`, then `~/.local/share/brnrd/self`.

Init merges these entries into existing configuration:

| File/event | Action |
| --- | --- |
| `.claude/settings.json` SessionStart | `self wake --hook`: fresh wake through `additionalContext` |
| PostToolUse | `self encode --hook`: journal act and reason |
| PreCompact + Stop | `self checkpoint --hook`: preserve authored orientation |
| `CLAUDE.md` | Remove the exact generated wake import from earlier init; SessionStart is the wake |
| `.mcp.json` | Register `self` stdio server: `recall`, `note`, `obligations`, `consolidate`, `proposals` |

Approve project MCP servers when Claude asks. Init leaves other hooks, imports
and servers in place and refuses a conflicting server/home. Its hook commands
use the installing Python's absolute path, so keep that environment available.
To detach, remove the self entries from settings and MCP configuration and
`.claude/.self.json`; your home data remains. Config contains absolute local
paths: decide whether it belongs in your project's Git history.

## The operations

`self wake --situation 'the task' --budget 16384` reuses brnrd's manifest
selector and trigger matcher. Identity is always included. The memory content
budget is divided among the ordered `self-inject` entries, matching pitfalls,
and exact situational knowledge hits. Identity and omission accounting are
additional bytes. Collapse receipts stay visible even at a tiny budget;
this is a content budget, not a total prompt-size guarantee. Claude Code turns
hook context above 10,000 characters into a partial file preview. The adapter
retries the existing selector with smaller budgets until the complete render
fits below that ceiling, and records the reduction in `wake.md`. If identity
and omission accounting alone cannot fit, the hook explicitly tells Claude to
read the full generated file; it does not claim a preview delivered the wake.
Omissions carry counts per directory and pitfall reason, plus at most ten
situational near misses: budget-cut KB hits and omitted pitfalls ranked by
shared trigger words. Missing manifest entries are named too.
The SessionStart payload usually has no task text, so its wake contains the
standing slice and current `now.md`. Call wake with a situation to match
specific pitfalls; automatic per-prompt refresh is outside this spike.

`self encode` consumes a PostToolUse JSON payload on stdin. Daily JSONL rows
contain `{at, act, why}` and session/tool ids when supplied. `why` comes from
the existing Bash `description`/`# why:` extractor and redactor. Missing reasons
are `null`; we do not invent them. Raw tool payloads, outputs and chain of
thought are never retained. The shared redactor masks known credential shapes;
it is not a secret classifier. Authored home notes remain your responsibility.

Keep `now.md` current with the goal, plan, completed steps and open forks.
`self checkpoint` saves that text and the authored obligations into
`journal/checkpoints/*.json` at Stop and before compaction. A checkpoint is a
snapshot, not an inferred summary of the transcript.

MCP `recall(query)` searches **only** this home's `kb/`: it preserves the
existing `knowledge.search` exact substring behavior, then uses SQLite FTS5
for a multi-term miss. That derived index lives in
`$XDG_CACHE_HOME/brnrd/self/` (default `~/.cache/brnrd/self/`), never in the
Markdown vault. Edits and deletions refresh it on demand; deleting the index
is safe. If SQLite lacks FTS5, exact recall still works. No semantic search is
claimed. `note(kind, text)` appends authored text to notebook, playbook, pitfall,
knowledge, obligation or now. `obligations()` returns authored commitment
files and their text, without inferring their states. Notes are not auto-curated.

## Consolidate and curate

`self consolidate --since last` (the default) gathers journal acts/reasons and
checkpoints after the last accepted proposal's input cutoff. An explicit
`--since 2026-10-03T00:00:00Z` selects a timezone-aware ISO boundary. It includes
current authored Markdown, now.md, self-inject and notebook/playbook/pitfall
sizes against the wake budget. The cutoff is captured before the model call;
new boundaries arriving while it runs remain eligible for the next proposal.

The home's `.self.json` configures the cheap limb:

```json
{
  "wake_budget_bytes": 16384,
  "consolidate": {
    "command": "claude -p --model haiku",
    "timeout_seconds": 180
  }
}
```

The default adds switches disabling built-in tools, slash commands and MCP,
and starts a separate session. A custom command is a **trusted local program**,
not an OS sandbox; configure it with the same proposal-only discipline. The
command is split into argv without shell evaluation; optional `{home}` and
`{proposal}` placeholders are substituted within each argument. The prompt goes
on stdin. Return JSON `{"files": {"notebook.md": "full replacement text"},
"rationale": "Commit subject\nReasons"}`. Fenced JSON is accepted too.

Each completed `proposals/<stamp>/` contains `files/` full-text replacements,
`rationale.md`, `input.json` (source texts/hashes, journal, checkpoint and budget
manifest), and `proposal.json`. Invalid responses leave the input for diagnosis
but never become open proposals or replace authored files. Notebook proposals
rewrite current knowledge; they do not append a changelog. A long form can move
to another linked Markdown file. MCP `consolidate(since?)` returns the path and
`proposals()` lists open ones. Model calls have a configurable bounded timeout.

Review the rationale and diff, then decide explicitly:

```sh
self proposals
self curate STAMP --accept notebook.md
self curate OTHER_STAMP --reject --why 'The proposed lesson lacks evidence'
self curate IDENTITY_STAMP --accept identity.md --identity
```

Accept with no file list selects all proposed files; an identity edit always
requires `--identity`. Unselected replacements are discarded by that decision.
Curation checks the original hashes before changing anything and refuses a
stale proposal. It replaces only selected files, records the decision in
`journal/curation.jsonl`, and advances the mark to the **input cutoff** on accept.
Reject records why without advancing the mark. Curate is deliberately absent
from MCP: the owner chooses it through CLI, whether human or agent.

If the home has its own `.git`, curation commits accepted files and the decision
record with the rationale's first line and accepted file list. Unrelated staged
or dirty files are excluded. A rejected decision gets its own commit. Configure
Git author identity beforehand. If a Git hook/commit fails, the decision remains
recorded and the CLI reports the failure; repair the commit explicitly. The
library never pushes. Journal/checkpoint retention and proposals are not pruned
in this spike. Input manifests contain authored home text; keep them private
with the home. See [the consolidation bench](bench/consolidation.md) for scoring
quality separately from transport correctness.

## Next

Semantic seed migrations, other harness adapters, daemon integration, journal
retention and automated Git synchronization remain outside this spike. The
home stays authored data; the library adds capture and explicit curation to the
agent you already use.
