Status: in progress

# Letter lifecycle facts

Continuation: run-261012-1456-afa8 is reviewing a6520cae and completing proof/gate/PR; inherited commits will not be rewritten.

Base: e5d4ec2a; branch: brr/loom-letter-facts.

## Baseline failures
Real codex/gpt-6-luna drove a person reply, an opened worker reply and settlement, and a failed wake start. Test identities only.

```
F..                                                                      [100%]
=================================== FAILURES ===================================
___________ test_every_prefix_of_a_real_drive_has_one_owner[person] ____________

path = PosixPath('/Users/gurio/Source/Projects/brnrd/.brr/worktrees/run-261012-1427-bad6/tests/loom/fixtures/letter-facts/person.json')

    @pytest.mark.parametrize('path', sorted(RECORDINGS.glob('*.json')), ids=lambda p: p.stem)
    def test_every_prefix_of_a_real_drive_has_one_owner(path):
        record = json.loads(path.read_text())
        assert record['body'].startswith(('codex/', 'grok/'))
        problems = violations([Fact.from_row(row) for row in record['facts']], record['main'])
>       assert not problems, '\n'.join(f'prefix {p}: {letter}: {why}' for p, letter, why in problems)
E       AssertionError: prefix 10: p-test/q6lkr: retired before send receipt; channel still owes reply
E       assert not [(10, 'p-test/q6lkr', 'retired before send receipt; channel still owes reply')]

tests/loom/test_letter_facts_replay.py:15: AssertionError
=========================== short test summary info ============================
FAILED tests/loom/test_letter_facts_replay.py::test_every_prefix_of_a_real_drive_has_one_owner[person]
1 failed, 2 passed in 0.43s

```

## Commits
Step 1: production prefix check and CLI, real-drive recorder and three unedited recorded ledgers, replay test (expected red).

## Contract evidence
Review findings (before continuation edits):
- Atomic climb/refusal dispositions mark the old draft done, then legacy bidirectional `stands` handling marks the embedded successor done too. The new notice is never shown. Fix pairing to distinguish explicit handling from replacement disposition; prove through live refusal and climb.
- The person's second failed attempt now belongs to the operator per the contract; the inherited old lifecycle test still expects an automatic failure message. Preserve two starts/no third and test visible operator debt + hand note instead.
- Bounce-chain, settlement disposal, refused receipt, ended/drained and main-only selection need live evidence beyond the inherited replay.

| Rule | Proof |
| --- | --- |
| Loom owner waiting/start | recorded person ledger before start; `test_routing_ignores_working_checkout_status_for_and_inventory` |
| Body owner | `test_unsent_reply_returns_to_its_body_with_visible_refusal` (refused and stale variants, same live body sees the notice and original) |
| Channel owner until receipt | recorded person prefix at accepted reply, clean CLI replay; fresh real codex drive sends RECEIVED once |
| Opener owner | `test_unhandled_bounces_walk_to_top_then_operator`; `test_climb_waits_for_ended_and_drained_then_opener_reads_it` |
| Operator owner + visible + person note | `test_two_failed_starts_are_visible_to_operator_and_a_note_retires_them`; `test_persons_letter_gets_two_starts_then_visible_operator_debt` |
| settled cause | fresh codex person drive: one ended row, settled, code 0 |
| molted cause | `test_molt_mid_work_lands_readme_and_inherits_unanswered_letter` (test_molt.py; molted cause asserted, 10-test repair gate passed) |
| idle cause | warm follow-up/live settled-worker tests; `test_settling_worker_disposes_its_reply_without_an_extra_note` (final gate pending) |
| paced cause, preserves owed and no failed count | `test_owing_worker_is_told_once_holds_twice_then_low_fuel_preserves_and_parks_the_ask` (test_holding.py; paced cause asserted, 10-test repair gate passed) |
| failed cause, only failed attempts count | two-failed-start live regression; person gets exactly two starts and no third |
| attempt.ended atomically carries dispositions/successors | live unhandled bounce chain and failure-to-operator paths |
| port.drained gates climb independently of ended | live closed-worker drive pauses exactly between ended and drained; no climb before release, opener reads after |
| Accepted reply does not retire person's letter | raw recorded person ledger prefix check; refusal/stale live regression returns original to current body |
| Bounce walks opener chain to operator | `test_unhandled_bounces_walk_to_top_then_operator`: worker → middle → top → operator note |
| Settling disposes worker reply on opener | `test_settling_worker_disposes_its_reply_without_an_extra_note`: opener settles worker, reply disappears from next boundary, no note was sent for it |
| Past failed-attempt limit owed visibly to operator | live two-failed-start and relay-person tests |
| Status, for and inventory from main | `test_routing_ignores_working_checkout_status_for_and_inventory`: uncommitted settled/for changes ignored, untracked ghost excluded |
| owed/actionable/unrunnable_ids deleted | source search: no definitions/imports; readers use letter_owners/letters_for |
| Every recorded real ledger prefix | CLI replay: 8 failed-start + 15 person + 28 worker prefixes clean; production replay test 3 passed |

New live evidence: `test_letter_owners_live.py`: **7 passed in 47.06s**. Full gate is still running.

## Departures
None.

## Could not verify
Replay, real-drive completion and gate have not run.

## Baseline real-drive output
```
2026-10-12T14:30:09.023994+00:00 start s-ffce-ri7eb9 thread inbox gen 1 pid 91671 codex/gpt-6-luna
2026-10-12T14:30:35.629936+00:00 exit s-ffce-ri7eb9 code 0
person home: /var/folders/r5/zx7bdmwd55x1djcrdvl6n92c0000gp/T/loom-letter-person-pjq6qr8f rows: 15 ends: 1
letter router lease body.pending shown body.started router.renewed shown router.renewed letter speech thread.closed router.renewed body.exited released
2026-10-12T14:30:37.935540+00:00 start s-5627-n4cuwm thread inbox gen 1 pid 93588 codex/gpt-6-luna
2026-10-12T14:30:54.255577+00:00 start s-5627-7tzcc8 thread worker gen 1 pid 94891 codex/gpt-6-luna
2026-10-12T14:31:06.451451+00:00 exit s-5627-7tzcc8 code 0
2026-10-12T14:31:25.242832+00:00 exit s-5627-n4cuwm code 0
worker home: /var/folders/r5/zx7bdmwd55x1djcrdvl6n92c0000gp/T/loom-letter-worker-2sg3npth rows: 28 ends: 2
letter router lease body.pending shown body.started router.renewed shown router.renewed letter lease shown body.started note router.renewed letter shown body.exited released router.renewed thread.closed shown router.renewed thread.closed attention note body.exited released
2026-10-12T14:31:27.688122+00:00 start failed s-be77-sz5yp9: SelfError: wake exited 3: deliberate-start-failure
2026-10-12T14:31:27.690501+00:00 wasted p-test/bxkab on thread inbox: nobody to tell
failed-start home: /var/folders/r5/zx7bdmwd55x1djcrdvl6n92c0000gp/T/loom-letter-failed-start-itef4eym rows: 8 ends: 1
letter router lease body.pending body.exited attention note released

```

## Step 2
One attempt.ended row carries the observed cause, code, ids and all failure dispositions, including embedded bounce letters. port.drained follows the final ingestion; accepted channel replies retire their inbound letter only on aggregate speech:sent. Refusal and stale/maybe-sent dispositions embed their notices in one append.

```
.............                                                            [100%]
13 passed in 0.39s

```

## Step 3
The pure letter_owners table derives owner, reason, action and reply address from accepted facts and a pinned main inventory. Router route/climb, wake and boundary mail, thread counts and attention read it. owed, actionable and unrunnable_ids are deleted. Operator debts are listed by attention and threads, and attention --note ID --why TEXT writes a person note. Tests of the deleted legacy body.died selector were deleted; live failed-start coverage replaces them.

```
.....                                                                    [100%]
5 passed in 20.44s

```


## Continuation: real drive and replay outputs

Recorded ledgers reconstructed in scratch homes with their recorded main snapshots (rows/ids/HLC unchanged), then run through the installed CLI dispatch with this worktree on PYTHONPATH:

```
failed-start: 8 prefixes
letter ownership clean at every ledger prefix
person: 15 prefixes
letter ownership clean at every ledger prefix
worker: 28 prefixes
letter ownership clean at every ledger prefix
```

Fresh real codex/gpt-6-luna, one person's letter, one reply, settled thread:

```
2026-10-12T15:03:23.921440+00:00 start s-eb9f-olxg62 thread inbox gen 1 pid 31555 codex/gpt-6-luna
2026-10-12T15:03:46.257424+00:00 exit s-eb9f-olxg62 code 0
person home: /var/folders/r5/zx7bdmwd55x1djcrdvl6n92c0000gp/T/loom-letter-person-cs2ivjny rows: 17 ends: 2
letter router lease body.pending shown body.started router.renewed shown shown letter speech router.renewed thread.closed body.exited attempt.ended port.drained released
letter ownership clean at every ledger prefix
```

No external chat was used: the real model's reply reached the fake channel's durable send sink. This proves the live body/port/router/send receipt path; actual Telegram delivery is outside this drive.


## Gate failure accounting

The first exploratory whole run was interrupted after 9 failed/67 passed because it was still exercising old imported code while fixes were underway; it is not a gate receipt. The completed pre-final run was **7 failed, 158 passed, 1 skipped, 3 xfailed** (484.12s). Each failure:

| Failure | Cause and resolution |
| --- | --- |
| usage, codex + grok | First boundary omitted the gauge already acquired for start; only later refresh added it. Start now passes cached fuel into the initial boundary. Both real captured gauge tests passed in the 10-test repair gate. |
| two_strands | Test searched raw rows for a bounce now embedded atomically in ended, and assumed the original author process must still be alive. It now searches accepted projection and checks exposure to a reader of the author's thread. Product delivery remains required; passed in repair gate. |
| restart recovery | Fixed 2s deadline expired after recovery had started its body; note files were waiting in port/out. Outcome and assertions unchanged; bounded 8s allows actual cold startup and ingestion. |
| relay saved cursor | Fixed 2s deadline included the explicit 1s retry and whole cold start. Outcome unchanged; allow 8s. |
| uncertain speech | Test assumed another successful message had already created the fake send sink. Wait for that sink before checking the failed key was never resent. |
| absent inbox owner (expected xfail) | Fixture edited `for:` only in the working tree and awaited the removed attention id. Land the ownership metadata; wait for visible operator debt. The still-unimplemented remote refusal remains an explicit expected failure. |

Earlier fixtures also constructed invalid `# # Work` headlines, or lease rows naming a nonexistent router grant; these now seed valid landed READMEs and actual router grants. Deleted body.died checks were replaced with meaningful failed-attempt exclusions. Legacy function tests were deleted in the inherited third commit because nothing ever writes body.died; live two-failed-start paths replace them.

Repair gate: `PYTHONPATH=src pytest -q tests/loom/test_usage.py tests/loom/test_holding.py tests/loom/test_two_strands.py tests/loom/test_molt.py`: **10 passed in 50.03s**.

Final whole gate is running after these repairs. No non-loom test file has been changed.
