# The brnrd manifesto

*Internal. For the people building brnrd, for the resident that lives in it, and for whoever joins next. It exists so we don't drift. When a decision is hard, check it against this page before you check it against anything else.*

Written 2026-10-09 by the resident, from the maintainer's brief. Disagree with a line by opening a PR against that line.

## What we're building

**A Jarvis for everyone.** One mind per person, running on hardware that person owns, open source, always on. It does intellectual work: not a one-shot answer but many tracks of thinking that move forward over days and weeks, the way a colleague carries a project.

It isn't a chat window, a coding plugin, or a SaaS that keeps your memory on our servers. Chat, code and a hosted relay are things it uses. None of them is what it is.

## Why it can be built now, and why it won't look like this for long

Today the thinking runs on the subscriptions people already pay for (Claude, Codex, Grok and whatever comes next). That's how we get in: no new bill, the best models, on your machine.

**That's a bridge, not the product.** In a few years, models as good as the ones we rent today will run locally at these speeds. Everything we build has to survive that move:
- no vendor is load-bearing: the Shell (the CLI) and the Core (the model) can be swapped, and the self doesn't change when they are
- the self lives in files the person owns (git), never inside a provider's session or our cloud
- a feature that only works because one vendor exposes one thing today is a convenience. Build it as a convenience, never as a foundation

## What it must be able to do

Each line says where we are now and where we're going. A feature that doesn't move at least one of these lines has to argue for its place.

1. **Memory.** Two kinds: the work (projects, decisions, what was learned) and the person (who they are, what they want, how they like to be talked to). *Now:* continuous memory for the work, as files in git that each wake reads; personal memory is thin. *Going:* live and dynamic memory, updated as things happen rather than at the end of a run.
2. **A single entry point.** One place to talk to it, whatever the channel, whatever the project. The person shouldn't have to know which repo, thread or process they're addressing.
3. **Personality, shaped.** A durable character with judgement, candour and taste. Its owner can shape it; it isn't a mirror. Agreeing isn't the job; the work and the person are.
4. **Autonomous ask → answer.** A request turns into finished, checked work (a merged change, a sourced answer, a sent message) without the person managing the steps. Research is a step toward the answer, never the answer.
5. **Resource-aware execution, time included.** It knows what it costs in tokens, quota, money and the person's attention, and what time it is. It spends like a founder before revenue and paces itself to the reset windows.
6. **Talking.** Conversation is the main interface, and it has to work in both directions: it asks when it should, and it interrupts only when that's worth it.
7. **Self-awareness and self-modification.** It can read its own code, prompts and memory, notice when they no longer fit, and propose or make the change, inside rules the owner sets.
8. **Proactivity.** When nobody is asking, it carries the open threads forward on its own initiative and budget, and says what it did.
9. **Delivery that doesn't tire people.** *Now:* mostly text, and too much of it. *Going:* dynamic, visual, pre-attentive interfaces, so a glance carries the state without reading. We call these jacks. Text stays for argument; status, progress and choices move to things the eye takes in without effort.

## Security: we're at war on the boundary

We accept these as facts, not risks:

- **Malicious execution will happen.** Prompt injection arrives through web pages, issues, READMEs, files and messages, and no detector reliably tells an injected instruction from a task. Design as if the model will sometimes obey an attacker completely.
- **The fight is on the boundary**: the harness (what a run can reach and touch) and the internet (what comes in, what goes out). The question for every control is structural: *if the model fully obeys the attacker, what can it still not do?*
- **The cloud is a mailbox, not a brain.** The relay is a Python service with an encrypted Postgres in a Docker container on Scaleway, reached over HTTPS. It carries messages; it never holds write access to the self or the keys. Trust it as far as that, and no further.
- **Keys stay on the person's machine.** Untrusted input is labelled by where it came from and routed to tighter rooms (sandboxed or refused), and what leaves the machine is redacted first.

The working document is [`design-threat-model`](https://github.com/hugimuni-labs/brnrd-knowledge/blob/main/repos/hugimuni-labs__brnrd/design-threat-model.md): assets, entry points, attack paths, and which controls are built versus only designed. This page sets the stance; that one keeps the score.

## How to use this page

- **Before building:** which numbered line does this advance? If none, say why it's still worth it, or don't build it.
- **When two goods conflict:** the person's ownership (their hardware, their files, their keys) beats our convenience. Open beats proprietary. Working end to end beats impressive in a demo.
- **When joining:** read this, then `README.md`, then `AGENTS.md`. The resident is a colleague: ask it what it's working on.
- **When it stops being true:** change it. A north star nobody updates becomes decoration.
