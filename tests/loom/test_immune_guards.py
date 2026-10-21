"""40-guards: a guard a person set lifts only with their letter, through a real hooked bare repo."""

from __future__ import annotations

from pathlib import Path

from brr.loom.runtime.merge import send_to_self
from test_immune_labels import SEED, commit, published, room, write

LETTER = "s-ae66-ada001/k4rfq"
GUARD = (
    'says = "do not touch the migrations folder"\n'
    f'set-by = "letter:{LETTER}"\n'
    '[[paths]]\nuntouched = ["migrations/**"]\n'
    '[[match]]\ntool = "Bash"\ninput = "migrations/"\n'
)


def guarded(tmp: Path) -> Path:
    """A hooked self whose main already holds a guard set by a letter."""
    bare, _work = published(tmp)
    strand = room(bare, tmp / "setter", "strand/setter")
    write(strand, "core/guards/migrations.toml", GUARD)
    commit(strand, "guard migrations")
    landed = send_to_self(strand)
    assert landed.status == "merged", landed
    return bare


def attempt(bare: Path, tmp: Path, name: str, text: str | None, msg: str):
    strand = room(bare, tmp / name, f"strand/{name}")
    path = strand / "core" / "guards" / "migrations.toml"
    if text is None:
        path.unlink()
    else:
        path.write_text(text, encoding="utf-8")
    commit(strand, msg)
    return send_to_self(strand)


def test_guard_check_is_executable() -> None:
    assert (SEED / "core" / "immune.d" / "40-guards").stat().st_mode & 0o111


def test_deleting_a_letter_guard_without_the_letter_is_refused(tmp_path: Path) -> None:
    bare = guarded(tmp_path)
    refused = attempt(bare, tmp_path, "drop", None, "drop the guard")
    assert refused.status == "refused", refused
    assert "core/guards/migrations.toml: deleted" in refused.stderr
    assert f"Guard-Lifted: {LETTER}" in refused.stderr


def test_loosening_a_letter_guard_without_the_letter_is_refused(tmp_path: Path) -> None:
    bare = guarded(tmp_path)
    for name, text in (
        ("glob", GUARD.replace('["migrations/**"]', '["migrations/old/**"]')),
        ("match", GUARD.split("[[match]]")[0]),
        ("scope", 'scope = "thread:other"\n' + GUARD),
        ("setby", GUARD.replace(f"letter:{LETTER}", "scar:git.md#x")),
        ("broken", GUARD + "[[paths\n"),
    ):
        refused = attempt(bare, tmp_path, name, text, f"loosen {name}")
        assert refused.status == "refused", (name, refused)
        assert "core/guards/migrations.toml" in refused.stderr, name


def test_the_letter_lifts_it(tmp_path: Path) -> None:
    bare = guarded(tmp_path)
    landed = attempt(bare, tmp_path, "lift", None, f"lift the guard\n\nGuard-Lifted: {LETTER}\n")
    assert landed.status == "merged", landed


def test_another_letter_does_not_lift_it(tmp_path: Path) -> None:
    bare = guarded(tmp_path)
    refused = attempt(bare, tmp_path, "wrong", None, "lift\n\nGuard-Lifted: s-ae66-ada001/zzzzz\n")
    assert refused.status == "refused", refused


def test_tightening_or_rewording_lands_without_a_letter(tmp_path: Path) -> None:
    bare = guarded(tmp_path)
    tighter = GUARD.replace('says = "do not touch the migrations folder"', 'says = "migrations are his"')
    tighter += '[[paths]]\nabsent = ["migrations/*.sql.bak"]\n'
    landed = attempt(bare, tmp_path, "tight", tighter, "tighten the guard")
    assert landed.status == "merged", landed
