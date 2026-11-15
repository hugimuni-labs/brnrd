# VALUES

The [manifesto](MANIFESTO.md) says where brnrd is going. This page says what we will not trade on the way. A design that breaks one of these argues for it here first, or does not ship.

Each value has three lines: what it is, what it rules out, and where the product stands today. `∅` means nothing yet, said on purpose.

## 1 · Every brnrd updates itself, consciously

A new version is not something that happens to a resident. After an update the resident reads what changed, looks over what it and its person have added, repairs what it can, and tells the person the rest. This holds for every install: one that follows `main` and one that follows releases.

- rules out: updates that silently change behaviour; migration machinery that matches a person's additions against version numbers; an update the person has to supervise.
- today: an install that follows `main` loads new code by itself and is told which changes it loaded. Nothing asks the resident to look itself over. An install from a release does not update by itself at all.

## 2 · What is yours is yours

Every file has one owner. A person's repository belongs to them and their resident, and brnrd never writes into it. Everything else is brnrd, and an update replaces it whole and keeps the way back.

- rules out: copying our files into a person's repository; additions kept where no history protects them; a registry that stands between two people who want to share.
- today: a new install copies our files into the person's repository once and never updates them; replacement parts live outside it.

## 3 · Free form first: the mind does the matching

Compatibility problems come from rigid structure. Where a mind can read, adapt and repair, we leave the shape free and let it. We keep rigid only what the mind needs in order to do that work: brnrd always starts with nothing added, and every change is in history.

- rules out: schemas, version fields and contracts for things a resident can read and re-fit; adding a mechanism where a sentence would do.
- the honest limit: where one program calls another there is a real plug shape. The resident rewrites the plug, and the only check is that it runs.

## 4 · Fewer settings and files, more conversation

When something can be a setting, a file or a sentence between a person and their resident, it is the sentence.

- rules out: a new configuration key for a preference a person could simply state; a new file kind where an existing one will do.
- today: ∅ measured.

## 5 · Trust comes from standing permission and a record, not from a cage

A resident acts on what its person has said yes to, and leaves a record of what it did that neither of them can quietly rewrite. It checks itself against that record and faces the ugliest failures first. This has to work with no server.

- rules out: safety that only exists while our cloud is reachable; counting misses by a list of forbidden commands; a number the resident can edit.
- today: guards for a few classes of act; the record is designed, not built.

## 6 · The owner's hardware, files and keys beat our convenience

From the manifesto, kept here because it decides things weekly: what a person owns stays theirs to move, and working end to end beats a demo.

- rules out: features that need the person's data on our side; a paid tier that sells a feature instead of operating something.

<sub>changes · 2026-11-15: born from the maintainer's word that conscious self-update belongs in a values page (value 1). Values 2 to 5 are his steers of 2026-11-13 to 2026-11-15, written down by the resident for him to prune; value 6 is the manifesto's.</sub>
