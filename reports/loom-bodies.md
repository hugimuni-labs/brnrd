# Loom bodies

Status: ready for parent review. Four items implemented, targeted gate green, both real bodies answered. Claude fuel remains unknown. Branch `brr/loom-bodies`, seeded from freshly fetched `origin/main` at `7c421ccd`. No PR opened.

| Item | State | Commit | Net runtime lines | Tests touched |
| --- | --- | --- | ---: | --- |
| 1: fake/Claude folder bodies and packaging | done | 12586e85 | -3 | Existing `test_jack` adapter fixture calls the folder; `test_stuck_states` adapter fixture drops a home folder. No assertions weakened. |
| 2: choice at birth, persisted restart | done | 289fb9d0 | +34 | One new live test, `test_body_folders.py`. |
| 3: Codex | done | f1e8d4b7 | +17 | None. |
| 4: usage wrappers/cache/boundary fuel | done | this report's commit | +88 | None; no resolver/cache unit tests added. |

Runtime lines include all files under `src/brr/loom/runtime/`, including the body folders and their data files. Total: +136. Folder extraction shrank the runtime; choice and nonblocking usage add code. The overall “fewer runtime lines” aim was not achieved. No unrelated cuts were made to offset it.

## What changed and what the spec got wrong

- The home body folder wins over the bundled folder. Each launches `run <room> <core>` with the four required variables; native shell launch code stays in the folder. Fake uses the same launch path, retaining its existing policy-file admission rule.
- Birth order: the first owed letter's `body:` line, README frontmatter `body:`, flat home config `body`, then the flags. `body.started` carries `shell/core`; death, wall and molt read the first start for the strand. A later letter does not change that choice.
- README parsing and `immune` already accept arbitrary scalar fields. They accept `body:` without modification. Selection reads only its field; it adds no new README validation. An initial whole-README parser incorrectly changed routing admission and was removed, preserving all existing assertions.
- Codex has native hooks, but the existing renderer uses `continue:false/stopReason` for Stop, unlike Claude's continuation block. The body declares `jack: hand` and receives the start/post/stop instruction. No native Codex Stop hold is claimed or proved. Its invocation ignores the host's user config so daemon hooks do not leak into the scratch room.
- Usage wraps the existing provider collectors. A five-second process-group deadline bounds them; a daemon thread keeps the tick unblocked. Readings and unknown attempts are cached by home/shell for five minutes. Missing, failed and stale readings produce no fuel. Codex window names come from their reported duration, not their primary/secondary position.
- The five-second usage contract does not guarantee a Claude reading here: both bounded Claude attempts returned unknown. The real body answered; its fuel proof is incomplete, as recorded below. No number was borrowed or invented.

## Verification

- One new live-path test: `tests/loom/test_body_folders.py::test_each_thread_answers_through_its_own_body_folder`. A real `run()` loop and git self answer on bundled fake and a home-supplied `local/small` body. It passed in 2.15 s.
- Item 1: 16 existing jack/wall/live-self tests passed in 16.15 s. Item 2: 8 existing wall/two-strand/live-self tests passed in 19.54 s. After the fake-policy and wake-placement corrections, all three previously failing fuse/failover tests plus the new live test and existing live-self tests passed: 8 passed in 29.06 s.
- Final gate: **181 passed, 1 skipped, 4 xfailed in 286.78 s**, exit 0 — `python -m pytest -q -p no:cacheprovider tests/loom tests/test_loom_*.py`. This ran against the unchanged final source tree. The full suite was not run. Earlier runs exposed and led to repair of README admission and fake-policy source/timing changes; no assertions were weakened.
- The silent-body strict-xfail and new two-body test also ran in isolation after the snapshot repair: 1 passed / 1 xfailed in 5.96 s.
- Wheel built with isolated build dependencies (`pip wheel --no-deps`); all 11 body files shipped. ZIP modes preserve executable `run` and `usage` as `100755`. The initial non-isolated build could not import `setuptools.build_meta`; the isolated build succeeded. No editable install or shared git config change.
- No test deleted. Partial consolidation candidate for the new live test: `tests/loom/test_live_self.py::test_the_self_decides_the_live_bodys_wake` (self wake delivered to an answering body). Its custom-recipe override assertion still needs to move before deletion; no existing test is wholly superseded. The new test does not supersede hook protocol, fuse, wall, molt, speech-once or labeling assertions.

## Real provider proof

Each used a separate scratch home, a real git self, a real `run()` loop, and one injected letter. The channel effect was local fake so no connector or person was contacted; the bodies were real providers. Letter and reply facts follow.

### Claude haiku

Letter:

```json
{
  "id": "p-tester/4awvm",
  "to": "thread:proof",
  "body": "Reply to this letter through the loom port with EXACT text \"claude live proof\". Run python -m brr.loom.runtime send --to channel:fake --re <this-letter-id> \"claude live proof\". Use the id from the owed letters. Do not commit, do not use external connectors. Call the jack as your wake directs and end after answering. This is a scratch-home proof."
}
```

Reply fact:

```json
{
  "v": 1,
  "id": "s-058b-igwl6k/gca6c",
  "kind": "letter",
  "by": "strand:s-058b-igwl6k",
  "at": "2026-10-10T20:15:48.759854+00:00",
  "data": {
    "body": "claude live proof",
    "from": "s-058b-igwl6k",
    "gen": 1,
    "id": "s-058b-igwl6k/gca6c",
    "re": "p-tester/4awvm",
    "router_gen": 1,
    "to": "channel:fake"
  },
  "hlc": [
    1791663348760,
    0,
    "058b"
  ],
  "after": []
}
```

Boundary:

```text
status: strand s-058b-igwl6k · gen 1 · thread proof
```

The real Claude process exited 0, released its thread, and answered `claude live proof`. Fuel stopped at the usage step: the executable exited **1** with empty stdout/stderr; the loom cached `{"windows": []}`. That is the error receipt verbatim (no text emitted), not a provider wall. The bounded `/usage` scrape produced no reading; its underlying cause is unverified.

### Codex gpt-5.6-luna

Letter:

```json
{
  "id": "p-tester/ibxef",
  "to": "thread:proof",
  "body": "Reply to this letter through the loom port with EXACT text \"codex live proof\". Run python -m brr.loom.runtime send --to channel:fake --re <this-letter-id> \"codex live proof\". Use the id from the owed letters. Do not commit, do not use external connectors. Call the jack as your wake directs and end after answering. This is a scratch-home proof."
}
```

Reply fact:

```json
{
  "v": 1,
  "id": "s-f4f7-7x9i52/aiqp5",
  "kind": "letter",
  "by": "strand:s-f4f7-7x9i52",
  "at": "2026-10-10T20:17:16.344312+00:00",
  "data": {
    "body": "codex live proof",
    "from": "s-f4f7-7x9i52",
    "gen": 1,
    "id": "s-f4f7-7x9i52/aiqp5",
    "re": "p-tester/ibxef",
    "router_gen": 1,
    "to": "channel:fake"
  },
  "hlc": [
    1791663436344,
    0,
    "f4f7"
  ],
  "after": []
}
```

Boundary:

```text
status: strand s-f4f7-7x9i52 · gen 1 · thread proof · fuel codex session 89% week 39%
```

The local channel received `codex live proof` once. The hand jack returned the boundary above during the actual Codex session.

## Unverified and remaining edges

- Claude fuel within the five-second deadline: unknown. Settling it requires inspecting the actual bounded PTY screen or changing the existing collector; this branch preserves the contract's unknown behavior.
- Native Codex Stop continuation: not exercised; hand mode deliberately makes no hold claim.
- Existing live homes with pre-change `body.started` facts lack persisted `shell/core`. This change targets new births; a legacy strand cannot infer an unrecorded core. No live home or daemon was modified.
- Packaging was inspected in a wheel; no installation into the operator's environment.

Final review: the hand-jack instructions now enter the self-owned wake through `loom_part`, so its recipe and byte budget apply to them. They are not appended after the self has rendered the wake.

Fake extraction correction: the prior launcher passed `_policy(home, thread)` from the live home, not from the self clone. The loom snapshots that same home policy into the room before publishing shown, and the fake folder reads that snapshot. Reading the clone had silently frozen policy changes and broke fuse reset/failover; the original source, not changed assertions, determined this repair.

The snapshot is taken before the shown fact, matching the old launcher argument capture. This preserves the meaning of a boundary: a policy changed in response to shown cannot retroactively alter the body being launched. The narrow check crossed history cleanup and was interrupted; only the final unchanged-tree gate above is treated as final evidence.

## Diff receipt

`git diff --stat origin/main...HEAD` contains the four implementation commits, the one new live test, two adapter fixture updates, package-data change and this report. Runtime net per item is computed from `git show --numstat <commit> -- src/brr/loom/runtime`; total +136.
