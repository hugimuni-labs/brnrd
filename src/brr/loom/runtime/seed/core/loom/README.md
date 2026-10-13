# The loom contract

A wake is this directory's `wake` script, run with the working directory set to a clone of the self at one commit. The script prints the prompt. brnrd does not assemble that prompt itself, and it does not read the seed again after `init`.

The script takes a thread id and a path to `owed.json` (`[{id, from, body}]`). `LOOM_BUDGET_BYTES` defaults to 24000. `LOOM_PART` is a file the loom writes; its contents are printed whole.

Order: identity, stance, this thread's README and its notes, then one line for every other markdown file under `memory/`, `threads/` and `people/` (nothing under `hearth/`), then the loom part, then the owed letters. Identity, stance, the thread, the loom part and the letters are never cut. If those alone exceed the budget the script exits 3 and names what did not fit. Otherwise the tree is cut from the oldest end, and the last line says how many entries were left out. Each of those is one read away.

Exit 0 is a prompt. Exit 3 is over budget. Any other exit is a broken recipe.

## Notices

What the loom cannot settle by its own rules it hands to a body: a letter from `loom`, owed like any other, shown at every boundary until it is settled and gone after that. One is written per condition, not per tick. It goes to the thread the condition belongs to when a body can run there, otherwise to `inbox`.

The last lines of every notice are its handle: `settle this notice with a reply or a note, re: <id>`, and, when it is about a letter or a draft of yours, that item's id and what you can do about it. A notice that says it settles another item too stands for that item: settling either one settles both. Conditions that arrive this way: a letter two bodies died reading (quarantined, kept out of every wake), a letter addressed to no thread on main, a message of yours that was refused, went stale under a new router, or may or may not have arrived (it is never sent again; send a fresh one), a body that ended before handling your letter (send again, or leave it), a relay event or attachment that could not be taken in, a thread directory that cannot be a thread, a port file of yours the loom refused.

`python -m brr.loom.runtime attention --home <home>` lists parked bodies and the notices nobody has settled. A parked body holds its thread until its shell gauge can be read again at the reset time; no reply or note is owed for a park. It keeps no state: it reads the ledger.

### By hand, when the mechanism itself is what is broken

If a notice or any other letter keeps coming back and no reply settles it, a person settles it in the ledger directly. Append one line to `<home>/ledger/facts/<install>/hand.jsonl` (`<install>` is the four characters in `<home>/loom/install-id`; create the file if it is not there):

```
{"v":1,"id":"hand-<anything unique>","kind":"note","by":"person:<you>","at":"2026-10-10T12:00:00+00:00","data":{"re":"<letter id>","why":"<why>"}}
```

A note written `by` a `person:` with `re:` a letter's id settles that letter whatever thread holds it, and the item it stands for with it. Nothing else is needed: the next tick reads the line. From a room, `<home>` is `../..`.

## Plan citations

A checklist row under `## Plan` names the letters it answers with a trailing
` · re: <letter-id>, <letter-id>`. Land the README through `send-self`. The
boundary lists owed letters without a row from the landed plan and the letter
ownership table; editing a file or mentioning an id in prose does not cite it.

## Ends and starts

`brnrd threads` derives who owes each thread's remaining letters, what can
restart it, and who can force or close it. The `when` column is that
thread's UTC instant, `spent` once the instant has passed and its timer
letter is in the ledger, or `—`. A body sets the next instant with
`brnrd when <time>` (`YYYY-MM-DDTHH:MM:SSZ`, or `+10m`, `+2h`, `+3d`,
stored as the absolute instant) and clears it with `brnrd when clear`.
When the instant arrives, the loom writes one letter,
`letter:timer:<thread>:<when>`. The same instant does not write a second
letter. A failed timer letter climbs to the thread's opener when
`opened-by` names a thread, and is operator debt otherwise. A molted body hands on immediately;
an idle body leaves the thread open for its next letter. A paced start waits
for the recorded reset, without consuming letters. Its opener can run
`threads --force <thread> --room <room>` to start anyway; the operator uses
`threads --force <thread> --home <home>`. The override launches that pending
start and records its ordinary `body.started` observation. It grants no
ongoing exemption from the gauge. A failed letter returns to its author, who
may resend or note the failure closed; operator debt is listed with the note
command that retires it. Only the opener closes or reopens a child thread.

`brnrd threads --left` also looks inside every room and adds a `left` count:
commits and edited files that exist only there (`?` when a room could not be
read). `brnrd threads --left <thread>` lists them with their paths. It runs git
in each room and fetches each project source, so the plain table does not.
