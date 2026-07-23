"""The boundary facet schema — defined once, projected by every renderer.

Facets are the resident's perception of its *operating envelope*: the walls a
run can hit and the actionable state that changes a decision. The maintainer's
question was "agreeing by convention — how do I *choose* the facets?" and the
answer (``kb/design-resident-boundary.md`` §1, §8) is to stop choosing them by
editorial taste — three renderers that happen to list the same keys — and
**derive them from the walls we already agreed**, defining the set *here, once*.
"By schema, not by convention." The three renderers (the daemon JSON snapshot,
the woven hook line, ``brnrd portal state``) project from :data:`FACETS` so they
can never drift on *which* facets they carry, and an operator can list the
catalogue on demand with ``brnrd portal facets``.

A slot earns facet status iff it is one of:

- a **level** — a wall the run approaches, with a distance the resident plans
  against: subscription ``quota``, session ``spend``, ``context_window``
  headroom. (Wall-clock ``budget`` is a level too, but it has a proven local
  source and rides its own top-level ``budget`` block, so it is not repeated
  here.)
- a **state** — actionable operational posture that changes a decision without
  being a wall: ``coexisting_runs`` (sibling presence), ``remote_scm`` (PR /
  push posture).

Each facet is a uniform three-state record, because "missing" means two
different things the resident must not conflate:

- ``known`` — a value proven by the configured collector this heartbeat.
- ``absent`` — the collector ran and there is genuinely nothing yet: no PR for
  this branch, no quota snapshot this Shell exposes. The *affirmative-empty*
  signal — the same logic the closeout capsule uses for "0 pending events".
  Absence is data, surfaced on purpose, not a silent gap.
- ``unimplemented`` — no collector is wired for this slot on this Shell.
  ``required`` separates expected-to-grow (cost metering) from someday-niceties
  (coexisting runs while brr stays single-flight per dominion).

The level collectors are **per-Shell** (§8): Codex exposes live quota/context
through session-rollout ``token_count`` events, while Claude exposes terminal
spend/context through result JSON and cached subscription quota through the
interactive ``/usage`` PTY collector. A Shell with no collector for a slot reads
``unimplemented``. That asymmetry is the design, surfaced honestly, not a bug.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

# Three-state status values for a facet record.
KNOWN = "known"
ABSENT = "absent"
UNIMPLEMENTED = "unimplemented"

# Facet kinds.
LEVEL = "level"  # a wall the run approaches (distance-from-envelope)
STATE = "state"  # actionable posture that changes a decision


@dataclass(frozen=True)
class FacetSpec:
    """One facet's identity in the catalogue — the schema, not its live value.

    ``fills`` is the operator-facing one-liner: what a collector would put in
    this slot and why it might be empty. It is what ``brnrd portal facets`` prints
    so an operator can see *what the implemented facets are* without reading the
    code.
    """

    key: str
    label: str
    kind: str
    required: bool
    fills: str


# The ordered, wall-derived facet set. This tuple is the single definition;
# every renderer and the inspection command read it rather than re-listing keys.
FACETS: tuple[FacetSpec, ...] = (
    FacetSpec(
        "quota", "quota", LEVEL, True,
        "subscription quota headroom (used% + reset window) from a Shell-level "
        "source or local quota snapshot; absent until one is read",
    ),
    FacetSpec(
        "spend", "spend", LEVEL, True,
        "estimated session spend so far ($) handed over by the Shell — never a "
        "forward projection; unimplemented on Shells with no spend gauge",
    ),
    FacetSpec(
        "context_window", "context-window", LEVEL, True,
        "context-window headroom (% remaining) from the Shell's level source; "
        "unimplemented on Shells that do not expose it",
    ),
    FacetSpec(
        "coexisting_runs", "coexisting-runs", STATE, False,
        "live sibling runs sharing this dominion, read from the presence "
        "registry (brr stays single-flight per dominion for durable-memory "
        "writes, but a spawn: worker-stack child or an ad-hoc session can "
        "coexist); unimplemented on call sites with no presence collector "
        "wired",
    ),
    FacetSpec(
        "remote_scm", "remote-scm", STATE, True,
        "PR posture for the run branch — pushed?, PR open / not yet created — "
        "derived network-free from run metadata; absent = no PR recorded yet",
    ),
)

FACETS_BY_KEY: dict[str, FacetSpec] = {f.key: f for f in FACETS}


def _level_record(
    spec: FacetSpec, summary: str | None, *, has_collector: bool
) -> dict[str, object]:
    """Build a level facet record from an optional proven summary.

    ``has_collector`` is the per-Shell switch: when a collector is wired for
    this slot on this Shell but produced no value this heartbeat, the slot is
    ``absent`` (affirmative-empty); when no collector exists, it is
    ``unimplemented`` so the gap reads as "not built" rather than "empty".
    """
    text = str(summary or "").strip()
    if text:
        return {
            "status": KNOWN, "kind": spec.kind, "required": spec.required,
            "summary": text, "note": None,
        }
    if has_collector:
        return {
            "status": ABSENT, "kind": spec.kind, "required": spec.required,
            "summary": None,
            "note": f"no {spec.label} reading from this Shell yet",
        }
    return {
        "status": UNIMPLEMENTED, "kind": spec.kind, "required": spec.required,
        "summary": None,
        "note": f"no {spec.label} collector for this Shell yet",
    }


def _runner_block(
    runner_name: str | None,
    runner_meta: "dict[str, object] | None",
    quality_escalation: "dict[str, object] | None" = None,
    relay_consent: "dict[str, object] | None" = None,
    runner_catalog: "list[dict[str, object]] | None" = None,
    levels: "dict[str, object] | None" = None,
    wake_request: "dict[str, object] | None" = None,
) -> dict[str, object]:
    """Build the ``resources.runner`` governance block.

    Records which Shell/Core is actually executing this run so the
    resident (and future tooling) can see the selection without parsing
    the ``run.runner`` prose string. ``runner_name`` is the profile key;
    ``runner_meta`` is the raw profile dict (optional keys: model,
    class, provider, hooks, quota_source, cost_rank, owner).

    The block is always ``status: known`` when a runner_name is present
    — brr resolves the runner before the run starts, so it is always
    determined. ``status: absent`` when nothing is resolved yet (pre-run
    or test contexts).

    ``relay_consent`` is optional and carries spending plan details when
    relay fallback is being offered: reason, model, provider, estimated costs,
    per-run cap, relay balance, and consent state (pending/approved/denied/capped).

    ``wake_request`` (#577) is present exactly when a dashboard spool-rack
    tap was in play for this wake: ``requested_profile`` / ``resolved_profile``
    / ``applied`` (bool) / ``reason`` (``None`` when applied). This is the
    "you asked for X, you got Y, because Z" surface — a tap that existed and
    silently failed to apply used to be indistinguishable from no tap ever
    having been parked; this key is what makes the miss visible without the
    resident needing to notice on its own.
    """
    catalog = list(runner_catalog or [])
    if not runner_name:
        block: dict[str, object] = {
            "status": ABSENT, "summary": None, "note": "no runner resolved yet",
        }
        if catalog:
            block["catalog"] = catalog
        if wake_request:
            block["wake_request"] = wake_request
        return block
    from . import claude_status, runner_select

    meta = runner_meta or {}
    requested = str(meta.get("model") or "").strip() or None
    observed = str(meta.get("model_observed") or "").strip() or None
    if not observed and isinstance(levels, dict):
        ids = levels.get("model_ids")
        if isinstance(ids, list):
            observed = "+".join(
                str(item).strip() for item in ids if str(item).strip()
            ) or None
    mismatch = runner_select.core_mismatch(requested, observed)
    attestation = (
        "mismatch" if mismatch is True else
        "matched" if mismatch is False else
        "pending" if requested and requested != "default" else
        "unverifiable"
    )
    block: dict[str, object] = {
        "status": KNOWN,
        "name": runner_name,
        "model": requested,
        "model_requested": requested,
        "model_observed": observed,
        "core_mismatch": mismatch,
        "attestation": attestation,
        # *Why* the Core changed under us, when the session transcript recorded
        # a refusal/fallback. ``None`` on every clean run. Without this the
        # portal could say "mismatch" but never say what caused it, which is
        # the state that cost three days of guesswork (2026-07-13..16).
        "substitution_reason": claude_status.substitution_reason(levels),
        "class": str(meta.get("class") or "").strip() or None,
        "provider": str(meta.get("provider") or "").strip() or None,
        "hooks": str(meta.get("hooks") or "").strip() or None,
        "owner": str(meta.get("owner") or "user").strip() or "user",
        "quota_source": str(meta.get("quota_source") or "").strip() or None,
        "cost_rank": meta.get("cost_rank"),
        "capability_score": meta.get("capability_score"),
        "capability_source": str(meta.get("capability_source") or "").strip() or None,
        "capability_freshness": str(
            meta.get("capability_freshness") or ""
        ).strip() or None,
        "summary": runner_name,
    }
    if quality_escalation:
        block["quality_escalation"] = quality_escalation
    if relay_consent:
        block["relay_consent"] = relay_consent
    if catalog:
        block["catalog"] = catalog
    if wake_request:
        block["wake_request"] = wake_request
    return block


def build(
    *,
    quota_summary: str | None = None,
    levels: dict[str, object] | None = None,
    levels_collector: bool | Iterable[str] = False,
    branch: str | None = None,
    pr_number: str | None = None,
    runner_name: str | None = None,
    runner_meta: "dict[str, object] | None" = None,
    runner_catalog: "list[dict[str, object]] | None" = None,
    quality_escalation: "dict[str, object] | None" = None,
    relay_consent: "dict[str, object] | None" = None,
    pacing_status: "dict[str, object] | None" = None,
    coexisting: "list[dict[str, object]] | None" = None,
    wake_request: "dict[str, object] | None" = None,
) -> dict[str, object]:
    """Build the live ``resources`` facet dict from the collected inputs.

    Single construction point for every renderer (replaces the old hand-rolled
    ``daemon._resources_facet``). Inputs:

    - ``quota_summary`` — a quota one-liner from the local quota snapshot
      (``runner_quota``), the always-available quota path.
    - ``levels`` — a parsed level snapshot from the Shell's level collector
      (Claude result JSON, Codex session rollout), carrying ``quota`` /
      ``spend`` / ``context_window`` summaries. Its quota wins over
      ``quota_summary`` when present.
    - ``levels_collector`` — which level slots this Shell has a *wired*
      collector for, so an empty slot reads ``absent`` (collector ran, nothing
      yet) rather than ``unimplemented`` (no collector). ``True`` means all
      level slots; ``False`` means none; an iterable names the specific slots
      (per-Shell asymmetry: Codex collects ``quota`` + ``context_window`` but
      has no dollar-spend gauge, so ``spend`` stays ``unimplemented``).
    - ``branch`` / ``pr_number`` — run metadata for the ``remote_scm`` posture.
    - ``runner_name`` / ``runner_meta`` — which Shell/Core is executing; the
      profile dict carries model, class, provider, etc. Renders as
      ``resources.runner`` for governance visibility (step 3,
      ``design-runner-cores.md``).
    - ``runner_catalog`` — selectable local Runner/Core catalog, built from the
      same profile view as selection and marked with ``selected`` on the active
      profile.
    - ``quality_escalation`` — deterministic stronger local Runner metadata for
      a resident-authored ``respawn: true`` / ``quality: escalate`` handoff.
    - ``relay_consent`` — spending plan details for brnrd relay fallback when
      local quota is exhausted: reason, model, provider, costs, cap, balance,
      and consent state (pending/approved/denied/capped).
    - ``pacing_status`` — the quota-aware scheduler-pacing read (kb/design-
      director-loop.md §B1): ``{"binding_remaining_pct": ..., "floor": None |
      "low" | "critical", "excluded_thin": [...]}``. Attached as
      ``quota.pacing`` when present, so a mid-run boundary can see the same
      binding percent the scheduler used to stretch/pause ``every:`` entries.
      ``binding_remaining_pct`` only ever reflects the dispatched Core's own
      per-model bucket (or none, at a scheduling tick with no committed
      Core) — a different Core's thin ``week_models`` bucket never binds it,
      but rides along in the optional ``excluded_thin`` list (labels only)
      so it's still visible without pacing on it (#561). Absent (no sub-key)
      when quota isn't resolvable this heartbeat — never a fabricated
      number.
    - ``coexisting`` — a live presence-registry snapshot (``presence.
      list_active()``, this dominion, self excluded) as a list of entry
      dicts (``run_id``/``stream``/``label``/``kind``), or ``None`` when the
      call site has no presence collector wired. ``None`` renders
      ``unimplemented`` (matches every prior wake's behaviour exactly);
      an empty list renders ``absent`` ("ran, nothing there" — no sibling
      running right now); a non-empty list renders ``known`` with a short
      summary of who else is active. This is the one facet slice
      ``kb/design-multi-workstream-concurrency.md`` names as ready-to-wire
      infrastructure for any future concurrent-workstream slice, independent
      of which fan-out shape ships.
    - ``wake_request`` (#577) — ``task.meta["wake_request"]`` when a
      dashboard spool-rack tap was in play for this wake; attached under
      ``resources.runner.wake_request`` when present, absent otherwise. See
      ``_runner_block`` for the shape.
    """
    levels = levels or {}
    if isinstance(levels_collector, bool):
        wired_slots = {"spend", "context_window"} if levels_collector else set()
    else:
        wired_slots = {str(s) for s in levels_collector}

    def _level_summary(key: str) -> str | None:
        slot = levels.get(key)
        if isinstance(slot, dict):
            return str(slot.get("summary") or "").strip() or None
        if isinstance(slot, str):
            return slot.strip() or None
        return None

    quota = _level_summary("quota") or quota_summary
    # quota always has a collector (the local runner_quota snapshot), so an
    # empty quota slot is affirmative-``absent``, never ``unimplemented``.
    quota_facet = _level_record(
        FACETS_BY_KEY["quota"], quota, has_collector=True
    )
    if pacing_status:
        quota_facet["pacing"] = pacing_status
    spend_facet = _level_record(
        FACETS_BY_KEY["spend"], _level_summary("spend"),
        has_collector="spend" in wired_slots,
    )
    context_facet = _level_record(
        FACETS_BY_KEY["context_window"], _level_summary("context_window"),
        has_collector="context_window" in wired_slots,
    )

    spec_co = FACETS_BY_KEY["coexisting_runs"]
    if coexisting is None:
        coexisting_facet: dict[str, object] = {
            "status": UNIMPLEMENTED, "kind": spec_co.kind,
            "required": spec_co.required, "summary": None,
            "note": "no presence collector wired at this call site",
        }
    elif not coexisting:
        coexisting_facet = {
            "status": ABSENT, "kind": spec_co.kind,
            "required": spec_co.required, "summary": None,
            "note": "no sibling runs active right now",
        }
    else:
        names = [
            str(e.get("name") or e.get("label") or e.get("stream") or e.get("run_id") or "?")
            for e in coexisting[:3]
        ]
        n = len(coexisting)
        plural = "s" if n != 1 else ""
        coexisting_facet = {
            "status": KNOWN, "kind": spec_co.kind,
            "required": spec_co.required,
            "summary": f"{n} sibling run{plural}: " + "; ".join(names),
            "note": None,
            "siblings": list(coexisting),
        }

    pr = str(pr_number or "").strip()
    pr_recorded = bool(pr)
    spec_scm = FACETS_BY_KEY["remote_scm"]
    remote_scm = {
        "status": KNOWN if pr_recorded else ABSENT,
        "kind": spec_scm.kind, "required": spec_scm.required,
        "branch": branch,
        "pr_number": pr if pr_recorded else None,
        # `.pr` is a network-free handle, not authoritative forge state. It
        # proves only that a PR was recorded during this run; calling it open
        # becomes false the moment the resident merges it in the same wake.
        "pr_state": "recorded" if pr_recorded else "none",
        "summary": f"PR #{pr}" if pr_recorded else None,
        "note": None if pr_recorded else "no PR recorded for this branch yet",
    }

    return {
        "runner": _runner_block(
            runner_name,
            runner_meta,
            quality_escalation,
            relay_consent,
            runner_catalog,
            levels,
            wake_request,
        ),
        "quota": quota_facet,
        "spend": spend_facet,
        "context_window": context_facet,
        "coexisting_runs": coexisting_facet,
        "remote_scm": remote_scm,
    }


def facet_value(facet: dict[str, object] | None) -> str:
    """Render one facet record's value for a one-line view.

    Shared by every renderer so the three rails can never disagree on how a
    ``known`` / ``absent`` / ``unimplemented`` slot reads. ``known`` shows its
    value (a PR handle or a summary); the empty states name themselves and carry
    their reason in parentheses — substantially more legible than a flat
    "unavailable".
    """
    facet = facet if isinstance(facet, dict) else {}
    status = facet.get("status")
    if status == KNOWN:
        if facet.get("pr_number"):
            return f"PR #{facet.get('pr_number')}"
        summary = str(facet.get("summary") or "").strip()
        return summary or "known"
    note = str(facet.get("note") or "").strip()
    state = status if status in {ABSENT, UNIMPLEMENTED} else "unavailable"
    return f"{state} ({note})" if note else str(state)


def render_line(resources: dict[str, object] | None) -> str | None:
    """The woven ``- resources: …`` one-liner for the hook delta.

    Iterates :data:`FACETS` so the woven line carries exactly the schema's
    facets in order — add a facet to the schema and it appears here for free.
    """
    if not resources:
        return None
    parts = [
        f"{spec.label}={facet_value(resources.get(spec.key))}"
        for spec in FACETS
    ]
    return "- resources: " + "; ".join(parts) + "."


def describe_facets(
    resources: dict[str, object] | None = None,
) -> list[dict[str, object]]:
    """The facet catalogue for operator inspection (``brnrd portal facets``).

    Returns one row per schema facet: its identity (key / label / kind /
    required), the ``fills`` blurb describing what a collector would put there,
    and — when a live ``resources`` dict is supplied — the current status and
    value. This is the on-demand answer to "what are the implemented facets,
    and which are populated right now?"
    """
    rows: list[dict[str, object]] = []
    resources = resources if isinstance(resources, dict) else {}
    for spec in FACETS:
        live = resources.get(spec.key) if resources else None
        live = live if isinstance(live, dict) else None
        rows.append(
            {
                "key": spec.key,
                "label": spec.label,
                "kind": spec.kind,
                "required": spec.required,
                "fills": spec.fills,
                "status": (live or {}).get("status") if live else None,
                "value": facet_value(live) if live else None,
            }
        )
    return rows
