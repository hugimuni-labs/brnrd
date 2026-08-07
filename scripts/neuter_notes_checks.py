#!/usr/bin/env python3
"""Neuter each notes check, watch it go red, restore. Print the receipts.

The discipline this automates: a deterministic check that is silent when
clean is indistinguishable, from the outside, from a check that is silent
always. The only proof that a green suite means anything is to break what
each check guards and watch the suite refuse.

Every mutation is applied to the working tree, the named tests are run,
and the tree is restored with ``git checkout --`` whether or not the run
succeeded. A mutation whose tests stay **green** is reported as a
FAILURE — that is a check with no teeth, and the exit code says so.

Not part of the gate: this is a receipt-generating tool, run by hand
(``python scripts/neuter_notes_checks.py``) when the checks change.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

#: (label, file, old, new, pytest -k selector, what the mutation removes)
MUTATIONS: list[tuple[str, str, str, str, str, str]] = [
    (
        "inert-pitfall: the store's own inert() answer",
        "src/brr/pitfalls.py",
        "    return [p for p in pitfalls if not any(t.strip() for t in p.triggers)]",
        "    return []  # NEUTERED",
        "TestPitfallStore or InertPitfall",
        "the owning module stops reporting triggerless entries",
    ),
    (
        "unindexed-pitfall-section: the heading/parse diff",
        "src/brr/notes_preflight.py",
        "    for title in headings:\n        if title in parsed_titles:\n            continue",
        "    for title in headings:\n        if True:  # NEUTERED\n            continue",
        "TestPitfallStore",
        "the diff against what the parser actually indexed",
    ),
    (
        "pitfall-store-unreadable: the sanity assertion",
        "src/brr/notes_preflight.py",
        "    if headings and not parsed:",
        "    if False and headings and not parsed:  # NEUTERED",
        "TestPitfallStore",
        "the state where this check has gone blind",
    ),
    (
        "eviction-preview: the overflow record",
        "src/brr/notes_preflight.py",
        "    if overflow is None:\n        return []",
        "    if True:  # NEUTERED\n        return []",
        "TestSelfInjectEviction",
        "the budget shortfall the wake will actually pay",
    ),
    (
        "eviction-preview: the tuple trap the ticket named",
        "src/brr/notes_preflight.py",
        "    digest, overflow = dominion_mod.resolve_self_inject_digest(\n"
        "        dominion_dir, budget_bytes=budget_bytes,\n    )",
        "    _pair = dominion_mod.resolve_self_inject_digest(\n"
        "        dominion_dir, budget_bytes=budget_bytes,\n    )\n"
        "    digest, overflow = str(_pair), None  # NEUTERED: the == bug",
        "TestSelfInjectEviction",
        "reading the pair as a string, the bug the spec named in advance",
    ),
    (
        "self-inject-empty: the sanity assertion",
        "src/brr/notes_preflight.py",
        "    if entries and not digest.strip():",
        "    if False and entries and not digest.strip():  # NEUTERED",
        "TestSelfInjectEviction",
        "a manifest that resolves to nothing reported as clean",
    ),
    (
        "eviction-preview: the work-surface read-back",
        "src/brr/notes_preflight.py",
        "    for match in _PAGE_OMITTED_RE.finditer(text):",
        "    for match in []:  # NEUTERED",
        "TestWorkSurfaceEviction",
        "the assembler's own page-omitted marker",
    ),
    (
        "stale-signature: deletions-only (the 2026-07-25 amendment)",
        "src/brr/notes_preflight.py",
        '        if line.startswith("-"):\n'
        "            hit_sha, hit_date = sha, date.strip()",
        '        if line.startswith(("-", "+")):  # NEUTERED: append counts too\n'
        "            hit_sha, hit_date = sha, date.strip()",
        "TestSignatureFindings",
        "the append/rewrite distinction — a guard that fires for a non-reason",
    ),
    (
        "unsigned-clause: the coverage join",
        "src/brr/notes_preflight.py",
        "        if norm not in covered:",
        "        if False:  # NEUTERED",
        "TestSignatureFindings",
        "sections no signature scopes",
    ),
    (
        "signature-scope-unmatched: the sanity assertion",
        "src/brr/notes_preflight.py",
        "            if name not in by_norm:",
        "            if False:  # NEUTERED",
        "TestSignatureFindings",
        "a signature scoping a section that no longer exists",
    ),
    (
        "retracted signatures stop covering",
        "src/brr/notes_preflight.py",
        '        if "RETRACTED" in raw:\n            current["retracted"] = True',
        '        if False:  # NEUTERED\n            current["retracted"] = True',
        "TestSignatureParsing",
        "the retraction marker — a withdrawn clause read as agreed",
    ),
    (
        "the scan says what it could not read (roots)",
        "src/brr/notes_preflight.py",
        "    unread = notes_mod.unresolved_roots(roots, rows)",
        "    unread = []  # NEUTERED",
        "TestTheScanKnowsWhatItCouldNotSee",
        "the blind-spot report — a clean verdict about surfaces never located",
    ),
    (
        "a resolved-but-empty root is unread, not clean",
        "src/brr/notes.py",
        "        elif rows is not None and root not in populated:",
        "        elif False:  # NEUTERED",
        "TestTheScanKnowsWhatItCouldNotSee",
        "the #1193 fingerprint — every root resolves, none holds anything",
    ),
    (
        "the dominion candidate must hold a dominion surface",
        "src/brr/notes.py",
        "            if any((candidate.path / name).exists() for name in wanted):",
        "            if True:  # NEUTERED: first directory wins",
        "TestTheScanKnowsWhatItCouldNotSee or TestScanAndBlock",
        "picking the store the wake reads over the first bare directory",
    ),
    (
        "eviction-preview: the budget the wake actually spends",
        "src/brr/notes_preflight.py",
        "        budget_bytes = dominion_mod.inject_budget_bytes(cfg)",
        "        budget_bytes = dominion_mod.DEFAULT_INJECT_BUDGET_BYTES  # NEUTERED",
        "TestSelfInjectEviction",
        "reading the constant instead of the configured budget",
    ),
    (
        "stale-signature: HEAD's line numbers, not the worktree's",
        "src/brr/notes_preflight.py",
        "        rewrite = _last_rewrite(repo_dir, rel_path, *head_range)",
        "        rewrite = _last_rewrite(repo_dir, rel_path, start, end)  # NEUTERED",
        "TestSignatureFindings",
        "the HEAD-relative range — an uncommitted edit shifts the walk",
    ),
    (
        "eviction-preview: a page name with a space",
        "src/brr/notes_preflight.py",
        r'    r"^### (?P<page>.+)\n\n_\(page omitted',
        r'    r"^### (?P<page>\S+)\n\n_\(page omitted  # NEUTERED',
        "TestWorkSurfaceEviction",
        "matching a page name past its first word",
    ),
    (
        "brnrd notes: verdicts attributed by path, not basename",
        "src/brr/cli.py",
        '        if any(c == head or c.endswith("/" + head) for c in candidates):',
        "        if any(c.rsplit('/', 1)[-1] == head.rsplit('/', 1)[-1]\n"
        "               for c in candidates):  # NEUTERED",
        "TestNotesCommand",
        "path-suffix matching — two surfaces named index.md collide",
    ),
    (
        "signatures: the git-pin scrub on the history walk",
        "src/brr/notes_preflight.py",
        '            env=gitops.explicit_repo_env(),\n        )\n    except (OSError, ValueError):\n        return None, None',
        "            env=None,  # NEUTERED: obey an inherited GIT_DIR pin\n        )\n    except (OSError, ValueError):\n        return None, None",
        "TestSignatureFindings",
        "the scrub that stops a wake's GIT_DIR pin answering for the wrong repo",
    ),
    (
        "registry: every entry can be located",
        "src/brr/notes.py",
        '    "pitfalls": lambda r: _one(r.dominion, "pitfalls.md"),',
        "    # NEUTERED: resolver removed",
        "TestRegistry",
        "the registry's own sanity assertion",
    ),
]


def run(label: str, selector: str) -> bool:
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "tests/test_notes.py",
         "tests/test_prompts.py", "-k", selector],
        cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    return proc.returncode == 0


def main() -> int:
    dirty = subprocess.run(
        ["git", "status", "--porcelain", "--", "src/brr", "tests"],
        cwd=ROOT, stdout=subprocess.PIPE, text=True, check=False,
    ).stdout.strip()
    if dirty:
        print("refusing to run: src/ or tests/ has uncommitted changes.")
        print("Restoration is `git checkout --`, which would discard them.")
        return 2

    failures = 0
    print(f"{'check':<58} {'clean':<7} {'neutered':<9} verdict")
    for label, rel, old, new, selector, removes in MUTATIONS:
        path = ROOT / rel
        source = path.read_text(encoding="utf-8")
        if old not in source:
            print(f"{label:<58} {'—':<7} {'—':<9} MUTATION NO LONGER APPLIES")
            failures += 1
            continue
        before = run(label, selector)
        path.write_text(source.replace(old, new, 1), encoding="utf-8")
        try:
            after = run(label, selector)
        finally:
            subprocess.run(["git", "checkout", "--", rel], cwd=ROOT, check=True)
        ok = before and not after
        failures += 0 if ok else 1
        print(
            f"{label:<58} {'green' if before else 'RED':<7} "
            f"{'green' if after else 'red':<9} "
            f"{'✓ has teeth' if ok else '✗ NO-OP CHECK'}"
        )
        if not ok:
            print(f"{'':<58} removed: {removes}")

    print()
    print(f"{len(MUTATIONS) - failures}/{len(MUTATIONS)} checks proved they can fail")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
