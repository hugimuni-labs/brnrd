# The loom contract

A wake is this directory's `wake` script, run with the working directory set to a clone of the self at one commit. The script prints the prompt. brnrd does not assemble that prompt itself, and it does not read the seed again after `init`.

The script takes a thread id and a path to `owed.json` (`[{id, from, body}]`). `LOOM_BUDGET_BYTES` defaults to 24000. `LOOM_PART` is a file the loom writes; its contents are printed whole.

Order: identity, stance, this thread's README and its notes, then one line for every other markdown file under `memory/`, `threads/` and `people/` (nothing under `hearth/`), then the loom part, then the owed letters. Identity, stance, the thread, the loom part and the letters are never cut. If those alone exceed the budget the script exits 3 and names what did not fit. Otherwise the tree is cut from the oldest end, and the last line says how many entries were left out. Each of those is one read away.

Exit 0 is a prompt. Exit 3 is over budget. Any other exit is a broken recipe.

## Notices

What the loom cannot settle by its own rules it hands to a body: a letter from `loom`, owed like any other, shown at every boundary until it is settled and gone after that. One is written per condition, not per tick. It goes to the thread the condition belongs to when a body can run there, otherwise to `inbox`.

The last lines of every notice are its handle: `settle this notice with a reply or a note, re: <id>`, and, when it is about a letter or a draft of yours, that item's id and what you can do about it. A notice that says it settles another item too stands for that item: settling either one settles both. Conditions that arrive this way: a letter two bodies died reading (quarantined, kept out of every wake), a letter addressed to no thread on main, a message of yours that was refused, went stale under a new router, or may or may not have arrived (it is never sent again; send a fresh one), a fused thread (a new letter to it resets the fuse; a notice never does), a relay event or attachment that could not be taken in, a thread directory that cannot be a thread, a port file of yours the loom refused.

`python -m brr.loom.runtime attention --home <home>` lists the notices nobody has settled. It keeps no state: it reads the ledger.

### By hand, when the mechanism itself is what is broken

If a notice or any other letter keeps coming back and no reply settles it, a person settles it in the ledger directly. Append one line to `<home>/ledger/facts/<install>/hand.jsonl` (`<install>` is the four characters in `<home>/loom/install-id`; create the file if it is not there):

```
{"v":1,"id":"hand-<anything unique>","kind":"note","by":"person:<you>","at":"2026-10-10T12:00:00+00:00","data":{"re":"<letter id>","why":"<why>"}}
```

A note written `by` a `person:` with `re:` a letter's id settles that letter whatever thread holds it, and the item it stands for with it. Nothing else is needed: the next tick reads the line. From a room, `<home>` is `../..`.
