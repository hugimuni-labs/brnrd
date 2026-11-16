# VALUES

The [manifesto](MANIFESTO.md) says where brnrd is going. This page says what we will not trade on the way. A design that breaks one of these argues for it here first, or does not ship.

Each value has three lines: what it is, what it rules out, and where the product stands today. `∅` means nothing yet, said on purpose.

## 1 · Every brnrd changes itself, consciously

A change to a resident is not something that happens to it. The resident decides to take it, carries it out, and looks itself over afterwards: it reads what changed, checks what it and its person have added, repairs what it can, and tells the person the rest. How much it asks its person first follows what the person has said they want. This holds for a new version from us, and equally for a change the resident or its person starts: a feature added, a part replaced. It holds for every install.

- rules out: updates that silently change behaviour; a version that loads without the resident having decided to take it; migration machinery that matches a person's additions against version numbers; an update the person has to supervise; an install whose resident has no way to change its own program.
- today: an install that follows `main` loads new code by itself, with no decision by the resident, and is told which changes it loaded. Nothing asks the resident to look itself over. An install from a release does not update by itself at all, and its resident cannot change the program it runs on. Decided 2026-11-15: every install carries its own code, and takes ours as a merge. Not built yet.

## 2 · What is yours is yours

Every file has one owner. A person's repository belongs to them and their resident: the resident writes there as part of its work, under its own name or the person's. We, the people who ship brnrd, never write into it, and neither does an update from us. What we ship is a starting point: each install carries its own copy of the code, the resident may change it, and our new version arrives as a merge the resident chooses to make. There is always a way back.

- rules out: copying our files into a person's repository; additions kept where no history protects them; a registry that stands between two people who want to share.
- today: a new install copies our files into the person's repository once and never updates them; replacement parts live outside it. An install that carries its code stops receiving ours the moment its resident changes a line.

## 3 · Free form first: the mind does the matching

Compatibility problems come from rigid structure. Where a mind can read, adapt and repair, we leave the shape free and let it. We keep rigid only what the mind needs in order to do that work: brnrd always starts with nothing added, and every change is in history.

- rules out: schemas, version fields and contracts for things a resident can read and re-fit; adding a mechanism where a sentence would do.
- the honest limit: where one program calls another there is a real plug shape. The resident rewrites the plug, and the only check is that it runs.

## 4 · Fewer settings and files, more conversation

When something can be a setting, a file or a sentence between a person and their resident, it is the sentence. So the sentences are what the product keeps: every letter stored, and what was said in it findable, linked to what it led to. A decision can be followed back to the words it came from, and the words forward to what was done about them.

- rules out: a new configuration key for a preference a person could simply state; a new file kind where an existing one will do; a preference or a permission that cannot be traced to the sentence that gave it.
- today: letters are kept and cited by their number where work answers them. Finding one by what it said, and following it forward to what was done, is by hand.

## 5 · Trust comes from standing permission and a record, not from a cage

A resident acts on what its person has said yes to, and leaves a record of what it did that neither of them can quietly rewrite. It checks itself against that record and faces the ugliest failures first. This has to work with no server.

- rules out: safety that only exists while our cloud is reachable; counting misses by a list of forbidden commands; a number the resident can edit.
- today: guards for a few classes of act; the record is designed, not built.

## 6 · The owner's hardware, files and keys beat our convenience

From the manifesto, kept here because it decides things weekly: what a person owns stays theirs to move, and working end to end beats a demo.

- rules out: features that need the person's data on our side; a paid tier that sells a feature instead of operating something.
