"""The loom config: one declaration, seeded by init, strict at load, printable."""

from __future__ import annotations

import dataclasses
import subprocess
import sys
from pathlib import Path

import pytest

from brr.loom.runtime.config import (
    KEYS, ConfigError, describe_config, load_config, load_config_report,
    render_config, resolve_secret, secret_shell,
)
from brr.loom.runtime.selfrepo import init_self
from brr.loom.runtime.service import main as service_main


def _home(tmp_path: Path, text: str | None = None) -> Path:
    home = tmp_path / "home"
    (home / "loom").mkdir(parents=True)
    if text is not None:
        (home / "loom" / "config.toml").write_text(text)
    return home


def test_declaration_is_the_dataclass():
    assert [f.name for f in dataclasses.fields(load_config(Path("/nonexistent")))] == [k.name for k in KEYS]
    assert len(KEYS) == 21


def test_a_retired_key_still_loads_and_is_ignored(tmp_path):
    # boot-review v2 retired the shed trigger's keys; a file that names them must still load.
    from brr.loom.runtime.config import RETIRED, config_path
    path = config_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('shed_hold = ["home"]\nshed_excess = 60000\n')
    assert RETIRED == {"shed_excess", "shed_hold"}
    assert not hasattr(load_config(tmp_path), "shed_hold")


def test_unknown_key_is_refused_with_the_nearest(tmp_path):
    home = _home(tmp_path, 'forge_token_file = "x"\n')
    with pytest.raises(ConfigError) as err:
        load_config(home)
    msg = str(err.value)
    assert "config.toml" in msg and "forge_token" in msg and "did you mean forge_token?" in msg


@pytest.mark.parametrize("line", ['stop_grace = "soon"', "stop_grace = -1", "router_ttl = 0", "author = 5", "gh = true"])
def test_wrong_type_is_refused(tmp_path, line):
    with pytest.raises(ConfigError, match=line.split(" ")[0]):
        load_config(_home(tmp_path, line + "\n"))


def test_init_seeds_once_and_leaves_an_edit_alone(tmp_path):
    home = tmp_path / "h"
    init_self(home, person="p")
    path = home / "loom" / "config.toml"
    assert path.read_text() == render_config()
    path.write_text('margin = 2\n')
    init_self(home, person="p")
    assert path.read_text() == 'margin = 2\n'


def test_rendered_file_round_trips(tmp_path):
    seeded = render_config()
    assert all(f"# {k.name} = " in seeded and f"# {k.help}" in seeded for k in KEYS)
    base = load_config_report(_home(tmp_path, seeded))
    assert base[1] == frozenset()
    uncommented = "\n".join(l[2:] if l.startswith("# ") and l[2:].split(" = ")[0] in {k.name for k in KEYS} else l for l in seeded.splitlines())
    other = tmp_path / "other"
    (other / "loom").mkdir(parents=True)
    (other / "loom" / "config.toml").write_text(uncommented)
    cfg, from_file = load_config_report(other)
    assert cfg == base[0]
    assert from_file == {k.name for k in KEYS}


def test_loom_config_prints_key_value_source_help(tmp_path, capsys):
    home = _home(tmp_path, 'stop_grace = 9\nbody = "claude/sonnet"\n')
    assert service_main(["config", "--home", str(home)]) == 0
    out = capsys.readouterr().out.splitlines()
    rows = {l.split(" = ")[0].strip(): l for l in out if " = " in l}
    assert set(rows) == {k.name for k in KEYS}
    assert '= 9 · file ·' in rows["stop_grace"]
    assert '= "claude/sonnet" · file ·' in rows["body"]
    assert "= 30 · default ·" in rows["router_ttl"]


def test_loom_config_reports_a_refused_file(tmp_path, capsys):
    home = _home(tmp_path, "nope = 1\n")
    assert service_main(["config", "--home", str(home)]) == 1
    assert "unknown key 'nope'" in capsys.readouterr().err


@pytest.mark.parametrize(("raw", "normalised"), [
    (" command : echo token ", "command: echo token"),
    ("file: /tmp/token", "file: /tmp/token"),
    (" env : TOKEN_1 ", "env: TOKEN_1"),
    ("", None),
])
def test_secret_source_is_normalised(tmp_path, raw, normalised):
    cfg = load_config(_home(tmp_path, f'forge_token = "{raw}"\n'))
    assert cfg.forge_token == normalised


def test_secret_file_expands_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    cfg = load_config(_home(tmp_path, 'forge_token = "file: ~/token"\n'))
    assert cfg.forge_token == f"file: {tmp_path}/token"


@pytest.mark.parametrize("raw", ["token", "command:", "file: ", "env:", "env: 1BAD", "env: A B", "url: x"])
def test_bad_secret_source_is_refused(tmp_path, raw):
    with pytest.raises(ConfigError) as err:
        load_config(_home(tmp_path, f'forge_token = "{raw}"\n'))
    assert 'forge_token must be "command: <shell>", "file: <path>" or "env: <NAME>"' in str(err.value)


def test_secret_wrong_type_is_refused(tmp_path):
    with pytest.raises(ConfigError, match="forge_token must be a quoted string"):
        load_config(_home(tmp_path, "forge_token = 42\n"))


def test_secret_command_hash_is_not_a_config_comment(tmp_path):
    cfg = load_config(_home(tmp_path, 'forge_token = "command: echo token # shell comment" # config comment\n'))
    assert cfg.forge_token == "command: echo token # shell comment"
    assert resolve_secret(cfg.forge_token) == "token"


@pytest.mark.parametrize("source", ["command", "file", "env"])
def test_resolve_secret_and_shell_read_each_source(tmp_path, monkeypatch, source):
    token = tmp_path / "token with spaces"
    token.write_text("first-token\n")
    monkeypatch.setenv("TEST_FORGE_TOKEN", "first-token")
    spec = {"command": f"command: cat '{token}'", "file": f"file: {token}",
            "env": "env: TEST_FORGE_TOKEN"}[source]
    assert resolve_secret(spec) == "first-token"
    proc = subprocess.run(["sh", "-c", secret_shell(spec)], capture_output=True, text=True)
    assert proc.returncode == 0
    assert proc.stdout.strip() == "first-token"
    token.write_text("next-token\n")
    monkeypatch.setenv("TEST_FORGE_TOKEN", "next-token")
    assert resolve_secret(spec) == "next-token"


def test_failed_secret_command_does_not_expose_output():
    with pytest.raises(ConfigError) as err:
        resolve_secret("command: printf output; printf error >&2; exit 7")
    assert str(err.value) == "secret command exited 7: printf output; printf error >&2; exit 7"


@pytest.mark.parametrize("spec", ["command: true", "env: MISSING_FORGE_SECRET", "file"])
def test_empty_secret_is_refused(tmp_path, monkeypatch, spec):
    monkeypatch.delenv("MISSING_FORGE_SECRET", raising=False)
    if spec == "file":
        token = tmp_path / "empty"
        token.write_text(" \n")
        spec = f"file: {token}"
    with pytest.raises(ConfigError, match="gave nothing"):
        resolve_secret(spec)


def test_missing_secret_file_names_only_source(tmp_path):
    with pytest.raises(ConfigError, match="secret file"):
        resolve_secret(f"file: {tmp_path}/missing")


def test_config_description_and_cli_do_not_resolve_secrets(tmp_path, capsys, monkeypatch):
    import brr.loom.runtime.config as config
    monkeypatch.setattr(config, "resolve_secret", lambda _: pytest.fail("must not resolve"))
    home = _home(tmp_path, 'forge_token = "command: never run me"\n')
    row = next(row for row in describe_config(home) if row[0] == "forge_token")
    assert row[1:3] == ("command: never run me", "file")
    assert service_main(["config", "--home", str(home)]) == 0
    assert '= "command: never run me" · file ·' in capsys.readouterr().out


def test_secret_command_stderr_cannot_leak_a_resolved_value(tmp_path):
    token = tmp_path / "token"
    token.write_text("do-not-log-this-token\n")
    with pytest.raises(ConfigError) as err:
        resolve_secret(f"command: cat '{token}' >&2; exit 7")
    assert "do-not-log-this-token" not in str(err.value)


def test_non_utf8_secret_file_is_refused_without_its_bytes(tmp_path):
    token = tmp_path / "token"
    token.write_bytes(b"secret-value-\xff")
    with pytest.raises(ConfigError) as err:
        resolve_secret(f"file: {token}")
    assert str(err.value) == f"secret file {token}: not UTF-8 text"


def test_loom_start_folds_a_key_set_twice_and_runs_the_body_in_effect(tmp_path, monkeypatch):
    from brr.loom.runtime import verbs_loom

    home = _home(tmp_path, 'body = "claude/opus"\nguard = "off"\nbody = "codex/gpt-6"\n')
    ran = {}
    monkeypatch.setattr(verbs_loom, "run_loom", lambda home, **kw: ran.update(kw))
    assert verbs_loom._loom(["--home", str(home)]) == 0
    assert (ran["adapter"], ran["core"]) == ("codex", "gpt-6")
    assert load_config(home).body == "codex/gpt-6"
