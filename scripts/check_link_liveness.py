#!/usr/bin/env python3
"""Check that the outbound URLs this repo publishes still resolve.

An outbound link is a claim about another system, and until this script
existed nothing in the repo re-checked any of it. The link that prompted
this (`src/frontend/src/lib/Landing.svelte:52` -> a 404'd GitHub Pages URL
left over from the `Gurio/brr` -> `hugimuni-labs/brnrd` rename) is being
fixed on a different branch; this script is the guard, not that fix.

Distinct from ``docs/scripts/check-links.mjs``: that script walks the
*built* ``docs/dist/`` HTML and checks same-origin internal navigation only
(zero network calls, every external origin skipped in a bare ``continue``).
This script does the opposite job -- it extracts absolute ``http(s)://``
URLs from *source* text across docs, the frontend, both READMEs, and the
legal pack, and asks the real internet whether each one still answers. See
``kb`` (subject-documentation.md) / the #1033 PR description for the full
"why a second script" reasoning.

    python scripts/check_link_liveness.py                    # full sweep
    python scripts/check_link_liveness.py --first-party-only # cheap PR check
    python scripts/check_link_liveness.py --report FILE       # also write FILE

Exit code is non-zero **only** when a first-party link comes back with a
status this script can prove is dead (404/410). Third-party links, and any
link that merely failed to answer (timeout, DNS failure, 5xx, 403, ...),
are reported but never fail the run -- an unreachable link is not a dead
link, and a third-party outage is never this repo's defect. See the
"unreachable vs dead" section of the docstrings below for why the line is
drawn exactly there.
"""

from __future__ import annotations

import argparse
import ipaddress
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

import requests

REPO_ROOT = Path(__file__).resolve().parent.parent

# Text surfaces carrying absolute URLs as *claims* -- docs, the frontend
# app, both READMEs, the legal export pack. Tracked files only (git
# ls-files), so nothing untracked/gitignored is ever read.
SCOPE = ["docs", "src/frontend/src", "README.md", "packaging/npm/README.md", "docs/legal"]

# Extensions that carry prose or UI markup a human wrote. Deliberately
# narrower than "everything under SCOPE": docs/package-lock.json alone
# carries several hundred npm-sponsor-link URLs that are vendor metadata,
# not a claim this repo is making, and checking them buys nothing but
# noise and third-party request volume. .json/.svg/.css/.html are excluded
# for the same reason -- see the PR description for the full survey.
TEXT_EXTENSIONS = {".md", ".mdx", ".svelte", ".ts", ".tsx", ".mjs", ".js", ".astro"}

# Frontend test fixtures intentionally use fake/reserved URLs
# (evil.example, example.test, github.com/other-org/site, a bare "forge"
# host) as input data for assertions -- they are not claims this product
# makes to a reader. Most are already caught by the structural allowlist
# below (reserved TLDs, no-dot hosts); this narrows the remainder (real-
# looking third-party paths on real hosts, e.g. github.com/o/r) that the
# allowlist can't distinguish from a genuine link by shape alone.
TEST_FILE_SUFFIXES = (".test.ts", ".test.js", ".spec.ts", ".spec.js")

# --- extraction ------------------------------------------------------------

# Placeholder spans recognisable by *shape*, per the ticket's own examples:
# <owner>, ${...}, {{...}}. Masked before URL extraction so a templated URL
# (`https://github.com/${GITHUB_REPO}`) is captured whole and can be
# recognised as "target not statically known" rather than truncated into a
# meaningless bare-origin check.
_TEMPLATE_SPAN = re.compile(r"\$\{[^}\n]*\}|\{\{[^}\n]*\}\}|<[A-Za-z][\w.:-]*>")
_TEMPLATE_SENTINEL = "\x00PLACEHOLDER\x00"

# Stops at whitespace and the punctuation that typically wraps a URL in
# markdown/HTML/source (quotes, backtick, angle brackets, closing
# paren/bracket/brace) rather than at a fixed allowed-character list --
# URLs contain too much legitimate punctuation (&, =, #, %, ~, +) to
# enumerate the other way.
_URL_RE = re.compile(r"https?://[^\s<>\"'`)\]}]+")
_TRAILING_PUNCT = ".,;:!?"


def extract_urls(text: str) -> tuple[list[str], int]:
    """URLs found in *text*, plus a count of templated URLs skipped.

    A templated URL's real target isn't statically known, so it is neither
    a link to check nor a link to silently drop -- the count makes it
    visible in the report instead.
    """
    masked = _TEMPLATE_SPAN.sub(_TEMPLATE_SENTINEL, text)
    urls: list[str] = []
    templated = 0
    for match in _URL_RE.finditer(masked):
        url = match.group(0).rstrip(_TRAILING_PUNCT)
        if not url:
            continue
        if _TEMPLATE_SENTINEL in url:
            templated += 1
            continue
        urls.append(url)
    return urls, templated


def tracked_files(repo_root: Path, scope: list[str]) -> list[Path]:
    """Tracked files under *scope*, git's own idea of what's in the repo."""
    out = subprocess.run(
        ["git", "-C", str(repo_root), "ls-files", *scope],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return [repo_root / line for line in out.splitlines() if line]


def in_scope(path: Path) -> bool:
    if path.suffix not in TEXT_EXTENSIONS:
        return False
    return not path.name.endswith(TEST_FILE_SUFFIXES)


@dataclass(frozen=True)
class Link:
    file: str
    url: str


def gather_links(repo_root: Path = REPO_ROOT, scope: list[str] | None = None) -> tuple[list[Link], int]:
    """Every extracted link under *scope*, plus the templated-URL count."""
    links: list[Link] = []
    templated_total = 0
    for path in tracked_files(repo_root, scope if scope is not None else SCOPE):
        if not in_scope(path):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        rel = path.relative_to(repo_root).as_posix()
        urls, templated = extract_urls(text)
        templated_total += templated
        links.extend(Link(rel, url) for url in urls)
    return links, templated_total


# --- classification ----------------------------------------------------

# Our own surfaces. Unlike the allowlist below (recognisable by shape),
# "is this us" cannot be derived structurally -- it has to name what we
# own. Kept short and deliberate, not grown into a member-list of every
# link we happen to trust.
FIRST_PARTY_HOSTS = {"brnrd.dev", "hugimuni-labs.github.io"}
FIRST_PARTY_GITHUB_ORG = "hugimuni-labs"

# NOTE: gurio.github.io (the org this product was renamed away from) is
# deliberately *not* first-party. It is the exact dead link #1033 found --
# still present on this branch at Landing.svelte:52, fixed elsewhere, out
# of scope here ("do not touch"). Classifying it first-party would make
# this new guard block on a known, already-owned, not-mine-to-fix-here
# defect the moment it lands. Once the other branch removes the link the
# classification of that host stops mattering; until then, third-party is
# also the honest answer -- we no longer control anything at that host.

_RESERVED_HOST_SUFFIXES = (".test", ".example", ".invalid", ".localhost")
_RESERVED_HOSTS = {"example.com", "example.org", "example.net"}


def is_first_party(url: str) -> bool:
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    if host in FIRST_PARTY_HOSTS or host.endswith("." + "brnrd.dev"):
        return True
    if host == "github.com":
        first_segment = parts.path.strip("/").split("/", 1)[0]
        if first_segment == FIRST_PARTY_GITHUB_ORG:
            return True
    return False


def is_allowlisted(url: str) -> bool:
    """True when *url* is recognisable by shape as never worth checking.

    localhost, 127.0.0.1/private ranges, the IANA-reserved test TLDs
    (.test/.example/.invalid/.localhost) and example.{com,org,net}, and
    bare single-label hostnames with no public TLD at all -- every case
    here is a structural property of the URL, never a specific member
    someone had to remember to add.
    """
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    if not host:
        return True
    if host == "localhost" or host.endswith(".localhost"):
        return True
    if host in _RESERVED_HOSTS or any(
        host.endswith("." + reserved) for reserved in _RESERVED_HOSTS
    ):
        return True
    if host.endswith(_RESERVED_HOST_SUFFIXES):
        return True
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        addr = None
    if addr is not None and (
        addr.is_loopback or addr.is_private or addr.is_reserved or addr.is_link_local
    ):
        return True
    if "." not in host:
        return True
    return False


# --- liveness ------------------------------------------------------------

DEFAULT_TIMEOUT = 10
USER_AGENT = "brnrd-link-liveness/1 (+https://github.com/hugimuni-labs/brnrd)"

# Proven dead: the server told us, unambiguously, that the resource is
# gone. Everything else -- 403 (many sites block HEAD/bot UAs), 5xx,
# timeouts, DNS failures, connection resets -- is a fact about *this
# check*, not a proof about the resource, so it is "unreachable", never
# "dead". The two words never describe the same outcome anywhere in this
# script or its output.
DEAD_STATUSES = {404, 410}


@dataclass(frozen=True)
class LinkResult:
    url: str
    status: str  # "live" | "dead" | "unreachable"
    detail: str


def check_link(url: str, *, session=requests, timeout: float = DEFAULT_TIMEOUT) -> LinkResult:
    """Probe *url*; HEAD first, ranged GET on a 405 (a 405 is not a dead link).

    *session* is injected so tests can pass a fake object exposing
    ``head``/``get`` instead of hitting the network.
    """
    headers = {"User-Agent": USER_AGENT}
    try:
        response = session.head(url, allow_redirects=True, timeout=timeout, headers=headers)
    except requests.RequestException as exc:
        return LinkResult(url, "unreachable", f"{type(exc).__name__}: {exc}")

    if response.status_code == 405:
        try:
            response = session.get(
                url,
                allow_redirects=True,
                timeout=timeout,
                headers={**headers, "Range": "bytes=0-0"},
            )
        except requests.RequestException as exc:
            return LinkResult(url, "unreachable", f"HEAD 405, ranged GET failed: {type(exc).__name__}: {exc}")

    if response.status_code in DEAD_STATUSES:
        return LinkResult(url, "dead", f"HTTP {response.status_code}")
    if 200 <= response.status_code < 400:
        return LinkResult(url, "live", f"HTTP {response.status_code}")
    return LinkResult(url, "unreachable", f"HTTP {response.status_code} (not proof of dead)")


# --- orchestration ---------------------------------------------------------


@dataclass
class Report:
    templated: int
    allowlisted: int
    first_party: dict[str, list]  # url -> [Link, ...] (a URL can appear in >1 file)
    third_party: dict[str, list]
    results: dict[str, LinkResult]
    checked_third_party: bool

    def dead_first_party(self) -> list[LinkResult]:
        return [r for u, r in self.results.items() if u in self.first_party and r.status == "dead"]

    def unreachable(self) -> list[LinkResult]:
        return [r for r in self.results.values() if r.status == "unreachable"]

    def all_unreachable(self) -> bool:
        """Every attempted check failed to complete -- 0 dead here is not clean.

        This repo has shipped an unqualified "0 dead links" reading as "checked
        and clean" twice before (#1000, #770); this is the check for that
        specific silent failure, not a general health metric.
        """
        return bool(self.results) and not any(r.status in ("live", "dead") for r in self.results.values())


def build_report(
    links: list[Link], *, templated: int = 0, first_party_only: bool, session=requests
) -> Report:
    by_url: dict[str, list[Link]] = {}
    allowlisted_skipped = 0
    for link in links:
        if is_allowlisted(link.url):
            allowlisted_skipped += 1
            continue
        by_url.setdefault(link.url, []).append(link)

    first_party = {u: files for u, files in by_url.items() if is_first_party(u)}
    third_party = {u: files for u, files in by_url.items() if u not in first_party}

    to_check = dict(first_party)
    if not first_party_only:
        to_check.update(third_party)

    results: dict[str, LinkResult] = {}
    if to_check:
        with ThreadPoolExecutor(max_workers=min(8, len(to_check))) as pool:
            for result in pool.map(lambda u: check_link(u, session=session), to_check):
                results[result.url] = result

    return Report(
        templated=templated,
        allowlisted=allowlisted_skipped,
        first_party=first_party,
        third_party=third_party,
        results=results,
        checked_third_party=not first_party_only,
    )


def render(report: Report) -> str:
    lines: list[str] = []
    lines.append("# Link liveness")
    lines.append("")
    lines.append(
        f"extracted: {len(report.first_party) + len(report.third_party)} unique URL(s) checked-eligible "
        f"({len(report.first_party)} first-party, {len(report.third_party)} third-party) -- "
        f"{report.templated} templated URL(s) skipped (target not statically known), "
        f"{report.allowlisted} allowlisted by shape (localhost/reserved-TLD/private-IP/no-dot host)."
    )
    checked = len(report.results)
    live = sum(1 for r in report.results.values() if r.status == "live")
    dead = [r for r in report.results.values() if r.status == "dead"]
    unreachable = report.unreachable()
    scope_note = "first-party only" if not report.checked_third_party else "first-party + third-party"
    lines.append(
        f"checked ({scope_note}): {checked} -- {live} LIVE, {len(dead)} DEAD, {len(unreachable)} UNREACHABLE."
    )
    if report.all_unreachable():
        lines.append("")
        lines.append(
            "WARNING: every checked link came back UNREACHABLE -- this is not a clean "
            "report, it is no data (no network in this environment, or a systemic "
            "failure). 0 DEAD here does not mean 0 dead links; see #1000 / #770."
        )
    if dead:
        lines.append("")
        lines.append("## DEAD (proven -- HTTP 404/410)")
        for result in sorted(dead, key=lambda r: r.url):
            party = "first-party" if result.url in report.first_party else "third-party"
            files = report.first_party.get(result.url) or report.third_party.get(result.url) or []
            for link in files:
                lines.append(f"- [{party}] {result.url} ({result.detail}) -- {link.file}")
    if unreachable:
        lines.append("")
        lines.append("## UNREACHABLE (not proof of dead -- timeout, DNS, 403, 5xx, ...)")
        for result in sorted(unreachable, key=lambda r: r.url):
            party = "first-party" if result.url in report.first_party else "third-party"
            lines.append(f"- [{party}] {result.url} ({result.detail})")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--first-party-only",
        action="store_true",
        help="skip third-party origins entirely (cheap mode for a PR-triggered run)",
    )
    parser.add_argument("--report", metavar="FILE", help="also write the report to FILE (markdown)")
    args = parser.parse_args(argv)

    links, templated = gather_links()
    report = build_report(links, templated=templated, first_party_only=args.first_party_only)
    failing = report.dead_first_party()

    # The verdict belongs *inside* the report, not only on stdout (added in
    # review, 2026-08-03). While the CI step runs `continue-on-error`, the
    # check renders green whatever it found, so ``--report`` is the whole
    # visible output -- and a summary that ends at "2 DEAD" beside a green
    # check is worse than silence: the reader cannot tell whether that count
    # blocked anything. Exit codes do not survive into a job summary; a
    # sentence does.
    verdict = (
        f"FAIL -- {len(failing)} first-party link(s) confirmed dead (HTTP 404/410)."
        if failing
        else "PASS -- no first-party link is confirmed dead."
    )
    text = f"{render(report)}\n{verdict}\n"
    print(text, end="")
    if args.report:
        Path(args.report).write_text(text, encoding="utf-8")
    return 1 if failing else 0


if __name__ == "__main__":
    sys.exit(main())
