"""GitHub check conclusions for PRs claimed by live runs.

Called only by the forge cache's refresh worker. The existing dispatch-edge
address keeps these facts in the owning run's inbox (including strands), so
ordinary await evaluation needs no polling or new condition grammar.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

from . import forges, gitops, protocol, relics
from .run import list_runs


def owners(repo_root: Path, label: str) -> dict[int, list]:
    """Live run claims, scoped to this repository even when a relic has a URL."""
    found: dict[int, list] = {}
    runtime = gitops.shared_brr_dir(repo_root)
    for task in list_runs(runtime / "runs", status="running"):
        outbox = Path(task.meta.get("outbox_path") or runtime / "outbox" / task.event_id)
        for number in claimed_numbers(outbox, label):
            found.setdefault(number, []).append((task, outbox))
    return found


def claimed_numbers(outbox: Path, label: str | None = None) -> set[int]:
    """PR numbers one run claims: `.relics.jsonl` rows of kind ``pr`` plus the
    `.pr` control file. *label* given ⇒ claims naming another repo are
    dropped. The one definition of "this run's PRs" — the refresh worker and
    the bare ``brnrd do`` screen both read it, so the checks verdict a person
    sees is for exactly the PRs the daemon watches."""
    claims = relics.read_reported(outbox)
    try:
        control = (outbox / relics.PR_CONTROL_NAME).read_text().strip()
    except OSError:
        control = ""
    if control:
        claims.append({"kind": "pr", "ref": control})
    numbers: set[int] = set()
    for claim in claims:
        if claim.get("kind") != "pr":
            continue
        parsed = forges.parse_pull_request_ref(str(
            claim.get("url") or claim.get("ref") or claim.get("number") or ""
        ))
        if not parsed:
            continue
        claim_repo, number = parsed
        if label and (
            (claim_repo and claim_repo.lower() != label.lower())
            or (claim.get("repo") and str(claim["repo"]).lower() != label.lower())
        ):
            continue
        numbers.add(int(number))
    return numbers


def _json(repo_root: Path, args: list[str], timeout: float):
    result = subprocess.run(
        ["gh", *args], cwd=repo_root, capture_output=True, text=True,
        check=False, timeout=timeout,
    )
    # gh pr checks uses 1 for failed checks and 8 for pending checks.
    if result.returncode not in (0, 1, 8):
        raise ValueError(result.stderr.strip() or "GitHub check query failed")
    if not result.stdout.strip():
        # `gh pr checks --required` on a branch with no required checks
        # configured (gh 2.97: every repo without branch protection) exits 1
        # with *empty* stdout and the verdict on stderr. That is the
        # documented "no required checks → wait for every observed check"
        # case, not a failure — and `json.loads("")` turned it into
        # `Expecting value: line 1 column 1`, so no `pr_checks_concluded`
        # ever fired on this repo (2026-09-23, #2105 green at 14:55Z, the
        # owning seat woken by an unrelated tick 16 min later).
        if "no required checks" in result.stderr:
            return [], result
        raise ValueError(result.stderr.strip() or "empty GitHub check response")
    return json.loads(result.stdout), result


def read(repo_root: Path, label: str, pr: int, sha: str, timeout: float) -> dict:
    """Read latest check runs and GitHub's required-check classification.

    No observed checks is unknown, not success. With no required checks,
    wait for every observed check. API failures never reuse a prior verdict.
    """
    pages, result = _json(repo_root, [
        "api", f"repos/{label}/commits/{sha}/check-runs?filter=latest&per_page=100",
        "--paginate", "--slurp",
    ], timeout)
    if result.returncode or not isinstance(pages, list) or not pages:
        raise ValueError("invalid check-runs response")
    runs = []
    for page in pages:
        if not isinstance(page, dict) or not isinstance(page.get("check_runs"), list):
            raise ValueError("invalid check-runs page")
        runs.extend(page["check_runs"])
    required, _ = _json(repo_root, [
        "pr", "checks", str(pr), "--repo", label, "--required", "--json", "name,bucket,state",
    ], timeout)
    if not isinstance(required, list):
        raise ValueError("invalid required-check response")
    # Required results can include legacy commit status contexts as well as
    # check runs; gh owns that join and its pass/fail classification.
    if required:
        buckets = [row.get("bucket") for row in required]
        names = {row.get("name") for row in required}
        relevant = [row for row in runs if row.get("name") in names]
        pending = any(bucket not in {"pass", "fail", "skipping", "cancel"} for bucket in buckets)
        pending |= any(row.get("status") != "completed" or not row.get("conclusion") for row in relevant)
        failed = any(bucket in {"fail", "cancel"} for bucket in buckets)
    else:
        relevant = runs
        pending = not runs or any(row.get("status") != "completed" or not row.get("conclusion") for row in runs)
        failed = any(row.get("conclusion") not in {"success", "neutral", "skipped"} for row in runs)
    # The PR may have been pushed while the API reads were in flight. Never
    # label results from the new head with the old cache row's SHA.
    head, result = _json(repo_root, ["pr", "view", str(pr), "--repo", label, "--json", "headRefOid"], timeout)
    if result.returncode or not isinstance(head, dict) or head.get("headRefOid") != sha:
        return {"status": "pending", "reason": "head changed during refresh"}
    if pending:
        return {"status": "pending"}
    conclusion = "failure" if failed else "success"
    signature = hashlib.sha256(json.dumps(
        {"runs": sorted((str(row.get("id")), row.get("conclusion")) for row in relevant),
         "required": sorted((row.get("name"), row.get("state")) for row in required)},
        sort_keys=True,
    ).encode()).hexdigest()[:20]
    return {"status": "completed", "conclusion": conclusion, "signature": signature}


def refresh(repo_root: Path, label: str, rows: list[dict], timeout: float) -> None:
    """Enrich owned PR cache rows and emit one fact per run/check generation."""
    claimed = owners(repo_root, label)
    if not claimed:
        return
    inbox = gitops.shared_brr_dir(repo_root) / "inbox"
    known = protocol.known_origin_ids(inbox, "pr_checks_key")
    for row in rows:
        pr, sha = row["number"], row.get("head_sha")
        if pr not in claimed or row.get("state") != "OPEN" or not sha:
            continue
        try:
            checks = read(repo_root, label, pr, sha, timeout)
        except (OSError, ValueError, TypeError, subprocess.TimeoutExpired) as exc:
            row["checks"] = {"status": "error", "error": str(exc)}
            continue
        row["checks"] = checks
        if checks["status"] != "completed":
            continue
        for task, _outbox in claimed[pr]:
            key = f"{task.id}:{pr}:{sha}:{checks['signature']}"
            if key in known:
                continue
            protocol.create_event(
                inbox, "pr_checks_concluded",
                f"PR #{pr} at {sha}: checks concluded ({checks['conclusion']}).",
                pr=pr, sha=sha, conclusion=checks["conclusion"], pr_checks_key=key,
                repo=label, conversation_key=task.conversation_key or "",
                # Existing edge routing is deliberately shared with `to:`:
                # visible only to the owner, never a fresh dispatched thought.
                spawn_message_for_event=task.event_id,
            )
            known.add(key)
