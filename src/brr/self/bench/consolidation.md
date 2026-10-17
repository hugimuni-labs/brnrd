# Consolidation quality: compare the proposal with the owner's rewrite

Status: exploratory bench, 2026-10-04. One historical replay and one live
transport proof; no automated scorer or claim of general model quality.

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
