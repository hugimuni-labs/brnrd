"""The throw's seams — one frozen shape per phase output.

A phase reads only the shapes handed to it and returns one of these; nothing
reaches into another phase's locals. *Frozen* is a statement about the seam,
not about the world: a shape may carry a reference to a mutable object the
whole throw shares (the ``Run``, the delivery counters, the card state), and
those move exactly as they did when all of this was one function's locals.
What cannot happen is a phase rebinding a field another phase will read —
the next value is a new shape (``dataclasses.replace``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from ..account import AccountContext
    from ..branching import PublishPlan
    from ..daemon import _WorkerEmit
    from ..envs import EnvBackend, RunContext
    from ..run import Run
    from ..runner import RunnerResult
    from ..runner_select import RunnerProfile
    from ..spending_plan import SpendingPlan


@dataclass(frozen=True)
class Lane:
    """The Shell+Core an attempt runs on, and everything resolved from it.

    Replaced whole on an automatic fallback (``boundary``); refined between
    attempts (``dispatch`` strips the last attempt's observed model, reads the
    level quota on attempt 1, sets this attempt's mounted-transcript resume
    argv; ``stream`` records the observed core). Those refinements carry into
    the next attempt exactly as the old loop-scoped locals did.

    ``extra_args`` is the lane's own argv (codex's hook overrides) and is
    never rewritten after the lane is resolved. ``resume_args`` is the one
    attempt's mount — ``dispatch`` *replaces* it every attempt (a fresh
    session id, or ``[]`` when the attempt did not mount), and
    :meth:`runner_args` composes the two. Move 3b: the mount argv used to be
    prepended onto ``extra_args`` itself, so a retry on a mounted Shell
    stacked every earlier attempt's ``--resume <id> --fork-session`` behind
    its own (attempt 3 carried three, each a *different* session — no dedupe
    could have caught them), and a retry whose mount failed still resumed
    the previous attempt's seed under a prose prompt.
    """

    choice: RunnerProfile
    name: str
    meta: dict[str, object] | None
    quota_summary: str | None
    env: dict[str, str]
    extra_args: list[str]
    hooks_installed: bool
    catalog: list[dict[str, Any]]
    quality_escalation: dict[str, object] | None
    wake_note: str | None
    resume_args: list[str] = field(default_factory=list)

    def runner_args(self) -> list[str]:
        """The argv this attempt hands the Shell: the mount first, then the
        lane's own — the order the prepend always produced on attempt 1."""
        return [*self.resume_args, *self.extra_args]


@dataclass(frozen=True)
class Prepared:
    """Everything one throw fixes before its first runner attempt."""

    # the call
    event: dict
    repo_root: Path
    responses_dir: Path
    cfg: dict
    max_retries: int
    account_context: AccountContext | None
    inbox_dir: Path
    # identity and routing
    eid: str
    brr_dir: Path
    runs_dir: Path
    repo_label: str
    is_home_root: bool
    is_strand_run: bool
    conv_key: str
    correspondent_key: str
    failure_defer_seconds: float
    emit: _WorkerEmit
    task: Run
    presence_id: str | None
    shuttle_home: Path
    # the place
    branch_plan: PublishPlan
    env_backend: EnvBackend
    env_ctx: RunContext
    run_root: Path
    branch_name: str | None
    branch_setup_notice: str | None
    context_path: Path
    # the wake's orientation, assembled once
    event_body_for_prompt: str
    communication_snapshot: dict[str, Any] | None
    recent_conversation: list[dict[str, Any]]
    pending_events_snapshot: list[dict[str, Any]]
    present_snapshot: list[dict[str, Any]]
    prompt_diffense: bool
    # the portals, and the throw-wide state every phase shares by reference
    resp_path: Path
    outbox_dir: Path
    card_path: Path
    menu_path: Path
    flush_path: Path
    codex_events_path: Path
    card_state: dict[str, object]
    menu_state: dict[str, object]
    output_stats: dict[str, int]
    trace_dirs: list[str]
    seen_containers: set[str]
    run_started_monotonic: float
    # the first attempt's runner
    lane: Lane
    # Consumed daemon claim: never serialized onto the Run or read from it.
    resume_native_session_id: str | None = field(default=None, repr=False)
    resume_native_provider: str = ""


@dataclass(frozen=True)
class Attempt:
    """One pass of the runner — the loop state the old ``while True`` kept.

    One retry budget for every retryable class, not one per class: the
    counter was named ``clean_retries_used`` while a clean exit with missing
    artifacts was the only retryable failure; #729 added the transport-death
    class and the two **share** ``retries_used`` deliberately.
    ``response_retries`` (default 1) is the operator's ceiling on how many
    extra *expensive* attempts one event may buy, and that cost is the same
    whichever class asked for it; a per-class counter would let a run spend
    2× a budget nobody raised, and #729 forbids adding a config key to
    re-cap it.

    ``last_failure`` is *this* attempt's failure: ``None`` when the attempt
    began, set by ``boundary`` only if the attempt itself failed, and handed
    to the next attempt as ``None`` on a retry and on a fallback alike — so
    the ``Boundary`` that ends the run carries the failure of the attempt
    that ended it, never an earlier one. ``failures`` is the history: every
    recorded failure in attempt order, each row the failure dict plus its
    ``attempt`` number; it rides every retry *and* fallback. Move 3b: before
    it, ``last_failure`` survived a plain retry and only a fallback cleared
    it, so a transport drop on attempt 1 followed by a missing artifact on
    attempt 2 finalized as the transport drop.
    """

    n: int
    lane: Lane
    retries_used: int = 0
    attempted_runners: list[str] = field(default_factory=list)
    prompt_mode: str = "normal"
    fallback_notice: str | None = None
    last_failure: dict[str, object] | None = None
    failures: list[dict[str, object]] = field(default_factory=list)


@dataclass(frozen=True)
class Dispatched:
    """An attempt whose prompt is built and whose start is announced."""

    attempt: Attempt
    prompt: str
    started_monotonic: float
    started_wall: float
    resume_native_session_id: str | None = field(default=None, repr=False)


@dataclass(frozen=True)
class Streamed:
    """The runner returned; its boundaries were drained along the way and
    once more after it (the recovery drain, the ``finalizing`` portal write,
    the dominion capture)."""

    dispatched: Dispatched
    attempt: Attempt
    result: RunnerResult


BoundaryKind = Literal[
    "completed", "hold", "halt", "stopped", "retry", "fallback", "exhausted",
]


@dataclass(frozen=True)
class Boundary:
    """Where the turn ended, and what that obliges.

    ``retry`` / ``fallback`` carry ``next_attempt`` and loop back to
    ``dispatch``; every other kind is handed to ``finalize``.
    """

    kind: BoundaryKind
    attempt: Attempt
    next_attempt: Attempt | None = None
    stop_control: dict | None = None
    hold_spec: dict[str, object] | None = None
    #: ``halt:``'s accepted declaration (``daemon._halt_spec``). A halt is
    #: not a park: the seat ends, so this routes to ``_finalize_halt``,
    #: never to the hold-shaped finalizer.
    halt_spec: dict[str, object] | None = None
    terminal_reply: str | None = None
    success_signal: str | None = None
    has_new_commit: bool = False
    relay_candidate: RunnerProfile | None = None
    relay_plan: SpendingPlan | None = None


@dataclass(frozen=True)
class Finalized:
    """The throw's end: the run as its last phase left it.

    ``stage`` names which ending wrote it: ``deduplicated`` · ``refused`` ·
    ``env`` (the three that end inside ``prepare``, before any runner) ·
    ``stopped`` · ``held`` · ``done`` · ``failed``.
    """

    task: Run
    stage: str
