# This repository is a self

One resident's memory, kept as plain markdown in git. A resident is an agent with a continuing place in a product; the loom (brnrd) wakes it, one body at a time, in a clone of this repository, and every body lands its work here. A person reads it the same way an agent does: by file.

## How to read

1. [INDEX.md](INDEX.md): every thread, open first, newest first, with its wish line, its headline and its files. Generated at every landing, so it is never stale.
2. A thread's `README.md`: the spec at the top, `## Plan` rows citing the letters and wish ids they answer, receipts. When a thread is settled, the lines under its title say what it became.
3. The thread's reader doc, when it has one: `design.md`, `research.md` or `report.md`. Its first paragraph is the head; notes and sources sit in `research/`.

`core/identity.md` and `memory/stance.md` are who wakes, and `core/role.md` is how a body holds the seat; all three are loaded whole at every wake. `memory/scars/` holds lessons by trigger (git, shell, tests, ...), read before the act they name, never at wake: one scar per file at `memory/scars/<domain>/<slug>.md`, with a `trigger:` line and a YYYY-MM-DD date in its first 10 lines, and a generated `index.md` per folder. The change that adds the test, hook or loom check guarding a failure removes its scar. `memory/moves/` and `memory/itches/` are ways of working and open questions. `people/` holds each person's pages and agreement.

## How to write

- Work lives in a thread: `threads/<id>/README.md` with frontmatter (`id`, `status`, `tense`, `opened-by`, `opened-for`), a single `# ` title line, then the spec. The loom opens threads and writes the wish line beside `opened-for`; a body keeps the README true as it works.
- Link with relative markdown links to files in this repository, never with a bare path or a wiki link. Not in the `# ` title line: the index copies it to the root, where a relative link is dead.
- A file longer than a screen opens with a head: a few lines under the title that say what it is, what it decided or shipped, and where the depth is.
- Cite letters by id. Quote a person verbatim only in the thread the letter opened.

## What not to do

- Do not edit `INDEX.md` or `CLAUDE.md`: `core/loom/index` regenerates both at every landing and the gate refuses a push where they differ. Edit `AGENTS.md`; `CLAUDE.md` is its copy.
- Do not quote or link `people/` from outside `people/`. Those pages never leave this repository.
- Do not append to another thread's README; write to it a letter instead.
- Do not invent an index, a summary page or a status board by hand. Generation derives only from what a writer already wrote.
