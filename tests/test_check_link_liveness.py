"""``scripts/check_link_liveness.py`` -- #1033, the outbound-link guard.

No network here on purpose: every ``check_link`` call goes through a fake
session (``_FakeSession``), never ``requests`` itself. A test suite that hit
the real internet would make CI depend on the live internet, which is
exactly the failure mode #1033 asks this checker to avoid *causing* while
guarding against it elsewhere.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest
import requests

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "check_link_liveness.py"


def _load():
    spec = importlib.util.spec_from_file_location("check_link_liveness_under_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    # dataclass() needs sys.modules[cls.__module__] to resolve type hints;
    # a spec-loaded module isn't registered there by default.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


mod = _load()


# --- extraction --------------------------------------------------------


def test_extracts_a_plain_absolute_url():
    urls, templated = mod.extract_urls("see https://brnrd.dev/pricing for details.")
    assert urls == ["https://brnrd.dev/pricing"]
    assert templated == 0


def test_extracts_multiple_urls_from_markdown_and_html():
    text = (
        "[docs](https://hugimuni-labs.github.io/brnrd/) and "
        '<a href="https://brnrd.dev/terms">terms</a> and '
        "<https://example.org/autolink>"
    )
    urls, _ = mod.extract_urls(text)
    assert set(urls) == {
        "https://hugimuni-labs.github.io/brnrd/",
        "https://brnrd.dev/terms",
        "https://example.org/autolink",
    }


def test_strips_trailing_sentence_punctuation():
    urls, _ = mod.extract_urls("Read https://brnrd.dev/pricing. Then decide.")
    assert urls == ["https://brnrd.dev/pricing"]


@pytest.mark.parametrize(
    "text",
    [
        "git clone https://github.com/<owner>/<repo>.git",
        "href={`https://github.com/${GITHUB_REPO}`}",
        "endpoint: https://api.example.org/v1/{{tenant}}/status",
    ],
)
def test_templated_urls_are_recognised_and_skipped_not_truncated(text):
    """A placeholder-shaped URL (<owner>, ${...}, {{...}}) is not a claim
    with a known target -- it must not be silently truncated into a bare
    origin and checked as if that were the real link (the astro.config.mjs
    `site:`-without-`base:` false positive this exact bug would otherwise
    reproduce), and it must not be dropped invisibly either.
    """
    urls, templated = mod.extract_urls(text)
    assert urls == []
    assert templated == 1


def test_a_real_url_next_to_a_templated_one_is_still_caught():
    text = "fixed: https://brnrd.dev/pricing, dynamic: https://x.test/${id}"
    urls, templated = mod.extract_urls(text)
    assert urls == ["https://brnrd.dev/pricing"]
    assert templated == 1


# --- allowlist (skip entirely, structural) -----------------------------


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost/",
        "http://localhost:8080/health",
        "http://sub.localhost/",
        "http://127.0.0.1/",
        "http://10.0.0.5/internal",
        "http://192.168.1.1/",
        "https://example.com/pr/7",
        "https://www.example.com/",
        "https://example.org",
        "https://example.net/x",
        "https://foo.test/1",
        "https://evil.example/",
        "https://sub.evil.example/",
        "https://bar.invalid/",
        "https://forge/commit/abc1234",
    ],
)
def test_allowlisted_by_shape(url):
    assert mod.is_allowlisted(url) is True


@pytest.mark.parametrize(
    "url",
    [
        "https://brnrd.dev/pricing",
        "https://github.com/hugimuni-labs/brnrd",
        "https://eur-lex.europa.eu/eli/reg/2016/679/oj/eng",
        "https://gurio.github.io/brr/",
    ],
)
def test_not_allowlisted(url):
    assert mod.is_allowlisted(url) is False


# --- first-party / third-party partition, by shape of *our* surfaces ---


@pytest.mark.parametrize(
    "url",
    [
        "https://brnrd.dev",
        "https://brnrd.dev/pricing",
        "https://status.brnrd.dev/",
        "https://hugimuni-labs.github.io",
        "https://hugimuni-labs.github.io/brnrd/",
        "https://github.com/hugimuni-labs/brnrd",
        "https://github.com/hugimuni-labs/brnrd/issues/23",
        "https://github.com/hugimuni-labs/brnrd-knowledge/blob/main/x.md",
    ],
)
def test_first_party(url):
    assert mod.is_first_party(url) is True


@pytest.mark.parametrize(
    "url",
    [
        "https://github.com/other-org/site/issues/3",
        "https://github.com/",
        "https://eur-lex.europa.eu/eli/reg/2016/679/oj/eng",
        "https://brnrd.dev.evil.example/",  # suffix trick: NOT *.brnrd.dev
        # The dead link #1033 exists to guard: still on this branch at
        # Landing.svelte:52, fixed elsewhere, "do not touch" here. It must
        # classify third-party so this new gate does not collide with a
        # known, out-of-scope, already-being-fixed defect the moment it
        # lands -- see the module docstring's NOTE for the full reasoning.
        "https://gurio.github.io/brr/",
    ],
)
def test_third_party(url):
    assert mod.is_first_party(url) is False


# --- liveness: dead vs unreachable, and the 405 fallback ----------------


class _Response:
    def __init__(self, status_code):
        self.status_code = status_code


class _FakeSession:
    """Records calls; ``head``/``get`` fns per-instance so tests are terse."""

    def __init__(self, head=None, get=None):
        self._head = head
        self._get = get
        self.calls = []

    def head(self, url, **kwargs):
        self.calls.append(("HEAD", url, kwargs))
        return self._head(url, **kwargs)

    def get(self, url, **kwargs):
        self.calls.append(("GET", url, kwargs))
        return self._get(url, **kwargs)


def test_200_is_live():
    session = _FakeSession(head=lambda url, **kw: _Response(200))
    result = mod.check_link("https://brnrd.dev", session=session)
    assert result.status == "live"


@pytest.mark.parametrize("status", [404, 410])
def test_404_and_410_are_dead(status):
    session = _FakeSession(head=lambda url, **kw: _Response(status))
    result = mod.check_link("https://brnrd.dev/gone", session=session)
    assert result.status == "dead"
    assert str(status) in result.detail


@pytest.mark.parametrize("status", [400, 403, 429, 500, 502, 503])
def test_other_4xx_5xx_are_unreachable_not_dead(status):
    """Only a proven-gone response (404/410) is DEAD. Everything else --
    including a 403 from a site that blocks bot/HEAD user agents, observed
    live against legifrance.gouv.fr while validating this script -- is a
    fact about the probe, not proof the resource is gone.
    """
    session = _FakeSession(head=lambda url, **kw: _Response(status))
    result = mod.check_link("https://example-host.test/maybe", session=session)
    assert result.status == "unreachable"


def test_dead_and_unreachable_never_share_a_word():
    dead = mod.check_link("https://a.test/", session=_FakeSession(head=lambda u, **kw: _Response(404)))
    unreachable = mod.check_link("https://a.test/", session=_FakeSession(head=lambda u, **kw: _Response(500)))
    assert dead.status != unreachable.status
    assert "dead" not in unreachable.status and "unreachable" not in dead.status


def test_connection_error_is_unreachable():
    def _raise(url, **kw):
        raise requests.exceptions.ConnectionError("nope")

    session = _FakeSession(head=_raise)
    result = mod.check_link("https://brnrd.dev", session=session)
    assert result.status == "unreachable"
    assert "ConnectionError" in result.detail


def test_timeout_is_unreachable():
    def _raise(url, **kw):
        raise requests.exceptions.Timeout("slow")

    session = _FakeSession(head=_raise)
    result = mod.check_link("https://brnrd.dev", session=session)
    assert result.status == "unreachable"


def test_405_on_head_falls_back_to_ranged_get():
    """A 405 on HEAD is not a dead link -- retry with a ranged GET."""
    session = _FakeSession(
        head=lambda url, **kw: _Response(405),
        get=lambda url, **kw: _Response(200),
    )
    result = mod.check_link("https://brnrd.dev/no-head", session=session)
    assert result.status == "live"
    assert session.calls[0][0] == "HEAD"
    assert session.calls[1][0] == "GET"
    assert session.calls[1][2]["headers"].get("Range") == "bytes=0-0"


def test_405_then_404_ranged_get_is_dead():
    session = _FakeSession(
        head=lambda url, **kw: _Response(405),
        get=lambda url, **kw: _Response(404),
    )
    result = mod.check_link("https://brnrd.dev/no-head-gone", session=session)
    assert result.status == "dead"


def test_405_then_get_connection_error_is_unreachable():
    def _raise(url, **kw):
        raise requests.exceptions.ConnectionError("nope")

    session = _FakeSession(head=lambda url, **kw: _Response(405), get=_raise)
    result = mod.check_link("https://brnrd.dev/flaky", session=session)
    assert result.status == "unreachable"


# --- neuter-and-watch-red: the exact regression this file exists to catch


def test_neutering_the_dead_threshold_is_caught_by_this_suite():
    """Sanity pin: if DEAD_STATUSES stops including 404, a real dead link
    reads as merely unreachable and #1033's whole point (block on a *proven*
    first-party 404) silently stops working. Documented here rather than by
    literally mutating the module, per the run's neuter-and-watch-red pass
    (see the PR description for the transcript of the red run).
    """
    assert 404 in mod.DEAD_STATUSES
    assert 410 in mod.DEAD_STATUSES
    assert 403 not in mod.DEAD_STATUSES
    assert 500 not in mod.DEAD_STATUSES


# --- report: first-party-vs-third-party blocking policy -----------------


def _link(file, url):
    return mod.Link(file=file, url=url)


def test_dead_first_party_link_is_reported_as_blocking():
    links = [_link("README.md", "https://brnrd.dev/gone")]
    session = _FakeSession(head=lambda url, **kw: _Response(404))
    report = mod.build_report(links, first_party_only=True, session=session)
    assert len(report.dead_first_party()) == 1


def test_dead_third_party_link_never_blocks():
    links = [_link("docs/x.md", "https://widgets-inc.io/gone")]
    session = _FakeSession(head=lambda url, **kw: _Response(404))
    report = mod.build_report(links, first_party_only=False, session=session)
    assert report.dead_first_party() == []
    # but it is still visible in the full result set, not silently dropped
    assert report.results["https://widgets-inc.io/gone"].status == "dead"


def test_first_party_only_mode_never_makes_third_party_requests():
    links = [
        _link("README.md", "https://brnrd.dev/pricing"),
        _link("docs/x.md", "https://widgets-inc.io/whatever"),
    ]
    calls = []

    def _head(url, **kw):
        calls.append(url)
        return _Response(200)

    session = _FakeSession(head=_head)
    mod.build_report(links, first_party_only=True, session=session)
    assert calls == ["https://brnrd.dev/pricing"]


# --- silence at zero: never an unqualified "0 dead links" ----------------


def test_all_unreachable_is_flagged_as_no_data_not_a_clean_bill_of_health():
    links = [_link("README.md", "https://brnrd.dev/a"), _link("README.md", "https://brnrd.dev/b")]

    def _raise(url, **kw):
        raise requests.exceptions.ConnectionError("no network")

    session = _FakeSession(head=_raise)
    report = mod.build_report(links, first_party_only=True, session=session)
    assert report.all_unreachable() is True
    rendered = mod.render(report)
    assert "0 DEAD" in rendered  # the qualified count still appears...
    assert "no data" in rendered.lower()  # ...but so does the disqualifier


def test_a_real_clean_pass_is_not_flagged_as_no_data():
    links = [_link("README.md", "https://brnrd.dev/a")]
    session = _FakeSession(head=lambda url, **kw: _Response(200))
    report = mod.build_report(links, first_party_only=True, session=session)
    assert report.all_unreachable() is False
    assert "no data" not in mod.render(report).lower()


def test_zero_links_extracted_is_not_rendered_as_a_dead_check_either():
    report = mod.build_report([], first_party_only=True, session=_FakeSession())
    assert report.all_unreachable() is False  # nothing attempted, not "all failed"
    rendered = mod.render(report)
    assert "0 first-party" in rendered or "extracted: 0" in rendered


# --- gather_links: end-to-end over a real (temp) tracked tree ------------


def test_gather_links_end_to_end(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.test"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=tmp_path, check=True)

    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "guide.md").write_text("see https://brnrd.dev/pricing\n")
    (tmp_path / "docs" / "package-lock.json").write_text('{"resolved": "https://registry.npmjs.org/x"}\n')
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "thing.test.ts").write_text("expect(fetch).toHaveBeenCalledWith('https://evil.example/')\n")
    (tmp_path / "src" / "real.svelte").write_text('<a href="https://github.com/hugimuni-labs/brnrd">repo</a>\n')

    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "seed"], cwd=tmp_path, check=True)

    links, templated = mod.gather_links(repo_root=tmp_path, scope=["docs", "src"])
    urls = {link.url for link in links}

    assert "https://brnrd.dev/pricing" in urls
    assert "https://github.com/hugimuni-labs/brnrd" in urls
    # lockfile noise and test-fixture URLs never make it into the extracted set
    assert not any("registry.npmjs.org" in u for u in urls)
    assert "https://evil.example/" not in urls
    assert templated == 0


def test_gather_links_ignores_untracked_files(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.test"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=tmp_path, check=True)
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "committed.md").write_text("https://brnrd.dev/kept\n")
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "seed"], cwd=tmp_path, check=True)
    # untracked file, never `git add`ed
    (tmp_path / "docs" / "scratch.md").write_text("https://brnrd.dev/not-kept\n")

    links, _ = mod.gather_links(repo_root=tmp_path, scope=["docs"])
    urls = {link.url for link in links}
    assert "https://brnrd.dev/kept" in urls
    assert "https://brnrd.dev/not-kept" not in urls
