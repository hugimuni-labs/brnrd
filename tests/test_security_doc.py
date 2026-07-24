"""SECURITY.md states facts the code owns; this reads both sides (#687).

`SECURITY.md` has no build step, so nothing here can be *derived* — the doc is
hand-written prose and stays that way. What is enforceable is that a number the
prose states out loud still equals the constant that produces it.

The cadence is the case that failed: `SECURITY.md` claimed a ~25-second
dashboard publish cadence for months after the publisher moved to its own
3-second loop, 8x off in the reassuring direction, in the document a
privacy-conscious reader checks first. 25s was `_POLL_WAIT_S`, the inbox
long-poll; the commit that decoupled the two left an accurate comment at
`cloud.py:38-43` — where only a code reader ever finds it.

**The parse deliberately fails closed.** A regex hunting a number in free prose
goes *green* when a rewording makes it stop matching, which is the same silent
pass the defect was. So the assertion is on the **count** first: the document
must state the cadence in the canonical form exactly `_EXPECTED_CADENCE_CLAIMS`
times. Paraphrase one of them ("a 3-second cadence", "every 3s") and the count
drops and the test reds. Delete one and it reds. Add a third and it reds. In
every case a human is forced to look at both sides and decide, which is the
whole point — the failure mode being guarded against is not a wrong number, it
is a claim drifting out of anyone's view.

Family: `test_release_version.py` (#674, three version literals), the license
lists (#675), the packaging surfaces (#680). Same shape each time: one fact,
two homes, a test that reads both rather than a better comment.
"""

from __future__ import annotations

import re
from pathlib import Path

from brr.gates import cloud

REPO_ROOT = Path(__file__).resolve().parents[1]
SECURITY_MD = REPO_ROOT / "SECURITY.md"

# The one form the document is allowed to state the publish cadence in. Prose
# either matches this or the test reds; there is no "close enough" branch.
_CADENCE_CLAIM = re.compile(r"\bevery (\d+) seconds\b")

# How many times SECURITY.md states it: once in the *Credentials & data flow*
# bullet (the enable-or-not argument) and once opening *What dashboard
# publishing mirrors* (the render-cache setup). Both are load-bearing and
# neither is redundant, so the count is pinned rather than floored.
_EXPECTED_CADENCE_CLAIMS = 2


def test_imported_cloud_module_is_this_repos():
    """Guard the guard: pin which tree the constant was read out of.

    `pyproject.toml` sets `pythonpath = ["src"]` relative to *rootdir*, so a
    stray installed `brr` elsewhere on `sys.path` could satisfy the import
    while `SECURITY.md` below is read from this checkout. That combination
    passes cheerfully and proves nothing about either tree.
    """
    assert hasattr(cloud, "_DASHBOARD_PUBLISH_INTERVAL_S")
    assert Path(cloud.__file__).resolve().is_relative_to(REPO_ROOT), (
        f"imported brr.gates.cloud from {cloud.__file__}, which is outside "
        f"{REPO_ROOT} — this test would be comparing two different trees"
    )


def test_security_md_states_the_publish_cadence_the_code_runs():
    interval = cloud._DASHBOARD_PUBLISH_INTERVAL_S
    text = SECURITY_MD.read_text(encoding="utf-8")
    stated = _CADENCE_CLAIM.findall(text)

    assert len(stated) == _EXPECTED_CADENCE_CLAIMS, (
        f"SECURITY.md states the dashboard publish cadence in the canonical "
        f"form {_CADENCE_CLAIM.pattern!r} {len(stated)} time(s); expected "
        f"{_EXPECTED_CADENCE_CLAIMS}. This test fails closed on purpose: it "
        f"cannot tell a deliberate rewrite from a claim that quietly stopped "
        f"matching, and a cadence claim nobody is checking is exactly how "
        f"#687 happened. If you reworded or moved a claim, re-state it in the "
        f"canonical form or update _EXPECTED_CADENCE_CLAIMS on purpose."
    )

    for value in stated:
        assert int(value) == interval, (
            f"SECURITY.md says the daemon publishes every {value} seconds; "
            f"brr.gates.cloud._DASHBOARD_PUBLISH_INTERVAL_S is {interval}. "
            f"The document describes how often repo-derived content leaves "
            f"the user's machine — fix the prose to match the code (and "
            f"re-read the surrounding argument, which was calibrated against "
            f"the old number), or fix the constant."
        )
