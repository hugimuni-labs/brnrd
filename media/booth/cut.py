#!/usr/bin/env python3
"""Cut the booth trailer: raw recordings + cut.json -> booth.mp4 and readme.mp4.

    python3 media/booth/cut.py [--raw ~/Desktop/brnrd/booth/raw] [--out ~/Desktop/brnrd/booth] [--pr N]

1. Counts the numbers from this repo's git (and GitHub for merged PRs), reads
   the git logs the cut scrolls (this repo's and the self's), act 1's PR checks
   and the board stills capture.py wrote, so everything is true the day it renders.
2. Normalizes every raw recording cut.json names (any extension ffmpeg reads)
   to an h264 clip of exactly its slot, from its `start` second (a screenshot
   is copied as it is); a recording not there yet renders as a labelled
   placeholder.
3. Renders both compositions with remotion, then ffmpeg finishes them: booth
   muted for the loop; readme with `music.*` from the raw folder under it,
   faded out, or silent when there is none.
4. Prints path, size and sha256 of each render.

Needs node and ffmpeg; `npm ci` in media/booth once.
"""
import argparse
import datetime as dt
import hashlib
import json
import re
import shutil
import subprocess
from pathlib import Path

import gen_term

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
SINCE = "2026-03-28"
# The resident's commits over the months, under every name it has written as.
RESIDENT = ["brnrd-bot", "brnrd-dev[bot]", "brr agent", "brnrd", "brnrd-loom"]
VIDEO = (".mov", ".mp4", ".m4v", ".mkv", ".webm")
IMAGE = (".png", ".jpg", ".jpeg", ".heic")
AUDIO = (".mp3", ".m4a", ".wav", ".aac", ".flac", ".ogg")


def git(*args: str) -> str:
    return subprocess.run(["git", "-C", str(REPO), *args], check=True, capture_output=True, text=True).stdout


def merged_prs() -> int:
    """GitHub's count; squash merges leave no merge commit, so git alone undercounts."""
    q = '{search(query:"repo:hugimuni-labs/brnrd is:pr is:merged",type:ISSUE){issueCount}}'
    try:
        out = subprocess.run(["gh", "api", "graphql", "-f", f"query={q}", "--jq", ".data.search.issueCount"],
                             check=True, capture_output=True, text=True, timeout=30).stdout
        return int(out.strip())
    except (subprocess.SubprocessError, ValueError, FileNotFoundError):
        subjects = git("log", "--format=%s", f"--since={SINCE}")
        return len(set(re.findall(r"(?:Merge pull request #|\(#)(\d+)", subjects)))


def numbers() -> dict:
    commits = len(git("log", "--format=%H", f"--since={SINCE}").split())
    authors = git("log", "--no-merges", "--format=%an", f"--since={SINCE}").splitlines()
    resident = sum(a in RESIDENT for a in authors)
    return {
        "commits": commits,
        "resident": resident,
        "authored": len(authors),
        "prs": merged_prs(),
        "since": dt.date.fromisoformat(SINCE).strftime("%-d %B %Y"),
        "at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d"),
    }


def log(repo: Path, n: int) -> list[str]:
    return subprocess.run(["git", "-C", str(repo), "log", "--oneline", f"-{n}", "--no-decorate", "HEAD"],
                          check=True, capture_output=True, text=True).stdout.splitlines()


def checks(pr: int) -> list[list[str]]:
    """Act 1's PR checks as GitHub reports them: [name, state] per check that ran."""
    try:
        out = subprocess.run(["gh", "pr", "checks", str(pr), "-R", "hugimuni-labs/brnrd", "--json", "name,state"],
                             check=True, capture_output=True, text=True, timeout=30).stdout
        return [[c["name"], c["state"]] for c in json.loads(out) if c["state"] != "SKIPPED"]
    except (subprocess.SubprocessError, ValueError, FileNotFoundError):
        return []


def stills(shots: Path, public: Path) -> dict:
    """capture.py's stills, copied where remotion serves them, with their size and subject box."""
    index = shots / "shots.json"
    if not index.exists():
        return {}
    (public / "shots").mkdir(parents=True, exist_ok=True)
    out = {}
    for name, s in json.loads(index.read_text()).items():
        shutil.copy(shots / s["png"], public / "shots" / s["png"])
        out[name] = {**s, "file": f"shots/{s['png']}"}
    return out


def find(raw: Path, stem: str, exts: tuple) -> Path | None:
    for p in sorted(raw.glob(stem + ".*")):
        if p.suffix.lower() in exts:
            return p
    return None


def normalize(src: Path, dst: Path, start: float, sec: float, fps: int, phone: bool) -> None:
    """Exactly `sec` seconds from `start`, the last frame held if the recording is short."""
    box = "scale=-2:1080" if phone else "scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:-1:-1:color=0x0c0a08"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", str(start), "-i", str(src), "-an",
                    "-vf", f"{box},fps={fps},tpad=stop_mode=clone:stop_duration={sec}",
                    "-t", str(sec), "-c:v", "libx264", "-preset", "fast", "-crf", "18", "-pix_fmt", "yuv420p",
                    str(dst)], check=True)


def gen_props(gen: dict, ctx: dict) -> dict | None:
    """A generated shot's props: the terminal ones from gen_term, the phone ones from booth-chat's gen_phone."""
    if gen["comp"] in gen_term.GEN:
        return gen_term.props(gen, ctx)
    try:
        import gen_phone
    except ImportError:
        return None
    return gen_phone.props(gen["comp"])


LEDGER = Path.home() / ".local/state/brnrd/accounts/acc_bdda426da378d4f0c3cad2eb/loom-home/ledger/facts/ae66/loom.jsonl"


def stamps(cut: dict) -> list:
    """Act 1's strip, the real minutes: his ask and the green reply from the ledger, the merge from GitHub."""
    a = cut.get("act1")
    if not a:
        return cut["stamps"]
    want, at = {a["asked"], a["green"]}, {}
    for path in [LEDGER, *LEDGER.parent.glob("s-*.jsonl")]:
        with open(path, errors="replace") as f:
            for line in f:
                if any(w in line for w in want - set(at)):
                    d = json.loads(line)
                    if d.get("id") in want and d.get("kind") == "letter":
                        at[d["id"]] = d["at"]
        if len(at) == len(want):
            break
    try:
        merged = subprocess.run(["gh", "pr", "view", str(a["pr"]), "-R", "hugimuni-labs/brnrd", "--json", "mergedAt", "--jq", ".mergedAt"],
                                check=True, capture_output=True, text=True, timeout=30).stdout.strip()
    except (subprocess.SubprocessError, FileNotFoundError):
        merged = ""
    if len(at) < 2 or not merged:
        print(f"stamps: fell back to cut.json (ledger found {sorted(at)}, merged {merged!r})")
        return cut["stamps"]
    hm = lambda t: t[11:16]
    return [[hm(at[a["asked"]]), "asked"], [hm(at[a["green"]]), "green"], [hm(merged), "merged"]]


def timeline(cut: dict, raw: Path, public: Path, ctx: dict) -> tuple[list, list]:
    out, missing = [], []
    shutil.rmtree(public / "raw", ignore_errors=True)
    (public / "raw").mkdir(parents=True)
    for e in cut["timeline"]:
        e = dict(e)
        if e.get("gen"):
            p = gen_props(e["gen"], ctx)
            if p is None:
                missing.append(f"{e['gen']['comp']} (generated, not built)")
            e["gen"] = {**e["gen"], "props": p or {}}
        elif e["kind"] == "rec" and not e.get("held"):
            src = find(raw, e["raw"], VIDEO + IMAGE)
            if src is None:
                missing.append(e["raw"])
            elif src.suffix.lower() in IMAGE:
                e["file"], e["image"] = f"raw/{e['raw']}{src.suffix.lower()}", True
                shutil.copy(src, public / e["file"])
            else:
                e["file"] = f"raw/{e['raw']}.mp4"
                normalize(src, public / e["file"], e.get("start", 0), e["sec"], cut["fps"], e.get("phone", False))
        elif e["kind"] == "rec":
            missing.append(f"{e['raw']} ({e['held']})")
        out.append(e)
    return out, missing


def render(variant: str, props: Path, dst: Path) -> None:
    subprocess.run(["npx", "remotion", "render", "src/index.jsx", variant, str(dst), f"--props={props}",
                    "--codec=h264", "--crf=18", "--log=error"], check=True, cwd=HERE)


def finish(src: Path, dst: Path, music: Path | None, sec: float) -> None:
    if music is None:
        args = ["-i", str(src), "-an", "-c:v", "copy"]
    else:
        fade = max(sec - 2.5, 0)
        args = ["-i", str(src), "-i", str(music), "-map", "0:v", "-map", "1:a", "-c:v", "copy",
                "-af", f"afade=t=in:d=0.5,afade=t=out:st={fade}:d=2.5", "-c:a", "aac", "-b:a", "192k", "-t", str(sec)]
    subprocess.run(["ffmpeg", "-v", "error", "-y", *args, "-movflags", "+faststart", str(dst)], check=True)


def receipt(p: Path) -> str:
    h = hashlib.sha256(p.read_bytes()).hexdigest()
    return f"{p}  {p.stat().st_size} bytes  sha256 {h}"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--raw", type=Path, default=Path.home() / "Desktop/brnrd/booth/raw")
    ap.add_argument("--out", type=Path, default=Path.home() / "Desktop/brnrd/booth")
    ap.add_argument("--pr", type=int, help="this project's PR, for the end card")
    ap.add_argument("--shots", type=Path, default=Path.home() / "Desktop/brnrd/booth/shots", help="capture.py's stills")
    ap.add_argument("--self", type=Path, default=REPO.parent / "self", help="the self's clone, whose log answers pain 1")
    ap.add_argument("--act1-pr", type=int, default=2606, help="act 1's PR, whose checks sit under the numbers")
    ap.add_argument("--props-only", action="store_true", help="write props.json for stills and stop")
    a = ap.parse_args()

    cut = json.loads((HERE / "cut.json").read_text())
    public = HERE / "public"
    tl, missing = timeline(cut, a.raw, public, {"self": a.self})
    shots = stills(a.shots, public)
    props = {**{k: v for k, v in cut.items() if k not in ("timeline", "backdrop")}, "stamps": stamps(cut), "timeline": tl, "numbers": numbers(), "pr": a.pr,
             "shots": shots, "backdrop": shots.get(cut.get("backdrop")), "studio": shots.get("studio"),
             "logs": {"repo": log(REPO, 160), "self": log(a.self, 160)}, "checks": {"pr": a.act1_pr, "rows": checks(a.act1_pr)}}
    missing += [f"{e['still']} (still)" for e in tl if e["kind"] == "still" and e["still"] not in shots]
    (HERE / "props.json").write_text(json.dumps(props, indent=1, ensure_ascii=False))
    # an entry's `booth` or `readme` object overrides its keys in that render (src/Trailer.jsx `cast`)
    sec = {v: sum(round({**e, **e.get(v, {})}["sec"] * cut["fps"]) for e in tl) / cut["fps"] for v in ("booth", "readme")}

    if a.props_only:
        print(f"props.json written; stamps {props['stamps']}; placeholders: {', '.join(missing) or 'none'}")
        return
    a.out.mkdir(parents=True, exist_ok=True)
    build = HERE / "out"
    build.mkdir(exist_ok=True)
    music = find(a.raw, "music", AUDIO)
    for variant in ("booth", "readme"):
        render(variant, HERE / "props.json", build / f"{variant}.mp4")
        finish(build / f"{variant}.mp4", a.out / f"{variant}.mp4", music if variant == "readme" else None, sec[variant])

    n = props["numbers"]
    print(f"{sec['booth']:.1f} s booth, {sec['readme']:.1f} s readme · numbers: {n['commits']} commits, {n['resident']} of {n['authored']} authored by the resident, {n['prs']} merged PRs")
    print("placeholders: " + (", ".join(missing) if missing else "none"))
    print("music: " + (str(music) if music else "none, readme is silent"))
    for variant in ("booth", "readme"):
        print(receipt(a.out / f"{variant}.mp4"))


if __name__ == "__main__":
    main()
