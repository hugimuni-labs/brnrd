"""Keep loom copy, comments, and seed files gender-neutral."""

import re
from pathlib import Path

LOOM = Path(__file__).resolve().parents[2] / "src" / "brr" / "loom"
PRONOUN = re.compile(r"\b(he|his|him|himself|she|her|hers|herself)\b", re.IGNORECASE)

# These exact lines recognize words in input. They do not describe anyone.
ALLOWED_LINES = {
    ("runtime/idle.py", 'WAITS_ON = r"his|her|hers|him|he|she|the person"'),
    ("runtime/recall.py", "each every for from had has have her here his how into its just like more most much must"),
    ("runtime/recall.py", "not now off once only other our out over own same she should since some such than that"),
}


def test_loom_does_not_gender_the_person():
    hits = []
    for path in sorted(LOOM.rglob("*")):
        if not path.is_file() or "__pycache__" in path.parts:
            continue
        relative = path.relative_to(LOOM).as_posix()
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if PRONOUN.search(line) and (relative, line.strip()) not in ALLOWED_LINES:
                hits.append(f"src/brr/loom/{relative}:{number}: {line.strip()}")

    assert not hits, "Gendered pronouns in loom source:\n" + "\n".join(hits)
