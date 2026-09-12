"""Did the held native session actually survive? Read it, don't believe it.

The architecture bet, in the maintainer's words (2026-09-12): "actual
waiting and 24h+ underlying Claude Code sessions are actually extremely
cheap compared to a new spawn (context boot price) and the context stays
fresh" — against his own new doubt that Claude Code fights it, "the logouts
happen after those long lingering runs".

Both halves are beliefs about a number nobody has read, and every fact
needed is already on disk. A hold records `native_session_id`, `provider`
and `armed_at`; the run that resumes it stamps `resume_native_session_id`
on its own manifest; that run then succeeded or it didn't. So the survival
curve is a join, not an experiment.

What it reports, per park:
  * how long the seat was parked before something came back for it
  * whether the resume attached the *same* session id (a resume that
    boots cold is not a resume, and it costs exactly what a new spawn costs)
  * how the resuming run ended — and specifically whether it died on auth

Usage: python3 tools/session_survival.py [--runs .brr/runs] [--json]
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
from pathlib import Path

FIELD = re.compile(r"^([a-z_]+):\s*(.*)$")
AUTH_DEATH = re.compile(r"auth|oauth|credential|expired|unauthori[sz]ed", re.I)


def parse_manifest(path: Path) -> dict:
    """The flat frontmatter block, values left as text (JSON parsed when it is)."""
    out: dict[str, object] = {}
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return out
    if not text.startswith("---"):
        return out
    body = text.split("---", 2)
    if len(body) < 3:
        return out
    for line in body[1].splitlines():
        m = FIELD.match(line)
        if not m:
            continue
        key, raw = m.group(1), m.group(2).strip()
        if raw.startswith("{") or raw.startswith("["):
            try:
                out[key] = json.loads(raw)
                continue
            except ValueError:
                pass
        out[key] = raw
    return out


def when(value: object) -> dt.datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=Path, default=Path(".brr/runs"))
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    manifests = {}
    for f in sorted(args.runs.glob("*/run.md")):
        data = parse_manifest(f)
        if data.get("id"):
            manifests[str(data["id"])] = data

    # Every run that *claimed* a native resume, indexed by the session it attached.
    resumers: dict[str, list[dict]] = {}
    for data in manifests.values():
        sid = data.get("resume_native_session_id")
        if isinstance(sid, str) and sid:
            resumers.setdefault(sid, []).append(data)

    parks: list[dict] = []
    for run_id, data in manifests.items():
        hold = data.get("resource_hold")
        if not isinstance(hold, dict):
            continue
        sid = hold.get("native_session_id")
        armed = when(hold.get("armed_at"))
        row = {
            "run": run_id,
            "provider": hold.get("provider"),
            "reason": hold.get("reason"),
            "resume_kind": hold.get("resume_kind"),
            "resume_condition": hold.get("resume_condition"),
            "armed_at": hold.get("armed_at"),
            "released": bool(hold.get("released")),
            "released_by": hold.get("released_by"),
            "session": sid,
            "status_now": data.get("status"),
            "came_back": None,
            "gap_minutes": None,
            "resumer": None,
            "resumer_status": None,
            "auth_death": None,
            "cold_reason": None,
        }
        # The join: a run that stamped this exact session id is the one that
        # actually attached. A hold released with no such run is a release on
        # paper — the seat resumed, the session did not.
        for candidate in resumers.get(str(sid), []):
            cand_start = when(candidate.get("started_at"))
            if armed and cand_start and cand_start < armed:
                continue
            # The stamp says the daemon *offered* the session. It does not
            # say the Shell took it: `_resume_session_for_dispatch` refuses a
            # provider mismatch and records `resume_cold_reason`, leaving the
            # stamp in place. Counting a stamped-but-refused resume as a
            # survival was this instrument's own first false positive, caught
            # by the guard's own receipt (run-260908-0537-ydbu) — so the
            # refusal reason is part of the join, not a footnote to it.
            row["cold_reason"] = candidate.get("resume_cold_reason") or None
            row["came_back"] = not row["cold_reason"]
            row["resumer"] = candidate.get("id")
            row["resumer_status"] = candidate.get("status")
            if armed and cand_start:
                row["gap_minutes"] = round((cand_start - armed).total_seconds() / 60, 1)
            err = " ".join(
                str(candidate.get(k) or "")
                for k in ("error", "failure_reason", "last_error", "publish_status")
            )
            row["auth_death"] = bool(AUTH_DEATH.search(err)) if err.strip() else None
            break
        else:
            row["came_back"] = False
        parks.append(row)

    parks.sort(key=lambda r: str(r["armed_at"] or ""))

    if args.json:
        print(json.dumps(parks, indent=1))
        return 0

    # The honest denominator. A park that never promised a native resume
    # cannot have broken one: `respawn` deliberately changes the Core, and a
    # hold whose `resume_kind` is not RESUME_NATIVE had no session to hand
    # back. Counting those as failures would inflate the number in the
    # direction the reader already suspects, which is the one direction a
    # measurement must never lean.
    NATIVE = "native"
    candidates = [r for r in parks
                  if r["resume_kind"] == NATIVE and r["session"]
                  and r["released_by"] not in {"respawn", None, "-"}]

    print(f"# Native session survival — {len(parks)} parks with a hold record\n")
    hdr = (f"  {'armed':<21} {'provider':<8} {'released':<10} {'came back':<10} "
           f"{'gap':<9} {'resumer ended':<14} reason")
    print(hdr)
    print("  " + "-" * (len(hdr) - 2))
    for r in parks:
        gap = "—" if r["gap_minutes"] is None else f"{r['gap_minutes']:.0f}m"
        if r["came_back"]:
            back = "same sid"
        elif r["cold_reason"]:
            back = "refused"
        else:
            back = "—" if r["resume_kind"] != NATIVE else "COLD"
        reason = str(r["reason"] or "?")
        reason = reason if len(reason) <= 34 else reason[:31] + "..."
        print(f"  {str(r['armed_at'] or '?'):<21} {str(r['provider'] or '?'):<8} "
              f"{str(r['released_by'] or '-'):<10} {back:<10} {gap:<9} "
              f"{str(r['resumer_status'] or '-'):<14} {reason}")

    attached = [r for r in candidates if r["came_back"]]
    cold = [r for r in candidates if not r["came_back"]]
    print(f"\n## Of the parks that promised a native resume")
    print(f"  candidates: {len(candidates)} · attached the same session: {len(attached)} "
          f"· booted cold anyway: {len(cold)}")
    if attached:
        gaps = sorted(r["gap_minutes"] for r in attached if r["gap_minutes"] is not None)
        if gaps:
            print(f"  gap when it DID attach — min {gaps[0]:.0f}m · "
                  f"median {gaps[len(gaps) // 2]:.0f}m · max {gaps[-1]:.0f}m")
        bad = [r for r in attached if r["resumer_status"] not in (None, "done", "held")]
        print(f"  of those, the resuming run still failed: {len(bad)}"
              + (" — " + ", ".join(f"{r['resumer']} ({r['resumer_status']})" for r in bad)
                 if bad else ""))
    refused = [r for r in candidates if r["cold_reason"]]
    if refused:
        print(f"  offered but refused by the provider guard: {len(refused)} — "
              + "; ".join(str(r["cold_reason"]) for r in refused))
    if cold:
        by_path: dict[str, int] = {}
        for r in cold:
            by_path[str(r["released_by"])] = by_path.get(str(r["released_by"]), 0) + 1
        print("  cold-boot releases by release path: "
              + " · ".join(f"{k}={v}" for k, v in sorted(by_path.items())))
    stuck = [r for r in parks if not r["released"] and r["status_now"] == "held"]
    if stuck:
        print(f"\n  ⚠ {len(stuck)} seat(s) still held and never released: "
              + ", ".join(r["run"] for r in stuck))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
