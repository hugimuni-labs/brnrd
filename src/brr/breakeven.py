"""Measured respawn payback. Unknown accounting stays unknown; no seat policy.

Input-equivalent weights are allowance's, not a quota exchange rate. Native
cache counters describe the actual request, including a cold write after TTL;
we never infer cache hits from elapsed time. A boundary is a distinct model
request, not each tool in its parallel batch. First-three post-boot requests
form each run's baseline; the last ten matching Shell+Core runs form a cohort.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from statistics import median
from typing import Any, Iterator, Iterable

from . import allowance, codex_status, protocol, vibe_usage

HISTORY_RUNS = 10
FRESH_BOUNDARIES = 3


def number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        return float(value) if math.isfinite(value) and value >= 0 else None
    except OverflowError:
        return None


def rows(path: Path) -> Iterator[dict]:
    try:
        with path.open(encoding="utf-8", errors="replace") as handle:
            for line in handle:
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if isinstance(row, dict):
                    yield row
    except OSError:
        return


def mapping(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _usage(usage: Any, shell: str) -> dict | None:
    if not isinstance(usage, dict):
        return None
    if shell == "claude":
        def field(snake: str, camel: str) -> float | None:
            return number(usage.get(snake, usage.get(camel)))
        fresh = field("input_tokens", "inputTokens")
        cached = field("cache_read_input_tokens", "cacheReadInputTokens")
        writes = field("cache_creation_input_tokens", "cacheCreationInputTokens")
        output = field("output_tokens", "outputTokens")
    else:
        total = number(usage.get("input_tokens"))
        cached = number(usage.get("cached_input_tokens"))
        output = number(usage.get("output_tokens"))
        # Codex/Vibe define cached as a subset of input; absent write counter
        # means fresh-input billing, not an inferred cache creation class.
        writes = number(usage.get("cache_write_input_tokens", 0))
        fresh = total - cached - writes if None not in (total, cached, writes) else None
    if None in (fresh, cached, writes, output) or fresh < 0:
        return None
    if fresh + cached + writes + output == 0:
        return None  # placeholder usage before a billed request, not a free call
    return {
        "weighted": allowance.weighted_tokens(input=fresh, output=output,
                       cache_read=cached, cache_creation=writes),
        # Preserve the existing boot definition: establishing context, excluding
        # output and already cached context. Zero is a measured value.
        "boot_weighted": allowance.weighted_tokens(input=fresh, cache_creation=writes),
        "input": fresh, "cache_read": cached, "cache_creation": writes, "output": output,
    }


def requests(path: Path | Iterable[dict], shell: str, *, core: str | None = None) -> Iterator[dict]:
    """Usage-only projection from native records. Never copy prompt/tool text."""
    model = core
    pending = None
    for row in rows(path) if isinstance(path, Path) else path:
        usage = key = None
        if shell == "claude" and row.get("type") == "assistant":
            msg = mapping(row.get("message"))
            usage, key = msg.get("usage"), msg.get("id")
            model = msg.get("model")
            if isinstance(model, str) and model.startswith("<"):
                continue
        elif shell == "codex":
            payload = mapping(row.get("payload"))
            if row.get("type") == "turn_context":
                model = payload.get("model")
            if row.get("type") == "event_msg" and payload.get("type") == "token_count":
                info = mapping(payload.get("info"))
                usage = info.get("last_token_usage")
                total = mapping(info.get("total_token_usage"))
                # Cumulative input changes once per request, not for a quota-only
                # token_count re-emission. Never confuse the last with the total.
                key = total.get("input_tokens")
        elif shell == "vibe" and row.get("type") == "action_result":
            payload = mapping(row.get("payload"))
            result = mapping(mapping(payload.get("result")).get("result"))
            usage, key = result.get("usage"), payload.get("action_id")
        parts = _usage(usage, shell)
        if parts is None or key is None:
            continue
        request = {**parts, "request": str(key), "core": model}
        # Streaming updates of one request replace its usage, not add to it.
        if pending is not None and pending["request"] != request["request"]:
            yield pending
        pending = request
    if pending is not None:
        yield pending


def native(path: Path, shell: str, *, core: str | None = None) -> dict:
    first: list[dict] = []
    latest = None
    for request in requests(path, shell):
        if len(first) < FRESH_BOUNDARIES + 1:
            first.append(request)
        latest = request
    if not first:
        return {}
    return {"shell": shell, "core": core or latest.get("core"),
            "boot": first[0]["boot_weighted"],
            "boot_request": first[0]["request"],
            "baseline": first[1:], "latest": latest}


def coordinates(meta: dict, runner_name: str | None, work_dir: Path | None,
                outbox_dir: Path | None, *, not_before: float | None = None) -> tuple:
    paths: list[Path] = []
    core = None
    if codex_status.supported(runner_name):
        shell = "codex"
        sid = codex_status._safe_thread_id(meta.get("codex_thread_id"))
        if sid:
            path = codex_status._rollout_for_thread(codex_status.sessions_root(), sid)
            paths = [path] if path else []
    elif vibe_usage.supported(runner_name):
        shell = "vibe"
        sidecar = (vibe_usage.load_sidecar(outbox_dir) if outbox_dir else None) or meta.get("_historical_vibe_sidecar")
        sid = (sidecar or {}).get("session_id")
        if isinstance(sid, str) and vibe_usage.valid_session_id(sid):
            home = vibe_usage._vibe_home()
            session = home / "logs/session/unified" / sid
            paths = sorted((session / "journal").glob("*.jsonl"))
            core = sidecar.get("model") or (vibe_usage.collect(sid) or {}).get("model")
    elif str(runner_name or "").startswith("claude"):
        shell = "claude"
        from . import claude_status
        sid = meta.get("claude_session_id")
        path = (claude_status.session_transcript_path(sid) if sid else
                allowance.latest_claude_transcript(work_dir, not_before=not_before))
        paths = [path] if path else []
    else:
        return None, [], None
    return shell, paths, core


def collect(meta: dict, runner_name: str | None, work_dir: Path | None,
            outbox_dir: Path | None, *, not_before: float | None = None) -> dict:
    """Resolve only this run's coordinate. Cache by file signatures on task meta.

    Claude's existing cwd/not-before locator is retained; Codex requires the
    exact thread id, Vibe its adapter's exact session sidecar. No newest-global
    session fallback. Caches belong to a task, never module-global test state.
    """
    shell, paths, core = coordinates(meta, runner_name, work_dir, outbox_dir,
                                     not_before=not_before)
    try:
        signature = [(str(p), p.stat().st_mtime_ns, p.stat().st_size) for p in paths]
    except OSError:
        return {}
    if not paths:
        return {}
    cached = meta.get("_breakeven_native") or {}
    coordinate = (shell, str(paths[0].parent.parent) if shell == "vibe" else
                  tuple(str(p) for p in paths))
    if cached.get("coordinate") != coordinate:
        if cached.get("coordinate"):
            meta["break_even_multiple_sessions"] = True
        cached = {}
    if cached.get("signature") == signature:
        return cached.get("value") or {}
    offsets = cached.get("offsets", {})
    first = list((cached.get("value") or {}).get("first", []))
    latest = (cached.get("value") or {}).get("latest")
    # Read only appended bytes after the first visit. A torn final line is
    # retried next heartbeat, never mistaken for a completed usage record.
    for path in paths:
        offset = offsets.get(str(path), 0)
        try:
            if path.stat().st_size < offset:
                offset, first, latest = 0, [], None
            with path.open("rb") as handle:
                handle.seek(offset)
                raw = handle.read()
            complete = raw.rfind(b"\n") + 1
            offsets[str(path)] = offset + complete
            parsed = []
            for line in raw[:complete].splitlines():
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if isinstance(row, dict):
                    parsed.append(row)
        except OSError:
            continue
        for r in requests(parsed, shell, core=(latest or {}).get("core")):
            matching = next((i for i, old in enumerate(first)
                             if old["request"] == r["request"]), None)
            if matching is not None:
                first[matching] = r
            elif len(first) < FRESH_BOUNDARIES + 1:
                first.append(r)
            latest = r
    value = ({"shell": shell, "core": core or latest.get("core"),
              "boot": first[0]["boot_weighted"], "boot_request": first[0]["request"],
              "baseline": first[1:], "first": first, "latest": latest} if first else {})
    meta["_breakeven_native"] = {"coordinate": coordinate, "signature": signature, "offsets": offsets, "value": value}
    return value


def historical_boot(run_dir: Path | None, shell: str) -> dict:
    """Recover old missing/placeholder stamps from an exact local manifest.

    Read only the first four billed requests, not a long session's whole
    scroll. No manifest/session means unknown. No warm-resume boot samples.
    """
    if run_dir is None:
        return {}
    from .run import Run
    task = Run.from_file(run_dir / "run.md")
    if task is None or task.meta.get("resume_native_session_id") or task.meta.get("break_even_multiple_sessions"):
        return {}
    outbox = Path(task.meta["outbox_path"]) if task.meta.get("outbox_path") else None
    meta = dict(task.meta)
    if shell == "vibe":
        # A terminal sidecar is retained under its exact run-scoped filename.
        sidecar = vibe_usage.load_sidecar(run_dir.parent.parent, run_id=task.id)
        if sidecar:
            meta["_historical_vibe_sidecar"] = sidecar
    found_shell, paths, core = coordinates(meta, shell, None, outbox)
    if not paths or found_shell != shell:
        return {}
    first = []
    for path in paths:
        for request in requests(path, shell):
            first.append(request)
            if len(first) == FRESH_BOUNDARIES + 1:
                break
        if len(first) == FRESH_BOUNDARIES + 1:
            break
    if not first:
        return {}
    core = core or first[0].get("core")
    if any(r.get("core") and r["core"] != core for r in first):
        return {}  # a model-switch prefix is not a fresh same-Core baseline
    return {"weighted": first[0]["boot_weighted"], "core": core,
            "accounting_version": 1, "request": first[0]["request"],
            "baseline": first[1:]}


def baseline(spend: dict, boundary_path: Path) -> float | None:
    boot = mapping(mapping(spend).get("boot"))
    stored = boot.get("baseline")
    if isinstance(stored, list) and stored:
        samples = [number(mapping(r).get("weighted")) for r in stored]
        samples = [n for n in samples if n is not None]
        return median(samples) if samples else None
    samples = []
    seen = {boot.get("request")}
    for row in rows(boundary_path):
        if row.get("phase") != "post-tool" or row.get("subagent"):
            continue
        cost = mapping(row.get("cost"))
        key, value = cost.get("request"), number(cost.get("weighted"))
        if key is None or key in seen or value is None:
            continue
        seen.add(key)
        samples.append(value)
        if len(samples) == FRESH_BOUNDARIES:
            break
    return median(samples) if samples else None


def cohort(runs_dir: Path | None, shell: str, core: str,
           *, exclude: str | None = None, limit: int = HISTORY_RUNS,
           local_runs_dir: Path | None = None) -> dict:
    boots, fresh = [], []
    considered = 0
    candidates = []
    if runs_dir is not None and shell and core:
        try:
            for path in runs_dir.glob("*/state.md"):
                fm = protocol.parse_frontmatter(path.read_text(encoding="utf-8"))
                if fm.get("run_id") != exclude and fm.get("runner_shell") == shell and fm.get("runner_core") == core:
                    candidates.append((fm.get("started_at", ""), path.parent, fm.get("runner_core")))
        except OSError:
            pass
    for _at, directory, attested_core in sorted(candidates, reverse=True)[:limit]:
        try:
            spend = mapping(json.loads((directory / "spend.json").read_text()))
        except (OSError, ValueError):
            spend = {}
        boot = mapping(mapping(spend).get("boot"))
        if boot.get("accounting_version") != 1 and local_runs_dir is not None:
            recovered = historical_boot(local_runs_dir / directory.name, shell)
            if recovered:
                boot = recovered
                spend = {**spend, "boot": boot}
        if (boot.get("core") or attested_core) != core:
            continue
        considered += 1
        if boot.get("resumed") or boot.get("accounting_version") != 1:
            continue
        b = number(boot.get("weighted"))
        if b is not None:
            boots.append(b)
        boundary_path = directory / "boundaries.jsonl"
        if local_runs_dir is not None and not boundary_path.exists():
            boundary_path = local_runs_dir / directory.name / "boundaries.jsonl"
        c = baseline(spend, boundary_path)
        if c is not None:
            fresh.append(c)
    return {"B": median(boots) if boots else None,
            "c0": median(fresh) if fresh else None,
            "boot_samples": len(boots), "baseline_samples": len(fresh),
            "runs_considered": considered, "history_limit": limit,
            "fresh_boundaries": FRESH_BOUNDARIES}


def calculate(B: Any, ct: Any, c0: Any) -> dict:
    b, c, fresh = number(B), number(ct), number(c0)
    if None in (b, c, fresh):
        return {"state": "unknown", "n": None}
    if c <= fresh:
        return {"state": "infinite", "n": None}
    return {"state": "measured", "n": b / (c - fresh)}


def project(terms: dict, latest: dict | None, *, shell: str | None, core: str | None) -> dict:
    ct = number((latest or {}).get("weighted"))
    return {**terms, "ct": ct, "shell": shell, "core": core,
            "unit": "weighted_tokens", "latest": latest,
            **calculate(terms.get("B"), ct, terms.get("c0"))}


def chip(value: Any) -> str:
    if not isinstance(value, dict) or value.get("state") == "unknown":
        return "n* ?"
    if value.get("state") == "infinite":
        return "n* ∞"
    n = number(value.get("n"))
    return f"n* {math.ceil(n)}" if n is not None else "n* ?"
