"""Tests for config parser."""

from brr.config import parse_frontmatter, load_config


def test_parse_basic():
    text = """\
---
brr:
  version: 1
  mode: paused
  default_executor: auto
  commands:
    verify: "make test"
    status: ""
  task_sources: []
  state_file: agent_state.md
---

# Body
"""
    fm = parse_frontmatter(text)
    brr = fm["brr"]
    assert brr["version"] == 1
    assert brr["mode"] == "paused"
    assert brr["default_executor"] == "auto"
    assert brr["commands"]["verify"] == "make test"
    assert brr["task_sources"] == []
    assert brr["state_file"] == "agent_state.md"


def test_parse_list():
    text = "---\nbrr:\n  sources: [a, b, c]\n---\n"
    fm = parse_frontmatter(text)
    assert fm["brr"]["sources"] == ["a", "b", "c"]


def test_parse_no_frontmatter():
    assert parse_frontmatter("# Just a doc") == {}


def test_load_config_missing(tmp_path):
    assert load_config(tmp_path) == {}
