# Plan: Branch Modes, Execution Environments & Task Lifecycle

## Revision History

- v1 (2026-04-08): Initial plan — event-level `branch` field, two cases
- v2 (2026-04-08): Major redesign based on feedback — branch as task property,
  agent-decided branching, per-worktree log files, graceful "need context" exit,
  execution environment abstraction

## Key Design Shift: Branch as Task Property, Not Event Property

### Previous Design (v1)

The `branch` field lived on the event. The daemon read it and set up the
environment accordingly. The agent had no say.

### New Design (v2)

Branching is a **task-level** decision. The flow:

1. Event arrives (from Telegram, Slack, Git, etc.)
2. Runner creates a **task** from the event
3. The task spec defines the branching strategy
4. The runner/agent decides — based on the task spec and context — whether to:
   - Run on the current branch
   - Create a new branch (and what to name it)
   - Use an existing branch

This offloads the branching decision to the agent, which has more context
about what the task actually requires.

### Task Spec: Branch Strategy

The task file (created by the runner from an event) includes a branch field:

```yaml
---
id: task-...
event: evt-...
branch: auto | current | <name> | new:<name>
---
```

Values:
- **`current`** — run on whatever branch is checked out. No branching.
- **`auto`** — let the agent decide the branch name (new or existing).
  The agent writes the branch name into its response metadata.
- **`<name>`** — use this specific branch (checkout if exists, create if not).
- **`new:<name>`** — always create a new branch with this name.
- **`task`** — use the task ID as the branch name (e.g., `brr/task-123`).

**Who sets this?** Three levels of precedence:

1. Event field (explicit per-event override) — highest
2. Task file spec defaults (set by the runner based on task analysis)
3. `.brr/config` default — lowest

The runner, when creating the task from the event, decides the branch strategy.
In the simplest case it uses the config default. In smarter cases the agent
analyzes the task and picks a strategy.

### Branch Creation

When `branch` specifies a name (explicit or agent-decided) and the branch
doesn't exist, **create it**. This is a supported flow, not an error.

```python
def setup_branch(repo_root: Path, branch: str, create_if_missing: bool = True):
    """Checkout or create the target branch."""
    if branch_exists(branch):
        checkout(branch)
    elif create_if_missing:
        create_branch(branch, from_ref="HEAD")
    else:
        raise BranchNotFoundError(branch)
```

In concurrent/worktree mode, this becomes:

```python
def setup_worktree(repo_root: Path, task_id: str, branch: str):
    """Create a worktree, creating the branch if needed."""
    if branch_exists(branch):
        # Existing branch — worktree for isolation only
        git("worktree", "add", worktree_path, branch)
    else:
        # New branch — worktree + branch creation
        git("worktree", "add", worktree_path, "-b", branch, "HEAD")
```

## The kb/log.md Problem — Revised

### Previous Approach

Orchestrator writes log entries in worktree mode, agent skips log entirely.
Problem: orchestrator-written entries are thin (no agent perspective).

### New Approach: Per-Task Log Files + Post-Merge Squash

Each worktree gets a **copy** of `kb/log.md` at creation time. The agent writes
to it normally. But since concurrent agents write to separate copies, there's
no conflict during execution.

**Post-task merge of logs:**

When the orchestrator cleans up a completed task:

1. Read the worktree's `kb/log.md` (agent's version with new entries)
2. Read the main repo's `kb/log.md` (may have been updated by other completed tasks)
3. Merge: append new entries from the worktree version to the main version
4. The structured format (`## [date] type | title`) makes this automatable

**Alternative: Separate log files per task**

Instead of copying log.md, the prompt tells the agent to create a new log file:

```
Write your log entry to kb/log-<task-id>.md instead of kb/log.md.
```

Then the orchestrator squashes all `kb/log-*.md` files into `kb/log.md` after
merge. Advantages:
- No merge logic needed — just concatenate
- Agent creates file from scratch, no copy needed
- Multiple entries from the same task stay together

**Recommended: separate log files.** Simpler, no merge conflicts possible,
easy to implement. The squash step is trivial given the structured format.

### Log Format for Merge-Friendliness

The current format (`## [date] type | title`) is already pretty good for
automated merging. Each entry is self-contained and append-only.

Options for even safer merging:

1. **Current format** (recommended) — good enough. Entries are independent blocks.
   Squashing = concatenation + sort by date.

2. **JSONL** — one JSON object per line. Trivially mergeable, machine-parseable.
   But less human-readable when browsing the repo.
   ```jsonl
   {"date":"2026-04-08","type":"implement","title":"...","body":"..."}
   ```

3. **CRDT-style** — each entry has a unique ID, merge = union. Overkill for
   an append-only log.

**Decision: stick with current markdown format.** It's LLM-friendly,
human-readable, and the separate-file approach eliminates merge conflicts
entirely. If we ever need machine parsing, `grep "^##" | parse` works.

## Execution Environments

### The Bigger Picture

Branching strategy is one axis. Execution environment is another:

| Environment | Isolation | Use Case |
|-------------|-----------|----------|
| **Current branch** | None | Simple, serial tasks |
| **Worktree (new branch)** | Filesystem | Concurrent, independent tasks |
| **Worktree (existing branch)** | Filesystem | Project-managed branches |
| **Docker container** | Full | Paranoid mode, untrusted tasks |

Docker is a natural extension: instead of a worktree, spin up a container
with the repo mounted (or cloned). The agent runs inside the container.
This gives:
- Full filesystem isolation
- Network isolation (optional)
- Resource limits
- Clean environment (no local state leakage)

### Consistency Across Environments

The question: should state injection be via **file copying** or **prompt building**?

| Approach | Worktree | Docker | Consistency |
|----------|----------|--------|-------------|
| **Prompt injection** | Works | Works | High — universal |
| **File copying** | Works | Works (mount/copy) | High — but cleanup needed |
| **Hybrid** | Prompt for config, files for kb/ | Same | Medium |

**For worktrees:** files are already there (committed state). Only uncommitted
state needs injection. Prompt is cleaner.

**For Docker:** nothing is there. Must either mount the repo or copy files in.
If mounting, committed state is there. If cloning, everything starts fresh.

**Recommendation:** Prompt injection for orchestrator-owned state (config,
context, environment info). File-level access for project-owned state (kb/,
AGENTS.md). This works for both worktrees (automatic) and Docker (mount/clone).

The per-task log file approach is consistent across all environments: the agent
creates `kb/log-<task-id>.md`, the orchestrator collects it regardless of where
the agent ran.

### Environment Spec in Task

```yaml
---
id: task-...
event: evt-...
branch: auto
env: worktree | docker | local
---
```

`env` defaults to `worktree` in concurrent mode, `local` in serial mode.
Docker support is a future enhancement but the abstraction should be ready.

## Graceful "Need More Context" Exit

### The Problem

An agent might not have enough information to complete a task. Currently there's
no way to signal this — the agent either produces a result or fails.

### The Solution

A "need context" outcome is a **successful run**. The agent:

1. Does whatever research it can (reads code, checks logs, etc.)
2. Determines it can't adequately complete the task
3. Writes a response file explaining:
   - What it found
   - What's missing
   - What specific information it needs
4. Exits normally

The response file format could include a status indicator:

```markdown
---
status: needs_context
---

## Research Completed

I investigated X, Y, Z and found:
- ...

## What I Need

To complete this task, I need:
1. Access to the API documentation for service Foo
2. Clarification on whether the migration should be backwards-compatible
3. ...
```

The orchestrator reads the status and:
- Marks the task as `needs_context` (not `done`, not `error`)
- Notifies the user via the originating gate (Telegram, Slack, etc.)
- The user provides context → new event → task resumes (or new task)

**Prompt encouragement:** The run prompt should explicitly tell the agent:

```
If you determine that you don't have enough information to complete the task
adequately, that is a valid outcome. Write your response file explaining what
you found, what's missing, and what you need. Do not guess or produce
low-quality work when you could ask for clarification instead.
```

## AGENTS.md Considerations

### The Tension

AGENTS.md is used by multiple tools (Claude Code, Cursor, potentially others).
It should stay generic. But some instructions (branching, log handling) vary
by execution mode.

### Resolution

AGENTS.md stays tool-agnostic. Mode-specific overrides come from prompt
injection by the brr orchestrator:

```
Prompt assembly:
1. run.md (base — "read AGENTS.md, read kb/")
2. mode-specific template (worktree/branch/docker overrides)
3. Injected state files
4. Environment context (what branch, what mode, what to skip)
5. Task body
```

The mode-specific template explicitly says "these instructions override
AGENTS.md where they conflict." This is clean: AGENTS.md is the base layer,
brr adds orchestration-specific overrides on top.

If we need AGENTS.md itself to be mode-aware, we could add a section like:

```markdown
## Orchestrator Overrides

If your prompt includes orchestrator-specific instructions (e.g., from brr,
a CI system, or another runner), those take precedence over the workflow
section above for the specific points they address.
```

This is a small, tool-agnostic addition that any orchestrator can leverage.

## Execution Context in Prompt

The agent should know:

1. **What environment it's in:** worktree, docker, local
2. **What branch it's on** and whether it owns the branch
3. **Whether other agents are running concurrently**
4. **What to skip** (e.g., don't modify kb/log.md in worktree mode)
5. **How the agent was invoked** (daemon, CLI, which gate)

Example prompt injection:

```
## Execution Context

- Environment: worktree (isolated copy)
- Branch: brr/task-abc123 (created for this task, you own it)
- Concurrent: yes (other tasks may be running in parallel)
- Log: write to kb/log-task-abc123.md (NOT kb/log.md)
- Push: do not push — orchestrator handles it after merge
- Response: /abs/path/to/.brr/responses/evt-xxx.md
```

## Updated Implementation Plan

### Phase 1: Task Abstraction

Create a task model that sits between events and execution:

```python
@dataclass
class Task:
    id: str
    event_id: str
    body: str
    branch: str          # "current", "auto", "<name>", "new:<name>", "task"
    env: str             # "local", "worktree", "docker"
    status: str          # "pending", "running", "done", "needs_context", "error"
    
    @classmethod
    def from_event(cls, event: dict, cfg: dict) -> "Task":
        """Create a task from an event, applying config defaults."""
```

### Phase 2: Branch Resolution

```python
def resolve_branch(task: Task, repo_root: Path) -> ResolvedBranch:
    """Determine the actual branch name and whether to create it."""
    if task.branch == "current":
        return ResolvedBranch(name=current_branch(), create=False, worktree=False)
    elif task.branch == "auto":
        # Agent decides — use task ID as default, agent can override
        return ResolvedBranch(name=f"brr/{task.id}", create=True, worktree=True)
    elif task.branch == "task":
        return ResolvedBranch(name=f"brr/{task.id}", create=True, worktree=True)
    elif task.branch.startswith("new:"):
        name = task.branch[4:]
        return ResolvedBranch(name=name, create=True, worktree=True)
    else:
        exists = branch_exists(task.branch)
        return ResolvedBranch(name=task.branch, create=not exists, worktree=True)
```

### Phase 3: Per-Task Log Files

- Prompt tells agent: "write log to `kb/log-<task-id>.md`"
- After task completion, orchestrator squashes:
  ```python
  def squash_logs(repo_root: Path):
      """Merge all kb/log-*.md files into kb/log.md, then delete them."""
      log_path = repo_root / "kb" / "log.md"
      main_log = log_path.read_text()
      for f in sorted((repo_root / "kb").glob("log-*.md")):
          entries = f.read_text()
          main_log += "\n" + entries
          f.unlink()
      log_path.write_text(main_log)
  ```

### Phase 4: "Needs Context" Status

- Add `needs_context` as a valid task status
- Response file can include `status: needs_context` frontmatter
- Orchestrator parses this and notifies via originating gate
- Does NOT retry — waits for user input

### Phase 5: Execution Environment Abstraction

```python
class ExecutionEnvironment(Protocol):
    def setup(self, task: Task, repo_root: Path) -> Path:
        """Set up the environment, return working directory."""
    
    def teardown(self, task: Task) -> None:
        """Clean up after task completion."""

class LocalEnv: ...      # No-op setup, run in repo root
class WorktreeEnv: ...   # git worktree add/remove
class DockerEnv: ...     # Future: container lifecycle
```

### Phase 6: Prompt Assembly

```python
def build_prompt(task: Task, env: ExecutionEnvironment, cfg: dict) -> str:
    """Assemble the full prompt for the agent."""
    parts = [
        load_template("run.md"),
        load_mode_template(task),        # worktree/branch/docker overrides
        build_context_block(task, env),  # execution context
        inject_state_files(cfg),         # configured state files
        task.body,                       # the actual task
    ]
    return "\n\n".join(parts)
```

## Open Questions

1. **`auto` branch — who really decides?** If the agent decides the branch name,
   it needs a way to communicate that back. Options: write it in response
   frontmatter, or the orchestrator picks a default (`brr/<task-id>`) and the
   agent can override. Recommend: orchestrator picks default, agent can override.

2. **Log squash timing:** Immediately after each task? Or batch at intervals?
   Immediate is simpler and keeps the log current.

3. **Docker mount strategy:** Full repo mount (read-write)? Read-only mount +
   output directory? Clone inside container? Needs more research when we
   get to Docker support.

4. ~~**Task persistence:** Should tasks be persisted to disk (like events)?~~
   **Resolved (2026-04-08):** Yes. Tasks are persisted to `.brr/tasks/` as
   frontmatter+body files, mirroring the event file format.  Events were
   already persisted — the Task abstraction extends this pattern.
   Implemented in `src/brr/task.py`.

5. **Agent branch override for `auto`:** How does the agent communicate its
   chosen branch name back? Frontmatter in response file is cleanest:
   ```yaml
   ---
   branch: feature/better-name
   ---
   ```

## Resolved Design Decisions (2026-04-08)

- **Branch is a task property, decided by the triage agent.** The flow is:
  event arrives → triage agent analyzes it → creates a Task with branch
  strategy → execution agent runs the task.  The triage agent has context
  about task complexity and can make informed branching decisions.

- **Conversation context via log injection.** The orchestrator reads recent
  entries from `kb/log.md` and injects them into the agent's prompt.  This
  gives continuity across sessions without manual context sharing (gists,
  copy-paste).  The log is maintained by agents per AGENTS.md, keeping it
  proportional.

- **Two-stage agent pipeline.** Event → triage agent (creates Task) →
  execution agent (runs Task).  The triage prompt is in
  `src/brr/prompts/triage.md`.  This separation lets the triage agent
  make decisions (branching, environment) that the execution agent
  simply follows.

## Summary of Changes from v1

| Aspect | v1 | v2 |
|--------|----|----|
| Branch ownership | Event property | Task property |
| Branch creation | Fail if missing | Create if missing (supported flow) |
| Agent role in branching | None | Can decide branch name (`auto` mode) |
| Log handling | Orchestrator writes thin entries | Per-task log files, squashed post-merge |
| "Need context" | Not supported | First-class outcome |
| Execution environments | Worktree only | Abstraction: local/worktree/docker |
| AGENTS.md changes | Mode-specific sections | Stays generic + override clause |
| File copying vs prompts | Prompts only | Prompts for config, files for kb/ |
