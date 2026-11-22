"""What an idle body could move, and the one mess it should not walk past (conatus step 1).

The keepalive said "nothing new arrived: stop", and a body took the hook's
word over stance's "fuel left and a queue ahead: take its top". When the
week's fuel is ahead of pace, the keepalive and a quiet-face boundary carry
the top candidate acts instead, each drawn from what is already derived:
the person's order, a red or stale own pull request, the stalest wait, a
wish never read, a child thread quiet for days with nothing in hand. The
body takes one and logs the pick in one line, or says in one line why not.
Behind pace a thread's own block carries nothing: resting is then the right call.

The inbox is the resident's boredom, not one thread's. Its candidates are
drawn from every open thread, most owed first, and its block also names
every engine whose week is ahead of pace. One unease line rides the same
places, on every thread, whatever the pace. The loom writes ``port/in/idle.md``
and ``port/in/unease.md``; the jack reads those and the gauge, never the ledger.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import time
from datetime import datetime
from pathlib import Path

from .usage import _reset

TOP = 3
WEEK = 7 * 86400.0
STALE_PR = 86400.0
QUIET_THREAD = 3 * 86400.0
# ``idle.md``'s mtime is the stamp: acts are derived at most this often per room, as the board paces its parts.
EVERY = 300.0
# Shown this many times with the same key, the unease line says nothing was done about it.
SHOWN_ACT = 5
HEAD = "idle? fuel ahead of pace"
_ASK = (': take one and log the pick in one line (`wish <id> --pick "<why>"`), '
        "or say in one line why not; nothing more. 'nothing new arrived' is no reason to stop")
_KEEP = ("cache keepalive, fuel ahead of pace ({pace}): nothing new arrived, which is no reason"
         ' to stop; take one of these, log the pick in one line (`wish <id> --pick "<why>"`),'
         " and work it, or end with one line saying why not; nothing more\n")
# Words that put a wish's next step in a person's hands; their names are added from the ledger.
WAITS_ON = r"his|her|hers|him|he|she|the person"
_WAKE_NOTE = re.compile(r"\[the wake (?:folded|cut) [^\]\n]*\]\n")
# Fixed parts of a wake, and the loom's own reservation around them, recomputed at this pace
# while the README is unchanged. A README edit busts both at once.
_FIXED: dict[tuple, tuple[float, int]] = {}
_AROUND: dict[str, tuple[tuple, float, int]] = {}
_WAKE = None


def ahead(value, now: float | None = None) -> bool:
    """The week's fuel left exceeds the week's time left: spending now stays on budget.

    An unknown or refreshing gauge, or one with no week window, is not ahead.
    """
    week = next((w for w in (value or {}).get("windows") or ()
                 if isinstance(w, dict) and w.get("name") == "week"), None)
    if week is None or not _reset(week):
        return False
    now = time.time() if now is None else now
    to_go = min(max(_reset(week) - now, 0.0) / WEEK, 1.0)
    try:
        return float(week["left_pct"]) / 100 > to_go
    except (KeyError, TypeError, ValueError):
        return False


def pace(value, now: float | None = None) -> str:
    """``week 92% left, 78% of it to go`` for the line that says why the body is asked."""
    week = next(w for w in value["windows"] if w.get("name") == "week")
    now = time.time() if now is None else now
    to_go = min(max(_reset(week) - now, 0.0) / WEEK, 1.0)
    return f"week {float(week['left_pct']):g}% left, {to_go:.0%} of it to go"


def _age(seconds: float) -> str:
    from .threads import _age as age
    return age(seconds)


def _epoch(at: str) -> float:
    return datetime.fromisoformat(str(at).replace("Z", "+00:00")).timestamp()


def _people(facts) -> frozenset[str]:
    """Each person's name a letter came from, for reading whom a wish's next step waits on."""
    from .ledger import kind_facts
    from .project import cached_projection
    return cached_projection(facts, "idle.people", (), lambda rows: frozenset(
        re.sub(r"^(?:person:|p-)", "", sender) for f in kind_facts(rows, "letter")
        if (sender := str(f.data.get("from") or "")).startswith(("person:", "p-"))) - {""})


def waits_on_person(wish, people=()) -> bool:
    """The newer of the latest park and the latest left's first step puts the next step in a person's hands.

    A read that is a finding, not a park, says nothing about who moves next.
    """
    said = []
    if wish.reads and wish.reads[-1]["reading"].startswith("waiting:"):
        said.append((wish.reads[-1]["at"], wish.reads[-1]["reading"]))
    if wish.left and wish.left.get("steps"):
        said.append((wish.left["at"], str(wish.left["steps"][0])))
    if not said:
        return False
    words = "|".join([WAITS_ON, *(re.escape(name) for name in sorted(people))])
    return re.search(rf"\b(?:{words})\b", max(said)[1], re.IGNORECASE) is not None


def _shared(home, facts, now: float | None) -> dict:
    """The reads boredom draws on, once per call however many threads are ranked."""
    from .lifecycle import CLOSED, main_snapshot
    from .queues import queues
    from .receipts import receipts
    from .threads import pr_cards
    from .waits import live
    from .wishes import told, waits_on
    now = time.time() if now is None else now
    found = told(facts)
    main = main_snapshot(home)
    heads = waits_on(found, main)
    heard: dict[str, list] = {}
    for wish in sorted(found.values(), key=lambda w: (w.born, w.id)):
        if wish.open and wish.state == "heard" and wish.sender != "resident":
            heard.setdefault(wish.thread, []).append(wish)
    return {
        "now": now, "found": found, "main": main, "q": queues(found, heads),
        "people": _people(facts), "cards": pr_cards(home),
        "receipts": receipts(facts, home.install_id()), "parked": list(live(found)),
        "heard": heard,
        "open": [name for name, row in main.items() if row.get("status") not in CLOSED],
    }


def _top_wish(shared, thread: str, held: set[str]):
    return next((w for w in [*shared["q"].mine, *shared["q"].idle]
                 if w.thread == thread and w.id not in shared["q"].blocked and w.id not in held
                 and not waits_on_person(w, shared["people"])), None)


def _top_line(wish) -> str:
    from .wishes import _short
    return (f'{wish.id} is this thread\'s top in their order ({wish.state}): "{_short(wish.line, 60)}"'
            f' — `wish {wish.id} --pick "<why>"` and build its next step')


def _prs(shared, thread: str) -> list[tuple]:
    out = []
    for receipt in shared["receipts"].get(thread, ()):
        card = shared["cards"].get(f"{receipt.repo}#{receipt.number}") or {}
        if receipt.merge_commit or card.get("state") != "open":
            continue
        try:
            age = shared["now"] - _epoch(card["pushed"])
        except (KeyError, TypeError, ValueError):
            age = 0.0
        out.append((receipt, card, age))
    return out


def _stale_line(receipt, card, age: float) -> str:
    return (f"#{receipt.number} {card.get('ci') or 'pending'}, untouched {_age(age)}: "
            "push what it lacks, ask for its review, or close it")


def _park_line(wish, read, now: float) -> str:
    from .wishes import _short
    reading = read["reading"].removeprefix("waiting:").strip()
    return (f'{wish.id} parked {_age(now - _epoch(read["at"]))} on "{_short(reading, 50)}": '
            "check whether it turned; re-park, or move it")


def _heard_line(wish) -> str:
    from .wishes import _short
    return (f'{wish.id} is heard, never read: "{_short(wish.line, 60)}" — '
            f'`wish {wish.id} --read "<what it asks>"`')


def _quiet_line(row) -> str:
    match = re.fullmatch(r"idle (\d+)d", row["now"])
    if (row["status"] in {"settled", "retired"} or not match or row["owed"] or row.get("prs")
            or row.get("waits") or int(match[1]) * 86400 < QUIET_THREAD):
        return ""
    return (f"thread {row['thread']} quiet {match[1]}d with nothing in hand: "
            "write to it with its next step, or settle it")


def _held(shared, thread: str) -> tuple[list, set[str]]:
    parked = sorted((row for row in shared["parked"] if row[0].thread == thread), key=lambda row: row[1]["at"])
    return parked, {wish.id for wish, _ in parked}


def _one(shared, thread: str, children) -> list[str]:
    """One thread's acts, in the order a thread's own block has always used."""
    parked, held = _held(shared, thread)
    out: list[str] = []
    top = _top_wish(shared, thread, held)
    if top is not None:
        out.append(_top_line(top))
    for receipt, card, age in _prs(shared, thread):
        if card.get("ci") == "red":
            out.append(f"#{receipt.number} is red: read the failing run, fix and push")
        elif age >= STALE_PR:
            out.append(_stale_line(receipt, card, age))
    for wish, read in [row for row in parked if not row[1]["wait"].get("on")][:1]:
        out.append(_park_line(wish, read, shared["now"]))
    for wish in shared["heard"].get(thread, ()):
        if not (top and wish.id == top.id):
            out.append(_heard_line(wish))
    for row in children:
        line = _quiet_line(row)
        if line:
            out.append(line)
    return out[:TOP]


def _inbox(shared, thread: str, children) -> list[str]:
    """Every open thread's acts, most owed first, each line carrying its thread.

    Reds, then each thread's top in the person's order, then stales, prose parks,
    heard-never-read wishes, and the quiet children this thread already opened.
    The children table is the boundary's; it is not run again here.
    """
    order = {w.id: n for n, w in enumerate([*shared["q"].mine, *shared["q"].idle])}
    items: list[tuple] = []
    for name in shared["open"]:
        parked, held = _held(shared, name)
        top = _top_wish(shared, name, held)
        if top is not None:
            items.append(((1, order.get(top.id, 10**9), name, top.id), f"{name}: {_top_line(top)}"))
        for receipt, card, age in _prs(shared, name):
            ident = (name, str(receipt.repo), receipt.number)
            if card.get("ci") == "red":
                items.append(((0, -age, *ident), f"{name}: #{receipt.number} is red: read the failing run, fix and push"))
            elif age >= STALE_PR:
                items.append(((2, -age, *ident), f"{name}: {_stale_line(receipt, card, age)}"))
        prose = sorted((row for row in parked if not row[1]["wait"].get("on")), key=lambda row: row[1]["at"])
        if prose:
            wish, read = prose[0]
            items.append(((3, read["at"], name, wish.id), f"{name}: {_park_line(wish, read, shared['now'])}"))
        for wish in shared["heard"].get(name, ()):
            if top and wish.id == top.id:
                continue
            items.append(((4, wish.born, wish.id), f"{name}: {_heard_line(wish)}"))
    for row in children:
        line = _quiet_line(row)
        if not line:
            continue
        days = int(re.fullmatch(r"idle (\d+)d", row["now"])[1])
        items.append(((5, -days, row["thread"]), f"{thread}: {line}"))
    items.sort(key=lambda item: item[0])
    return [line for _, line in items[:TOP]]


def candidates(home, facts, thread: str, children=(), now: float | None = None) -> list[str]:
    """Up to ``TOP`` acts, most owed first, one line each.

    The inbox ranks across every open thread and prefixes each line with its
    thread. Any other thread's list is its own work, unprefixed, as before.

    ``children`` are the boundary's own ``thread_table`` rows for what this
    thread opened: the table is the dearest read here, so it is never run twice.
    """
    from .home import HOME_THREAD
    shared = _shared(home, facts, now)
    if thread == HOME_THREAD:
        return _inbox(shared, thread, children)
    return _one(shared, thread, children)


def fuel_line(home, now: float | None = None) -> str:
    """``fuel to burn: codex week 65% left, 3.8d to reset; grok 71%, 1.5d``.

    Every configured engine whose week is ahead of pace, in ``shells`` order.
    An engine with no reading is left out. Cached readings only: this never
    starts a collector.
    """
    from .usage import reading, shells
    now = time.time() if now is None else now
    parts = []
    for name in shells(home):
        value = reading(home, name, cached_only=True)
        if not ahead(value, now):
            continue
        week = next(w for w in value["windows"] if isinstance(w, dict) and w.get("name") == "week")
        days = max(_reset(week) - now, 0.0) / 86400
        pct = float(week["left_pct"])
        if not parts:
            parts.append(f"{name} week {pct:g}% left, {days:.1f}d to reset")
        else:
            parts.append(f"{name} {pct:g}% left, {days:.1f}d")
    return ("fuel to burn: " + "; ".join(parts)) if parts else ""


def wants_block(home, shell, thread: str) -> bool:
    """Whether this boundary keeps an idle block.

    A thread's own block follows its room's shell, as before. The inbox's
    boredom is the resident's: any configured engine ahead of pace is enough.
    """
    from .home import HOME_THREAD
    from .usage import reading
    if shell and ahead(reading(home, shell)):
        return True
    return thread == HOME_THREAD and bool(fuel_line(home))


def render(lines: list[str], fuel: str = "") -> str:
    """The block a quiet face and ``idle.md`` carry: one unit to the jack's news."""
    if not lines and not fuel:
        return ""
    text = f"{HEAD}{_ASK}\n"
    if fuel:
        text += fuel.rstrip("\n") + "\n"
    return text + "".join(f"  - {line}\n" for line in lines)


def keepalive(lines: str, value, stop_news: str, *, pace_text: str | None = None) -> str:
    """The keepalive's reason when fuel is ahead of pace and the thread has acts to move."""
    said = pace(value) if pace_text is None else pace_text
    kept = [line for line in lines.splitlines() if line.startswith("  - ") or line.startswith("fuel to burn:")]
    return _KEEP.replace("{pace}", said) + "".join(line + "\n" for line in kept) + stop_news


def _seed_wake():
    """The packaged wake recipe. It imports nothing and has no suffix, so it is loaded by path."""
    global _WAKE
    if _WAKE is None:
        import importlib.util
        from importlib.machinery import SourceFileLoader
        from .selfrepo import seed_root
        path = seed_root() / "core" / "loom" / "wake"
        loader = SourceFileLoader("brnrd_seed_wake", str(path))
        spec = importlib.util.spec_from_loader(loader.name, loader)
        if spec is None:
            raise ImportError(f"seed wake has no spec: {path}")
        module = importlib.util.module_from_spec(spec)
        loader.exec_module(module)
        _WAKE = module
    return _WAKE


def _bytes_removed(original: str, rendered: str) -> int:
    """Original bytes no longer visible: the wake's own fold and clip notes are not original."""
    notes = sum(len(match.group(0).encode("utf-8")) for match in _WAKE_NOTE.finditer(rendered))
    kept = len(rendered.encode("utf-8")) - notes
    return max(0, len(original.encode("utf-8")) - kept)


def _fixed_total(wake, root: Path, thread: str, key: tuple) -> int | None:
    slot = _FIXED.get(key)
    if slot and time.monotonic() - slot[0] < EVERY:
        return slot[1]
    from .selfrepo import default_loom_part
    handle = tempfile.NamedTemporaryFile("w", prefix="loom-owed-", delete=False)
    try:
        handle.write("[]")
        handle.close()
        try:
            parts = wake.fixed_parts(root, thread, default_loom_part(root, thread), Path(handle.name))
        except SystemExit:
            return None
    finally:
        os.unlink(handle.name)
    total = sum(len(part.encode("utf-8")) for part in parts.values())
    _FIXED[key] = (time.monotonic(), total)
    return total


def _around(home, root: Path, thread: str, facts, key: tuple) -> int:
    """Bytes ``run_wake`` reserves before the recipe fits the README: briefing, topic heads, wishes."""
    slot = _AROUND.get(thread)
    if slot and slot[0] == key and time.monotonic() - slot[1] < EVERY:
        base = slot[2]
    else:
        from .briefing import section as briefing_section
        from .selfrepo import _wake_topics
        base = len((briefing_section(home) + _wake_topics(root, thread, None)).encode("utf-8"))
        _AROUND[thread] = (key, time.monotonic(), base)
    extra = ""
    try:
        from .lifecycle import main_snapshot
        from .wishes import told, waits_on, wake_wishes
        main = main_snapshot(home)
        found = told(facts)
        extra = wake_wishes(facts, thread, main.get(thread, {}).get("plan_rows") or (), told=found)
        if thread == home.main_thread():
            from .queues import queues, render
            extra += render(queues(found, waits_on(found, main)))
    except Exception:  # noqa: BLE001 - a wishes section that fails reserves nothing; the cut still reads the rest
        extra = ""
    return base + len(extra.encode("utf-8"))


def _readme_unease(home, facts, thread: str) -> tuple[str, str] | None:
    """The README is cut at wake when the recipe folds or clips it and the wake still fits.

    The budget, the fold and the clip are the seed recipe's. The reservation is
    ``run_wake``'s, measured against an empty occasion: the next letter's size
    is not known at a quiet stop. A wake that would still refuse after the cut
    (exit 3) is not this line; trimming the README would not deliver it.
    """
    root = home.root / "self"
    path = root / "threads" / thread / "README.md"
    try:
        text = path.read_text(encoding="utf-8")
        stat = path.stat()
    except (OSError, UnicodeDecodeError):
        return None
    wake = _seed_wake()
    try:
        limit = wake.budget()
    except SystemExit:
        return None
    key = (str(root), thread, stat.st_mtime_ns, stat.st_size)
    total = _fixed_total(wake, root, thread, key)
    if total is None:
        return None
    recipe_limit = max(0, limit - _around(home, root, thread, facts, key))
    excess = total - recipe_limit
    if excess <= 0:
        return None
    size = len(text.encode("utf-8"))
    folded = wake.fold(text, excess)
    saved = size - len(folded.encode("utf-8"))
    rest = excess - saved
    rendered = wake.clip(folded, rest, recipe_limit) if rest > 0 else folded
    if total - size + len(rendered.encode("utf-8")) > recipe_limit:
        return None
    cut = _bytes_removed(text, rendered)
    if cut <= 0:
        return None
    return (f"readme:{thread}",
            f"unease: this README is cut at wake ({cut} of its {size} bytes cut): trim it")


def _who(home, head: str) -> str:
    """Who an ask waits on, as a person would say it. Never a hardcoded name."""
    from .person_names import label, names
    repo = home.root / "self"
    if head.startswith(("person:", "p-")):
        return label(head, names(repo))
    if head != "operator":
        return head
    titled = names(repo)
    if len(titled) == 1:
        return next(iter(titled.values()))
    folder = repo / "people"
    on_disk = sorted(p.name for p in folder.iterdir() if p.is_dir()) if folder.is_dir() else []
    if len(on_disk) == 1:
        return on_disk[0]
    return "the person"


def _ask_unease(home, facts, now: float) -> tuple[str, str] | None:
    """The oldest ask to a person, older than seven days, with no answer. Inbox only."""
    from .lifecycle import main_snapshot
    from .wishes import told, waits_on
    found = told(facts)
    heads = waits_on(found, main_snapshot(home))
    oldest = None
    for wish in found.values():
        head = heads.get(wish.id, "")
        if not (wish.open and wish.sender == "resident" and not wish.answer and head
                and not head.startswith("thread:")):
            continue
        try:
            age = now - _epoch(wish.born)
        except (TypeError, ValueError):
            continue
        if age <= WEEK:
            continue
        if oldest is None or (wish.born, wish.id) < (oldest[0].born, oldest[0].id):
            oldest = (wish, age, head)
    if oldest is None:
        return None
    wish, age, head = oldest
    return (f"ask:{wish.id}",
            f"unease: ask {wish.id} to {_who(home, head)} unanswered {_age(age)}: "
            "follow it up once, or close it")


def _scar_unease(room: Path, now: float) -> tuple[str, str] | None:
    """The scar that blocked most often, three times or more in seven days."""
    from .notes import hands, load, scar_block_count
    record = hands(room)
    best = None
    for note in load(room):
        if not note.path.startswith("memory/scars/"):
            continue
        count = scar_block_count(note, record, now)
        if count < 3:
            continue
        if best is None or count > best[0] or (count == best[0] and note.path < best[1]):
            best = (count, note.path)
    if best is None:
        return None
    count, path = best
    return (f"scar:{path}",
            f"unease: {path} blocked {count} times in 7d: make it a check, or rewrite it")


def unease(home, facts, thread: str, room, now: float | None = None) -> tuple[str, str]:
    """``(key, line)`` for the one mess, first source wins; ``("", "")`` when there is none.

    A README cut at wake, then (inbox only) the oldest unanswered ask to a person
    older than seven days, then a scar that blocked three times in seven days.
    """
    from .home import HOME_THREAD
    now = time.time() if now is None else now
    found = _readme_unease(home, facts, thread)
    if found:
        return found
    if thread == HOME_THREAD:
        found = _ask_unease(home, facts, now)
        if found:
            return found
    found = _scar_unease(Path(room), now)
    if found:
        return found
    return "", ""


def publish_unease(room, key: str, line: str) -> None:
    """``port/in/unease.md`` is the line without its shown-count. No key drops the record."""
    from .home import atomic_write
    room = Path(room)
    path = room / "port" / "in" / "unease.md"
    record = room / "port" / "unease.json"
    if not key or not line:
        path.unlink(missing_ok=True)
        record.unlink(missing_ok=True)
        return
    atomic_write(path, key.rstrip("\n") + "\n" + line.rstrip("\n") + "\n")


def present_unease(room, now: float | None = None) -> str:
    """The line as shown, counting this show. Five or more adds the acted-on suffix.

    A key that is not the one published is dropped. Nothing published clears the record.
    """
    from .home import atomic_write
    room = Path(room)
    path = room / "port" / "in" / "unease.md"
    record_path = room / "port" / "unease.json"
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        record_path.unlink(missing_ok=True)
        return ""
    key, _, base = text.partition("\n")
    key, base = key.strip(), base.strip()
    if not key or not base:
        path.unlink(missing_ok=True)
        record_path.unlink(missing_ok=True)
        return ""
    now = time.time() if now is None else now
    try:
        kept = json.loads(record_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        kept = {}
    entry = kept.get(key) if isinstance(kept, dict) else None
    if isinstance(entry, dict) and entry.get("since"):
        count = int(entry.get("n") or 0) + 1
        since = str(entry["since"])
    else:
        count = 1
        since = time.strftime("%Y-%m-%d %H:%MZ", time.gmtime(now))
    atomic_write(record_path, json.dumps({key: {"n": count, "since": since}}, sort_keys=True) + "\n")
    if count < SHOWN_ACT:
        return base
    return (f"{base} (shown {count} times since {since}, nothing changed: "
            "act, take it to the person, or retire it)")
