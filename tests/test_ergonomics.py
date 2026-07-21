"""Tests for the agent-ergonomics probe slice.

Covers the record shape, the Null/Log/Local proxies + owner-aware
resolution, the local store (read/summarize/clear), each probe's
detection logic, the orchestrator's never-raise contract, and the
``brnrd ergonomics`` CLI handlers.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from brr import cli
from brr.envs import RunContext
from brr.ergonomics import probes, store
from brr.ergonomics.proxy import (
    LocalErgoProxy,
    LogErgoProxy,
    NullErgoProxy,
    ergonomics_mode,
    reset_log_dedup,
    resolve_proxy,
)
from brr.ergonomics.record import Record


# ── fixtures / helpers ──────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _clear_log_dedup():
    """The log proxy dedups on a process-global window; isolate tests."""
    reset_log_dedup()
    yield
    reset_log_dedup()


def _ctx(name: str = "docker", *, owner: str = "user", **env_state) -> RunContext:
    root = Path("/repo")
    return RunContext(
        name=name,
        cwd=root,
        repo_root=root,
        runtime_dir=root / ".brr",
        response_path_host=root / ".brr" / "resp",
        response_path_env=root / ".brr" / "resp",
        env_state=dict(env_state),
        owner=owner,
    )


def _task(run_id: str = "t1", source: str | None = None):
    return SimpleNamespace(id=run_id, source=source, meta={})


class _CapturingProxy:
    def __init__(self):
        self.records: list[Record] = []

    def emit(self, record: Record) -> None:
        self.records.append(record)


def _pctx(repo_root: Path, brr_dir: Path, *, ctx_name="docker", source=None,
          cfg=None, **env_state) -> probes.ProbeContext:
    return probes.ProbeContext(
        task=_task(source=source),
        repo_root=repo_root,
        brr_dir=brr_dir,
        cfg=cfg or {},
        ctx=_ctx(ctx_name, **env_state),
    )


# ── record ──────────────────────────────────────────────────────────


def test_record_json_round_trip():
    rec = Record(kind="probe", issue="stale_image", severity="warn",
                 detail={"image": "x"}, env="docker", timestamp=123.0)
    back = Record.from_dict(json.loads(rec.to_json_line()))
    assert back == rec


def test_record_from_dict_ignores_unknown_and_fills_defaults():
    rec = Record.from_dict({"kind": "probe", "issue": "x", "severity": "info",
                            "bogus": 1})
    assert rec.detail == {}
    assert rec.run_id is None
    assert rec.project_id == ""
    assert not hasattr(rec, "bogus")


def test_record_from_dict_requires_core_fields():
    with pytest.raises(KeyError):
        Record.from_dict({"issue": "x", "severity": "info"})


# ── mode normalisation ──────────────────────────────────────────────


@pytest.mark.parametrize("cfg,expected", [
    ({}, "log"),                          # unset → default
    ({"ergonomics": "off"}, "off"),
    ({"ergonomics": "log"}, "log"),
    ({"ergonomics": "local"}, "local"),
    ({"ergonomics": "response"}, "log"),  # retired mode → collapses to log
    ({"ergonomics": "bogus"}, "log"),     # unrecognised → safe default
])
def test_ergonomics_mode(cfg, expected):
    assert ergonomics_mode(cfg) == expected


# ── proxy resolution (owner-aware) ──────────────────────────────────


@pytest.mark.parametrize("cfg,cls", [
    ({}, LogErgoProxy),                       # user default
    ({"ergonomics": "off"}, NullErgoProxy),
    ({"ergonomics": "log"}, LogErgoProxy),
    ({"ergonomics": "local"}, LocalErgoProxy),
    ({"ergonomics": "response"}, LogErgoProxy),  # retired mode collapses to log
])
def test_resolve_proxy_user(cfg, cls, tmp_path):
    assert isinstance(resolve_proxy(cfg, tmp_path, owner="user"), cls)


@pytest.mark.parametrize("cfg", [
    {},
    {"ergonomics": "local"},
    {"ergonomics": "response"},
    {"ergonomics": "log"},
])
def test_resolve_proxy_operator_ignores_knob(cfg, tmp_path):
    # Operator-owned runs aren't user-configurable: the knob is ignored
    # and nothing is captured until BrnrdErgoProxy lands.
    assert isinstance(resolve_proxy(cfg, tmp_path, owner="operator"), NullErgoProxy)


# ── log proxy ───────────────────────────────────────────────────────


def _log_record(issue="stale_image", severity="warn", **kw):
    kw.setdefault("kind", "probe")
    kw.setdefault("env", "docker")
    kw.setdefault("timestamp", time.time())
    return Record(issue=issue, severity=severity, **kw)


def test_log_proxy_emits_warn_to_stdout(capsys):
    LogErgoProxy().emit(_log_record(detail={"hint": "rebuild the image"}))
    out = capsys.readouterr().out
    assert "[brnrd:ergo] warn stale_image" in out
    assert "rebuild the image" in out


def test_log_proxy_drops_below_threshold(capsys):
    LogErgoProxy().emit(_log_record(issue="drifted_bundled_docs", severity="info"))
    assert capsys.readouterr().out == ""


def test_log_proxy_dedups_within_window(capsys):
    proxy = LogErgoProxy(dedup_s=10_000)
    now = time.time()
    proxy.emit(_log_record(timestamp=now))
    proxy.emit(_log_record(timestamp=now + 1))       # same signature → silent
    assert capsys.readouterr().out.count("[brnrd:ergo]") == 1


def test_log_proxy_relogs_after_window_and_per_signature(capsys):
    proxy = LogErgoProxy(dedup_s=100)
    now = time.time()
    proxy.emit(_log_record(timestamp=now))
    proxy.emit(_log_record(timestamp=now + 500))     # window elapsed → again
    proxy.emit(_log_record(image="other:tag", timestamp=now))  # new sig → again
    assert capsys.readouterr().out.count("[brnrd:ergo]") == 3


# ── local store ─────────────────────────────────────────────────────


def _emit(brr_dir: Path, **kw) -> None:
    proxy = LocalErgoProxy(store.ergonomics_dir(brr_dir))
    kw.setdefault("kind", "probe")
    kw.setdefault("detail", {})
    kw.setdefault("timestamp", time.time())
    proxy.emit(Record(**kw))


def test_local_proxy_writes_daily_jsonl(tmp_path):
    _emit(tmp_path, issue="low_disk", severity="warn", env="host",
          timestamp=time.time())
    files = list(store.ergonomics_dir(tmp_path).glob("*.jsonl"))
    assert len(files) == 1
    day = time.strftime("%Y-%m-%d", time.gmtime())
    assert files[0].name == f"{day}.jsonl"


def test_store_read_filters_and_summary(tmp_path):
    now = time.time()
    _emit(tmp_path, issue="low_disk", severity="error", env="host", timestamp=now)
    _emit(tmp_path, issue="low_disk", severity="warn", env="docker", timestamp=now)
    _emit(tmp_path, issue="stale_image", severity="warn", env="docker", timestamp=now)
    # well outside any reasonable window
    _emit(tmp_path, issue="old", severity="info", env="host", timestamp=now - 999 * 86400)

    assert len(store.read_records(tmp_path)) == 4
    assert len(store.read_records(tmp_path, days=7)) == 3
    assert len(store.read_records(tmp_path, issue="low_disk")) == 2

    summ = store.summarize(store.read_records(tmp_path, days=7))
    # low_disk: worst severity error + highest count → first
    assert summ[0].issue == "low_disk"
    assert summ[0].count == 2
    assert summ[0].severity == "error"
    assert summ[0].envs == {"host", "docker"}


def test_store_skips_malformed_lines(tmp_path):
    d = store.ergonomics_dir(tmp_path)
    d.mkdir(parents=True)
    (d / "2026-01-01.jsonl").write_text(
        "not json\n"
        + json.dumps({"kind": "probe", "issue": "ok", "severity": "info"}) + "\n"
        + json.dumps({"issue": "missing-core"}) + "\n",
        encoding="utf-8",
    )
    recs = store.read_records(tmp_path)
    assert [r.issue for r in recs] == ["ok"]


def test_store_clear_all_and_before(tmp_path):
    d = store.ergonomics_dir(tmp_path)
    d.mkdir(parents=True)
    for day in ("2026-01-01", "2026-02-01", "2026-03-01"):
        (d / f"{day}.jsonl").write_text("", encoding="utf-8")
    removed = store.clear(tmp_path, before="2026-02-15")
    assert set(removed) == {"2026-01-01.jsonl", "2026-02-01.jsonl"}
    assert {p.name for p in d.glob("*.jsonl")} == {"2026-03-01.jsonl"}
    assert store.clear(tmp_path) == ["2026-03-01.jsonl"]


# ── individual probes ───────────────────────────────────────────────


def test_probe_github_auth_docker_no_token(tmp_path):
    (tmp_path / "gates").mkdir()
    (tmp_path / "gates" / "github.json").write_text("{}", encoding="utf-8")
    p = _pctx(tmp_path, tmp_path, ctx_name="docker")
    findings = probes.probe_github_auth(p)
    assert [f.issue for f in findings] == ["auth_unresolvable"]


def test_probe_github_auth_satisfied_with_token(tmp_path):
    p = _pctx(tmp_path, tmp_path, ctx_name="docker", source="github",
              github_token="ghs_x")
    assert probes.probe_github_auth(p) == []


def test_probe_github_auth_skips_non_docker(tmp_path):
    p = _pctx(tmp_path, tmp_path, ctx_name="worktree", source="github")
    assert probes.probe_github_auth(p) == []


def test_probe_stale_image_older_than_dockerfile(tmp_path, monkeypatch):
    dockerfile = tmp_path / "Dockerfile"
    dockerfile.write_text("FROM x", encoding="utf-8")
    import os
    os.utime(dockerfile, (1_000_000_000, 1_000_000_000))
    monkeypatch.setattr(probes, "_BUNDLED_DOCKERFILE", dockerfile)
    monkeypatch.setattr(probes, "_docker_image_created_epoch",
                        lambda _img: 999_000_000.0)  # older than dockerfile
    p = _pctx(tmp_path, tmp_path, ctx_name="docker", docker_image="img:tag")
    findings = probes.probe_stale_image(p)
    assert [f.issue for f in findings] == ["stale_image"]


def test_probe_stale_image_newer_is_clean(tmp_path, monkeypatch):
    dockerfile = tmp_path / "Dockerfile"
    dockerfile.write_text("FROM x", encoding="utf-8")
    import os
    os.utime(dockerfile, (1_000_000_000, 1_000_000_000))
    monkeypatch.setattr(probes, "_BUNDLED_DOCKERFILE", dockerfile)
    monkeypatch.setattr(probes, "_docker_image_created_epoch",
                        lambda _img: 1_001_000_000.0)  # newer than dockerfile
    p = _pctx(tmp_path, tmp_path, ctx_name="docker", docker_image="img:tag")
    assert probes.probe_stale_image(p) == []


def test_probe_worktree_buildup(tmp_path, monkeypatch):
    fake = [SimpleNamespace(path=tmp_path / f"wt{i}") for i in range(6)]
    monkeypatch.setattr("brr.worktree.list_worktrees", lambda _root: fake)
    p = _pctx(tmp_path, tmp_path, cfg={"ergonomics.worktree_warn": 5})
    findings = probes.probe_worktree_buildup(p)
    assert findings and findings[0].detail["count"] == 6


def test_probe_worktree_under_threshold(tmp_path, monkeypatch):
    fake = [SimpleNamespace(path=tmp_path / "wt0")]
    monkeypatch.setattr("brr.worktree.list_worktrees", lambda _root: fake)
    p = _pctx(tmp_path, tmp_path)
    assert probes.probe_worktree_buildup(p) == []


def test_probe_disk_low(tmp_path, monkeypatch):
    monkeypatch.setattr(probes.shutil, "disk_usage",
                        lambda _p: SimpleNamespace(total=100, used=99, free=10 ** 8))
    p = _pctx(tmp_path, tmp_path, cfg={"ergonomics.disk_warn_gb": 2.0})
    findings = probes.probe_disk(p)
    assert findings and findings[0].issue == "low_disk"
    assert findings[0].severity == "error"  # < threshold/2


def test_probe_disk_ok(tmp_path, monkeypatch):
    monkeypatch.setattr(probes.shutil, "disk_usage",
                        lambda _p: SimpleNamespace(total=10 ** 12, used=0, free=10 ** 12))
    p = _pctx(tmp_path, tmp_path)
    assert probes.probe_disk(p) == []


def test_probe_doc_drift(tmp_path, monkeypatch):
    bundled = tmp_path / "bundled_AGENTS.md"
    bundled.write_text("> Revision: 2026-06-01\nnew guidance\n", encoding="utf-8")
    monkeypatch.setattr(probes, "_BUNDLED_AGENTS_MD", bundled)
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "AGENTS.md").write_text("> Revision: 2026-01-01\nold guidance\n",
                                    encoding="utf-8")
    findings = probes.probe_doc_drift(probes.ProbeContext(
        task=_task(), repo_root=repo, brr_dir=tmp_path, cfg={}, ctx=_ctx()))
    assert findings and findings[0].issue == "drifted_bundled_docs"
    assert findings[0].detail["repo_revision"] == "Revision: 2026-01-01"


def test_probe_doc_drift_identical_is_clean(tmp_path, monkeypatch):
    bundled = tmp_path / "bundled_AGENTS.md"
    bundled.write_text("same\n", encoding="utf-8")
    monkeypatch.setattr(probes, "_BUNDLED_AGENTS_MD", bundled)
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "AGENTS.md").write_text("same\n", encoding="utf-8")
    findings = probes.probe_doc_drift(probes.ProbeContext(
        task=_task(), repo_root=repo, brr_dir=tmp_path, cfg={}, ctx=_ctx()))
    assert findings == []


def test_probe_doc_drift_block_aware(tmp_path, monkeypatch):
    """L1: tailoring per-repo prose is not drift; a lagging universal block is."""
    from brr import constitution

    template = constitution.stamp(
        "# Project\n\ntemplate prose\n\n"
        "<!-- brnrd:block id=stewardship v=2 hash=PENDING -->\n"
        "## Stewardship\nsteward with judgement\n"
        "<!-- /brnrd:block -->\n"
    )
    bundled = tmp_path / "constitution.md"
    bundled.write_text(template, encoding="utf-8")
    monkeypatch.setattr(probes, "_BUNDLED_AGENTS_MD", bundled)

    repo = tmp_path / "repo"
    repo.mkdir()
    # Adopter kept the block verbatim but tailored the project prose heavily.
    tailored = template.replace("template prose", "a completely different project blurb")
    (repo / "AGENTS.md").write_text(tailored, encoding="utf-8")
    ctx = probes.ProbeContext(
        task=_task(), repo_root=repo, brr_dir=tmp_path, cfg={}, ctx=_ctx())
    assert probes.probe_doc_drift(ctx) == []

    # Now the adopter's block lags an older version and body.
    stale = constitution.stamp(
        "# Project\n\na completely different project blurb\n\n"
        "<!-- brnrd:block id=stewardship v=1 hash=PENDING -->\n"
        "## Stewardship\nold stewardship text\n"
        "<!-- /brnrd:block -->\n"
    )
    (repo / "AGENTS.md").write_text(stale, encoding="utf-8")
    findings = probes.probe_doc_drift(ctx)
    assert findings and findings[0].issue == "drifted_bundled_docs"
    assert "stewardship" in findings[0].detail["stale_blocks"]


# ── orchestration ───────────────────────────────────────────────────


def test_run_probes_emits_and_survives_a_raising_probe(tmp_path, monkeypatch):
    def good(_p):
        return [probes.Finding("ok_issue", "info", {"a": 1})]

    def boom(_p):
        raise RuntimeError("probe blew up")

    monkeypatch.setattr(probes, "_PROBES", (good, boom, good))
    proxy = _CapturingProxy()
    records = probes.run_probes(
        task=_task("task-9", source="github"),
        repo_root=tmp_path, brr_dir=tmp_path, cfg={}, ctx=_ctx("docker"),
        proxy=proxy,
    )
    assert len(records) == 2  # both good() findings; boom swallowed
    assert all(r.kind == "probe" and r.run_id == "task-9" for r in records)
    assert proxy.records == records


def test_probe_run_prep_noop_when_off(tmp_path):
    # ergonomics=off → null proxy → no probes run, empty result
    assert probes.probe_run_prep(
        task=_task(), repo_root=tmp_path, brr_dir=tmp_path,
        cfg={"ergonomics": "off"}, ctx=_ctx(),
    ) == []


def test_probe_run_prep_noop_on_operator_run(tmp_path, monkeypatch):
    # operator-owned: knob ignored, null sink, short-circuit even on local
    monkeypatch.setattr(probes, "_PROBES",
                        (lambda _p: [probes.Finding("x", "warn", {})],))
    assert probes.probe_run_prep(
        task=_task(), repo_root=tmp_path, brr_dir=tmp_path,
        cfg={"ergonomics": "local"}, ctx=_ctx(owner="operator"),
    ) == []
    assert store.read_records(tmp_path) == []


def test_probe_run_prep_default_logs(tmp_path, monkeypatch, capsys):
    # default cfg (user-owned) → LogErgoProxy: probes run, warn+ to stdout
    monkeypatch.setattr(probes, "_PROBES",
                        (lambda _p: [probes.Finding("x", "warn", {"hint": "h"})],))
    records = probes.probe_run_prep(
        task=_task(), repo_root=tmp_path, brr_dir=tmp_path, cfg={}, ctx=_ctx(),
    )
    assert [r.issue for r in records] == ["x"]
    assert "[brnrd:ergo] warn x" in capsys.readouterr().out
    assert store.read_records(tmp_path) == []  # log proxy writes no store


def test_probe_run_prep_runs_on_local_proxy(tmp_path, monkeypatch):
    monkeypatch.setattr(probes, "_PROBES",
                        (lambda _p: [probes.Finding("x", "info", {})],))
    records = probes.probe_run_prep(
        task=_task(), repo_root=tmp_path, brr_dir=tmp_path,
        cfg={"ergonomics": "local"}, ctx=_ctx(),
    )
    assert [r.issue for r in records] == ["x"]
    assert len(store.read_records(tmp_path)) == 1


# ── CLI handlers ────────────────────────────────────────────────────


def test_cli_ergonomics_summary_json(tmp_path, monkeypatch, capsys):
    _emit(tmp_path, issue="low_disk", severity="warn", env="host")
    monkeypatch.setattr(cli, "_brr_dir", lambda: tmp_path)
    cli.cmd_ergonomics_summary(SimpleNamespace(days=7, json=True))
    out = json.loads(capsys.readouterr().out)
    assert out["total"] == 1
    assert out["issues"][0]["issue"] == "low_disk"


def test_cli_ergonomics_clear(tmp_path, monkeypatch, capsys):
    _emit(tmp_path, issue="x", severity="info", env="host")
    monkeypatch.setattr(cli, "_brr_dir", lambda: tmp_path)
    cli.cmd_ergonomics_clear(SimpleNamespace(before=None))
    assert "cleared 1" in capsys.readouterr().out
    assert store.read_records(tmp_path) == []


def test_cli_ergonomics_summary_empty_hint(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "_brr_dir", lambda: tmp_path)
    cli.cmd_ergonomics_summary(SimpleNamespace(days=7, json=False))
    assert "ergonomics=local" in capsys.readouterr().out
