"""The injected `spawn:` row must name every key the dispatcher reads (#1086).

Why a test module and not a comment: ``daemon-substrate.md``'s ``spawn:`` row
documented *capacity* and nothing else for weeks while
``daemon._queue_spawn_request`` read six frontmatter keys. Two of them —
``branch:`` / ``report:``, #640's declared contract — appeared in no prompt
surface and no page of ``brnrd docs portals``; they lived in one inline comment
in ``daemon.py``. So every spawn this account ever dispatched ran on the
completion check's weak tier, and the strong one was unreachable by
construction:

- **declared** (``spawn_contract_branch`` / ``spawn_contract_report``) ⇒
  ``_notify_spawn_parent`` *indicts* — ``status_label = "contract-mismatch"``,
  a spec-vs-published branch block, ``spec report: … (MISSING)``.
- **scanned** (the first ``brr/<slug>`` found in the spec's prose) ⇒ *advises
  only*, because "a scanned read may flag, only a declared one may indict".

``core:`` was the same shape one axis over: unset, a child boots on the
account's configured default, which is the *strongest* local Core — so bounded
mechanical work silently costs the most, against a ``run.md`` §Orchestration
line that tells the resident to use economy cores.

The property pinned here is structural, not a member list: a key added to
``_queue_spawn_request`` fails this module unless it is either documented in
the row or explicitly exempted **with a reason**. Defaulting to failure is the
point — the previous state was a comment nobody re-read.
"""

import ast
from pathlib import Path

from brr import prompts


#: Keys the row deliberately does not carry. Each entry is a decision, not an
#: oversight; add one only with the reason beside it.
ROW_EXEMPT = {
    # Internal plumbing: the dispatcher stamps the event source, never the
    # resident. Not a knob, so not a pin.
    "source",
    # Legacy aliases for `shell:` and `environment:`. Documenting an alias
    # teaches it; the row names the canonical spelling only.
    "runner",
    "env",
    # Choreography rather than cost or evidence — the isolation ladder and the
    # free-text note. `brnrd docs portals` owns both, and the row's own header
    # says that split is deliberate. `test_exempt_keys_stay_reachable` pins the
    # claim rather than trusting it.
    "environment",
    "reason",
}

#: The four keys #1086 exists for. Pinned by name as well as structurally: the
#: structural test would pass if the *code* stopped reading them, this one
#: would not. Losing `core:` re-opens the cost hole and losing
#: `branch:`/`report:` re-opens the evidence hole, whatever the code does.
COST_AND_CONTRACT_KEYS = ("shell", "core", "branch", "report")


def _spawn_row() -> str:
    body = (prompts._PROMPTS_DIR / "daemon-substrate.md").read_text(encoding="utf-8")
    rows = [line for line in body.splitlines() if line.lstrip().startswith("| `spawn:")]
    assert len(rows) == 1, f"expected exactly one `spawn:` row, found {len(rows)}"
    return rows[0]


def _keys_read_by_queue_spawn_request() -> set[str]:
    """Every string literal passed to ``fm.get(...)`` inside the function.

    Parsed out of the source rather than imported and introspected: the read
    set is a property of the code's *text*, and a hand-maintained mirror of it
    is the exact thing this module exists to stop.
    """
    from brr import daemon as daemon_mod

    tree = ast.parse(Path(daemon_mod.__file__).read_text(encoding="utf-8"))
    fn = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == "_queue_spawn_request"
    )
    keys: set[str] = set()
    for node in ast.walk(fn):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not isinstance(func, ast.Attribute) or func.attr != "get":
            continue
        if not isinstance(func.value, ast.Name) or func.value.id != "fm":
            continue
        if node.args and isinstance(node.args[0], ast.Constant):
            value = node.args[0].value
            if isinstance(value, str):
                keys.add(value)
    assert keys, (
        "found no `fm.get(...)` reads in _queue_spawn_request — the AST walk "
        "missed the function, so a green result here proves nothing"
    )
    return keys


def test_every_key_the_dispatcher_reads_is_documented_or_exempt():
    read = _keys_read_by_queue_spawn_request()
    # `spawn` itself is the verb, checked by `_truthy` at the call site rather
    # than read here; guard against it drifting in anyway.
    read.discard("spawn")
    row = _spawn_row()
    undocumented = {
        key for key in read if key not in ROW_EXEMPT and f"`{key}:`" not in row
    }
    assert not undocumented, (
        "daemon-substrate.md's `spawn:` row does not name "
        f"{sorted(undocumented)} — keys `_queue_spawn_request` reads. Document "
        "them in the row, or add them to ROW_EXEMPT with the reason (#1086)."
    )


def test_the_cost_and_contract_keys_are_named_specifically():
    row = _spawn_row()
    missing = [key for key in COST_AND_CONTRACT_KEYS if f"`{key}:`" not in row]
    assert not missing, (
        f"{missing} missing from the spawn row — `core:` is what makes a "
        "worker cheap and `branch:`/`report:` are what let its completion "
        "check indict rather than advise (#1086)"
    )


def test_the_row_says_what_an_unset_core_costs():
    """Naming the key is not the same as pricing it.

    The failure #1086 records was not that `core:` was unknown — it was that
    nothing said what happens when you omit it. A row that lists the key and
    leaves the default unstated re-creates the same silence one step later.
    """
    row = _spawn_row()
    assert "default" in row.lower() and "strongest" in row.lower(), (
        "the spawn row names `core:` but does not say that omitting it means "
        "the config default, which is the strongest local Core (#1086)"
    )


def test_exempt_keys_stay_reachable():
    """An exemption is a routing decision, not a deletion.

    ``environment:`` is exempt from the *row* on the grounds that
    ``brnrd docs portals`` carries it. If that stops being true the exemption
    is hiding the key instead of relocating it, so pin the claim.
    """
    from brr import docs as docs_mod

    portals = (Path(docs_mod.__file__).parent / "portals.md").read_text(
        encoding="utf-8"
    )
    spawn_para = next(
        line for line in portals.splitlines() if "`spawn: true` frontmatter" in line
    )
    for key in ("environment",):
        assert f"`{key}:`" in spawn_para, (
            f"`{key}:` is exempt from daemon-substrate.md's spawn row on the "
            "grounds that `brnrd docs portals` carries it, and it does not"
        )
