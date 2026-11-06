"""Capture the board (D) shots from the ledger: one recipe per shot, each a still of the real board at a moment.

Every recipe names a ledger moment, the column it looks at and the letter or tape line it is about. For each
moment ``board_at.py`` serves the home as it was then; a headless Chromium opens it at 1920x1080 and device scale
2 (3840x2160 pixels, room for the cut's push-ins), opens the column's letters, expands the tape fold that holds
the line, scrolls the subject to the middle and takes the still. Two things on the page belong to the present,
not the moment, and are patched: the top bar's clock (set to the moment) and the fuel line (set to what a
letter of that moment says, else hidden).

    python3 -I media/booth/capture.py [--only p5-fuel,a2-handover] [--out ~/Desktop/brnrd/booth/shots]

Writes ``<name>.png`` per shot and ``shots.json``: the moment, the size, and ``focus``, the subject's box in the
png's pixels, so the cut can push in on it. A recipe whose subject is not found fails the run; nothing is guessed.
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
PYTHON = Path("~/.brnrd/current/bin/python").expanduser()

# name: at (the moment), column (thread id), letter (the subject's ledger id) or find (its text in a tape), fold and beat (open the tape fold of this strand
# and its beat holding the subject), fuel (the fuel line as a letter of that moment states it), wide (css width, a dolly plate).
RECIPES = {
    "a1-taken": dict(at="2026-11-04T09:51:40Z", column="inbox",
                     why="act 1 (provisional): the inbox thread at work on the ask while its PR, brnrd#2618, runs CI"),
    "a1-delivered": dict(at="2026-11-04T09:55:45Z", column="inbox", letter="s-ae66-kd0n4v/hv34q",
                         why="act 1 (provisional): the reply with the merged PR, s-ae66-kd0n4v/hv34q at 09:55:39Z"),
    "a5-seat": dict(at="2026-11-05T21:08:00Z", column="board-chain",
                    why="answer 5: board-chain's first seat on grok (s-ae66-fbfgj1, lease board-chain:1 at 21:06:34Z), the worker on another subscription"),
    "a5-gauges": dict(url="http://127.0.0.1:4777/", sel="#fuel", ready="section.column", at="live",
                      why="answer 5: the live header's gauges, claude, codex and grok, as they read at capture"),
    "studio": dict(studio=True, at="live",
                   why="act 3: remotion studio with this cut open (props.json from the last cut.py run); the preview box is where the cut plays act 1"),
    "g1-pr": dict(url="https://github.com/hugimuni-labs/brnrd/pull/2618", zoom=1.5, text="Merged",
                  at="2026-11-04T09:55:05Z", why="act 1 (provisional): brnrd#2618 on GitHub, merged at 52361e4f"),
    "a3-night": dict(at="2026-11-06T06:00:00Z", column="inbox", letter="s-ae66-sp1alj/jv2ea",
                     why="answer 3 (night): the board at 06:00Z after his 00:1xZ sign-off; the subject is this cut's own overnight receipt (s-ae66-sp1alj/jv2ea)"),
    "a2-handover": dict(at="2026-11-05T22:26:20Z", column="inbox", letter="s-ae66-q5mdqs/0fqjc",
                        why="answer 2: the old body's letter to the new on the same thread (s-ae66-q5mdqs/0fqjc)"),
    "a4-strip": dict(at="2026-11-05T22:26:20Z", wide=5760,
                     why="answer 4: the board, every piece of work a thread; a plate for the dolly"),
    "p5-fuel": dict(at="2026-11-05T18:19:35Z", column="inbox", letter="letter:cloud:telegram:155783668:10296",
                    fuel="claude session 1%   codex session 100% week 100%",
                    why="pain 5, the board's side: his 10296 parked, no disposition; the loom's notice itself "
                        "(loom/parked-043ca41af7) went to his chat and is not drawn on the board, so the shot is his"),
    "a5-resumed": dict(at="2026-11-05T20:05:40Z", column="inbox", letter="s-ae66-y9p63m/581jx",
                       why="answer 5: the inbox after the refill answering the same 10296 (s-ae66-y9p63m/581jx)"),
    "p6-guard": dict(at="2026-11-05T00:28:50Z", column="booth-demo", find="Probe never-guard", fold="s-ae66-6pqd47",
                     beat="Let me probe the never-guard",
                     why="pain 6: the body's rm -r under the home in its tape; the guard refused it (README, Decisions)"),
}

PATCH = """([hhmmss, fuel]) => {
  const at = document.querySelector('#at');
  if (at) at.textContent = at.textContent.replace(/loom \\d\\d:\\d\\d:\\d\\dZ/, 'loom ' + hhmmss);
  const live = document.querySelector('#live'); if (live) live.remove();
  const f = document.querySelector('#fuel');
  if (f && fuel) f.textContent = fuel;
  else if (f) { f.previousElementSibling?.remove(); f.remove(); }
}"""

OPEN_COLUMN = """(name) => {
  const col = [...document.querySelectorAll('section.column')]
    .find(c => [...(c.querySelector('.heading h2')?.childNodes || [])]
      .filter(n => n.nodeType === 3).map(n => n.textContent).join('').trim() === name);
  if (!col) return false;
  col.scrollIntoView({inline: 'center', block: 'nearest'});
  const tog = [...col.querySelectorAll('.heading .tog')].find(e => /▸ letters/.test(e.textContent));
  if (tog) tog.click();
  col.dataset.capture = '1';
  return true;
}"""

FIND = """([strand, beat]) => {
  const col = document.querySelector('section.column[data-capture]') || document;
  const folds = [...col.querySelectorAll('.tool.tape[data-strand="' + strand + '"]')];
  const f = folds.find(e => e.textContent.includes(beat));
  if (!f) return false;
  if (!f.classList.contains('on')) f.querySelector('.ep-h').click();
  return true;
}"""

BEAT = """(beat) => {
  const col = document.querySelector('section.column[data-capture]') || document;
  const h = [...col.querySelectorAll('.tool.tape .beat-h')].find(e => e.textContent.includes(beat));
  if (!h) return false;
  h.click();
  return true;
}"""

LOCATE = """([text, open]) => {
  const col = document.querySelector('section.column[data-capture]') || document;
  const el = text.letter ? col.querySelector('article[data-letter="' + text.letter + '"]')
    : text.text ? [...col.querySelectorAll('*')].filter(e => e.textContent.includes(text.text))
        .sort((a, b) => a.textContent.length - b.textContent.length)[0]
    : [...col.querySelectorAll('.tool.tape *')].filter(e => e.textContent.includes(text.find))
        .sort((a, b) => a.textContent.length - b.textContent.length)[0];
  if (!el) return null;
  const box = el.closest('article.letter') || el.closest('.tool.tape > *') || el;
  if (open && box.matches('article.letter.collapsed')) box.click();
  box.scrollIntoView({block: 'center', inline: 'nearest'});
  const r = box.getBoundingClientRect();
  return [r.x, r.y, r.width, r.height];
}"""


def serve(at: str, port: int) -> subprocess.Popen:
    proc = subprocess.Popen([str(PYTHON), str(HERE / "board_at.py"), "--at", at, "--port", str(port)],
                            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, start_new_session=True)
    deadline = time.time() + 300
    while time.time() < deadline:
        if proc.poll() is not None:
            raise SystemExit(f"board_at {at} exited {proc.returncode}: {proc.stderr.read().decode()[-800:]}")
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/v1/loom/index", timeout=5) as r:
                if b'"error"' not in r.read(200):
                    return proc
        except OSError:
            pass
        time.sleep(1)
    stop(proc)
    raise SystemExit(f"board_at {at}: no index after 300 s")


def stop(proc: subprocess.Popen) -> None:
    try:
        os.killpg(proc.pid, signal.SIGTERM)
        proc.wait(10)
    except (ProcessLookupError, subprocess.TimeoutExpired):
        os.killpg(proc.pid, signal.SIGKILL)


def shoot(page, name: str, r: dict, port: int, out: Path) -> dict:
    page.set_viewport_size({"width": r.get("wide", 1920), "height": 1080})
    page.goto(f"http://127.0.0.1:{port}/")
    page.wait_for_selector("section.column")
    page.wait_for_timeout(1500)
    if r.get("internal"):
        page.check("#internal input")
        page.wait_for_timeout(800)
    page.evaluate(PATCH, [r["at"][11:19] + "Z", r.get("fuel")])
    focus = None
    if r.get("column"):
        if not page.evaluate(OPEN_COLUMN, r["column"]):
            cols = page.evaluate("() => [...document.querySelectorAll('section.column .heading h2')].map(h => h.innerText)")
            raise SystemExit(f"{name}: no column {r['column']} at {r['at']}; columns: {cols}")
        page.wait_for_timeout(800)
        if r.get("fold"):
            if not page.evaluate(FIND, [r["fold"], r["beat"]]) or (page.wait_for_timeout(600) or not page.evaluate(BEAT, r["beat"])):
                raise SystemExit(f"{name}: no beat {r['beat']!r} in a tape of {r['fold']} at {r['at']}")
            page.wait_for_timeout(800)
        subject = {"letter": r.get("letter"), "find": r.get("find"), "text": r.get("text")}
        page.wait_for_timeout(800)
        if not any(subject.values()):
            subject = None
        box = page.evaluate(LOCATE, [subject, True]) if subject else page.evaluate(
            "() => { const r = document.querySelector('section.column[data-capture]').getBoundingClientRect(); return [r.x, r.y, r.width, r.height] }")
        if box is None:
            raise SystemExit(f"{name}: {subject} not on the board at {r['at']}")
        page.wait_for_timeout(800)
        box = page.evaluate(LOCATE, [subject, False]) if subject else box
        if box is None:
            raise SystemExit(f"{name}: {subject} gone once opened at {r['at']}")
        focus = [round(v * 2) for v in box]
    page.keyboard.press("Escape")  # opening a tape opens its strand's panel over the right columns
    for close in page.get_by_role("button", name="×").all():
        if close.is_visible():
            close.click()
    page.wait_for_timeout(400)
    png = out / f"{name}.png"
    page.screenshot(path=str(png))
    return {"at": r["at"], "png": png.name, "size": [r.get("wide", 1920) * 2, 2160], "focus": focus, "why": r["why"]}


def shoot_url(page, name: str, r: dict, out: Path) -> dict:
    """A G shot: the public GitHub page as it is now (a merged PR does not change), zoomed for the booth."""
    page.set_viewport_size({"width": 1920, "height": 1080})
    page.goto(r["url"], wait_until="networkidle")
    if r.get("ready"):
        page.wait_for_selector(r["ready"], timeout=120000)
        page.wait_for_timeout(1500)
    if r.get("zoom"):
        page.evaluate("(z) => { document.body.style.zoom = z }", r["zoom"])
    page.wait_for_timeout(800)
    el = page.locator(r["sel"]).first if r.get("sel") else page.get_by_text(r["text"], exact=True).first
    box = el.bounding_box()
    if box is None:
        raise SystemExit(f"{name}: {r['text']!r} not on {r['url']}")
    png = out / f"{name}.png"
    page.screenshot(path=str(png))
    at = r["at"] if r["at"] != "live" else time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    return {"at": at, "png": png.name, "size": [3840, 2160], "focus": [round(v * 2) for v in box.values()],
            "why": r["why"]}


def shoot_studio(page, name: str, r: dict, out: Path, port: int = 3123) -> dict:
    """remotion studio on this cut, as the editor shows it; the cut draws act 1 into the preview's box."""
    proc = subprocess.Popen(["npx", "remotion", "studio", "src/index.jsx", f"--port={port}", f"--props={HERE / 'props.json'}"],
                            cwd=HERE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True,
                            env={**os.environ, "BROWSER": "none"})
    try:
        page.set_viewport_size({"width": 1920, "height": 1080})
        deadline = time.time() + 120
        while True:
            try:
                page.goto(f"http://localhost:{port}/booth")
                break
            except Exception:
                if time.time() > deadline:
                    raise SystemExit("studio: no page after 120 s")
                time.sleep(2)
        page.wait_for_timeout(8000)
        box = page.evaluate("""() => {
          const c = [...document.querySelectorAll('div')].filter(d => {
            const r = d.getBoundingClientRect(); return r.width > 300 && Math.abs(r.width / r.height - 16 / 9) < 0.01 && r.y > 20; });
          c.sort((a, b) => b.getBoundingClientRect().width - a.getBoundingClientRect().width);
          if (!c.length) return null;
          const r = c[0].getBoundingClientRect(); return [r.x, r.y, r.width, r.height];
        }""")
        if box is None:
            raise SystemExit("studio: no 16:9 preview on the page")
        png = out / f"{name}.png"
        page.screenshot(path=str(png))
        return {"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "png": png.name, "size": [3840, 2160],
                "focus": [round(v * 2) for v in box], "preview": [round(v * 2) for v in box], "why": r["why"]}
    finally:
        stop(proc)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--only", help="comma-separated recipe names")
    ap.add_argument("--out", type=Path, default=Path.home() / "Desktop/brnrd/booth/shots")
    ap.add_argument("--port", type=int, default=4791)
    a = ap.parse_args()
    names = a.only.split(",") if a.only else [n for n in RECIPES if not RECIPES[n].get("studio")]
    a.out.mkdir(parents=True, exist_ok=True)
    index = a.out / "shots.json"
    done = json.loads(index.read_text()) if index.exists() else {}
    from playwright.sync_api import sync_playwright
    by_moment: dict[str, list[str]] = {}
    for n in names:
        if RECIPES[n].get("url") or RECIPES[n].get("studio"):
            continue
        by_moment.setdefault(RECIPES[n]["at"], []).append(n)
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1920, "height": 1080}, device_scale_factor=2)
        for n in names:
            if RECIPES[n].get("url") or RECIPES[n].get("studio"):
                shoot_ = shoot_studio if RECIPES[n].get("studio") else shoot_url
                done[n] = shoot_(page, n, RECIPES[n], a.out)
                print(n, done[n]["png"], done[n]["focus"], flush=True)
        for at, group in by_moment.items():
            proc = serve(at, a.port)
            try:
                for n in group:
                    done[n] = shoot(page, n, RECIPES[n], a.port, a.out)
                    print(n, done[n]["png"], done[n]["focus"], flush=True)
            finally:
                stop(proc)
        browser.close()
    index.write_text(json.dumps(done, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
