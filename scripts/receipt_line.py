"""Re-measure the README's receipt line — the git-log numbers under the title.

A numeral in prose is a drift bomb: "1,109 merged PRs" was true for the hour
it was written (2026-09-05 21:00Z) and stale by the next merge. This script
measures the three numbers the line carries and rewrites the line in place,
so the counter ticks in the git log itself — the thing the line is about.

    python scripts/receipt_line.py            # rewrite README.md if changed
    python scripts/receipt_line.py --check    # exit 1 if README is stale

Measurements (all from this checkout + the GitHub search API):
- commits on main: ``git rev-list --count --no-merges HEAD`` — merges excluded
  on both counts so the two numbers share a denominator
- resident commits: authors matching ``_RESIDENT_AUTHORS`` in ``git shortlog --no-merges``
- merged PRs: ``search/issues?q=repo:<slug>+is:pr+is:merged`` → ``total_count``
  (``GH_TOKEN`` / ``GITHUB_TOKEN`` for the rate limit; anonymous works at 10/min)

The line's shape is the contract: the regex below is what the workflow
rewrites, so edit the README's wording *and* the regex together.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import urllib.request
from datetime import date
from pathlib import Path

REPO_SLUG = "hugimuni-labs/brnrd"
FIRST_COMMIT_MONTH = "March 2026"
_RESIDENT_AUTHORS = ("brnrd-bot", "brnrd-dev[bot]")
_LINE_RE = re.compile(
    r"<strong>(?P<commits>[\d,]+) commits on main · (?P<resident>[\d,]+) by the resident · "
    r"(?P<prs>[\d,]+) merged PRs · since [A-Za-z]+ \d{4}"
    r"(?: · as of \d{4}-\d{2}-\d{2})?\.</strong>"
)


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], capture_output=True, text=True, check=True).stdout


def measure() -> dict[str, int]:
    commits = int(_git("rev-list", "--count", "--no-merges", "HEAD").strip())
    resident = 0
    for line in _git("shortlog", "-sn", "--no-merges", "HEAD").splitlines():
        count, _, author = line.strip().partition("\t")
        if author.strip() in _RESIDENT_AUTHORS:
            resident += int(count)
    url = f"https://api.github.com/search/issues?q=repo:{REPO_SLUG}+is:pr+is:merged&per_page=1"
    req = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json"})
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310 — fixed https URL
        prs = int(json.load(resp)["total_count"])
    return {"commits": commits, "resident": resident, "prs": prs}


def render(numbers: dict[str, int], today: date | None = None) -> str:
    today = today or date.today()
    return (
        f"<strong>{numbers['commits']:,} commits on main · {numbers['resident']:,} by the resident · "
        f"{numbers['prs']:,} merged PRs · since {FIRST_COMMIT_MONTH} · as of {today.isoformat()}.</strong>"
    )


def rewrite(readme: Path, numbers: dict[str, int], today: date | None = None) -> bool:
    text = readme.read_text(encoding="utf-8")
    match = _LINE_RE.search(text)
    if not match:
        raise SystemExit("receipt line not found in README — wording and _LINE_RE drifted apart")
    new = render(numbers, today)
    if match.group(0) == new:
        return False
    readme.write_text(text[: match.start()] + new + text[match.end():], encoding="utf-8")
    return True


def main(argv: list[str]) -> int:
    readme = Path(__file__).resolve().parents[1] / "README.md"
    numbers = measure()
    if "--check" in argv:
        current = _LINE_RE.search(readme.read_text(encoding="utf-8"))
        stale = current is None or current.group(0) != render(numbers)
        print(("stale: " if stale else "current: ") + render(numbers))
        return 1 if stale else 0
    changed = rewrite(readme, numbers)
    print(("rewrote: " if changed else "unchanged: ") + render(numbers))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
