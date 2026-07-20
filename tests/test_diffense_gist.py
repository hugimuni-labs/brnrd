"""Tests for gist-backed diffense pack publication."""

from __future__ import annotations

from pathlib import Path

from brr.diffense import gist


def _pack(repo: str | None = None) -> dict:
    meta = {}
    if repo:
        meta["pr"] = {"repo": repo}
    return {"schema_version": "0.1-test", "metadata": meta, "cards": []}


class _Result:
    def __init__(self, returncode: int, stdout: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout


class _Response:
    def __init__(self, body: str, status: int = 200) -> None:
        self._body = body.encode("utf-8")
        self.status = status
        self.closed = False

    def read(self, _limit: int = -1) -> bytes:
        return self._body

    def close(self) -> None:
        self.closed = True


def test_render_url_points_shell_at_raw_pack():
    url = gist.render_url(
        "https://gist.githubusercontent.com/u/abc/raw/sha/diffense-pack.json"
    )
    assert url.startswith("https://brnrd.dev/r?pack=")
    assert "gist.githubusercontent.com" in url


def test_renderer_shell_available_requires_gist_loader():
    calls = []

    def fetch(request, **_kwargs):
        calls.append(request.full_url)
        return _Response('new URLSearchParams(location.search).get("pack")')

    assert gist.renderer_shell_available("https://brnrd.example/r", fetch=fetch)
    assert calls == [
        "https://brnrd.example/r?pack=https%3A%2F%2Fexample.invalid%2F"
        "diffense-pack-probe.json"
    ]


def test_renderer_shell_unavailable_without_loader_marker():
    def fetch(_request, **_kwargs):
        return _Response('{"detail":"Not Found"}', status=404)

    assert not gist.renderer_shell_available("https://brnrd.example/r", fetch=fetch)


def test_review_url_available_requires_rendered_diffense_page():
    def fetch(_request, **_kwargs):
        return _Response('<script id="diffense-pack" type="application/json">')

    assert gist.review_url_available("https://brnrd.example/r/tok", fetch=fetch)


def test_review_url_unavailable_for_server_error():
    def fetch(_request, **_kwargs):
        return _Response("Internal Server Error", status=500)

    assert not gist.review_url_available("https://brnrd.example/r/tok", fetch=fetch)


def test_diffense_renderer_template_is_packaged():
    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    text = pyproject.read_text(encoding="utf-8")
    package_data = text.split("[tool.setuptools.package-data]", 1)[1].split(
        "\n[", 1
    )[0]

    # Exact filename, not a `diffense/*.html` wildcard: the wildcard also
    # swept up diffense/review-pr64.html (an internal review artifact) into
    # the wheel. render.py only ever loads template.html at runtime.
    assert '"diffense/template.html"' in package_data


def test_create_pack_gist_uses_secret_gist_and_fetches_raw_url(monkeypatch):
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        if cmd[:3] == ["gh", "gist", "create"]:
            assert "--public" not in cmd
            assert kwargs["input"].endswith("\n")
            return _Result(0, "https://gist.github.com/octo/abc123\n")
        if cmd[:3] == ["gh", "api", "/gists/abc123"]:
            return _Result(
                0,
                "https://gist.githubusercontent.com/octo/abc123/raw/sha/"
                "diffense-pack.json\n",
            )
        raise AssertionError(cmd)

    monkeypatch.setattr(gist.subprocess, "run", fake_run)

    published = gist.create_pack_gist(_pack())

    assert published == gist.GistPack(
        html_url="https://gist.github.com/octo/abc123",
        raw_url=(
            "https://gist.githubusercontent.com/octo/abc123/raw/sha/"
            "diffense-pack.json"
        ),
    )
    assert calls[0][0] == [
        "gh", "gist", "create", "-f", "diffense-pack.json", "-",
        "-d", "brr diffense review pack",
    ]


def test_create_pack_gist_skips_private_repo_before_writing(monkeypatch):
    def forbidden_run(*_args, **_kwargs):
        raise AssertionError("gist should not be created for private repos")

    monkeypatch.setattr(gist.subprocess, "run", forbidden_run)

    published = gist.create_pack_gist(
        _pack("acme/private"),
        repo_visibility_fn=lambda _repo: "PRIVATE",
    )

    assert published is None


def test_create_pack_gist_repo_argument_controls_visibility(monkeypatch):
    def forbidden_run(*_args, **_kwargs):
        raise AssertionError("gist should not be created for private repos")

    monkeypatch.setattr(gist.subprocess, "run", forbidden_run)

    published = gist.create_pack_gist(
        _pack(),
        repo="acme/private",
        repo_visibility_fn=lambda _repo: "internal",
    )

    assert published is None


def test_create_pack_gist_returns_none_when_gh_missing(monkeypatch):
    def fake_run(*_args, **_kwargs):
        raise FileNotFoundError

    monkeypatch.setattr(gist.subprocess, "run", fake_run)

    assert gist.create_pack_gist(_pack()) is None


def test_create_pack_gist_falls_back_to_latest_raw_url(monkeypatch):
    def fake_run(cmd, **_kwargs):
        if cmd[:3] == ["gh", "gist", "create"]:
            return _Result(0, "https://gist.github.com/octo/abc123\n")
        if cmd[:3] == ["gh", "api", "/gists/abc123"]:
            return _Result(1)
        raise AssertionError(cmd)

    monkeypatch.setattr(gist.subprocess, "run", fake_run)

    published = gist.create_pack_gist(_pack())

    assert published
    assert published.raw_url == (
        "https://gist.githubusercontent.com/octo/abc123/raw/diffense-pack.json"
    )
