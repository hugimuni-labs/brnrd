# The loom contract

A wake is this directory's `wake` script, run with the working directory set to a clone of the self at one commit. The script prints the prompt. brnrd does not assemble that prompt itself, and it does not read the seed again after `init`.

The script takes a thread id and a path to `owed.json` (`[{id, from, body}]`). `LOOM_BUDGET_BYTES` defaults to 24000. `LOOM_PART` is a file the loom writes; its contents are printed whole. When `LOOM_CORE` names a file, the core (identity and stance) is written there instead of stdout and the budget still counts it; the loom hands it to a body's system slot, and to a body without one at the head of the wake.

Order: identity, stance, this thread's README and its notes, then one line for every other markdown file under `memory/`, `threads/` and `people/` (nothing under `hearth/`), then the loom part, then the owed letters. Identity, stance, the thread, the loom part and the letters are never cut. If those alone exceed the budget the script exits 3 and names what did not fit. Otherwise the tree is cut from the oldest end, and the last line says how many entries were left out. Each of those is one read away.

Exit 0 is a prompt. Exit 3 is over budget. Any other exit is a broken recipe.

## The index

`INDEX.md` at the root is the wake tree without its byte cap, for a person or
an outside agent: one row per thread (status, wish line, headline, files with
first line and size), open threads first, newest first; one row per file under
`memory/`, scar folders as one row with a count; `people/` as names only. The
self's own `core/loom/index` derives it from the committed tree, same contract
as `wake` (the self owns it, it imports nothing from brnrd): `index` prints it,
`index --write` writes `INDEX.md` and `CLAUDE.md` as a copy of `AGENTS.md`,
`index --check` compares. `send-self` runs `--write` after the rebase and folds
the two files into the landing commit; `core/immune.d/30-index` runs `--check`
at the incoming tip and refuses a push where either differs. Nobody edits
them; `AGENTS.md` is written by hand and is the one source. A thread's wish
line is copied into its README at open as `wish-<w-id>: "<line>"` beside
`opened-for`, a copy of an immutable fact, so the room needs no ledger.

## Notices

What the loom cannot settle by its own rules it hands to a body: a letter from `loom`, owed like any other, shown at every boundary until it is settled and gone after that. One is written per condition, not per tick. It goes to the thread the condition belongs to when a body can run there, otherwise to `inbox`, the home thread.

The last lines of every notice are its handle: `settle this notice with a reply or a note, re: <id>`, and, when it is about a letter or a draft of yours, that item's id and what you can do about it. A notice that says it settles another item too stands for that item: settling either one settles both. Conditions that arrive this way: a letter two bodies died reading (quarantined, kept out of every wake), a letter addressed to no thread on main, a message of yours that was refused, went stale under a new router, or may or may not have arrived (it is never sent again; send a fresh one), a body that ended before handling your letter (send again, or leave it), a relay event or attachment that could not be taken in, a thread directory that cannot be a thread, a port file of yours the loom refused.

`python -m brr.loom.runtime attention --home <home>` lists parked bodies and the notices nobody has settled. A parked body holds its thread and starts when its shell gauge reads a refill; a person's letter meanwhile is told it is parked, and a bare `wake` in their DM starts it now. No reply or note is owed for a park. It keeps no state: it reads the ledger.

### By hand, when the mechanism itself is what is broken

If a notice or any other letter keeps coming back and no reply settles it, a person settles it in the ledger directly. Append one line to `<home>/ledger/facts/<install>/hand.jsonl` (`<install>` is the four characters in `<home>/loom/install-id`; create the file if it is not there):

```
{"v":1,"id":"hand-<anything unique>","kind":"note","by":"person:<you>","at":"2026-10-10T12:00:00+00:00","data":{"re":"<letter id>","why":"<why>"}}
```

A note written `by` a `person:` with `re:` a letter's id settles that letter whatever thread holds it, and the item it stands for with it. Nothing else is needed: the next tick reads the line. From a room, `<home>` is `../..`.

A failed preparation (including an over-budget home or a broken wake recipe)
keeps its letters owed for retry. One self-refusal notice asks for home repair,
in inbox when preparation, spawn, or the holding install cannot run. Only an input that
itself exceeds the wake budget, with a recipe that runs without mail, uses the
letter-failure path. A failure to prepare does not spend a letter's body-crash
retry allowance. The person receives no failure message for a home refusal. A nonzero body exit
requires a fresh gauge observation; depleted fuel keeps the letter owed and
parks its next start until all exhausted windows reset.

## Plan citations

A row under `## Plan` is a step, `- <step> · re: <letter-id>, <w-id>`, with
no checkbox: it carries steps and receipts, never status. Land the README
through `send-self`; mentioning an id in prose does not cite it. Progress is
the thread's wishes, delivered and accepted over all that are not dropped:
the status line's `plan x/y`. A person letter on a thread with open wishes is
shown with a `steers?` line naming them and the `send` that says it steers one.

## Linking a file

The loom mirrors `self` main to its forge remote within a minute of a landing. A
path you send a person is a link: `<remote>/blob/<sha>/<path>`, where `<remote>`
is `git remote get-url origin` without `.git` and `<sha>` is your room's `HEAD`
after `send-self` returned `merged`. The sha pins the file as you wrote it. Send
no bare path, and no link to a commit that has not landed.

## Wishes

A wish is what someone wants from a thread, kept as facts; the wake lists the
thread's open ones under `## Wishes`. Stamp it when you reply: `send --re <id>
--to <reply address> --wish new:"<what they want>"`, or `--wish <w-id>` for
one already heard. A new wish cites what it answers: `--say <letter>`, or
`--parent <w-id>` for a wish born of another; the ledger refuses one with
neither. A wish in the wisher's own words is said; one you infer (its says
cite only your own letters) is derived: when you reply about it, name it as
derived, your reading and not their words.

A person letter gets a disposition before a reply to it is sent: `send --re`
to one with none yet refuses unless the send carries `--wish` or `--chatter`
(it wants nothing; `--chatter=<id>,…` names other letters too). After a
`--wish`, `--says "<quote>"` (or `<letter id>:"<quote>"`) cites its words,
matched ignoring case, spacing and punctuation; a quote that matches nothing
is kept and cites the whole letter. `--ref <kind>:<ref>` records where it is
being worked (`pr:2390`, a URL; `pid:N` keeps the process's command line), and
`--answered` makes this reply its delivery. Each binds to the `--wish` before
it; only the person's verdict closes a wish. The send prints the graph it
wrote on stderr: `w-… ← "<quote>" (<letter>) → refs: …`.

A thread is opened for a wish. `send --to thread:<new>` requires `--wish
<w-id>` (moves that wish to the new thread) or `--wish new:"<line>"`
(repeatable). The new wish cites `--re` when given, otherwise the opening
letter itself, which makes it derived. The README records the ids once as
`opened-for: [w-…]`: why the thread was opened, while each wish's thread
says where its work is now. When a result exists,
record it with `wish <w-id> --result <kind>:<ref> --line "…"`, adding
`--deliver` on the one you hand over. What is left on a wish is the wish's
own: `wish <w-id> --left "<step>" --left "<step>"` restates the whole list,
and the latest one wins (`--left same` when nothing moved). The loom asks for
it at a stop and refuses a molt while a wish moved since its last left: a
steer, a result, a fact this body wrote on it, or, on a thread with one open
wish, this body's start. The stop asks once per wish (a `left.asked` fact), so
nothing is trapped; the wake shows each open wish as done, left and the steers
since. A Plan row that cites a wish (` · re: <letter-id>, <w-id>`) with a
receipt still grants it, and a pull request opened on a thread with one open
wish is that wish's result. `settle` refuses while a delivered wish
awaits a verdict; `--hand-to <thread>` moves those to a thread that will carry
them. Heard and working wishes stay with their thread.

## Guards

A guard is a rule the loom holds for you: `core/guards/<id>.toml` on
main, read from main and never from your branch. `says` is the rule in its
own words, `set-by` is `letter:<id>` (a person asked) or
`scar:<path>#<title-slug>` (a path under `memory/scars/`, the slug its
heading's; a landing whose guard cites a scar that is not in the tree is
refused, whichever side moved), and `scope` is `self`, `thread:<id>` or
`repo:<name>`. A `[[match]]` (`at` pre or post, `tool`, `input`, `unless`,
`path`, `cwd`, `output`, all regexes) warns on the call that trips it; a
pre match with `refuse = true` denies the call instead, so keep that for a
regex whose false positives cost less than its misses. A `[[paths]]` (`repo`, `untouched` and `absent` globs) warns
on an Edit or Write into its paths and blocks your stop once. send-self,
publish and settle refuse while it is broken; settle judges every room
serving the settled thread, not only yours. send-self refuses a landing
whose new or changed guard does not parse, or names in `absent` a file
already on that repo's main. A broken guard file is skipped
whole and named in a `guards:` line at your start. When a scar becomes a
guard, delete the scar in the same commit. A guard a person set is deleted or
loosened only by a commit carrying `Guard-Lifted: <their letter id>`.

## Answer, Done, asks

A thread README opens with `## Answer`: what is true now, decided, and still
open, written for the person and rewritten in place, never appended. Each
produce gets one line under `## Done`, written when it exists:
`- <kind> <url-or-path> — <what it is>`, kind one of pr, issue, doc, research,
file, answer, post, action. A pull request of your published branch also
gets a receipt git proves: `publish <repo> --pr N` once it is open, and
`publish <repo> --merged N` once it is merged with a merge commit (a squash
is refused). Threads, the snapshot and `asks --all` draw each as opened,
or merged and loaded, not loaded or unknown by the loom's latest start.
Merging your own pull request under the merges agreement ends with
`--merged N`. What you need from the person is an ask: `ask
"<the decision or act>" --for <w-id> --rec "<what you recommend>"`, where
`--for` names the wish it unblocks; their verdict closes it. An ask waits on
your thread's opener; when that is a thread, the ask mails it, and it answers
or asks onward with `--for <your ask>`, so the wait passes along until it
reaches a person. The same ask again (same line, same `--for`) while it is
open reuses it and sends only the mail that did not leave; it says `reused
<id>`. Reword it to ask anew. `brnrd asks`
lists, over every thread, what is in hand, the asks, the unanswered letters
and the Done lines. A thread a person opened settles on their word only: when
their reply says it is done, `settle <thread> --why "…" --word <their
letter>`; silence never settles. `brnrd graph` lists each
thread's strands, `--strands` each strand's threads, with the origin of each.

## Ends and starts

`brnrd threads` derives who owes each thread's remaining letters, what can
restart it, and who can force or close it. To wake yourself later, send a
letter to your own thread: `python -m brr.loom.runtime send --to thread:<your-thread> --at +10m "<what to do>"`. The time can be `YYYY-MM-DDTHH:MM:SSZ`, `+10m`, `+2h`, or
`+3d`; the letter stores the absolute UTC instant. Until that time it is
waiting, unseen by bodies, and starts nothing. `brnrd threads --due` lists
waiting letters with their times, ids, destination and sender threads, and
first lines. Cancel one before its time with `python -m brr.loom.runtime send --to thread:<your-thread> --re <letter-id> --note "<why>"`. To change its time,
cancel it and send another. A due letter is ordinary mail, even if the loom
was down at its time. A thread may write to itself only with `--at`; a due
time cannot be used with a channel address, `--note`, or `--reopen`. For a
repeat, the woken body sends the next letter. A molted body hands on immediately;
an idle body leaves the thread open for its next letter. A paced start waits
for the recorded reset, without consuming letters. Its opener can run
`threads --force <thread> --room <room>` to start anyway; the operator uses
`threads --force <thread> --home <home>`. The override launches that pending
start and records its ordinary `body.started` observation. It grants no
ongoing exemption from the gauge. A failed letter returns to its author, who
may resend or note the failure closed; operator debt is listed with the note
command that retires it. Only the opener closes or reopens a child thread by
verb. A wish placed on a settled or retired thread (`send --wish`, a forward
that carries one, or `wish <w-id> --say` on one of its wishes) opens it again:
the send lands `status: open` on main before the stamp, and a `thread.reopened`
fact names the wish and the settle it undid, which the next wake shows above
its wishes. Placed with `--home`, outside any room, such a wish is refused.

`brnrd threads --left` also looks inside every room and adds a `left` count:
commits and edited files that exist only there (`?` when a room could not be
read). `brnrd threads --left <thread>` lists them with their paths. It runs git
in each room and fetches each project source, so the plain table does not.

Open a worker and declare its checkouts together with `send --to thread:<new> --wish <w-id> --body <shell/core> --repo <url> "<work>"`; repeat `--repo` for each checkout. The README and seat land together before its first lease. Repo directory names must be distinct.

A thread opened by another thread carries an allocation: `--budget <n>` on the opening letter (`500k`, `2m`), or the home's `thread_budget` (2M). The status line shows `spent <s>/<n>` in weighted tokens (input 1, output 5, cache read 0.1, cache write 1.25), read from the harness's own transcripts. Past it, every boundary says so: write to the opener what is done, what is left and how much more you ask. It warns; it never stops a body. The opener raises it with `send --to thread:<child> --budget <n> "…"`, which adds n; read what was spent against what came back before granting.

`molt --fresh --why "<reason>"` starts a successor from main without inheriting the room or its label. The old room and unlanded work remain for review. The reason stays in the molt fact; the successor receives a fixed fresh-start message. Plain `molt` carries the room and label forward.

A Plan citation records an ask, not an answer. Boundaries also list `unanswered`
person letters cited by that thread; `threads` counts the same distinct letters,
including settled threads. Only a reply `re:` the letter with a `speech.part`
receipt on its originating channel, or that person's own hand note, clears it.
Resident notes, done dispositions, and loom status notices do not. This line informs the body; it
adds no stop block.
