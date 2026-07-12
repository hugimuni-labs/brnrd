# Pointer, not the contract

The contract every AI tool follows in this repo is [`AGENTS.md`](AGENTS.md).
Claude Code does not read `AGENTS.md` natively; this stub exists so a Claude
session starts oriented anyway. Read it before writing to shared surfaces
(knowledge base, commits, workflow) — it also tells you which stage you are
in (ad-hoc session vs brnrd-hosted run) and what each stage may touch.

Drop-in sessions (no brnrd host in the loop):

- `brnrd agent inject` prints the live wake context a brnrd resident gets —
  memory digest, pitfalls matched to your task, recent activity, kb health.
- The knowledge base is **not** the repo-root `kb/` (an editor-config husk);
  it lives at `.brnrd-kb/repos/Gurio__brr/` and via `brnrd kb <query>`.
- `.brr/` is brnrd's runtime directory — don't explore or edit it beyond
  what your task explicitly asks.

brnrd-hosted wakes already receive their orientation injected; for them this
file adds nothing new. Keep it a pointer — content belongs in `AGENTS.md`.
