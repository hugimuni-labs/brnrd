"""The pull-request footer links back to the wish, and only when the ledger can."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from brr.loom.runtime.config import ConfigError
from brr.loom.runtime.ledger import Fact
from brr.loom.runtime import publish as publish_mod
from brr.loom.runtime.verify import with_line
from brr.loom.runtime.wishes import Say, Wish


REQUEST = "please trace this pull request back to the letter that asked"


def _letter(sender: str = "person:ada") -> Fact:
    return Fact(kind="letter", by="loom:ae66", id="l-ada", data={
        "id": "l-ada", "from": sender, "body": REQUEST})


def _wish(letter: str | None = "l-ada") -> Wish:
    says = [Say(letter)] if letter else []
    return Wish(id="w-sign", says=says, sender="person", line=REQUEST, thread="work", born="t")


def _who(letter: Fact | None) -> publish_mod._Caller:
    class Home:
        root = Path("/home")

        def install_id(self) -> str:
            return "ae66"

    return publish_mod._Caller(
        home=Home(), facts=[letter] if letter else [], room=Path("."), strand="s",
        thread="work", name="widget", checkout=Path("."))


def _footer(monkeypatch, base: str | None, *, letter: Fact | None = None,
            wish: Wish | None = None, keep_letter: bool = True) -> str:
    monkeypatch.setattr(publish_mod, "dashboard_base", lambda home: base)
    import brr.loom.runtime.wishes as wishes
    monkeypatch.setattr(wishes, "told", lambda facts: {"w-sign": wish or _wish()})
    held = _letter() if keep_letter and letter is None else letter
    return publish_mod.footer(_who(held))


def test_footer_links_on_the_cloud_and_keeps_the_request_off_the_line(monkeypatch) -> None:
    line = _footer(monkeypatch, "https://brnrd.dev")
    assert line == (
        "Opened by resident brnrd for ada · install ae66 · "
        "thread [work](https://brnrd.dev/dashboard/thread/work) · "
        "wish [w-sign](https://brnrd.dev/dashboard/thread/work?wish=w-sign)")
    assert REQUEST not in line


def test_footer_links_on_the_local_board(monkeypatch) -> None:
    line = _footer(monkeypatch, "http://127.0.0.1:8765")
    assert "[work](http://127.0.0.1:8765/dashboard/thread/work)" in line
    assert "wish [w-sign](http://127.0.0.1:8765/dashboard/thread/work?wish=w-sign)" in line
    assert REQUEST not in line


def test_footer_stays_plain_with_no_base_and_with_no_person_letter(monkeypatch) -> None:
    # No dashboard: the same words as before, nothing to click.
    assert _footer(monkeypatch, None) == (
        "Opened by resident brnrd for ada · install ae66 · thread work · wish w-sign")
    # A base, but the letter is gone: omit the link. The id stays, as text.
    gone = _footer(monkeypatch, "https://brnrd.dev", keep_letter=False, wish=_wish(None))
    assert "http" not in gone and "[" not in gone
    assert gone == "Opened by resident brnrd · install ae66 · thread work · wish w-sign"
    # A letter that is not a person's is not provenance either.
    strand = _footer(monkeypatch, "https://brnrd.dev", letter=_letter("s-ae66-bijouk"))
    assert "http" not in strand and "[" not in strand


def test_dashboard_base_is_cloud_then_local_then_none(monkeypatch) -> None:
    home = SimpleNamespace(root=Path("/home"))
    monkeypatch.setattr(publish_mod, "load_config", lambda root: SimpleNamespace(board_port="8765"))
    monkeypatch.setattr("brr.loom.runtime.gates.state_dir", lambda root, config: Path("/gate"))
    assert publish_mod.dashboard_base(home) == "https://brnrd.dev"
    monkeypatch.setattr("brr.loom.runtime.gates.state_dir", lambda root, config: None)
    assert publish_mod.dashboard_base(home) == "http://127.0.0.1:8765"
    monkeypatch.setattr(publish_mod, "load_config", lambda root: SimpleNamespace(board_port=None))
    assert publish_mod.dashboard_base(home) is None
    monkeypatch.setattr(publish_mod, "load_config", lambda root: SimpleNamespace(board_port="nope"))
    assert publish_mod.dashboard_base(home) is None

    def broken(root):
        raise ConfigError("no config")

    monkeypatch.setattr(publish_mod, "load_config", broken)
    assert publish_mod.dashboard_base(home) is None


def test_sign_and_the_weakened_line_still_find_a_linked_footer() -> None:
    linked = (
        "Opened by resident brnrd · install ae66 · "
        "thread [work](https://brnrd.dev/dashboard/thread/work) · "
        "wish [w-sign](https://brnrd.dev/dashboard/thread/work?wish=w-sign)")
    body = f"Fixes it.\n\n{linked}\n\n<!-- brnrd -->"
    assert publish_mod.sign(body, "Opened by resident brnrd for bo", "ab12") == body
    # The old plain line is still the anchor too.
    plain = "Fixes it.\n\nOpened by resident brnrd for ada\n\n<!-- brnrd -->"
    assert publish_mod.sign(plain, linked, "ab12") == plain
    weakened = "tests lost an assert"
    got = with_line(body, weakened)
    assert got.index(weakened) < got.index("Opened by resident brnrd")
    assert got.count(weakened) == 1


@pytest.mark.parametrize("base", ["https://brnrd.dev", "http://127.0.0.1:9", None])
def test_each_base_renders(base, monkeypatch) -> None:
    """Cloud, local and none each produce one line ``sign`` will recognise later."""
    line = _footer(monkeypatch, base)
    assert line.startswith("Opened by resident brnrd")
    assert ("http" in line) is (base is not None)
