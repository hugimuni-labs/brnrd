# Consolidation quality: compare the proposal with the owner's rewrite

Status: exploratory bench, 2026-10-04; the same replay repeated on Haiku 5.5
with a same-day Haiku 4.5 control, 2026-10-13. One historical input and one
live transport proof; no automated scorer or claim of general model quality.

This measures TELOS measurement 2 from the self-library design: a cheap limb
on a day's boundary reasons versus what the resident actually wrote. Passing
JSON, shrinking a file, and a successful Git commit measure transport. They
do not establish that memory survived. Curation owns that judgement.

## Replay protocol

1. Freeze the authored home **before** the reference curation. Export only
   `{at, act, why}` boundary rows for the working day, plus checkpoints and
   authored now.md. Preserve missing reasons as missing. Do not give the limb
   the owner's subsequent rewrite, its commit message, or an answer key.
2. Run `self consolidate` in an isolated copy. Retain input.json, configuration,
   timestamps, corpus hash, proposed full texts and rationale. Never apply the
   proposal to the reference home.
3. Read the reference commits and compare calls, evidence, losses and budget
   behavior. Check the actual wake selector and pitfall parser, not byte size
   alone. Curation may accept a subset or reject the whole proposal.
4. Report a profile with denominators and unavailable evidence, not one total
   score. Separate input coverage failures from model failures. Repeat with
   complete inputs before comparing models or changing defaults.

## Rubric

| Dimension | Measurement | Acceptance evidence |
| --- | --- | --- |
| Grounding | Supported new claims / all new claims | Each claim traces to an act/reason, checkpoint or existing authored text; no completed outcome inferred from an intention |
| Reference coverage | Reference changes addressed / observable reference changes | Operational course and durable lessons remain distinct; an unavailable event is excluded from the denominator and named |
| Preservation | Retained sampled calls, reasons, open forks and links / sampled source items | Compression preserves meaning; long form moved to a supplied, linked file rather than silently discarded |
| Budget usefulness | Standing bytes plus actual selector result | The important downstream entry is delivered under the same budget and self-inject order as the reference |
| Format integrity | Valid authored formats / changed files needing a parser | Pitfalls retain level-two headings and trigger lines; supplied links resolve; identity requires explicit curation |
| Curation cost | Acceptable files / proposed files, plus repairs needed | Full replacements and a truthful rationale make accept/reject reviewable without reconstructing omitted prose |

Hard rejection: invented outcome, lost authorization/open commitment, missing
promised destination, or damage to a format that controls what the next wake
can retrieve. Savings cannot compensate for any of these.

## Worked case: the 10-03 resident consolidation

The reference is the resident's own private home Git history, read-only:

- `99ad815a9`: rewrote the active course for daemon2 being live, and added the
  lesson that a sequential test could not reveal a resident blocking its own
  strands. The first live body supplied the missing evidence.
- `98cbd3271`: rewrote the notebook so the playbook could ride the wake again.
  The reference notebook is 4,479 bytes; its long form survives in
  `surprises.md` (15,002 bytes). The playbook remains 13,349 bytes. The commit
  identifies a 13.7 KB self-inject overflow and a 20,480-byte standing budget.

The replay used baseline `99ad815a9^` and the available 185 post-tool rows with
reasons from 2026-10-03 UTC through the reference cutoff 2026-10-04 00:11:39Z.
Corpus SHA-256:
`f523b39d7527b1c7ead9df1d49d5334c1a9c2b230070e9203261ef3de1629b38`.
The later live-seat boundary file was unavailable in this execution surface.
Therefore this is a **partial day's replay**, and it cannot score whether the
limb would discover the sequential-test lesson from the missing live incident.
The baseline course was supplied as now.md. No reference rewrite was supplied.

The installed spike's default real `claude -p --model haiku` limb produced
proposal `20261004T012325650242Z-330872bd`, with three replacements:

| File | Before | Proposed | Verdict |
| --- | ---: | ---: | --- |
| notebook.md | 20,348 B | 1,690 B | Shrinks, but no long-form preservation file supplied |
| playbook.md | 13,349 B | 4,901 B | Compression proposed beyond the reference's unchanged playbook; requires a separate semantic review |
| pitfalls.md | 289,712 B | 3,190 B | Reject: 185 lesson records become one spurious metadata record |

The rationale's first line was “Consolidate bloated pitfalls file and tighten
recency.” It said it would archive retired/old entries to
`pitfalls-archive.md`, but that file was absent from the proposal. The proposed
pitfalls were bullet summaries beneath one heading, with no per-lesson `trigger:`
lines. The real parser turns its introductory “trigger:, defect class…” text
into one spurious metadata record, replacing 185 properly indexed lessons.
A reader sees lesson prose; the matcher has lost the original trigger-bound
records. The reference's preservation mechanism was also absent: neither
`surprises.md` nor any equivalent long-form destination was supplied.

Profile:

| Dimension | Observed result |
| --- | --- |
| Reference coverage | 0/2 observable structural reference changes: no course replacement and no supplied long-form move. Sequential-test discovery is unscored because its incident input is missing |
| Preservation | 0/2 required destination files supplied: surprises equivalent and the archive promised in the rationale |
| Budget usefulness | Raw sizes fell; selector equivalence unproved. The replay used the library's default 16,384 content bytes / 10,922 standing bytes, not the resident's 20,480 standing bytes |
| Format integrity | 0/1 changed trigger-indexed files retain their lesson records (185 → one metadata record) |
| Grounding | No exhaustive claim audit performed; the missing archive already decides rejection |
| Curation cost | 0/3 accepted in this bench. At least restore trigger structure, supply the long forms and reassess semantic deletions before accepting any subset |

**Verdict: reject this historical proposal.** Haiku performed compression but
its rationale overstated preservation. The bench detects that discrepancy;
shipping consolidate is not evidence that this consolidator is fit to curate.
Future replay: recover the live-seat whys/checkpoints, equalize the standing
budget, and review a sampled claim inventory before grading recall or comparing
cores. The real home was never changed by this experiment.

## Live transport proof and its boundary

Separately, a fresh temp home and installed wheel ran a real Haiku coding
session implementing and testing a Unicode slug function. It produced 13
journal rows (three retained Bash reasons) and a real Stop checkpoint. The
session reached its deliberately bounded 16-turn cap after writing the files;
a same-model resume delivered the final report and Stop checkpoint. The first
real consolidation returned an empty proposal because the authored note was
already concise. That is a valid advisory result, not an acceptance receipt.
The actual session report was then preserved through MCP note and a second
real consolidation tested rewriting that overlapping authored material.
The handoff report carries its rationale and explicit one-file curation commit.

This proves session capture and the proposal/curation transport separately
from the historical proposal's quality. It does not prove retention policy,
full-day input completeness, cross-harness operation, identity judgement,
concurrent curation, or general consolidation fidelity. CLI identity gating,
stale input refusal and Git pin containment have focused contract tests.

## The same replay on Haiku 5.5, 2026-10-13

The 10-04 run left one row from one model. This section repeats it on the next
model and adds the control the first run lacked.

**What is identical.** Library head `0ad9019c`, installed as a wheel in a
scratch virtualenv. Baseline `99ad815a9^`, the same four files at the same
sizes (notebook 20,348 B, playbook 13,349 B, pitfalls 289,712 B with 185
records, plan 11,009 B). The same 185 journal rows: the rebuilt corpus hashes
to `f523b39d7527b1c7ead9df1d49d5334c1a9c2b230070e9203261ef3de1629b38`, the
value recorded above. The same prompt (440,008 bytes on stdin in the
recorded runs), the same flags, the library default budget of
16,384 bytes. The export script was recovered from the first run's session log
because its evidence directory no longer exists.

**What differs.** The `haiku` alias answered as `claude-haiku-5-5` on a probe a
minute before the runs; on 10-04 it was `claude-haiku-4-5-20251001`. The Claude
Code CLI is nine days newer, so the control below pins the old model by full
name under today's CLI and the same flags. The missing live-seat boundary file
is still missing: this remains a partial day, and the sequential-test lesson
stays unscored.

| Model, date | Runs | Runs proposing a rewrite | Files proposed | Runs with a hard rejection |
| --- | ---: | ---: | ---: | ---: |
| Haiku 4.5, 10-04 (original) | 1 | 1 | 3 | 1 |
| Haiku 4.5, 10-13 (control) | 4 | 4 | 7 | 3 |
| Haiku 5.5, 10-13 | 8 | 0 | 0 | 0 |

**Haiku 5.5: 0 of 8 runs changed a byte.** Five returned a valid empty
proposal with a rationale. Three answered in prose, which the library rejects
as "did not return proposal JSON"; the one whose text was recorded listed
three options and asked the caller to choose, in a pipe nobody reads. Each run
took 10 to 15 seconds.

**Haiku 4.5 control: 4 of 4 runs rewrote memory, in 52 to 73 seconds.**

| Run | Proposed | Verdict |
| --- | --- | --- |
| a | notebook 20,348 to 2,450 B; playbook 13,349 to 1,946 B | Reject: the playbook's three maintainer-set rule lines become zero; the notebook's cut text is sent to "implicit history (git log holds them)" |
| b | notebook to 3,139 B; plan to 3,735 B | Reject: the plan says "LIVE. Restart completed" and "daemon2 running". The journal's last row is the resident sending the restart commands |
| c | plan to 5,058 B | Reject: "The restart protocol executed", "First seat on daemon2 operating", a "three-week" rewrite and a run id with the month changed. None is in the input |
| d | notebook to 5,151 B; plan to 4,627 B | The plan is the only file of the seven that survives a first read: it says "restart unblocked" and stops there. The notebook cut has no long-form destination |

The restart in runs b and c did happen. The evidence for it was in the boundary
file this replay does not have, so both runs wrote a true sentence they could
not have known. The rubric's grounding row exists for exactly this: an outcome
inferred from an intention. None of the four touched pitfalls.md, which the
10-04 run destroyed; one sample of a model is one sample.

Profile for Haiku 5.5, eight runs:

| Dimension | Observed result |
| --- | --- |
| Reference coverage | 0/2, the same as Haiku 4.5: no course replacement, no long-form move. Sequential-test discovery unscored, same missing input |
| Preservation | Nothing to lose: 0 bytes changed, 185/185 pitfall records untouched |
| Budget usefulness | 0 bytes saved. The overflow the owner fixed that night is still there. Four of the six rationales read name the budget pressure and decline to act on it. The budget is still the library default, not the resident's 20,480 |
| Format integrity | No file changed, so nothing to parse. The one recorded prose answer names the `## ` and `trigger:` layout as a reason not to rewrite pitfalls in one pass |
| Grounding | 13 factual claims sampled from six rationales: 10 hold against the frozen files. Three do not: "playbook sits near its 20,480 B budget" (13,349 B, 65%); "the journal's claims conflict with the memory files", followed by two examples of agreement; cookie paths placed in the plan, which has none. Not an exhaustive audit |
| Curation cost | 0 files to review, 0 repairs, and 3/8 runs unusable by the pipeline as written |

**Verdict.** Haiku 4.5 does the job badly: five runs out of five across both
dates rewrite, and four of the five would have cost something a later wake
could not get back (a trigger index, the maintainer's rules, a plan stating an
unobserved outcome). Haiku 5.5 declines the job: eight of eight, with reasons
that mostly check out. Neither did what the owner did the same night, which
was to cut the notebook to 4,479 B and move 15,002 B to a linked file. For a
proposal-only pipeline a refusal is the cheaper failure, and it is still a
failure: `consolidate` on this default and this input now returns nothing.

Limits: one historical input, a partial day, n of 8 and 4, one reader and no
blind grading. A prompt that hands the limb one file and a named long-form
destination is untested; so is any larger model. Two defects in the bench
itself turned up: the library retains the input of a rejected limb answer and
drops the answer (two of the three prose replies are unrecorded), and the
default self-inject order puts an 11,009 B plan in front of a 10,922 B
standing budget.
