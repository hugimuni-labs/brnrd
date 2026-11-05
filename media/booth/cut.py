#!/usr/bin/env python3
"""Cut the booth trailer: raw recordings + cut.json -> booth.mp4 and readme.mp4.

    python3 media/booth/cut.py [--raw ~/Desktop/brnrd/booth/raw] [--out ~/Desktop/brnrd/booth] [--pr N]

1. Counts the numbers card from this repo's git (and GitHub for merged PRs),
   so the card is true on the day it renders.
2. Normalizes every raw recording cut.json names (any extension ffmpeg reads)
   to an h264 clip of exactly its slot, from its `start` second; a shot with
   no recording renders as a labelled placeholder, an optional one is dropped
   and its seconds go to the shot before it.
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

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
SINCE = "2026-03-28"
# The resident's commits over the months, under every name it has written as.
RESIDENT = ["brnrd-bot", "brnrd-dev[bot]", "brr agent", "brnrd", "brnrd-loom"]
VIDEO = (".mov", ".mp4", ".m4v", ".mkv", ".webm")
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


def timeline(cut: dict, raw: Path, public: Path) -> tuple[list, list]:
    out, missing, src_of = [], [], {}
    shutil.rmtree(public / "raw", ignore_errors=True)
    (public / "raw").mkdir(parents=True)
    for e in cut["timeline"]:
        e = dict(e)
        if e["kind"] == "shot":
            src = find(raw, e["raw"], VIDEO)
            if src is None and e.get("optional"):
                if out:
                    out[-1]["sec"] += e["sec"]
                continue
            if src is None:
                missing.append(e["raw"] + (" (its fallback card)" if e.get("fallback") else ""))
            else:
                e["file"], src_of[id(e)] = f"raw/{e['raw']}.mp4", src
        out.append(e)
    # Encoded after the walk: a slot grows when the optional shot after it is dropped.
    for e in out:
        if e.get("file"):
            normalize(src_of[id(e)], public / e["file"], e.get("start", 0), e["sec"], cut["fps"], e.get("phone", False))
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
    a = ap.parse_args()

    cut = json.loads((HERE / "cut.json").read_text())
    public = HERE / "public"
    shutil.copy(REPO / "media/brnrd-boot.gif", public / "boot.gif")
    tl, missing = timeline(cut, a.raw, public)
    props = {**{k: v for k, v in cut.items() if k != "timeline"}, "timeline": tl, "numbers": numbers(), "pr": a.pr}
    (HERE / "props.json").write_text(json.dumps(props, indent=1, ensure_ascii=False))
    sec = sum(round(e["sec"] * cut["fps"]) for e in tl) / cut["fps"]

    a.out.mkdir(parents=True, exist_ok=True)
    build = HERE / "out"
    build.mkdir(exist_ok=True)
    music = find(a.raw, "music", AUDIO)
    for variant in ("booth", "readme"):
        render(variant, HERE / "props.json", build / f"{variant}.mp4")
        finish(build / f"{variant}.mp4", a.out / f"{variant}.mp4", music if variant == "readme" else None, sec)

    n = props["numbers"]
    print(f"{sec:.1f} s · numbers: {n['commits']} commits, {n['resident']} of {n['authored']} authored by the resident, {n['prs']} merged PRs")
    print("placeholders: " + (", ".join(missing) if missing else "none"))
    print("music: " + (str(music) if music else "none, readme is silent"))
    for variant in ("booth", "readme"):
        print(receipt(a.out / f"{variant}.mp4"))


if __name__ == "__main__":
    main()
