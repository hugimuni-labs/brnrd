from types import SimpleNamespace
import subprocess

from brr.loom.runtime import installs


def test_name_uses_alias_then_macos_name_then_hostname(monkeypatch):
    installs.computer_name.cache_clear()
    monkeypatch.setattr(installs.platform, "system", lambda: "Darwin")
    calls = []
    def run(args, **kw):
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, "Living room iMac\n", "")
    monkeypatch.setattr(installs.subprocess, "run", run)
    monkeypatch.setattr(installs.socket, "gethostname", lambda: "fallback.local")
    try:
        assert installs.display_name(SimpleNamespace(name="Desk")) == "Desk"
        assert not calls
        assert installs.display_name(SimpleNamespace(name="")) == "Living room iMac"
        assert calls == [["scutil", "--get", "ComputerName"]]
        installs.computer_name.cache_clear()
        monkeypatch.setattr(installs.subprocess, "run", lambda *a, **kw: (_ for _ in ()).throw(OSError()))
        assert installs.display_name(SimpleNamespace(name="")) == "fallback.local"
    finally:
        installs.computer_name.cache_clear()


def test_alias_command_updates_config_and_announces_only_changes(tmp_path, monkeypatch):
    from brr.loom.runtime.config import load_config
    from brr.loom.runtime.home import Home
    from brr.loom.runtime.ledger import read_facts
    from brr.loom.runtime.router import Router
    from brr.loom.runtime.board import machines_of
    from brr.loom.runtime.verbs_machines import _machines
    from brr.daemon2.facts import Fact

    home = Home(tmp_path / "home", install="aaaa")
    monkeypatch.setattr(installs, "computer_name", lambda: "iMac")
    assert _machines(["--home", str(home.root), "name", "Desk"]) == 0
    assert load_config(home.root).name == "Desk"
    router = Router(home)
    facts = router.begin_tick(read_facts(home))
    facts = router.begin_tick(facts)
    names = [f for f in facts if f.kind == "machines.name"]
    assert len(names) == 1 and names[0].data["name"] == "Desk"
    facts.append(Fact(kind="machines.name", by="loom:bbbb", id="peer-name",
                      data={"install": "bbbb", "name": "Laptop"}))
    facts.append(Fact(kind="router", by="loom:bbbb", id="peer-router",
                      data={"install": "bbbb", "gen": 999}))
    rows = machines_of(home, facts)
    assert rows[0]["name"] == "Laptop" and rows[0]["lead"]
    assert next(r for r in rows if r["here"])["name"] == "Desk"
    assert _machines(["--home", str(home.root), "name", ""]) == 0
    router.config = load_config(home.root)
    facts = router.begin_tick(facts)
    assert [f.data["name"] for f in facts if f.kind == "machines.name"
            and f.data["install"] == "aaaa"] == ["Desk", "iMac"]
