"""Tests for the runner-profile data model and deterministic selector.

These cover the foundation slice (``kb/design-runner-cores.md`` step 1): the
profile schema, the legacy implicit shim, available-profile filtering, and the
conservative cost-aware selector. Dispatch wiring is a later slice, so there
are no daemon/subprocess paths here.
"""



def test_fallback_from_an_unclassed_runner_does_not_escalate_to_strong():
    """The merge-time guard on the nearest-class key (2026-08-19).

    A legacy bare-``shell=`` profile has no cost class, which ranks past
    every local class — so the distance term, unguarded, would hand an
    unclassed failure to the strongest installed core: silent escalation,
    the contract's own forbidden move. With no class to be near, the key
    goes neutral and cheapest-capable wins, exactly as before the change.
    """
    from brr import runner_select as rs
    from brr import runner_failures

    current = rs.implicit_runner("mystery")  # cost_class None
    candidates = [
        current,
        rs.RunnerProfile(name="cheap", profile="cheap", shell="x",
                         cost_class="economy", cost_rank=10,
                         quota_source="a"),
        rs.RunnerProfile(name="mighty", profile="mighty", shell="y",
                         cost_class="strong", cost_rank=50,
                         quota_source="b"),
    ]
    picked = rs.automatic_fallback_runner(
        candidates, current="mystery",
        failure_kind=runner_failures.AUTH_ERROR,
    )
    assert picked is not None and picked.name == "cheap"


# ── #1931: a substitute that cannot finish is not a substitute ────────
#
# Measured 2026-09-11: a claude 401 at 19:19:17Z fell through to
# codex-gpt-5.6-sol, whose weekly window read 1%; that reading armed a
# starvation hold fourteen minutes later and the seat parked. Every filter
# the selector already had passed the candidate — different failure domain,
# same class, capable. None of them asked whether it could run.


