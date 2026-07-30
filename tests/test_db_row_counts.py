"""The cutover parity check must fail loudly, and must not go stale silently.

Every test here guards one way ``scripts/db_row_counts.py`` could stop being a
go/no-go gate: a table added to the schema and never watched, a hand-written
name that no longer exists, a truncated dump read as a clean census, or a
mismatch that prints and still exits 0.
"""

from __future__ import annotations

import gzip
import importlib.util
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "db_row_counts.py"


def _module():
    spec = importlib.util.spec_from_file_location("db_row_counts_under_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


DUMP = """\
--
-- PostgreSQL database dump
--
CREATE TABLE public.terms_acceptances (id text NOT NULL);
COPY public.terms_acceptances (id, account_id) FROM stdin;
ta_1\tacc_1
ta_2\tacc_2
\\.

COPY public.events (id) FROM stdin;
evt_1
\\.

COPY public.stripe_events (stripe_event_id) FROM stdin;
\\.
"""


def test_counts_from_dump_reads_copy_blocks(tmp_path):
    path = tmp_path / "dump.sql"
    path.write_text(DUMP)
    assert _module().counts_from_dump(path) == {
        "terms_acceptances": 2,
        "events": 1,
        "stripe_events": 0,
    }


def test_counts_from_dump_reads_gzip(tmp_path):
    path = tmp_path / "dump.sql.gz"
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        handle.write(DUMP)
    assert _module().counts_from_dump(path)["terms_acceptances"] == 2


def test_truncated_dump_is_an_error_not_a_census(tmp_path):
    """A dump cut mid-COPY must never read as "that table has N rows"."""
    path = tmp_path / "dump.sql"
    path.write_text("COPY public.accounts (id) FROM stdin;\nacc_1\n")
    module = _module()
    try:
        module.counts_from_dump(path)
    except SystemExit as exc:
        assert "truncated" in str(exc)
    else:  # pragma: no cover - the assertion below reports the failure
        raise AssertionError("a truncated dump was accepted")


def test_mismatch_on_a_critical_table_exits_nonzero(capsys):
    module = _module()
    left = {"terms_acceptances": 4, "events": 340}
    right = {"terms_acceptances": 3, "events": 340}
    assert module.compare(left, right, check_all=False) == 1
    assert "MISMATCH" in capsys.readouterr().out


def test_regenerable_drift_passes_by_default_and_fails_under_all(capsys):
    """A shadow restore keeps taking heartbeats; phase 4 tolerates nothing."""
    module = _module()
    left = {"terms_acceptances": 4, "events": 340}
    right = {"terms_acceptances": 4, "events": 351}
    assert module.compare(left, right, check_all=False) == 0
    capsys.readouterr()
    assert module.compare(left, right, check_all=True) == 1


def test_a_table_missing_from_one_side_is_a_mismatch(capsys):
    module = _module()
    assert module.compare({"stripe_events": 30}, {}, check_all=False) == 1
    assert "MISMATCH" in capsys.readouterr().out


def test_regenerable_names_all_still_exist():
    """A hand-written name that outlives its table would silently stop watching."""
    from brnrd.models import Base

    known = {table.name for table in Base.metadata.tables.values()}
    assert _module()._REGENERABLE <= known, _module()._REGENERABLE - known


def test_every_new_table_is_critical_until_someone_says_otherwise():
    """The set is derived by subtraction, so an unlisted table fails loud."""
    from brnrd.models import Base

    module = _module()
    known = {table.name for table in Base.metadata.tables.values()}
    critical = known - module._REGENERABLE
    for name in ("terms_acceptances", "stripe_events", "billing_ledger", "subscriptions"):
        assert name in critical
    # The property itself: nothing in the schema is unaccounted for.
    assert critical | module._REGENERABLE == known
