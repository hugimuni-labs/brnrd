Status: in progress

# Letter lifecycle facts

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
Pending implementation and drives.

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
