"""Tests for gist-backed diffense pack publication."""

from __future__ import annotations

from pathlib import Path



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


