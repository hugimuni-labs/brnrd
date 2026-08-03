"""``scripts/scw_rollout.py`` — the container rollout, after #894's 409.

#894 shipped the rollout as four inline ``curl`` calls and it failed on both
of its live runs with ``curl: (22) The requested URL returned error: 409`` —
no reason, because ``--fail -o /dev/null`` discarded the body Scaleway had
written one. Two defects under that one number, both pinned here:

- the ``PATCH`` fired without waiting for the container to settle, so an
  operator editing container variables in the console was enough to refuse
  the release;
- ``POST /deploy`` was chained after ``PATCH``, which is the vendor-documented
  cause of the very ``409`` observed.

The regression these tests exist to catch is the third one: **a rollout
failure that reaches the log as an integer.**
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
from pathlib import Path

import pytest

from _helpers import commit_files, init_git_repo

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "scw_rollout.py"


def _load():
    spec = importlib.util.spec_from_file_location("scw_rollout_under_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


mod = _load()


class _Api:
    """A scripted Scaleway, recording every call it is asked to make."""

    def __init__(self, statuses, *, patch_results=(), error_message=""):
        self.statuses = list(statuses)
        self.patch_results = list(patch_results)
        self.error_message = error_message
        self.calls: list[tuple[str, str]] = []

    def __call__(self, method, url, token, payload=None, **kw):
        self.calls.append((method, url))
        if method == "GET":
            status = self.statuses.pop(0) if self.statuses else "ready"
            return {"status": status, "error_message": self.error_message}
        if method == "PATCH":
            if self.patch_results:
                outcome = self.patch_results.pop(0)
                if isinstance(outcome, Exception):
                    raise outcome
            return {}
        return {}

    @property
    def methods(self):
        return [m for m, _ in self.calls]


class _Clock:
    """A fake clock where sleeping is what advances time.

    Stands in for the whole ``time`` module so no test spends a real second,
    and so a timeout can be driven by the code's own sleeps.
    """

    def __init__(self, sleeps: list[float]) -> None:
        self.sleeps = sleeps
        self._t = 0.0

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self._t += seconds

    def monotonic(self) -> float:
        return self._t


def _install(monkeypatch, api, sleeps=None):
    monkeypatch.setattr(mod, "request", api)
    monkeypatch.setattr(mod, "time", _Clock(sleeps if sleeps is not None else []))


# ── region derivation: one copy of the fact ──────────────────────────


def test_region_comes_from_the_registry_host():
    assert mod.region_of("rg.fr-par.scw.cloud/brnrd/brnrd:sha-abc1234") == "fr-par"
    assert mod.region_of("rg.nl-ams.scw.cloud/ns/img:tag") == "nl-ams"


def test_a_registry_host_that_is_not_scaleways_is_refused_loudly():
    with pytest.raises(SystemExit) as excinfo:
        mod.region_of("ghcr.io/hugimuni-labs/brnrd:sha-abc1234")
    assert "::error::" in str(excinfo.value)


# ── settled is ready-or-error, everything else waits ─────────────────


def test_transient_states_are_waited_out_not_enumerated(monkeypatch):
    # `creating` is not in any allow-list this script carries; it must still
    # read as "not yet" rather than "go ahead". That inversion is the point.
    api = _Api(["creating", "pending", "deploying", "ready"])
    sleeps: list[float] = []
    _install(monkeypatch, api, sleeps)
    assert mod.wait_settled("https://api/x", "tok") == "ready"
    assert len(sleeps) == 3


def test_a_container_wedged_in_a_transient_state_fails_the_release(monkeypatch):
    api = _Api(["pending"] * 200)
    sleeps: list[float] = []
    _install(monkeypatch, api, sleeps)
    with pytest.raises(SystemExit) as excinfo:
        mod.wait_settled("https://api/x", "tok", timeout_s=300.0)
    assert "transient state" in str(excinfo.value)
    # It really polled for the whole window rather than giving up on the first
    # non-ready read: 300s at a 5s interval.
    assert sum(sleeps) >= 300.0


# ── the PATCH: retry a 409, surface anything else ────────────────────


def test_a_409_is_waited_out_rather_than_reported_as_a_failure(monkeypatch):
    busy = mod.ApiError(409, json.dumps({"type": "transient_state", "message": "busy"}))
    api = _Api([], patch_results=[busy, busy, None])
    sleeps: list[float] = []
    _install(monkeypatch, api, sleeps)
    mod.patch_image("https://api/x", "tok", "img:tag", sleep=lambda s: sleeps.append(s))
    assert api.methods == ["PATCH", "PATCH", "PATCH"]
    assert sleeps == [mod.PATCH_BACKOFF_S, mod.PATCH_BACKOFF_S]


def test_a_non_409_refusal_is_not_retried(monkeypatch):
    denied = mod.ApiError(403, json.dumps({"type": "denied", "message": "no rights"}))
    api = _Api([], patch_results=[denied])
    _install(monkeypatch, api)
    with pytest.raises(mod.ApiError):
        mod.patch_image("https://api/x", "tok", "img:tag", sleep=lambda s: None)
    assert api.methods == ["PATCH"]


def test_a_permanently_busy_container_gives_up_with_the_api_words(monkeypatch):
    busy = mod.ApiError(409, json.dumps({"type": "transient_state", "message": "busy"}))
    api = _Api([], patch_results=[busy] * mod.PATCH_ATTEMPTS)
    _install(monkeypatch, api)
    with pytest.raises(mod.ApiError) as excinfo:
        mod.patch_image("https://api/x", "tok", "img:tag", sleep=lambda s: None)
    assert "transient_state: busy" in str(excinfo.value)


# ── the error message is the whole point ─────────────────────────────


def test_an_api_error_renders_scaleways_own_sentence():
    exc = mod.ApiError(409, json.dumps({"type": "transient_state", "message": "wait"}))
    rendered = str(exc)
    assert "transient_state: wait" in rendered
    assert "409" in rendered


def test_an_unparseable_body_still_carries_something(monkeypatch):
    assert "gateway down" in str(mod.ApiError(502, "gateway down"))
    assert "no body" in str(mod.ApiError(500, "   "))


# ── the whole rollout ────────────────────────────────────────────────


def test_the_rollout_never_posts_deploy(monkeypatch):
    # Chaining DeployContainer after UpdateContainer is the documented cause
    # of `409 transient_state`. UpdateContainer redeploys on its own.
    api = _Api(["ready", "ready"])
    _install(monkeypatch, api)
    assert mod.rollout("rg.fr-par.scw.cloud/ns/img:tag", "cid", "tok") == 0
    assert api.methods == ["GET", "PATCH", "GET"]
    assert not any(url.endswith("/deploy") for _, url in api.calls)


def test_the_rollout_settles_before_it_patches(monkeypatch):
    api = _Api(["deploying", "ready", "ready"])
    _install(monkeypatch, api)
    assert mod.rollout("rg.fr-par.scw.cloud/ns/img:tag", "cid", "tok") == 0
    # GET, GET (still moving), then the PATCH — never PATCH first.
    assert api.methods[:3] == ["GET", "GET", "PATCH"]


def test_a_refused_rollout_reports_the_reason_not_a_status_code(monkeypatch, capsys):
    denied = mod.ApiError(403, json.dumps({"type": "denied", "message": "bad key"}))
    api = _Api(["ready"], patch_results=[denied])
    _install(monkeypatch, api)
    assert mod.rollout("rg.fr-par.scw.cloud/ns/img:tag", "cid", "tok") == 1
    out = capsys.readouterr().out
    assert "::error::" in out
    assert "denied: bad key" in out


def test_a_container_that_lands_in_error_reports_its_error_message(monkeypatch, capsys):
    api = _Api(["ready", "error"], error_message="port 8080 never opened")
    _install(monkeypatch, api)
    assert mod.rollout("rg.fr-par.scw.cloud/ns/img:tag", "cid", "tok") == 1
    assert "port 8080 never opened" in capsys.readouterr().out


def test_missing_credentials_are_named_before_any_request(monkeypatch):
    monkeypatch.delenv("SCW_SECRET_KEY", raising=False)
    monkeypatch.delenv("SCW_CONTAINER_ID", raising=False)
    called = []
    monkeypatch.setattr(mod, "request", lambda *a, **k: called.append(a))
    assert mod.main(["scw_rollout.py", "rg.fr-par.scw.cloud/ns/img:tag"]) == 2
    assert called == []


# ── commit ordering: refuse a rollout that isn't a descendant (#1045) ─
#
# Real temporary git repositories throughout, not mocks — the thing being
# tested is what `git merge-base --is-ancestor` actually answers, including
# the one answer a mock would have to be told to give rather than earn: a
# shallow clone that genuinely cannot resolve the deployed commit.


def _bare_origin(tmp_path: Path) -> Path:
    origin = tmp_path / "origin.git"
    subprocess.run(
        ["git", "init", "--bare", "-b", "main", str(origin)],
        check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    return origin


def test_deployed_sha_tag_reads_the_workflows_own_tag_shape():
    assert mod._deployed_sha_tag(
        {"registry_image": "rg.fr-par.scw.cloud/ns/brnrd:sha-abc1234"}
    ) == "abc1234"


def test_deployed_sha_tag_none_when_no_image_on_record():
    assert mod._deployed_sha_tag({}) is None
    assert mod._deployed_sha_tag({"registry_image": ""}) is None


def test_deployed_sha_tag_none_for_a_foreign_tag_shape():
    # A container pointed somewhere by hand ("latest", a manual tag) is not
    # evidence of "no ancestor" — it's evidence this check can't speak to
    # the image at all, and must fall through to unknown rather than lie.
    assert mod._deployed_sha_tag(
        {"registry_image": "rg.fr-par.scw.cloud/ns/brnrd:latest"}
    ) is None


def test_read_deployed_commit_delegates_to_a_single_get(monkeypatch):
    calls = []
    monkeypatch.setattr(mod, "request", lambda *a, **k: (calls.append(a), {
        "registry_image": "rg.fr-par.scw.cloud/ns/brnrd:sha-1234567"
    })[1])
    assert mod.read_deployed_commit("https://api/x", "tok") == "1234567"
    assert len(calls) == 1
    assert calls[0][:2] == ("GET", "https://api/x")


# ── ancestry_status: the three outcomes, on real git repos ────────────


def test_ancestry_status_ancestor_when_deployed_is_a_true_ancestor(tmp_path):
    repo = tmp_path / "repo"
    init_git_repo(repo)
    deployed = commit_files(repo, {"a.txt": "one\n"})
    commit_files(repo, {"b.txt": "two\n"}, message="second")  # HEAD moves on

    status, deployed_sha, candidate_sha = mod.ancestry_status(repo, deployed)
    assert status == "ancestor"
    assert deployed_sha == deployed


def test_ancestry_status_not_ancestor_when_deploy_would_go_backwards(tmp_path):
    # This is #1045's actual regression, reproduced directly: a build for an
    # OLDER commit finishes and tries to roll out after a NEWER commit is
    # already deployed. "Deployed" here is the tip; "candidate" (HEAD) is an
    # earlier commit whose slow build only just finished.
    repo = tmp_path / "repo"
    init_git_repo(repo)
    older = commit_files(repo, {"a.txt": "one\n"})
    deployed = commit_files(repo, {"b.txt": "two\n"}, message="second, deployed")
    subprocess.run(["git", "checkout", older], cwd=repo, check=True,
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    status, deployed_sha, candidate_sha = mod.ancestry_status(repo, deployed)
    assert status == "not_ancestor"
    assert deployed_sha == deployed
    assert candidate_sha == older


def test_ancestry_status_not_ancestor_when_history_diverged(tmp_path):
    repo = tmp_path / "repo"
    init_git_repo(repo)
    base = commit_files(repo, {"a.txt": "one\n"})
    subprocess.run(["git", "branch", "side", base], cwd=repo, check=True)
    deployed = commit_files(repo, {"main-only.txt": "m\n"}, message="on main")
    subprocess.run(["git", "checkout", "side"], cwd=repo, check=True,
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    commit_files(repo, {"side-only.txt": "s\n"}, message="on side")

    status, _, _ = mod.ancestry_status(repo, deployed)
    assert status == "not_ancestor"


def test_ancestry_status_unknown_when_deployed_revision_is_unresolvable(tmp_path):
    # The realistic cause named in #1045: actions/checkout defaults to
    # fetch-depth 1, so a genuinely-older deployed commit simply isn't in
    # the object store. A shallow clone off a real bare origin reproduces
    # that honestly rather than asserting it by construction.
    origin = _bare_origin(tmp_path)
    seed = tmp_path / "seed"
    init_git_repo(seed)
    old = commit_files(seed, {"a.txt": "one\n"})
    subprocess.run(["git", "remote", "add", "origin", str(origin)], cwd=seed, check=True)
    subprocess.run(["git", "push", "origin", "main"], cwd=seed, check=True,
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    commit_files(seed, {"b.txt": "two\n"}, message="second")
    subprocess.run(["git", "push", "origin", "main"], cwd=seed, check=True,
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    shallow = tmp_path / "shallow"
    # `--no-local`: a same-filesystem clone silently ignores `--depth`
    # ("--depth is ignored in local clones") and hardlinks the full object
    # store, which would make this fixture assert nothing. `--no-local`
    # forces the real network-clone codepath so the shallow-ness is genuine.
    subprocess.run(
        ["git", "clone", "--no-local", "--depth", "1", str(origin), str(shallow)],
        check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )

    status, deployed_sha, candidate_sha = mod.ancestry_status(shallow, old)
    assert status == "unknown"
    assert deployed_sha is None
    assert candidate_sha is not None  # HEAD itself always resolves


def test_ancestry_status_unknown_when_no_deployed_revision():
    # No API answer at all (first-ever deploy, or the GET failed) — the
    # caller passes None/"" straight through rather than guessing.
    status, deployed_sha, candidate_sha = mod.ancestry_status("/nonexistent", None)
    assert (status, deployed_sha, candidate_sha) == ("unknown", None, None)


# ── check_commit_ordering: the decision, and what it prints ───────────


def test_check_commit_ordering_proceeds_on_a_true_ancestor(tmp_path, monkeypatch, capsys):
    repo = tmp_path / "repo"
    init_git_repo(repo)
    deployed = commit_files(repo, {"a.txt": "one\n"})
    commit_files(repo, {"b.txt": "two\n"}, message="second")
    monkeypatch.setattr(mod, "request", lambda *a, **k: {
        "registry_image": f"rg.fr-par.scw.cloud/ns/brnrd:sha-{deployed[:7]}"
    })

    assert mod.check_commit_ordering("https://api/x", "tok", repo) is True
    assert "::notice::" not in capsys.readouterr().out


def test_check_commit_ordering_declines_and_names_both_shas(tmp_path, monkeypatch, capsys):
    repo = tmp_path / "repo"
    init_git_repo(repo)
    older = commit_files(repo, {"a.txt": "one\n"})
    deployed = commit_files(repo, {"b.txt": "two\n"}, message="second, deployed")
    subprocess.run(["git", "checkout", older], cwd=repo, check=True,
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    monkeypatch.setattr(mod, "request", lambda *a, **k: {
        "registry_image": f"rg.fr-par.scw.cloud/ns/brnrd:sha-{deployed[:7]}"
    })

    assert mod.check_commit_ordering("https://api/x", "tok", repo) is False
    out = capsys.readouterr().out
    assert "::notice::" in out
    assert deployed in out
    assert older in out


def test_check_commit_ordering_rolls_out_when_ordering_is_unknown(tmp_path, monkeypatch, capsys):
    repo = tmp_path / "repo"
    init_git_repo(repo)
    commit_files(repo, {"a.txt": "one\n"})
    # No image on record yet — first deploy.
    monkeypatch.setattr(mod, "request", lambda *a, **k: {})

    assert mod.check_commit_ordering("https://api/x", "tok", repo) is True
    assert "::notice::" in capsys.readouterr().out


def test_check_commit_ordering_rolls_out_when_the_scaleway_read_fails(tmp_path, monkeypatch, capsys):
    repo = tmp_path / "repo"
    init_git_repo(repo)
    commit_files(repo, {"a.txt": "one\n"})

    def _boom(*a, **k):
        raise mod.ApiError(502, "bad gateway")

    monkeypatch.setattr(mod, "request", _boom)

    assert mod.check_commit_ordering("https://api/x", "tok", repo) is True
    out = capsys.readouterr().out
    assert "::warning::" in out
    assert "::notice::" in out


# ── neuter check: an out-of-order deploy that goes green is the bug ───


def test_a_declined_rollout_never_reaches_the_scaleway_patch(tmp_path, monkeypatch):
    # A guard that only prints a notice but still calls rollout() would pass
    # every test above by accident (they only assert the return value and
    # the printed text). This nails the actual side effect: main() must
    # short-circuit before rollout() ever calls PATCH.
    repo = tmp_path / "repo"
    init_git_repo(repo)
    older = commit_files(repo, {"a.txt": "one\n"})
    deployed = commit_files(repo, {"b.txt": "two\n"}, message="second, deployed")
    subprocess.run(["git", "checkout", older], cwd=repo, check=True,
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    monkeypatch.setenv("SCW_SECRET_KEY", "tok")
    monkeypatch.setenv("SCW_CONTAINER_ID", "cid")
    monkeypatch.setenv("GITHUB_WORKSPACE", str(repo))
    calls: list[tuple[str, str]] = []

    def _fake_request(method, url, token, payload=None, **kw):
        calls.append((method, url))
        if method == "GET":
            # "ready" so that IF the guard is broken and rollout() runs for
            # real, it completes fast and the PATCH assertion below catches
            # the regression cleanly — instead of wait_settled looping on a
            # container that never reports a status, which would fail this
            # test by real-time hang rather than by a readable assertion.
            return {
                "status": "ready",
                "registry_image": f"rg.fr-par.scw.cloud/ns/brnrd:sha-{deployed[:7]}",
            }
        return {}

    monkeypatch.setattr(mod, "request", _fake_request)

    rc = mod.main(["scw_rollout.py", "rg.fr-par.scw.cloud/ns/brnrd:sha-whatever"])
    assert rc == 0
    assert "PATCH" not in [c[0] for c in calls]


# ── the workflow calls it, and calls nothing else ────────────────────


def test_the_workflow_delegates_the_rollout_to_this_script():
    text = (REPO_ROOT / ".github" / "workflows" / "publish-container.yml").read_text(
        encoding="utf-8"
    )
    _, _, tail = text.partition("Deploy the mirrored image to the Serverless Container")
    step, _, _ = tail.partition("- name: Note the skipped deploy tail")
    assert "scripts/scw_rollout.py" in step
    # The inline curl shape is what broke; it must not creep back.
    assert "curl" not in step
    assert "/deploy" not in step
