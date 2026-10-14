"""The loom config: one declaration, seeded by init, strict at load, printable."""

from __future__ import annotations

import dataclasses
import subprocess
import sys
from pathlib import Path

import pytest

from brr.loom.runtime.config import KEYS, ConfigError, load_config, load_config_report, render_config
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
    assert len(KEYS) == 11


def test_unknown_key_is_refused_with_the_nearest(tmp_path):
    home = _home(tmp_path, 'forge_token = "x"\n')
    with pytest.raises(ConfigError) as err:
        load_config(home)
    msg = str(err.value)
    assert "config.toml" in msg and "forge_token" in msg and "did you mean forge_token_file?" in msg


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
