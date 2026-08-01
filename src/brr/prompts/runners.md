---
claude:
  cmd: 'claude --print --output-format json --dangerously-skip-permissions --setting-sources local --system-prompt "You are a brnrd runner. Follow the supplied prompt and operate on the files available in the working directory."'
  capabilities:
    headless: native:--print
    stdout_reply: native:claude-json
    boundary_injection: native:claude
    model_pin: native:--model@binary
    quota_read: degraded:cached-tui
  provider: anthropic
  owner: user
  class: balanced
  cost_rank: 30
  quota_source: claude-local
claude-bare-api-only:
  binary: claude
  shell: claude
  cmd: 'claude --print --output-format json --dangerously-skip-permissions --bare --system-prompt "You are a brnrd runner. Follow the supplied prompt and operate on the files available in the working directory."'
  capabilities:
    headless: native:--print
    stdout_reply: native:claude-json
    boundary_injection: degraded:heartbeat
    model_pin: native:--model@binary
    quota_read: degraded:cached-tui
  provider: anthropic
  owner: user
  class: balanced
  cost_rank: 30
  auth_variant: anthropic-api-key
  auth_env: ANTHROPIC_API_KEY
codex:
  cmd: 'codex exec --dangerously-bypass-approvals-and-sandbox --dangerously-bypass-hook-trust -c base_instructions="You are a brnrd runner. Follow the supplied prompt and operate on the files available in the working directory." -c include_permissions_instructions=false -c include_apps_instructions=false -c include_collaboration_mode_instructions=false -c include_skill_instructions=false'
  capabilities:
    headless: native:exec
    stdout_reply: native:codex-jsonl
    boundary_injection: native:codex
    model_pin: native:--model@exec
    quota_read: native:session-rollout
  provider: openai
  owner: user
  class: balanced
  cost_rank: 25
  quota_source: codex-local
---
Bundled runner profile catalog for brnrd.

Each profile declares the command brnrd executes and five capability cells.
A cell is either a native adapter mapping (`native:<mapping>`) or a named,
observable degradation (`degraded:<name>`); `unknown:<reason>` is reserved
for facts that have not been established. Matrix-aware profiles declare every
cell; profiles with no matrix at all retain the pre-#907 compatibility path.

The adopter-facing runner interface and custom-profile format live in
`docs/src/content/docs/guides/runner-profiles.md`. Selection, fallback, and
Core-registry design live in the knowledge base:
`design-runner-cores.md` and `design-runner-back-channel.md`.
