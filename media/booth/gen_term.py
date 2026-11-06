"""The terminal shots' data, read at render time (cut.py calls ``props``): nothing on screen is typed by hand.

Each function returns the props of one composition in ``src/gen/term``; receipts.md says where every line
comes from. A source that is gone (a moved folder, a rewritten log) fails the render loudly, never a guess.
"""
from __future__ import annotations

import datetime as dt
import json
import re
import subprocess
from pathlib import Path

# pain 1 lists brnrd's one project folder, not ~/.claude/projects/ (booth-demo README, Decisions)
MEMDIR = Path.home() / ".claude/projects/-Users-gurio-Source-Projects-brnrd"
# act 1's commit in the self's log, and the thread whose README opens on it
LAND = r"^\w+ wiki: #2606 merged"
THREAD = "threads/wiki/README.md"
PR = "https://github.com/hugimuni-labs/brnrd/pull/2606"


def _h(n: int) -> str:
    for unit in ("B", "K", "M", "G"):
        if n < 1024 or unit == "G":
            return f"{n:.0f}{unit}" if unit == "B" or n >= 10 else f"{n:.1f}{unit}"
        n /= 1024
    return str(n)


def memdir(_: dict, ctx: dict) -> dict:
    files = sorted(p for p in MEMDIR.iterdir() if p.is_file())
    rows = []
    for p in files:
        st = p.stat()
        when = dt.datetime.fromtimestamp(st.st_mtime).strftime("%b %e %H:%M")
        rows.append([f"-rw-------  {_h(st.st_size):>5}  {when}", p.name])
    total = sum(p.stat().st_size for p in files)
    uuid = [i for i, (_, n) in enumerate(rows) if re.match(r"[0-9a-f]{8}-", n)]
    return {"rows": rows, "title": f"{MEMDIR.name} — zsh", "cwd": "brnrd", "pick": uuid[-8] if len(uuid) >= 8 else None,
            "count": len(rows), "bytes": total}


def _self(ctx: dict, *args: str) -> str:
    return subprocess.run(["git", "-C", str(ctx["self"]), *args], check=True, capture_output=True, text=True).stdout


def _land(ctx: dict) -> str:
    for line in _self(ctx, "log", "--oneline", "--no-decorate", "-400").splitlines():
        if re.match(LAND, line):
            return line.split()[0]
    raise SystemExit(f"gen_term: no commit matching {LAND!r} in the self's last 400")


def _sentences(text: str, n: int) -> list[str]:
    return re.split(r"(?<=[.)])\s+(?=[A-Z])", text.strip())[:n]


def _status(ctx: dict, sha: str) -> list[str]:
    readme = _self(ctx, "show", f"{sha}:{THREAD}")
    para = next(l for l in readme.splitlines() if "#2606 merged" in l)
    return _sentences(para, 2)


def selflog(_: dict, ctx: dict) -> dict:
    lines = _self(ctx, "log", "--oneline", "--no-decorate", "-140").splitlines()
    sha = _land(ctx)
    target = next(i for i, l in enumerate(lines) if l.startswith(sha))
    return {"lines": lines, "target": target,
            "readme": {"path": f"{THREAD} @ {sha}", "lines": ["wiki", *_status(ctx, sha)]}}


def compact(_: dict, ctx: dict) -> dict:
    """The newest compact_boundary in brnrd's Claude Code sessions: its line, the tokens it summarized, the
    summary's first sentence (the message after it)."""
    best = None
    for p in MEMDIR.glob("*.jsonl"):
        lines = p.read_text(errors="replace").splitlines()
        for i, l in enumerate(lines):
            if '"compact_boundary"' not in l:
                continue
            d = json.loads(l)
            if best and d["timestamp"] <= best[0]["timestamp"]:
                continue
            nxt = json.loads(lines[i + 1]) if i + 1 < len(lines) else {}
            c = nxt.get("message", {}).get("content", "")
            c = c[0].get("text", "") if isinstance(c, list) and c else c
            best = (d, c, p.name)
    if not best:
        raise SystemExit(f"gen_term: no compact_boundary under {MEMDIR}")
    d, summary, name = best
    return {"said": d["content"], "tokens": d["compactMetadata"]["preTokens"], "summary": _sentences(summary, 1)[0],
            "title": "claude — ~/Source/Projects/brnrd", "session": name, "at": d["timestamp"],
            "trigger": d["compactMetadata"].get("trigger")}


def repos(gen: dict, ctx: dict) -> dict:
    names = gen["repos"]
    base = Path.home() / "Source/Projects"
    # his pick (10327); the remotion fork shots.md asked for was never made, so a name may have no folder yet
    return {"repos": names, "base": "~/Source/Projects", "folders": [r for r in names if (base / r).is_dir()]}


def readme(_: dict, ctx: dict) -> dict:
    sha = _land(ctx)
    text = _self(ctx, "show", f"{sha}:{THREAD}")
    wish = re.search(r'^wish-(w-\w+): "(.*)"$', text, re.M)
    return {"path": THREAD, "sha": sha, "link": PR.removeprefix("https://"),
            "lines": [["meta", f"wish {wish.group(1)}"], ["wish", wish.group(2)], ["p", " ".join(_status(ctx, sha))], ["link", "PR"]]}


GEN = {"t1-memdir": memdir, "self-log": selflog, "t2-compact": compact, "t4-repos": repos, "g6-readme": readme}


def props(gen: dict, ctx: dict) -> dict:
    return GEN[gen["comp"]](gen, ctx)


if __name__ == "__main__":
    import sys
    here = Path(__file__).resolve().parent
    ctx = {"self": Path(sys.argv[1]) if len(sys.argv) > 1 else here.parent.parent.parent / "self"}
    for k, f in GEN.items():
        out = f({"comp": k, "repos": ["brnrd", "hugimuni", "animations", "BeCenter", "remotion"]}, ctx)
        print(k, json.dumps(out, ensure_ascii=False)[:300])
