"""boundary.md, wake.md, and the files a body writes into port/out."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from brr.daemon2.facts import Fact
from .ledger import LedgerView, kind_facts
from .project import cached_projection

from .home import (
    atomic_write, channel_parts, home_of_room, is_channel, mint, strand_of_room, thread_of,
)


def wake_parts(env) -> tuple[str, str]:
    """(core, occasion) for a body with a system slot.

    The core is "" when the self's recipe kept it in the wake; the occasion
    is then the whole wake, so the core is never lost.
    """
    core = env.get("BRNRD_CORE", "")
    text = Path(core).read_text() if core and Path(core).is_file() else ""
    if not text:
        return "", Path(env["BRNRD_WAKE"]).read_text()
    return text, Path(env["BRNRD_OCCASION"]).read_text()


class PortError(RuntimeError):
    """A port file or an address the loom will not guess its way past."""


_HEADER = re.compile(r"<!-- port (\S+) gen (\d+) · owed([^>]*)-->")
_ITEM = re.compile(
    r"^- (?P<id>\S+) · from (?P<sender>\S+) · reply (?P<reply>\S+)"
    r"(?P<mark> · compact)? — (?P<first>.*)$"
)


@dataclass
class LetterView:
    id: str
    sender: str
    reply: str
    body: str
    compact: bool = False


@dataclass
class Boundary:
    strand: str = ""
    gen: int = 0
    ids: list[str] = field(default_factory=list)
    letters: list[LetterView] = field(default_factory=list)
    settled: str | None = None


def parse_boundary(text: str) -> Boundary:
    """Parse a boundary or the owed section of a wake. Empty text is nothing owed.

    A non-empty text with no port header, or a header that disagrees with the
    letter list, is corrupt. The jack fails open on that; it does not guess.
    """
    if not text or not text.strip():
        return Boundary()
    header = _HEADER.search(text)
    if header is None:
        raise PortError("boundary has no port header")
    ids = header.group(3).split()
    letters: list[LetterView] = []
    current: LetterView | None = None
    settled = None
    for line in text[header.end():].splitlines():
        # Continuation first: a blank line inside a body renders as two
        # spaces, and must not end the letter (#2223 review).
        if line.startswith("  ") and current is not None:
            current.body += "\n" + line[2:]
            continue
        if line.startswith("settled:"):
            settled = line.partition(":")[2].strip()
            current = None
            continue
        if not line.strip() or line.startswith("status:") or line.startswith("owed:") or line.startswith("opened:"):
            current = None
            continue
        match = _ITEM.match(line)
        if match:
            current = LetterView(
                id=match.group("id"), sender=match.group("sender"),
                reply=match.group("reply"), body=match.group("first"),
                compact=bool(match.group("mark")),
            )
            letters.append(current)
            continue
        raise PortError(f"boundary line is not a letter: {line!r}")
    if [letter.id for letter in letters] != ids:
        raise PortError("owed ids do not match the letter list")
    return Boundary(strand=header.group(1), gen=int(header.group(2)),
                    ids=ids, letters=letters, settled=settled)


def _reply(letter: Fact, sender_threads: dict[str, str], own: str,
           channels: dict[str, str]) -> str:
    """Where an answer goes: the sender's thread, a person's chat, or nowhere.

    ``-`` means nobody is waiting for words. A thread never names itself: a
    live haiku body answered its own thread, owed itself the answer, and its
    successor acknowledged that into three more.
    """
    thread = sender_threads.get(letter.by.removeprefix("strand:"))
    if thread and thread != own:
        return f"thread:{thread}"
    # A notice about a person's letter (a late one that climbed, say) answers to
    # the chat the letter came from: the body was told `-` and noted an answer
    # nobody heard (live, 2026-10-12).
    return (channels.get(str(letter.data.get("id")))
            or channels.get(str(letter.data.get("about")), "-"))


def letter_attachments(root: Path, facts: list[Fact]) -> dict[str, list[str]]:
    """Resolve a letter's cited source into lines both body deliveries can use.

    Bodies share the host filesystem: name the stored blob, without copying
    it into each room or putting binary data in the wake.
    """
    if isinstance(facts, LedgerView):
        return facts.project_letters("attachments", str(root.resolve()),
                                     lambda rows, selected: _letter_attachments(root, rows, selected))
    return cached_projection(facts, "attachments", str(root.resolve()),
                             lambda rows: _letter_attachments(root, rows))


def _letter_attachments(root, facts, selected=None):
    sources = facts.by_id if isinstance(facts, LedgerView) else {f.id: f for f in kind_facts(facts, "source")}
    blobs = facts.by_id if isinstance(facts, LedgerView) else {f.id: f for f in kind_facts(facts, "blob")}
    folder = root.resolve() / "blobs"
    result = {}
    for letter in (kind_facts(facts, "letter") if selected is None else selected):
        if letter.kind != "letter":
            continue
        source = sources.get(letter.data.get("cites"))
        if source is None or source.kind != "source":
            continue
        lines = []
        # Follow this source's blob facts: identical bytes can arrive under
        # different filenames (and hence MIME types) in another source.
        for ref in source.after:
            blob_fact = blobs.get(ref)
            blob = blob_fact.data if blob_fact is not None and blob_fact.kind == "blob" else None
            if blob is not None and blob["sha"] in source.data.get("blobs", ()):
                lines.append(f"attachment: {blob['mime']} · {blob['size']} bytes · {folder / blob['sha']}")
        lines.extend(f"attachment {number} could not be fetched"
                     for number in source.data.get("blobs_missing", ()))
        result[letter.id] = lines
    return result


def letter_replies(facts: list[Fact]) -> dict[str, str]:
    """Reply context for inbound sources; an unknown native id is not a letter id."""
    if isinstance(facts, LedgerView):
        return facts.project_letters("reply-context", (), _letter_replies)
    return cached_projection(facts, "reply-context", (), _letter_replies)


def _letter_replies(facts, selected=None):
    sources = facts.by_id if isinstance(facts, LedgerView) else {f.id: f for f in kind_facts(facts, "source")}
    letters = facts.named_letters if isinstance(facts, LedgerView) else {str(f.data.get("id") or f.id): f for f in kind_facts(facts, "letter")}
    result = {}
    for letter in (letters.values() if selected is None else selected):
        source_fact = sources.get(letter.data.get("cites"))
        source = source_fact.data if source_fact is not None and source_fact.kind == "source" else {}
        native = source.get("re")
        if native in (None, ""):
            continue
        target = letters.get(letter.data.get("re"))
        if target is not None:
            first = str(target.data.get("body") or "").split("\n", 1)[0]
            result[letter.id] = f"replying to your {letter.data['re']}: {first}"
        else:
            result[letter.id] = f"replying to an earlier message ({native})"
    return result


def _letter_lines(letter: Fact, reply: str, compact: bool,
                  attachments: list[str] | tuple[str, ...] = (), context: str = "") -> list[str]:
    body = str(letter.data.get("body") or "")
    first, *rest = body.split("\n") if body else [""]
    mark = " · compact" if compact else ""
    lines = [
        f"- {letter.data['id']} · from {letter.data.get('from', '')} · "
        f"reply {reply}{mark} — {first}"
    ]
    if not compact:
        lines.extend(f"  {extra}" for extra in rest)
    if context:
        lines.append(f"  {context}")
    lines.extend(f"  {attachment}" for attachment in attachments)
    return lines


def render_boundary(strand: str, gen: int, thread: str, letters: list[Fact],
                    shown: set[str], sender_threads: dict[str, str], fuel: str = "",
                    settled: str | None = None,
                    channels: dict[str, str] | None = None,
                    opened: list[dict] | tuple[dict, ...] = (), plan: str = "",
                    replies: dict[str, str] | None = None,
                    attachments: dict[str, list[str]] | None = None,
                    reply_context: dict[str, str] | None = None) -> str:
    """Shown letters keep their first line, reply context and attachments when compacted."""
    lines = [
        f"<!-- port {strand} gen {gen} · owed "
        + " ".join(str(letter.data['id']) for letter in letters) + " -->",
        f"status: strand {strand} · gen {gen} · thread {thread}"
        + (f" · {fuel}" if fuel else "") + (f" · {plan}" if plan else ""),
        "owed:",
    ]
    if settled is not None:
        lines.insert(2, f"settled: {settled}")
    children = []
    for row in opened:
        if row['opened-by'] != f"thread:{thread}" or row['status'] in {"settled", "retired"}:
            continue
        now, _, detail = row['now'].partition(' ')
        # Child fuel is not a change of state; ages cost at most one write a minute.
        if re.fullmatch(r"\d+[smhd]", detail):
            now += ' ' + (f"{int(detail[:-1]) // 60}m" if detail.endswith('s') else detail)
        children.append(f"opened: {row['thread']} {now} · owed {row['owed']}")
    lines[2:2] = children
    for letter in letters:
        compact = str(letter.data["id"]) in shown
        reply = replies.get(letter.id, "-") if replies is not None else _reply(letter, sender_threads, thread, channels or {})
        lines.extend(_letter_lines(letter, reply, compact, (attachments or {}).get(letter.id, ()),
                                   (reply_context or {}).get(letter.id, "")))
    lines.append("")
    return "\n".join(lines)


def parse_frontmatter(text: str) -> dict[str, str]:
    if not text.startswith("---\n"):
        raise PortError("port file missing frontmatter")
    end = text.find("\n---\n", 4)
    if end < 0:
        raise PortError("port file unclosed frontmatter")
    found: dict[str, str] = {}
    for line in text[4:end].splitlines():
        if not line.strip():
            continue
        if ":" not in line:
            raise PortError(f"port frontmatter not key: value: {line!r}")
        key, value = line.split(":", 1)
        found[key.strip()] = _unquote(value.strip())
    body = text[end + 5:]
    if body.startswith("\n"):
        body = body[1:]
    found["body"] = body.rstrip("\n")
    return found


def _quote(value) -> str:
    """One line per field: a value with a newline, or one that opens with a
    quote, is written as a JSON string. A raw newline would end the field and
    make its next line a field of its own, or close the frontmatter early."""
    value = str(value)
    return json.dumps(value) if "\n" in value or value.startswith('"') else value


def _unquote(value: str) -> str:
    if value.startswith('"'):
        try:
            loaded = json.loads(value)
        except ValueError:
            return value
        if isinstance(loaded, str):
            return loaded
    return value


def _front(fields: dict[str, str], body: str = "") -> str:
    lines = ["---", *[f"{key}: {_quote(value)}" for key, value in fields.items()], "---"]
    text = "\n".join(lines) + "\n"
    if body:
        text += body if body.endswith("\n") else body + "\n"
    return text


def write_port(room: Path, stem: str, text: str) -> None:
    atomic_write(Path(room) / "port" / "out" / f"{stem}.md", text)


def _full_re(room: Path, re: str) -> str:
    """A bare stem that names exactly one owed letter becomes its full id.

    The router demo's haiku answered ``--re imf8b`` for ``p-test/imf8b``: the
    answer handled nothing, the letter stayed owed, and the body answered it
    twice. Anything else passes through untouched; a reply may name a letter
    that is no longer owed.
    """
    if "/" in re:
        return re
    path = room / "port" / "in" / "boundary.md"
    try:
        ids = parse_boundary(path.read_text()).ids if path.is_file() else []
    except PortError:
        return re
    matches = [item for item in ids if item.endswith("/" + re)]
    return matches[0] if len(matches) == 1 else re


_CLEAN_KINDS = frozenset({"found", "done", "failed", "question", "proposal"})


def clean_accepts(room: Path, kind: str | None, refs: list[str] | tuple[str, ...],
                  body: str) -> bool:
    """A clean letter is an enum, checked refs, and no free text.

    A ref is a path that exists in the self or the room, or an ``https://``
    URL checked for shape only. Nothing is fetched.
    """
    if kind not in _CLEAN_KINDS:
        return False
    if body.strip():
        return False
    if not refs:
        return False
    return all(_ref_ok(Path(room), ref) for ref in refs)


def _done_is_published(room: Path, strand: str, facts, refs) -> None:
    """A clean strand's ``done`` that names a project checkout says the work
    is on the source. A tainted strand cannot publish: its ``done`` is the
    handoff, and its opener publishes.
    """
    from .adapters import repos, seat_text
    from .home import home_of_room
    from .project import sender_threads
    from .publish import unpublished
    if room.name == "self":
        room = room.parent
    try:
        home = home_of_room(room)
    except ValueError:
        return
    thread = sender_threads(facts).get(strand)
    if thread is None:
        return
    declared = {name for name, _source in repos(seat_text(home, thread))}
    for name in sorted({Path(ref).parts[0] for ref in refs
                        if ref and not ref.startswith("https://") and Path(ref).parts}):
        if name not in declared:
            continue
        left = unpublished(facts, thread, name, room / name)
        if left:
            raise PortError(
                f"send: {name} has {left[1]} commit(s) on loom/{thread} that are not "
                f"published, so `done` would name work its reader cannot fetch. "
                f"Run `publish {name}` first, or send --kind failed if it cannot be pushed."
            )


def _ref_ok(room: Path, ref: str) -> bool:
    if not isinstance(ref, str) or not ref or any(ch in ref for ch in " \n\t"):
        return False
    if ref.startswith("https://"):
        host = ref[8:].split("/", 1)[0]
        return bool(host) and host not in {".", ".."} and ".." not in host
    if ref.startswith("/") or "\\" in ref or "://" in ref:
        return False
    parts = Path(ref).parts
    if not parts or any(part in {".", ".."} for part in parts):
        return False
    return any((root / ref).exists() for root in _ref_roots(room))


def _ref_roots(room: Path) -> list[Path]:
    roots = [room]
    if (room / "self").is_dir():
        roots.append(room / "self")
    if room.name == "self":
        roots.append(room.parent.parent / "self")
    if room.parent.name == "rooms":
        roots.append(room.parent.parent / "self")
    return roots


def _refuse_other_loom(home, thread: str) -> None:
    """A thread whose README names another loom in ``for:`` never runs here.

    Refusing here tells the writer now, instead of a notice after the router
    declines it (2026-10-16: two PR verdicts sent to ``thread:arseni``, whose
    ``for:`` held prose, printed ids and reached nobody).
    """
    from .config import load_config
    from .router import readme_for
    target = readme_for(home, thread)
    name = load_config(home.root).name
    if target and target != name:
        raise PortError(
            f"send: thread {thread} says `for: {target}`, and this loom is {name or '(unnamed)'}: "
            "a letter there is never run here. Words for a person go to the reply address "
            "on their letter; to run the thread here, empty its `for:`")


OPEN_NEEDS_WISH = ('send: opening a thread needs the wish behind it: '
                   '--wish <id> | --wish new:"<line>"')


def write_send(room: Path, *, to: str, sender: str, body: str = "",
               re: str | None = None, note: str | None = None,
               clean: bool = False, kind: str | None = None,
               refs: tuple[str, ...] | list[str] = (),
               molt: str | None = None, body_choice: str | None = None,
               at: str | None = None, repo_sources: tuple[str, ...] | list[str] = (),
               wish: str | tuple[str, ...] | list[str] | None = None) -> str:
    home = home_of_room(Path(room))
    opening = None
    if at is not None:
        from .timers import stored_when
        if is_channel(to) or note is not None or molt is not None:
            raise PortError("send: --at is only for a thread letter, without --note or --reopen")
        at = stored_when(at)
    if note is not None and not re:
        raise PortError("send: --note requires --re")
    if note is not None and body:
        raise PortError("send: --note is the whole no-answer; drop the body")
    if is_channel(to):
        if body_choice is not None or repo_sources:
            raise PortError("send: --body is only for opening a new thread; --repo is only for opening a new thread")
        try:
            channel_parts(to, home)
        except ValueError as exc:
            from .ledger import read_facts
            from .project import sender_threads
            from .loom import notice
            facts = read_facts(home)
            notice(home, facts, 'speech-refused', f'{sender}:{to}', str(exc),
                   'choose an enabled channel and write a fresh letter',
                   thread=sender_threads(facts).get(sender))
            raise PortError(str(exc)) from exc
    else:
        thread = thread_of(to)
        if home.routable(thread):
            if body_choice is not None or repo_sources:
                raise PortError("send: --body is only for opening a new thread; --repo is only for opening a new thread")
        else:
            if note is not None or molt is not None:
                raise PortError(f"send: no thread {thread} on main")
            opening = thread
    if note is None and molt is None and not is_channel(to):
        _refuse_other_loom(home, thread_of(to))
    if note is None and molt is None and at is None and not is_channel(to):
        from .ledger import read_facts
        from .project import sender_threads
        if sender_threads(read_facts(home)).get(sender) == thread_of(to):
            raise PortError(
                f"send: {to} is your own thread, and a thread does not write to itself without --at. "
                "Answer to the reply address on the letter's line; where that is `-`, "
                "settle the letter with --re <id> --note \"<what you did>\"")
    if re:
        re = _full_re(Path(room), re)
    if note is not None and body:
        raise PortError("send: --note is the whole no-answer; drop the body")
    asked = [wish] if isinstance(wish, str) else list(dict.fromkeys(wish or ()))
    if opening is not None and not asked:
        raise PortError(OPEN_NEEDS_WISH)
    stem = mint(5)
    letter_id = f"{sender}/{stem}"
    wish_stamps = []
    if asked:
        from .ledger import read_facts
        from .lifecycle import own_thread
        from .registry import admit
        from .verbs_wishes import say_span, wish_fact
        if not re and opening is None:
            raise PortError("send --wish requires --re, unless the letter opens a thread")
        facts = read_facts(home)
        for value in asked:
            ident, line = ("new", value[4:]) if value.startswith("new:") else (value, None)
            if re:
                says = [say_span(re, facts)]
            else:
                # Nothing to cite but the opening letter itself: a
                # resident's say, so the fold reports the wish as derived.
                says = [[letter_id, 0, None]] if ident == "new" else []
            stamp = wish_fact(facts, by="strand:" + sender, ident=ident, says=says, line=line,
                              thread=opening or (own_thread(Path(room)) if ident == "new" else None))
            admit(stamp)
            wish_stamps.append(stamp)
    fields = {
        "kind": "note" if note is not None else "letter",
        "id": letter_id, "to": to, "from": sender,
    }
    if at is not None:
        fields["at"] = at
    if re:
        fields["re"] = re
    if molt:
        fields["molt"] = molt
    if note is not None:
        fields["note"] = note
    elif clean:
        # Schema decides. A passing letter is taint 0. Anything else keeps
        # the sender's label, including a jack log that tainted them.
        from .labels import label_inputs, strand_label
        _strand, facts, self_root, log = label_inputs(Path(room))
        sender_label = strand_label(facts, sender, self_root=self_root, jack_log=log)
        fields["clean"] = kind or ""
        if refs:
            fields["refs"] = " ".join(refs)
        if kind == "done" and not sender_label.taint:
            _done_is_published(Path(room), sender, facts, refs)
        if clean_accepts(Path(room), kind, list(refs), body):
            fields["label"] = f"taint=0; audience={','.join(sorted(sender_label.audience))}"
            body = ""
        else:
            fields["label"] = sender_label.trailer()
    text = _front(fields, "" if note is not None else body)
    if opening is not None:
        from .lifecycle import open_thread
        open_thread(Path(room), opening, sender, body, body_choice, repo_sources,
                    opened_for=[stamp.data["wish"] for stamp in wish_stamps],
                    wish_lines={stamp.data["wish"]: stamp.data.get("line", "") for stamp in wish_stamps})
    write_port(room, stem, text)
    if wish_stamps:
        from .ledger import append
        for stamp in wish_stamps:
            append(home, stamp)
    return letter_id


def write_molt(room: Path, why: str, *, fresh: bool = False) -> str:
    if not why:
        raise PortError("molt: --why is empty")
    from .ledger import read_facts
    from .project import sender_threads
    from .merge import send_to_self
    from .selfrepo import git, resolve_clone

    room = Path(room)
    strand = strand_of_room(room)
    home = home_of_room(room)
    thread = sender_threads(read_facts(home)).get(strand)
    if thread is None:
        raise PortError("molt: room has no thread")
    clone = resolve_clone(room)
    path = f"threads/{thread}/README.md"
    if not fresh and git(clone, "diff", "HEAD", "--quiet", "--", path, check=False).returncode:
        git(clone, "add", "--", path)
        git(clone, "commit", "--only", "-m", f"molt: {why}", "--", path)
    if not fresh and not _current_label(clone, strand).taint:
        outcome = send_to_self(clone)
        if outcome.status != "merged":
            raise PortError(outcome.stderr or f"molt: landing {outcome.status}: {', '.join(outcome.files)}")
    stem = mint(5)
    fact_id = f"{strand}/{stem}"
    fields = {"kind": "molt", "id": fact_id, "why": why}
    if fresh:
        fields["fresh"] = "true"
    write_port(room, stem, _front(fields))
    write_send(room, to=f"thread:{thread}", sender=strand,
               body="Fresh molt: start from main." if fresh else why, molt=fact_id)
    from .jack import _set_state
    _set_state(room, molted=True)
    return fact_id


def _current_label(room: Path | None, strand: str):
    from .labels import CLEAN, label_inputs, strand_label
    if room is None:
        return CLEAN
    _strand, facts, self_root, log = label_inputs(Path(room))
    return strand_label(facts, strand, self_root=self_root, jack_log=log)


def fact_from_taint(text: str, strand: str, fact_id: str) -> Fact:
    """The jack's ``taint-*.json``. WebFetch and WebSearch only."""
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise PortError(f"taint file is not json: {exc}") from exc
    if not isinstance(payload, dict):
        raise PortError("taint file is not an object")
    tool = payload.get("tool")
    if tool not in {"WebFetch", "WebSearch"}:
        raise PortError(f"taint file tool {tool!r} is not a taint source")
    return Fact(
        kind="label.tainted", by=f"strand:{strand}", id=fact_id,
        data={"strand": strand, "tool": tool},
    )


def fact_from_port(fm: dict[str, str], strand: str, gen: int | None,
                   room: Path | None = None) -> Fact:
    """Turn one ingested port file into a fact. ``gen`` is the strand's live lease.

    A ``clean`` claim is checked again here. The file's own ``label`` line is
    not a way to become clean: only the schema does that.
    """
    kind = fm.get("kind")
    fact_id = fm.get("id")
    if not fact_id:
        raise PortError("port file has no id")
    if kind in {"letter", "note", "molt"} and gen is None:
        raise PortError(f"strand {strand} has no lease; refusing {kind} {fact_id}")
    if kind == "letter":
        data: dict = {
            "id": fact_id, "to": fm["to"], "body": fm.get("body", ""),
            "from": strand, "gen": gen,
        }
        if not is_channel(data["to"]):
            thread_of(data["to"])
        if fm.get("at") is not None:
            from .timers import parse_instant
            if is_channel(data["to"]) or fm.get("molt") or parse_instant(fm["at"]) is None:
                raise PortError("send --at: expected a thread letter with YYYY-MM-DDTHH:MM:SSZ")
            data["at"] = fm["at"]
        if fm.get("re"):
            data["re"] = fm["re"]
        if fm.get("edit"):
            data["edit"] = fm["edit"]
        if fm.get("molt"):
            data["molt"] = fm["molt"]
        if fm.get("clean"):
            refs = (fm.get("refs") or "").split()
            sender = _current_label(room, strand)
            if room is not None and clean_accepts(Path(room), fm.get("clean"), refs, data["body"]):
                data["body"] = ""
                data["clean"] = fm["clean"]
                data["refs"] = refs
                data["label"] = {"taint": False, "audience": sorted(sender.audience)}
            else:
                data["label"] = sender.as_dict()
        return Fact(kind="letter", by=f"strand:{strand}", data=data, id=fact_id)
    if kind == "note":
        if not fm.get("re") or not fm.get("note"):
            raise PortError(f"note {fact_id} requires re and note")
        return Fact(
            kind="note", by=f"strand:{strand}", id=fact_id,
            data={"re": fm["re"], "why": fm["note"], "gen": gen},
        )
    if kind == "shown":
        owner = fm.get("strand") or strand
        if owner != strand:
            raise PortError(f"shown strand {owner} is not room {strand}")
        if "gen" not in fm:
            raise PortError(f"shown {fact_id} has no gen")
        return Fact(
            kind="shown", by=f"strand:{strand}", id=fact_id,
            data={"strand": owner, "gen": int(fm["gen"]), "ids": fm.get("ids", "").split()},
        )
    if kind == "molt":
        if not fm.get("why"):
            raise PortError(f"molt {fact_id} has no why")
        return Fact(
            kind="molt", by=f"strand:{strand}", id=fact_id,
            data={"strand": strand, "gen": gen, "why": fm["why"], **({"fresh": True} if fm.get("fresh") == "true" else {})},
        )
    raise PortError(f"unknown port kind {kind!r}")
