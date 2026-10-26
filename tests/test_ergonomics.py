"""Tests for the agent-ergonomics probe slice.

Covers the record shape, the Null/Log/Local proxies + owner-aware
resolution, the local store (read/summarize/clear), each probe's
detection logic, the orchestrator's never-raise contract, and the
``brnrd ergonomics`` CLI handlers.
"""

from __future__ import annotations

import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from brr.envs import RunContext
from brr.ergonomics import probes, store
from brr.ergonomics.proxy import (
    LocalErgoProxy,
    reset_log_dedup,
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


# ── mode normalisation ──────────────────────────────────────────────


# ── proxy resolution (owner-aware) ──────────────────────────────────


# ── log proxy ───────────────────────────────────────────────────────


# ── local store ─────────────────────────────────────────────────────


def _emit(brr_dir: Path, **kw) -> None:
    proxy = LocalErgoProxy(store.ergonomics_dir(brr_dir))
    kw.setdefault("kind", "probe")
    kw.setdefault("detail", {})
    kw.setdefault("timestamp", time.time())
    proxy.emit(Record(**kw))


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
    monkeypatch.setattr("brr.worktree.list_brr_worktrees", lambda _root: fake)
    p = _pctx(tmp_path, tmp_path, cfg={"ergonomics.worktree_warn": 5})
    findings = probes.probe_worktree_buildup(p)
    assert findings and findings[0].detail["count"] == 6


def test_probe_worktree_under_threshold(tmp_path, monkeypatch):
    fake = [SimpleNamespace(path=tmp_path / "wt0")]
    monkeypatch.setattr("brr.worktree.list_brr_worktrees", lambda _root: fake)
    p = _pctx(tmp_path, tmp_path)
    assert probes.probe_worktree_buildup(p) == []


def test_probe_worktree_buildup_counts_only_brr_worktrees(tmp_path, monkeypatch):
    """#721 widened the producer; this count drives a *deletion* hint.

    The probe's mechanism ("finalize keeps worktrees on failure/dirty exit")
    is brnrd's own, and "remove stale ones to reclaim space" is only sound
    advice about trees brnrd created and abandoned. Aimed at a Shell's live
    agent-isolation directory, or at a resident's ``/tmp`` worktree holding
    unpushed work, the same sentence proposes data loss. So the wider set
    must not reach it: 4 brr worktrees stay under a threshold of 5 even with
    six more worktrees of this repo living elsewhere.
    """
    from brr.worktree import WorktreeInfo

    wide = [
        WorktreeInfo(
            path=tmp_path / f"wt{i}", run_id=f"run-{i}",
            branch=f"brr/run-{i}", kind="brr",
        )
        for i in range(4)
    ] + [
        WorktreeInfo(
            path=Path(f"/tmp/brr-wt-{i}"), run_id=None,
            branch=f"brr/slug-{i}", kind="external",
        )
        for i in range(6)
    ]
    monkeypatch.setattr("brr.worktree.list_worktrees", lambda _root: wide)
    p = _pctx(tmp_path, tmp_path, cfg={"ergonomics.worktree_warn": 5})

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


