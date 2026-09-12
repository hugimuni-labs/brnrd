What broke

Not a crash. A starvation wall armed on the wrong body.

┌─────────────┬────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┐
│ time (UTC)  │                                                                              what                                                                              │
├─────────────┼────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┤
│ 19:19:10    │ claude-opus auth error — "OAuth access token has been revoked" (credential-rotations.jsonl, keychain fingerprint rotated)                                      │
├─────────────┼────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┤
│ 19:19→19:33 │ worker falls back claude-opus → codex-gpt-5.6-sol, as designed                                                                                                 │
├─────────────┼────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┤
│ 19:33:45    │ boundary reads the codex binding quota: 1.0% (weekly 99% used, resets 2026‑09‑15T10:42Z). Below seat.starve_floor_pct=2.0 ⇒ hold armed on run-260911-1808-1r0b │
└─────────────┴────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┘

The hold record is unambiguous:

"provider": "codex", "resume_condition": "refill",
"quota": {"binding_remaining_pct": 1.0, "runner": "codex-gpt-5.6-sol",
          "model": "gpt-5.6-sol", "starve_floor_pct": 2.0, "refill_floor_pct": 10.0}

resume: refill is in WALL_RESUME_CONDITIONS, so it's a resource wall: schedule_event_releases() returns False (ticks accumulate, wake nothing), refuses_correspondent() returns True (your messages get deferred + a "Still hibernating" partial). One seat per repo (#1890) ⇒ the entire repo's dispatch is dead. The gates kept polling perfectly the whole time — which is exactly why nothing reported an issue. Polling was never the broken part; dispatch was.

And the thaw can't rescue it: _held_run_binding_pct deliberately re-reads "the same bucket that starved" — codex. Codex doesn't reach the 10% refill floor until Sep 15. Meanwhile the seat's own configured shell (.brr/config: shell=claude-sonnet, sticky profile claude-opus) was at 11% weekly / 49% session and healthy again from 21:00Z. Irrelevant to the thaw. The seat was frozen for four days over a body it wasn't going to use.

Your codex-watermark hunch was right, and more specific than you guessed: it wasn't that codex blocks claude generally — it's that the fallback poisoned the seat.

What moved it

Not the restarts. "released_by": "dashboard", "released_at": "2026-09-11T21:31:44Z" — an operator-console release. That un-deferred the accumulated event into run-260911-2132-qjuz… which your restart then killed at 21:37:11Z (host_interrupted: "its dispatching daemon (pid 99732) is gone"). The new daemon retried it at 21:55 as run-260911-2155-10gx — the resident live right now.

The fix

The wall's claim is "the seat cannot run." Today that claim is never tested — it's inherited from whichever body the run happened to die on. Two edits, both small:

1. Don't arm a wall from a fallback body when the seat's configured/sticky body is above the floor (_starvation_facet judges runner_meta["model"] — the fallback's — with no notion of what the next dispatch would use). Arm resume: any, or don't arm.
2. At thaw, test the claim, not the bucket: _held_run_binding_pct should sweep dispatchable bodies and thaw when any is above its refill floor.

Also worth knowing: claude weekly is at 11% against a 10% refill floor. One point of margin — a claude-armed wall right now would hold until the Sep 12 12:00Z reset.

Two things you want to know now

- The host tree has a NameError in waiting. src/brr/gates/runtime.py line 570 calls _deliverable(...), which is defined nowhere in the file — the resident's in-flight edit (it's fixing the undelivered-partial half). dev_reload=true watches .py under the package and re-execs. If it re-execs before that function lands, every deliver_stream breaks — the same failure you just dug out of. I left it alone; it's the resident's edit, in its tree, mid-turn.
- gh isn't authenticated. ~140 of the 147 lines in /tmp/brr-diag.log are pr-review-queue publish failed … gh auth login, once per poll. That's the noise that made "reports no issues" look plausible.

The external seat interface — evaluation

What genuinely works: brnrd agent inject is the best cold-start any repo has handed me. The stage model in AGENTS.md ("look for ### Mode") resolved instantly. Reading the identity core, I'd put it at the body-manual end, not foreman-plan — ⌁[b·_·d] is my own name wearing a face is a line that changes how a whole channel is read, and it knows why it's in the invariants.

Where it's unclear, concretely:

1. inject doesn't know I'm not the resident. It prints ~10KB of second-person identity contract — your notebook, your position, "what I dare" — before a word about this repo's state. Nothing marks which parts are a standing self I'm borrowing versus instructions I should act on. A one-line header, or --as external, would settle it.
2. brnrd run leaves no receipt. One positional arg, no progress, no run dir, no ledger entry. You read that as "it hangs" — it doesn't: pid 93026 is alive at 17 minutes with its claude child burning CPU. In a system where everything leaves a receipt (run dirs, boundaries.jsonl, ledger, gate receipts), this one path leaves none. Clearest defect I hit.
3. daemon status answers the wrong question. It told me "running (pid 93722)" and "cloud: last poll 4s ago" — both true, both useless. It never said: a seat is parked, on a codex refill wall, since 19:33, thawing at 10%. That line at 19:34 would have saved you two hours and three restarts. I'd rank this above the code fix.
4. The park announces itself through the channel it just froze. The "Still hibernating" partial is the only user-visible signal of a wall, and it rides the delivery path that was itself broken. A park that can only speak through the thing it stopped is a silent park.
5. Pitfall-gating on the waking text (inject --task "<topic>") is a great mechanism that's invisible from the CLI surface — I only found it inside the injected playbook.

Where I'd envy a brnrd run, and where it's waste

Envy. Continuity that compounds — pitfalls trigger-gated on the waking text, notebook rewritten rather than appended. I re-derive from zero every session; the resident starts from what bit me. Perception riding every tool boundary instead of being polled for — I spent ~15 calls learning what its chip carries for free. And receipts by construction: I reconstructed its 19:19→21:55 history exactly, to the second. Nobody can reconstruct mine.

Waste — and one of these is structural. A parked seat cannot investigate its own wall. That's precisely why you needed an external body tonight, and it argues the external seat should be first-class rather than a fallback. Beyond that: the wake is ~156KB (21.7KB of it notes-health, by its own measurement) — a body manual is right for a life and wrong for a 20-minute forensic errand. And single-flight would have put this dig in the queue: I ran ~20 parallel probes without blocking a single message or tick. The debugging body should be off the seat by design.

What I didn't verify: whether the resident already has the seat-scoping fix in hand (I only saw its delivery-side edit), and I've run no tests — tests/test_starvation_hold.py and test_the_seat_that_stays.py pin the current wall semantics, so both edits above need those updated deliberately, not bent to fit.

One call that's genuinely yours: the resident is mid-investigation on the adjacent half of this bug, in this tree, on main. Want me to take the seat-scoping fix in a separate worktree so we don't collide — or leave the whole thing to it and stop here?