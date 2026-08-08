"""The run-ledger mirror parity guard (2026-08-08).

The local ledger (`brr.run_ledger._ROW_FIELDS`) and the server's mirror
schema (`brnrd.schemas.RunLedgerRowIn`) are two lists that must agree, and
nothing structural made them: pydantic silently drops undeclared fields at
``PUT /v1/daemons/run-ledger``, so a field added locally but not mirrored
renders as *absent* on every dashboard surface while both ends look
correct. That is how the bolt shipped a writer and a reader with a courier
that stripped the parcel — ``bolt: accepted`` on disk, `BoltSummons`
deployed, and no bolt ever reached the page.

The guard asks the *owning* module what the row contains and requires the
consumer to answer for every member — or to name the omission in
``WITHHELD`` with a reason. An omission is a decision, never a hole.
"""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

from brr.run_ledger import _ROW_FIELDS  # noqa: E402
from brnrd.schemas import RunLedgerRowIn  # noqa: E402

#: Local ledger fields deliberately NOT mirrored to brnrd.dev. Every entry
#: carries its reason; removing a field from the local ledger removes it
#: here too (the stale-entry assertion below enforces that).
WITHHELD: dict[str, str] = {
    "reply_archive": (
        "a host-local filesystem path; no cloud reader exists, and "
        "mirroring host paths into the dashboard store leaks machine "
        "layout for nothing"
    ),
}


def test_row_fields_is_a_real_population():
    # Sanity anchor: if the owning tuple is renamed or emptied, this guard
    # must fail loudly instead of passing over nothing.
    assert len(_ROW_FIELDS) >= 20
    assert "run_id" in _ROW_FIELDS
    assert "bolt" in _ROW_FIELDS


def test_every_local_field_is_mirrored_or_deliberately_withheld():
    mirrored = set(RunLedgerRowIn.model_fields)
    missing = [
        field
        for field in _ROW_FIELDS
        if field not in mirrored and field not in WITHHELD
    ]
    assert not missing, (
        f"local run-ledger fields the server mirror silently drops: {missing} — "
        "declare them on RunLedgerRowIn, or add them to WITHHELD with a reason"
    )


def test_withheld_entries_stay_current():
    stale = [field for field in WITHHELD if field not in _ROW_FIELDS]
    assert not stale, f"WITHHELD names fields the local ledger no longer has: {stale}"
    leaked = [field for field in WITHHELD if field in set(RunLedgerRowIn.model_fields)]
    assert not leaked, (
        f"WITHHELD names fields the server now mirrors: {leaked} — drop the entry"
    )
