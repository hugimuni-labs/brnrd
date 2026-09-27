# Fix #2119: Automatic resume when held:refill park releases on measured quota refill

## Status: Implemented and Ready for Testing

Implemented the core fix to `src/brr/daemon.py` at `_release_reset_holds_due()`.

## The Fix

When a `held:refill` hold is released by measured quota refill (above the refill floor):

1. **Lines 16766-16790**: Modified hold release logic:
   - Collect accumulated event IDs before processing
   - Undeferr accumulated events as before
   - **NEW**: If NO accumulated events exist, create synthetic "measured-refill" event in inbox
   - This synthetic event ensures dispatch trigger on next scan

2. **Key insight**: Previously, released seat with no accumulated events would idle until unrelated mail arrived. Now it immediately has a dispatch trigger.

3. **Pending resume claim**: Already-present `pending_resume.arm()` call ensures resumed dispatch will use native session (session ID, provider, conversation thread).

## Test File Created

`tests/test_seat_refill_resume.py` with two behavioral tests:

1. `test_held_refill_resume_creates_dispatch_event_when_no_accumulated()` 
   - Verifies synthetic "measured-refill" event created when hold released with no accumulated events
   - Verifies pending_resume claim armed for session resumption
   - Verifies synthetic event has correct metadata

2. `test_held_refill_resume_undefers_accumulated_events()`
   - Verifies accumulated events properly undeferred on release
   - Verifies no synthetic event created when accumulated events exist
   - Tests the original code path still works

## Changes Summary

- `src/brr/daemon.py`: Lines 16766-16790 in `_release_reset_holds_due()`
- `tests/test_seat_refill_resume.py`: NEW test file (2 tests)

## Still Needed

- Run test suite: `pytest tests/test_seat_refill_resume.py -v`
- Run full gate: `python scripts/gate.py --job backend --targeted`
- Rename branch to `brr/the-refill-that-resumes` (currently on `brr/run-260926-2358-u4s1`)
- Commit with proper message
- Stage report at `/tmp/brr-refill-fix-report.md` for submission
