# Receipts: every line the trailer shows, to its source

What a visitor reads on screen, traced to the ledger (`ledger/facts/ae66/`: `loom.jsonl` for his letters and the loom's notices, `<strand>.jsonl` for the resident's replies) or to GitHub. Times are UTC, as the ledger stores them; the on-screen stamps show HH:MM of the same.

## chat

The phone shots (`src/gen/chat/`, data in `script.json`) are drawn, not recorded: one stylised chat, no messenger's skin. Every message below is on screen as written, except the edits listed under it.

| shot | on screen | from | source | at (UTC) |
|---|---|---|---|---|
| a1-ask | "Hey we still need brnrd#2606 don't we? Could you please rebase it? There are conflicts. Lmk if somethings changed, or if wasn't it merged for some reason" + PR card | him | letter 10416 (`letter:cloud:telegram:155783668:10416`) | 2026-11-06 21:56:36 |
| a1-ask | "Yes, #2606 is still wanted and was never merged; it just sat while main moved on. The wiki thread is rebasing it now." | resident | `s-ae66-x5fukd/wrt3a` | 2026-11-06 22:00:48 |
| a1-reply (spare) | "Also: the change that puts the closest notes in front of me when I wake is updated, all checks green, ready for you: brnrd#2606" + PR card | resident | `s-ae66-n5nv7e/yun90`, last paragraph | 2026-11-06 22:15:21 |
| a1-reply, a1-nice, p2-agree | delivered card: "brnrd#2606 … rebased on main …" / "Reply ok to accept, or say what is off." | resident | `s-ae66-79qfzj/yy6ug` | 2026-11-06 22:18:46 |
| a1-reply, a1-nice, p2-agree | the delivered card's Merged pill, "22:18" (yy6ug's second line: "brnrd#2606 merged") | GitHub | `gh pr view 2606 --json mergedAt`: 2026-11-06T22:18:13Z | 2026-11-06 22:18:13 |
| a1-nice | "Nice 👌", quoting the delivered card | him | letter 10435, `re: s-ae66-79qfzj/yy6ug` | 2026-11-06 22:30:09 |
| a1-loaded (spare) | "Thanks. It's loaded now" | resident | `s-ae66-79qfzj/mvsel`, first sentence cut at its parenthesis | 2026-11-06 22:30:32 |
| p4-signoff | "Okay, thanks, I am signing off for today" | him | letter 10324, first line | 2026-11-06 00:15:12 |
| p3-fuel | "Out of claude session fuel (1% left) until about 21:00." / "Your letter is kept on thread inbox and is answered when it refills." / options wake, wake codex, wake grok; the gauge drains 3 % → 1 % | the loom | `loom/parked-043ca41af7` (the first of five identical notices that minute) | 2026-11-05 18:19:19 |
| p2-agree | a thumb taps ok on the delivered card; it turns ACCEPTED, "ok ✓" | staged | none: the ledger holds no agree on w-hxx3euxqdo1k; his answer to that card was "Nice 👌" (10435) | n/a |
| a3-human | "How does it feel to feel time? 😅" | him | letter 10426 | 2026-11-06 22:07:40 |
| a3-human | "Honestly, not yet 😅" | resident | `s-ae66-x5fukd/3be0e`, first two words of its first sentence | 2026-11-06 22:08:14 |
| a3-human | corner clock 22:08 → 22:15 over the 6-frame dip | | the gap between 3be0e and yun90 | |
| a3-human | "Now you can answer that? 🥹" | him | letter 10432 | 2026-11-06 22:14:33 |
| a3-human | "Now I can 🙂 It's less like feeling time than like finally wearing a watch." | resident | `s-ae66-n5nv7e/yun90`, first two sentences | 2026-11-06 22:15:21 |

Edits:

- 10416: "Hew" → "Hey" (typo); the PR URL `https://github.com/hugimuni-labs/brnrd/pull/2606` is drawn as the token `brnrd#2606` plus a card. Straight apostrophes in the ledger are drawn as typographic ones throughout.
- The PR card's title is the delivered card's name for the PR, "topics in the wake, recall and related:" (yy6ug); GitHub's title is "topics: the wake shows the closest heads; recall, related: and INDEX name topics first; the trace keeps the self path". The card's state is the PR's at that message: "Open · conflicts" at 21:56 (his own words in 10416), "Open · checks green" at 22:15 (yun90's words), Merged at 22:18:13 (on the delivered card, which arrived 33 s after the merge).
- yy6ug is four lines; the card keeps its PR, "rebased on main", and its last line. Its first line in full: "delivered &w-hxx3euxqdo1k: brnrd#2606 (topics in the wake, recall and related:) lands: rebased on main, checked against threads/wiki/design.md F4+F5, with what main already carries named".
- wrt3a and 3be0e are cut to their first sentences; yun90 is shown as its first two sentences (a3-human) and its last paragraph (a1-reply), never whole.
- Order: in the ledger 10421 (21:57:47, the same ask restated) follows 10416; it is not drawn. In a3-human, x5fukd's 22:10 reply about reloading and 10426's neighbours are cut by the dip.
- p2-agree is the one staged shot: the gesture stands for "you judge", the ledger does not hold it.

## terminal

Compositions in `src/gen/term/index.jsx`, data from `gen_term.py` at every render (`python3 gen_term.py` prints it).

| shot | on screen | source | drawn or staged |
|---|---|---|---|
| t1-memdir (pain 1) | 65 rows `-rw-------  size  date  <uuid>.jsonl`, the title `-Users-gurio-Source-Projects-brnrd — zsh` | `~/.claude/projects/-Users-gurio-Source-Projects-brnrd/`, every file's real name, size and mtime read at render (65 files, 83,975,605 bytes on 2026-11-06, 84 MB; the booth-demo notes said 88 MB) | the permission column is written `-rw-------` for every row, not read; the prompt is `brnrd $`, a short prompt, not his zsh theme; the zoom lands on the 8th UUID from the end, any one would do |
| self-log (answer 1) | the self's `git log --oneline`, 140 lines, landing on `5b463a6c wiki: #2606 merged (4714e0ea), w-hxx3euxqdo1k delivered`; a card `threads/wiki/README.md @ 5b463a6c` with two sentences | `git -C <self> log --oneline -140` at render; the card is `git show 5b463a6c:threads/wiki/README.md`, the paragraph naming `#2606 merged`, its first two sentences | the title bar `brnrd-self — git log --oneline`; the log scrolls from old to new (git prints new first), the scroll is the camera |
| t2-compact (pain 2) | `> /compact` typed; `✻ Compacting conversation…`; `Conversation compacted · 219,854 tokens → one summary`; `This session is being continued from a previous conversation that ran out of context.` | the newest `compact_boundary` record in brnrd's Claude Code sessions: `7621a63f-….jsonl`, 2026-10-27T20:36:55Z, `content` "Conversation compacted", `compactMetadata.preTokens` 219854; the summary sentence is the first sentence of the message after it | that compaction was **automatic** (`trigger: auto`); the shot types `/compact`, the manual way to the same line. The transcript above it is abstract bars, not words. `Compacting conversation…` and the glyphs are Claude Code's look from memory, not read from a file; the `· N tokens → one summary` tail is ours, the number is the record's |
| t4-repos (pain 4) | five windows titled `brnrd — -zsh`, `hugimuni — -zsh`, `animations — -zsh`, `BeCenter — -zsh`, `remotion — -zsh`, prompts `~/Source/Projects/<name> $` | his pick, letter 10327, as `five-terminals.sh` takes it; `cut.json` `repos` | `remotion` has no folder in `~/Source/Projects` on this iMac (the fork shots.md asked for was never made); the title format is the one five-terminals.sh tested ("brnrd — brnrd — -zsh"), shortened |
| g6-readme (answer 6) | `hugimuni-labs/brnrd-self / threads/wiki/README.md · 5b463a6c`, `wish w-wqzggf89r72v` and its line, the status sentences, `PR github.com/hugimuni-labs/brnrd/pull/2606` underlined | `git show 5b463a6c:threads/wiki/README.md`: the frontmatter's wish line and the paragraph naming `#2606 merged` | drawn in GitHub's dark colours, not captured: brnrd-self is private, so a headless browser sees a 404. The PR link line is ours, its URL is #2606's |

## edit

`cut.json` is the edit; `cut.py` reads the data and renders. Times in UTC.

| what | on screen | source |
|---|---|---|
| act 1, the ask | his 10416, 2026-11-06T21:56:36Z: "Hew we still need …/pull/2606 don’t we? Could you please rebase it? There are conflicts…" | ledger `letter:cloud:telegram:155783668:10416` (booth-chat draws it) |
| act 1, the board at work | the wiki column at 22:12:00Z: s-ae66-79qfzj's tape, the card "Rebase brnrd#2606 (loom/topics-read, CONFLICTING)…" at 21:59 | `capture.py` recipe `a1-taken`, `board_at.py --at 2026-11-06T22:12:00Z`; the top bar's clock is patched to the moment, the fuel line hidden |
| act 1, the PR | brnrd#2606's page, the Merged pill, "Gurio merged 2 commits into main" | `capture.py` recipe `g1-pr`, captured live; "1 hour ago" on it is the capture's hour, not the merge's |
| act 1, delivered | the wiki column at 22:19:00Z, the delivered card `s-ae66-79qfzj/yy6ug` (22:18:46Z) and the verdict box | `capture.py` recipe `a1-delivered` |
| act 1, the strip | `21:49 asked · 22:15 green · 22:18 merged` | read at render by `cut.py stamps()`: asked is `cut.json` `act1.asked_on_phone`, 21:49Z, the send time his phone shows (his screenshot, 22:49 local); the ledger has 10416 at 21:56:36Z, the loom's receipt after the lockout; `s-ae66-n5nv7e/yun90`'s `at` (22:15:21Z, "all checks green, ready for you"); `gh pr view 2606 --json mergedAt` (22:18:13Z). The brief said "22:19 green · 22:2x merged": 22:19 is the delivered card, a minute after the merge |
| act 3, the human beat | 10432 "How does it feel to feel time? 😅" and yun90's first sentence | booth-chat; yun90 at 22:15:21Z carries both the answer and the "ready for you" of act 1 |
| the numbers | commits since 2026-03-28, the resident's non-merge commits and share, merged PRs | `cut.py numbers()`, git and `gh api graphql` at render (definitions in threads/booth-demo of the self) |
| the checks row | #2606's checks, each ✓ or ✗ | `gh pr checks 2606 --json name,state` at render |
| timing | booth 48.00 s, readme 45.00 s | pain 6 is his real verdict, a1-nice (10435 'Nice 👌'), not a staged agree tap: p2-agree is dropped (the ledger has no agree on w-hxx3euxqdo1k), a1-nice takes its 1 s and half a second of g6-readme (2.5 → 2.0); act 2's two beats of air (g6-readme 3.5 → 2.5), the numbers card (5 → 4) and the studio push (4 → 3) give the human beat its 3 s; the brief named the first two, which make 2 |
