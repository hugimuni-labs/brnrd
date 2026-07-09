# AGENTS.md and the knowledge base

These are the two files `brnrd init` creates that matter most, and they
work independently of whether you ever run the brr daemon at all.

## `AGENTS.md`

`AGENTS.md` is a playbook for your project: conventions, workflow steps,
commit discipline, guardrails, and self-review instructions, written so
any AI coding tool can read it and behave the same way. Claude Code,
Cursor, Codex, and Gemini all look for this file (or an equivalent) at
the root of a repo — brr just gives you a well-considered starting
template instead of a blank page.

You can use `AGENTS.md` with no brr daemon running at all: copy the file,
adapt the conventions to your project, and any supported AI tool reads it
cold. The daemon is what turns it from "context I paste into my editor"
into "context every task, from every gate, automatically gets."

## The knowledge base (`kb/`)

A single `AGENTS.md` file can't hold everything an agent learns about a
project over weeks of work — the design decisions, the things that were
tried and rejected, the running narrative of what shipped and why. That's
what the knowledge base is for: a directory of markdown pages, written
and maintained by the agents that work on your project, that compounds
across sessions instead of resetting every time.

A few conventions make it useful rather than a pile of notes:

- **State-first, not a changelog.** Pages describe *current* understanding
  first; history of *how* that understanding changed lives in git blame
  and a short "Lineage" section at the bottom, not as accumulating
  inline diffs that make a page harder to read with every edit.
- **It's a graph, not a stack.** An `index.md` entry point organizes pages
  by subject, not by when they were written; pages link to their
  neighbors, and a page with no inbound links is a sign something's
  drifted out of the map.
- **A few page shapes, each with a job**: a running narrative log of
  what happened, subject pages describing how something currently works,
  decision records for choices that were actually made (with the
  alternatives that were considered and rejected), and design/research
  pages for work still in progress.

For a repo connected to a brnrd account, the kb doesn't have to live
inside the project's own git history at all — it can live in a private,
account-scoped knowledge store instead, so a public repo's history
doesn't have to carry internal design deliberation. Either way, the
conventions above are the same; only where the files physically live
changes.

## Why both, together

`AGENTS.md` tells an agent *how to behave here*. The kb tells it *what's
already been learned here*. A fresh clone with just `AGENTS.md` gets
consistent behavior; a project with a well-maintained kb on top of that
gets an agent that doesn't re-derive the same design decision for the
third time.

## Next

- [The daemon](daemon.md) — how tasks actually get run.
- [Gates and portals](gates-and-portals.md) — how tasks get in and
  results get out.
