# Loom bodies

Status: in progress. Branch `brr/loom-bodies`, seeded from fresh `origin/main` at `7c421ccd`.

| Item | State | Commit | Net runtime lines | Departure |
| --- | --- | --- | --- | --- |
| 1: folder bodies | done | 13ed5cb3 | -3 | None. Shell hooks live in the executable; the existing adapter-naming tests now invoke the folder. |
| 2: choice at birth | done | item 2 commit | +33 | README and immune already allow arbitrary scalar fields, including body; no parser weakening or duplicate validation added. |
| 3: Codex | pending | — | — | — |
| 4: usage | pending | — | — | — |

## Verification

Item 1: 16 existing jack/wall/live-self tests pass (16.15 s). An earlier run crossed a fixture edit and was interrupted during pytest old-temp cleanup; a fresh temp root ran clean.

Pending: the specified targeted gate, one live two-body test, package contents, and real Claude/Codex receipts.

## Unverified

Real provider hooks, holds and fuel have not yet been exercised.

Item 2: the new live two-body test passes (2.15 s); 8 existing wall/two-strand/live-self tests pass (19.54 s).

Deletion candidates covered by `test_body_folders.py::test_each_thread_answers_through_its_own_body_folder`: `test_live_self.py::test_the_self_decides_the_live_bodys_wake` (self wake reaches the body); `test_step1.py::test_a_letter_starts_a_body` if present (thread launch and letter settlement). Exact remaining names will be checked before closeout. Nothing deleted.
