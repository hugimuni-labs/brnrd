"""Limbs: a page per service, and the stand-in command the loom makes from it.

A page (``limbs/<name>.md`` at the top of the brnrd repository) says in words
how a service is reached and how far the resident goes, in four rows: look,
do, ask, never. Its small print (a ```limb block) is what the stand-in
matches; its ``e.g.`` lines are tests the loader runs, so a page that drifted
from its own table fails at load, not at the act. A command-line tool's
stand-in is a few lines of shell that hand the call to :func:`main` here: the
same matcher the loader checks is the one that runs (threads/limbs).

A person's own page, ``limbs/<name>.md`` on the main branch of their self,
overrides the product's page row by row (:func:`overlay`): its rules add to
the product's row, which can only tighten it, unless the row's table line
cites an owner's letter naming the service; then it replaces the product's
row and may loosen it. The resident tightens alone; it cannot loosen alone.
"""

from __future__ import annotations

import fnmatch
import re
import shlex
import subprocess
import sys
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Callable

ROWS = ("look", "do", "ask", "never")
RANK = {row: i for i, row in enumerate(ROWS)}
# limb_pages is a link to limbs/ at the top of the repository, so a wheel carries the pages too.
PRODUCT = Path(__file__).resolve().parent / "limb_pages"
SRC = str(Path(__file__).resolve().parents[3])
# The guard verb's classes; kept here as words so this module imports nothing
# of the runtime when the stand-in calls it at every gh call.
CLASSES = ("force-push", "delete-home", "merge-red", "publish")
_KEYS = ("say", "skip", "take", "default", "unless", "target", "why")


class LimbError(ValueError):
    """A page that does not parse or does not pass its own examples."""


@dataclass(frozen=True)
class Rule:
    row: str
    cls: str
    prefix: tuple[str, ...]
    conds: tuple[tuple, ...]
    details: dict
    text: str

    def same(self) -> str:
        return " ".join(self.text.split())


@dataclass(frozen=True)
class Page:
    name: str
    title: str
    way: str
    key: str
    table: dict[str, tuple[str, str, str]]  # row -> (what, how, the line as written)
    rules: tuple[Rule, ...]
    examples: tuple[tuple[str, tuple[str, ...]], ...]
    source: str = "product"
    own_rows: tuple[str, ...] = field(default=())

    @property
    def command(self) -> str:
        """The one command the page's rules name (``check`` refuses a page naming two)."""
        return self.rules[0].prefix[0] if self.rules else ""

    def small_print(self) -> str:
        return "\n".join(rule.text for rule in self.rules) + "\n"


# --- parsing ---------------------------------------------------------------

def _opt_aliases(spec: str, where: str) -> tuple[str, ...]:
    aliases = tuple(a for a in spec.split("|") if a)
    if not aliases or not all(re.fullmatch(r"-[A-Za-z0-9]|--[A-Za-z0-9][\w-]*", a) for a in aliases):
        raise LimbError(f"{where}: {spec!r} is not a list of options like -X|--method")
    return aliases


def _path_regex(template: str) -> tuple[re.Pattern, tuple[str, ...]]:
    out, names, at = [], [], 0
    for m in re.finditer(r"\*|\{(\w+)\}", template):
        out.append(re.escape(template[at:m.start()]))
        if m.group(0) == "*":
            out.append(r"[^/]+")
        else:
            names.append(m.group(1))
            out.append(rf"(?P<{m.group(1)}>.+?)")
        at = m.end()
    out.append(re.escape(template[at:]))
    return re.compile("".join(out)), tuple(names)


def _rule(head: str, details: list[str], where: str) -> Rule:
    words = head.split()
    row, words = words[0], words[1:]
    cls = ""
    if row == "ask":
        if not words or words[0] not in CLASSES:
            raise LimbError(f"{where}: an ask rule names its class first, one of {', '.join(CLASSES)}")
        cls, words = words[0], words[1:]
    prefix: list[str] = []
    conds: list[tuple] = []
    for word in words:
        if word.startswith("{") and word.endswith("}") and re.fullmatch(r"\{\w+\}", word):
            conds.append(("pos", word[1:-1]))
        elif word.startswith("@"):
            regex, names = _path_regex(word[1:])
            conds.append(("path", regex, names))
        elif word.startswith("!"):
            conds.append(("none", _opt_aliases(word[1:], where)))
        elif word.startswith("-"):
            spec, eq, value = word.partition("=")
            if not eq or not value:
                raise LimbError(f"{where}: {word!r} needs =VALUE or ={{name}}")
            capture = value[1:-1] if re.fullmatch(r"\{\w+\}", value) else ""
            conds.append(("opt", _opt_aliases(spec, where), capture, "" if capture else value.lower()))
        elif conds:
            raise LimbError(f"{where}: the word {word!r} comes after a condition; command words go first")
        elif re.fullmatch(r"[\w.*-]+(?:\|[\w.-]+)*", word):
            prefix.append(word)
        else:
            raise LimbError(f"{where}: {word!r} is not a command word")
    if not prefix or prefix[0] in ("*",) or "|" in prefix[0]:
        raise LimbError(f"{where}: a rule starts with the command's name")
    parsed: dict = {"skip": (), "take": [], "default": {}}
    for line in details:
        key, colon, value = line.strip().partition(":")
        key, _, arg = key.strip().partition(" ")
        value = value.strip()
        if not colon or key not in _KEYS or not value or bool(arg) != (key in ("take", "default")):
            raise LimbError(f"{where}: {line.strip()!r} is not one of say:, skip:, take <name>:, "
                            "take <name> from <name>:, default <name>:, unless:, target:, why:")
        if key == "skip":
            parsed["skip"] = _opt_aliases("|".join(value.split()), where)
        elif key == "take":
            # take <name>: the group of the pattern in the capture's own value.
            # take <name> from <other>: the group in <other>'s value, when it matches, over <name>'s own.
            name, _, source = arg.partition(" from ")
            if not re.fullmatch(r"\w+", name) or (source and not re.fullmatch(r"\w+", source)):
                raise LimbError(f"{where}: take {arg}: is 'take <name>:' or 'take <name> from <name>:'")
            try:
                regex = re.compile(value)
            except re.error as exc:
                raise LimbError(f"{where}: take {arg}: {exc}") from None
            if regex.groups != 1:
                raise LimbError(f"{where}: take {arg}: the pattern keeps exactly one group")
            parsed["take"].append((name, source, regex))
        elif key == "default":
            parsed["default"][arg] = value
        else:
            parsed[key] = value
    if row == "never" and "say" not in parsed:
        raise LimbError(f"{where}: a never rule says what it refuses (say:)")
    if row != "ask" and any(k in parsed for k in ("unless", "target", "why")):
        raise LimbError(f"{where}: unless:, target: and why: belong to an ask rule")
    names = {c[1] for c in conds if c[0] == "pos"} | {c[2] for c in conds if c[0] == "opt" and c[2]}
    names |= {n for c in conds if c[0] == "path" for n in c[2]}
    for name, source, _regex in parsed["take"]:
        for used in filter(None, (name, source)):
            if used not in names:
                raise LimbError(f"{where}: take: uses {{{used}}}, which the rule does not capture")
    for key in ("unless", "target"):
        for used in re.findall(r"\{(\w+)\}", parsed.get(key, "")):
            if used not in names:
                raise LimbError(f"{where}: {key}: uses {{{used}}}, which the rule does not capture")
    text = "\n".join([head.rstrip(), *(line.rstrip() for line in details)])
    return Rule(row, cls, tuple(prefix), tuple(conds), parsed, text)


def _rules(block: str, where: str) -> tuple[list[Rule], list[tuple[str, tuple[str, ...]]]]:
    rules: list[Rule] = []
    examples: list[tuple[str, tuple[str, ...]]] = []
    head: str | None = None
    details: list[str] = []
    lineno = 0

    def close():
        if head is not None:
            rules.append(_rule(head, details, f"{where}, rule at line {lineno}"))

    for n, line in enumerate(block.splitlines(), 1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if line[0].isspace():
            if head is None:
                raise LimbError(f"{where}, line {n}: an indented line belongs under a rule")
            details.append(line)
            continue
        close()
        head, details = None, []
        first = line.split()[0]
        if first == "e.g.":
            parts = line.split(None, 2)
            if len(parts) < 3 or parts[1] not in ROWS:
                raise LimbError(f"{where}, line {n}: an example is 'e.g. <row> <command>'")
            examples.append((parts[1], tuple(shlex.split(parts[2]))))
        elif first in ROWS:
            head, lineno = line, n
        else:
            raise LimbError(f"{where}, line {n}: a line starts with look, do, ask, never or e.g.")
    close()
    return rules, examples


def parse(name: str, text: str, *, source: str = "product") -> Page:
    """A page, checked against its own examples. Raises :class:`LimbError`."""
    where = f"limbs/{name}.md"
    title = next((line[2:].strip() for line in text.splitlines() if line.startswith("# ")), "")
    if not title:
        raise LimbError(f"{where}: no '# ' title line")
    head = dict(re.findall(r"^(way|key):[ \t]*(.+?)[ \t]*$", text, re.M))
    table: dict[str, tuple[str, str, str]] = {}
    for line in text.splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")] if line.lstrip().startswith("|") else []
        if cells and cells[0] in ROWS:
            if cells[0] in table:
                raise LimbError(f"{where}: the {cells[0]} row is in the table twice")
            table[cells[0]] = (cells[1] if len(cells) > 1 else "", cells[2] if len(cells) > 2 else "", line)
    blocks = re.findall(r"^```limb[ \t]*\n(.*?)^```", text, re.M | re.S)
    if len(blocks) != 1:
        raise LimbError(f"{where}: a page has one ```limb block of small print, found {len(blocks)}")
    rules, examples = _rules(blocks[0], where)
    page = Page(name, title, head.get("way", ""), head.get("key", ""), table, tuple(rules), tuple(examples), source)
    check(page)
    return page


def check(page: Page) -> None:
    """The table, the small print and the examples agree, or a :class:`LimbError` says where not."""
    where = f"limbs/{page.name}.md"
    if not page.table:
        raise LimbError(f"{where}: no table with look / do / ask / never rows")
    if not page.rules:
        raise LimbError(f"{where}: the small print has no rule")
    commands = {rule.prefix[0] for rule in page.rules}
    if len(commands) != 1:
        raise LimbError(f"{where}: one page is one command; the small print names {sorted(commands)}")
    for rule in page.rules:
        if rule.row not in page.table:
            raise LimbError(f"{where}: a {rule.row} rule, but the table has no {rule.row} row: {rule.text.splitlines()[0]}")
    for row in page.table:
        if row != "do" and not any(rule.row == row for rule in page.rules):
            raise LimbError(f"{where}: the table has a {row} row and the small print no {row} rule")
    decided: set[int] = set()
    for row, argv in page.examples:
        if row not in page.table and row != "do":
            raise LimbError(f"{where}: e.g. {row} {shlex.join(argv)}: the table has no {row} row")
        got, rule = decide(page.rules, argv)
        if got != row:
            by = f" (by: {rule.text.splitlines()[0].strip()})" if rule else ""
            raise LimbError(f"{where}: e.g. {row} {shlex.join(argv)} lands in {got}{by}")
        if rule is not None:
            decided.add(id(rule))
    for rule in page.rules:
        if id(rule) not in decided:
            raise LimbError(f"{where}: no e.g. line is decided by: {rule.text.splitlines()[0].strip()}")


# --- matching --------------------------------------------------------------

def _values(rest: list[str], aliases: tuple[str, ...]) -> list[str]:
    """Each value given to one of ``aliases``: ``-X v``, ``-Xv``, ``--method v``, ``--method=v``."""
    found = []
    for i, arg in enumerate(rest):
        for alias in aliases:
            if arg == alias:
                if i + 1 < len(rest):
                    found.append(rest[i + 1])
            elif alias.startswith("--") and arg.startswith(alias + "="):
                found.append(arg[len(alias) + 1:])
            elif len(alias) == 2 and arg.startswith(alias) and not arg.startswith("--"):
                found.append(arg[2:])
    return found


def _present(rest: list[str], aliases: tuple[str, ...]) -> bool:
    return any(arg == a or (a.startswith("--") and arg.startswith(a + "="))
               or (len(a) == 2 and arg.startswith(a) and not arg.startswith("--"))
               for arg in rest for a in aliases)


def _positionals(rest: list[str], valued: set[str]) -> list[str]:
    out, skip = [], False
    for arg in rest:
        if skip:
            skip = False
        elif arg in valued:
            skip = True
        elif not arg.startswith("-"):
            out.append(arg)
    return out


def _path(arg: str) -> str:
    """An argument as an API path: no query, no scheme and host (gh takes a full address),
    no Enterprise ``api/v3/``, no edge slash."""
    path = re.sub(r"^[A-Za-z][\w+.-]*://[^/]*", "", arg.split("?", 1)[0])
    path = path[1:] if path.startswith("/") else path
    path = path[len("api/v3/"):] if path.startswith("api/v3/") else path
    return path[:-1] if path.endswith("/") else path


def match(rule: Rule, argv: list[str] | tuple[str, ...]) -> dict[str, str] | None:
    """The rule's captures when it matches ``argv`` (the command's name first), else None."""
    argv = list(argv)
    if len(argv) < len(rule.prefix):
        return None
    for word, arg in zip(rule.prefix, argv):
        if word != "*" and arg not in word.split("|"):
            return None
    rest = argv[len(rule.prefix):]
    valued = set(rule.details["skip"]) | {a for c in rule.conds if c[0] == "opt" for a in c[1]}
    positionals = iter(_positionals(rest, valued))
    found: dict[str, str] = {}
    for cond in rule.conds:
        kind = cond[0]
        if kind == "pos":
            found[cond[1]] = next(positionals, "")
        elif kind == "opt":
            values = _values(rest, cond[1])
            if cond[2]:
                found[cond[2]] = values[-1] if values else ""
            elif not any(fnmatch.fnmatchcase(v.lower(), cond[3]) for v in values):
                return None
        elif kind == "none":
            if _present(rest, cond[1]):
                return None
        else:
            hit = next((m for m in (cond[1].fullmatch(_path(a)) for a in rest) if m), None)
            if hit is None:
                return None
            found.update(hit.groupdict())
    raw = dict(found)
    for name, source, regex in rule.details["take"]:
        if source:
            # The source wins: a PR link names its repository, and gh acts on the link's.
            m = regex.match(raw.get(source, ""))
            if m:
                found[name] = m.group(1)
        else:
            m = regex.match(raw.get(name, ""))
            found[name] = m.group(1) if m else found.get(name, "")
    return found


def decide(rules, argv) -> tuple[str, Rule | None]:
    """The strictest row a rule matching ``argv`` names, and that rule; ``do`` when none matches."""
    best: Rule | None = None
    for rule in rules:
        if (best is None or RANK[rule.row] > RANK[best.row]) and match(rule, argv) is not None:
            best = rule
    return (best.row, best) if best else ("do", None)


def fill(template: str, found: dict[str, str], real: str) -> list[str]:
    """A command line of the small print as argv: ``[...]`` drops out when a capture in it is empty."""
    out: list[str] = []
    for group, plain in re.findall(r"\[([^\]]*)\]|(\S+)", template):
        words = group.split() if group else [plain]
        used = [n for w in words for n in re.findall(r"\{(\w+)\}", w)]
        if group and not all(found.get(n) for n in used):
            continue
        out += [re.sub(r"\{(\w+)\}", lambda m: found.get(m.group(1), ""), w) for w in words]
    if out:
        out[0] = real
    return out


def _sub(template: str, found: dict[str, str]) -> str:
    return re.sub(r"\{(\w+)\}", lambda m: found.get(m.group(1), ""), template)


# --- the stand-in ----------------------------------------------------------

def _call(page: Page, verb: str, extra: str = "") -> str:
    boot = f"import sys; sys.path.insert(0, {SRC!r}); from brr.loom.runtime.limbs import main; sys.exit(main())"
    return (f"{shlex.quote(sys.executable)} -I -c {shlex.quote(boot)} {verb} "
            f"--name {shlex.quote(page.name)} --command {shlex.quote(page.command)} --rules {shlex.quote(page.small_print())}{extra} -- \"$@\"")


def refuse_shell(page: Page) -> str:
    """The stand-in's first lines: a ``never`` call stops here, before the key is fetched."""
    return f"_brnrd_limb=$({_call(page, 'first')}) || exit 1\n"


def gate_shell(page: Page) -> str:
    """The stand-in's ``ask`` lines, after the key: ``{<command>}`` is the real program, ``{brnrd}`` the shim."""
    real = "{" + page.command + "}"
    return (f"[ -n \"${{_brnrd_limb+x}}\" ] || {{ _brnrd_limb=$({_call(page, 'first')}) || exit 1; }}\n"
            f"if [ \"$_brnrd_limb\" = ask ]; then\n"
            f"  {_call(page, 'ask', ' --real ' + real + ' --brnrd {brnrd}')} || exit 1\n"
            f"fi\nunset _brnrd_limb\n")


def _first(rules: list[Rule], argv: list[str], command: str) -> int:
    row, rule = decide(rules, argv)
    if row == "never":
        sys.stderr.write(f"{command}: {rule.details['say']}\n")
        return 1
    sys.stdout.write(row + "\n")
    return 0


def _ask(rules: list[Rule], argv: list[str], real: str, brnrd: str) -> int:
    row, rule = decide(rules, argv)
    if row != "ask":
        return 0
    found = match(rule, argv) or {}
    for name, template in rule.details["default"].items():
        if not found.get(name):
            proc = subprocess.run(fill(template, found, real), capture_output=True, text=True)
            found[name] = proc.stdout.strip() if proc.returncode == 0 else ""
    if "unless" in rule.details:
        check_argv = fill(rule.details["unless"], found, real)
        if subprocess.run(check_argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0:
            return 0
    target = _sub(rule.details.get("target", ""), found) or "?"
    return subprocess.run([brnrd, "runtime", "guard", rule.cls, target, "--why", rule.details.get("why", "")]).returncode


def main(argv: list[str] | None = None) -> int:
    """The stand-in's verbs: ``first`` (never, or the row) and ``ask`` (the default, the check, the guard)."""
    args = list(sys.argv[1:] if argv is None else argv)
    if args[:1] in (["first"], ["ask"]):
        verb, opts, call = args[0], {}, []
        rest = args[1:]
        while rest and rest[0] != "--":
            opts[rest[0].lstrip("-")] = rest[1]
            rest = rest[2:]
        call = rest[1:]
        name = opts["name"]
        rules, _examples = _rules(opts["rules"], f"limbs/{name}.md")
        command = opts["command"]
        strays = sorted({r.prefix[0] for r in rules} - {command})
        if strays:
            # A rule for another command would never match this one's calls: fail closed.
            sys.stderr.write(f"{command}: brnrd's stand-in for {command} carries rules for {', '.join(strays)}; "
                             "refused until its page is fixed\n")
            return 1
        argv_ = [command, *call]
        if verb == "first":
            return _first(rules, argv_, command)
        return _ask(rules, argv_, opts["real"], opts["brnrd"])
    return cli(args)


# --- pages: the product's, a person's, and the two together ---------------

def product(name: str) -> Page:
    return parse(name, (PRODUCT / f"{name}.md").read_text(encoding="utf-8"))


def products() -> list[Page]:
    return [product(p.stem) for p in sorted(PRODUCT.glob("*.md")) if p.name != "README.md"]


def own_texts(self_repo: Path) -> dict[str, str]:
    """The person's pages on the self's main, by name: read from git, never from the room."""
    from .guards import _git, _git_batch
    from .lifecycle import main_tip
    self_repo = Path(self_repo)
    if not (self_repo / ".git").exists():
        return {}
    listing = _git(self_repo, "ls-tree", main_tip(self_repo), "limbs/")
    names = []
    for line in listing.stdout.splitlines():
        meta, _, path = line.partition("\t")
        parts = meta.split()
        if len(parts) == 3 and parts[1] == "blob" and path.endswith(".md") and path != "limbs/README.md":
            names.append((Path(path).stem, parts[2]))
    batch = _git_batch(self_repo, [blob for _, blob in names]) if names else {}
    return {name: batch[blob] for name, blob in names if blob in batch}


def loosened(base: Page, own: Page, row: str) -> list[Rule]:
    """The product's ``ask`` and ``never`` rules of ``row`` that the person's page keeps in no row as strict."""
    if row not in ("ask", "never"):
        return []
    kept = {rule.same() for rule in own.rules if RANK[rule.row] >= RANK[row]}
    return [rule for rule in base.rules if rule.row == row and rule.same() not in kept]


def overlay(base: Page, own: Page, allows: Callable[[str], bool]) -> tuple[Page, list[str]]:
    """The product's page with the person's rows in it, and a line for each row that loosened.

    A row of the person's page adds its rules to the product's row: the
    strictest matching row decides, so an added rule only tightens, and the
    resident may write one alone. A row whose table line cites a letter
    that ``allows`` (the owner's, naming the service) replaces the product's
    row instead, and so may loosen it.
    """
    if own.command != base.command:
        return base, [f"limbs/{own.name}.md: your page is for `{own.command}`, the product's for "
                      f"`{base.command}`; the product's page stands"]
    rules = list(base.rules)
    table = dict(base.table)
    taken, notes = [], []
    for row in ROWS:
        if row not in own.table:
            continue
        cited = re.findall(r"letter:([\w:.-]+)", own.table[row][2])
        granted = [letter for letter in cited if allows(letter)]
        if granted:
            dropped = loosened(base, own, row)
            rules = [r for r in rules if r.row != row]
            if dropped:
                notes.append(f"limbs/{own.name}.md: the {row} row replaces the product's on letter:{granted[0]}, "
                             f"dropping '{dropped[0].text.splitlines()[0].strip()}'")
        elif cited:
            notes.append(f"limbs/{own.name}.md: the {row} row cites {', '.join('letter:' + c for c in cited)}, "
                         f"not a letter of the owner naming {base.title}; its rules add to the product's row")
        rules += [r for r in own.rules if r.row == row]
        table[row] = own.table[row]
        taken.append(row)
    if not taken:
        return base, notes
    rules.sort(key=lambda r: RANK[r.row])
    return replace(base, table=table, rules=tuple(rules), source="person", own_rows=tuple(taken)), notes


def owner_letters(home) -> Callable[[str], bool]:
    """Whether a letter is the owner's, not held, and names the service: the loosening rule's test."""
    from .dispositions import held
    from .grants import owners
    from .ledger import read_facts
    cache: dict = {}

    def allows_for(service_words: tuple[str, ...]) -> Callable[[str], bool]:
        def allows(letter: str) -> bool:
            if "facts" not in cache:
                facts = read_facts(home)
                cache["facts"] = {str(f.data.get("id", f.id)): f for f in facts if f.kind == "letter"}
                cache["held"] = held(facts)
                cache["owners"] = owners(home.root / "self")
            fact = cache["facts"].get(letter) or cache["facts"].get(letter.removeprefix("letter:"))
            if fact is None or letter in cache["held"] or str(fact.data.get("from") or "") not in cache["owners"]:
                return False
            body = str(fact.data.get("body") or "")
            return any(re.search(rf"(?<!\w){re.escape(w)}(?!\w)", body, re.I) for w in service_words)
        return allows
    return allows_for


def effective(home, name: str) -> tuple[Page, list[str]]:
    """The page a body's stand-in runs: the product's, with the person's rows from the self's main."""
    base = product(name)
    if home is None:
        return base, []
    text = own_texts(home.root / "self").get(name)
    if text is None:
        return base, []
    try:
        own = parse(name, text, source="person")
    except LimbError as exc:
        return base, [f"{exc}; the product's page stands"]
    return overlay(base, own, owner_letters(home)((base.title, base.command, name)))


def every(home) -> tuple[list[Page], list[str]]:
    pages, notes = [], []
    for base in products():
        page, said = effective(home, base.name) if home is not None else (base, [])
        pages.append(page)
        notes += said
    return pages, notes


# --- the one glance --------------------------------------------------------

def _cell(text: str) -> str:
    return re.sub(r"\s*letter:[\w:.-]+", "", text).replace("|", "\\|").strip() or "—"


def table(pages: list[Page]) -> str:
    lines = ["| hand | way | ask | never | page |", "|---|---|---|---|---|"]
    for page in pages:
        whose = "product" if page.source == "product" else "person's: " + ", ".join(page.own_rows)
        ask = page.table.get("ask", ("", "", ""))[0]
        never = page.table.get("never", ("", "", ""))[0]
        lines.append(f"| [{page.title}]({page.name}.md) | {_cell(page.way)} | {_cell(ask)} | {_cell(never)} | {whose} |")
    return "\n".join(lines) + "\n"


def readme(pages: list[Page]) -> str:
    return ("# Limbs\n\n"
            "One page per service the resident reaches: how it is reached and how far it goes, in four rows "
            "(look, do, ask, never). Whatever a page does not list is `do`: it passes and is recorded as before. "
            "A person's own page, `limbs/<name>.md` on the main branch of their self, adds to the rows it lists, "
            "which only tightens them; a row replaces (and so may loosen) one of these only when its table line "
            "cites the owner's letter naming the service. "
            "`brnrd limbs` prints this table for an install, with the person's rows in place.\n\n"
            "Generated from the pages by `brnrd limbs --write`; never edited by hand.\n\n"
            + table(pages))


def cli(argv: list[str]) -> int:
    """``brnrd limbs``: the table of hands for this install. ``--write`` regenerates limbs/README.md."""
    if argv[:1] in (["-h"], ["--help"]):
        print("usage: brnrd limbs [--write]\n\nThe hands this install's resident has: one line per service, "
              "its ask and never rows, the product's page or the person's.\n--write regenerates limbs/README.md "
              "from the product's pages.")
        return 0
    if argv == ["--write"]:
        (PRODUCT / "README.md").write_text(readme(products()), encoding="utf-8")
        print(f"wrote {PRODUCT / 'README.md'}")
        return 0
    if argv:
        sys.stderr.write(f"brnrd limbs: unknown argument {argv[0]!r}\n")
        return 2
    from .home import home_for
    try:
        home = home_for()
    except SystemExit:
        home = None
    pages, notes = every(home)
    sys.stdout.write(table(pages))
    for note in notes:
        sys.stderr.write(note + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
