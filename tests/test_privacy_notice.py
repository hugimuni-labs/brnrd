"""The privacy notice's numbers are the numbers the code enforces.

A privacy notice is the one document in this repo where drift is not a
staleness bug but a **false statement about a person's data**. `/privacy`
therefore declares each duration once, as a named constant in its
``<script>`` block, and this module ties every one of those declarations to
the code that actually enforces it — by *driving* that code, not by reading
it. Age an event past the published day count and its body must really be
gone; ask the app for a session cookie and its ``max-age`` must really be
the published number of days.

The same move #675 and #680 made for the runner image's package list:
derive the claim from the artifact, never restate it in the test.

One test here (``test_the_notice_never_promises_more_than_the_mechanism``)
is a wording guard rather than a behaviour drive, and it is labelled as such
in its docstring — it cannot be turned red by a code change, only by an edit
to the page. That is the point of it.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("sqlalchemy")

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import select  # noqa: E402

from brnrd import create_app  # noqa: E402
from brnrd import inbox as inbox_service  # noqa: E402
from brnrd.activity_records import ACTIVITY_STALE_TTL, fresh_activity_records  # noqa: E402
from brnrd.config import Settings  # noqa: E402
from brnrd.models import ActivityRecord, Event  # noqa: E402
from brnrd.routers.accounts import SESSION_TTL  # noqa: E402
from _helpers import brnrd_account_headers  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]
FRONTEND_ROUTES = REPO_ROOT / "src" / "frontend" / "src" / "routes"
PRIVACY_PAGE = FRONTEND_ROUTES / "privacy" / "+page.svelte"
UPSUN_CONFIG = REPO_ROOT / ".upsun" / "config.yaml"


# ── reading the published numbers off the page ───────────────────────────

_TTL_DECL = re.compile(r"^\tconst (TTL_[A-Z_]+) = (\d+);", re.MULTILINE)


def published() -> dict[str, int]:
    """The durations `/privacy` actually tells the reader.

    Parsed from the page's own declarations, so this helper cannot drift
    from what a visitor sees — if someone deletes a constant, the lookups
    below raise ``KeyError`` rather than silently asserting nothing.
    """
    text = PRIVACY_PAGE.read_text(encoding="utf-8")
    found = {name: int(value) for name, value in _TTL_DECL.findall(text)}
    assert found, "no TTL_* declarations found in the privacy notice"
    return found


@pytest.fixture(scope="module")
def numbers() -> dict[str, int]:
    return published()


def _app(**overrides):
    kwargs = dict(
        database_url="sqlite:///:memory:",
        public_base_url="https://brnrd.example",
        github_oauth_client_id="gh-client",
        github_oauth_client_secret="gh-secret",
        github_oauth_authorize_url="https://github.example/login/oauth/authorize",
        github_oauth_token_url="https://github.example/login/oauth/access_token",
        github_api_base_url="https://api.github.example",
    )
    kwargs.update(overrides)
    return create_app(Settings(**kwargs))


@pytest.fixture()
def app():
    return _app()


def _repo_id(app) -> str:
    client = TestClient(app)
    headers = brnrd_account_headers(app, github_id="42", login="octocat", email="o@e.com")
    r = client.post(
        "/v1/accounts/repos", json={"repo_full_name": "Gurio/demo"}, headers=headers
    )
    assert r.status_code == 201, r.text
    return r.json()["repo_id"]


def _queued_event(app, repo_id: str, *, event_id: str, age: timedelta) -> None:
    with app.state.SessionLocal() as db:
        db.add(
            Event(
                event_id=event_id,
                repo_id=repo_id,
                source="telegram",
                body="the message text a real person sent",
                reply_to='{"platform": "telegram", "chat_id": "-100", "username": "someone"}',
                status=Event.STATUS_QUEUED,
                created_at=datetime.now(timezone.utc) - age,
            )
        )
        db.commit()


def _sweep(app) -> None:
    inbox_service.reset_gc_throttle()
    with app.state.SessionLocal() as db:
        inbox_service.gc_events(db, force=True)


def _event(app, event_id: str):
    with app.state.SessionLocal() as db:
        return db.execute(
            select(Event).where(Event.event_id == event_id)
        ).scalar_one_or_none()


# ── the retention numbers, driven ────────────────────────────────────────


def test_a_never_answered_message_body_survives_until_the_published_day(app, numbers):
    """Drive the real sweep on both sides of the number the page publishes.

    Not `_EVENT_BODY_TTL == 14`: that would only prove two literals agree.
    This proves the page's number is the age at which the queue actually
    forgets what someone wrote.
    """
    days = numbers["TTL_QUEUED_BODY_DAYS"]
    repo_id = _repo_id(app)
    _queued_event(app, repo_id, event_id="evt-just-inside", age=timedelta(days=days - 1))
    _queued_event(app, repo_id, event_id="evt-just-outside", age=timedelta(days=days + 1))

    _sweep(app)

    assert _event(app, "evt-just-inside").body is not None
    aged_out = _event(app, "evt-just-outside")
    assert aged_out.body is None
    # #525: the attachment pointers die with the body, which is what lets the
    # notice say "we store pointers, never the bytes" on the same schedule.
    assert aged_out.attachments_json == "[]"


def test_the_routing_row_survives_until_the_published_day(app, numbers):
    """The row — sender id, username, comment URL — outlives the body, and
    the notice says so. Prove both halves of that sentence."""
    body_days = numbers["TTL_QUEUED_BODY_DAYS"]
    row_days = numbers["TTL_EVENT_ROW_DAYS"]
    assert row_days > body_days, "the notice claims the row outlives the body"

    repo_id = _repo_id(app)
    _queued_event(app, repo_id, event_id="evt-body-gone", age=timedelta(days=row_days - 1))
    _queued_event(app, repo_id, event_id="evt-row-gone", age=timedelta(days=row_days + 1))

    _sweep(app)

    survivor = _event(app, "evt-body-gone")
    assert survivor is not None, "the row must still exist inside the window"
    assert survivor.body is None, "…but its body is long gone"
    assert "username" in survivor.reply_to, "…while the routing metadata is not"
    assert _event(app, "evt-row-gone") is None


def test_the_sign_in_cookie_expires_on_the_published_day(numbers, monkeypatch):
    """Drive a real login and read the lifetime off the real Set-Cookie."""
    from brnrd.oauth import GitHubIdentity

    app = _app()
    client = TestClient(app, base_url="https://testserver")
    monkeypatch.setattr(
        "brnrd.routers.web_auth.oauth.resolve_identity",
        lambda settings, *, code, redirect_uri, code_verifier: GitHubIdentity(
            github_id="42", login="octocat", email="o@e.com"
        ),
    )
    start = client.get("/auth/github/start?next=/", follow_redirects=False)
    state = re.search(r"[?&]state=([^&]+)", start.headers["location"]).group(1)
    callback = client.get(
        f"/auth/github/callback?code=ok&state={state}", follow_redirects=False
    )

    set_cookie = "\n".join(
        v for k, v in callback.headers.multi_items() if k.lower() == "set-cookie"
    )
    name = app.state.settings.session_cookie
    max_age = re.search(rf"{re.escape(name)}=[^;]+;.*?[Mm]ax-[Aa]ge=(\d+)", set_cookie)
    assert max_age, f"no max-age on the session cookie: {set_cookie!r}"
    assert int(max_age.group(1)) == numbers["TTL_SESSION_DAYS"] * 86400
    # The notice also calls the cookie HttpOnly / SameSite=Lax, and rests its
    # no-banner claim on it being strictly necessary.
    assert "HttpOnly" in set_cookie
    assert "SameSite=lax" in set_cookie.replace("Lax", "lax")
    assert SESSION_TTL.total_seconds() == numbers["TTL_SESSION_DAYS"] * 86400


def test_a_review_pack_is_dropped_on_the_published_second(numbers, monkeypatch):
    """Put a pack into the *app's own* relay and let its clock run out."""
    app = _app()
    seconds = numbers["TTL_REVIEW_PACK_SECONDS"]
    assert app.state.settings.pack_relay_ttl_s == seconds

    clock = {"t": 1_000_000.0}
    monkeypatch.setattr("brnrd.pack_relay.time.time", lambda: clock["t"])
    token, _expires = app.state.pack_relay.put({"kind": "review", "files": []})

    clock["t"] += seconds - 1
    assert app.state.pack_relay.get(token) is not None, "still inside the TTL"
    clock["t"] += 2
    assert app.state.pack_relay.get(token) is None, "past the TTL"

    # And the public renderer stops serving it — the capability really expires.
    assert TestClient(app).get(f"/r/{token}").status_code == 404


def test_a_task_row_goes_stale_on_the_published_minute(numbers):
    """The dashboard's activity projection, driven either side of the line."""
    minutes = numbers["TTL_ACTIVITY_MINUTES"]
    assert ACTIVITY_STALE_TTL == timedelta(minutes=minutes)
    now = datetime(2026, 7, 24, 12, 0, tzinfo=timezone.utc)
    rows = [
        ActivityRecord(id="a", record_id="fresh", reported_at=now - timedelta(minutes=minutes - 1)),
        ActivityRecord(id="b", record_id="stale", reported_at=now - timedelta(minutes=minutes + 1)),
    ]
    kept = {r.record_id for r in fresh_activity_records(rows, now=now)}
    assert kept == {"fresh"}


def test_run_pages_stop_being_mirrored_on_the_published_day(numbers):
    """Drive the daemon-side selection the notice describes as the default."""
    from brr.account import CorpusFile
    from brr.gates import cloud

    days = numbers["TTL_RUN_MIRROR_DAYS"]
    now = datetime(2026, 7, 21, tzinfo=timezone.utc)
    dummy = Path(__file__)

    def run_file(stamp: str) -> CorpusFile:
        return CorpusFile(
            layer="runs", path=f"runs/slug/run-{stamp}-1000-aaaa/state.md", abspath=dummy
        )

    inside = (now - timedelta(days=days - 1)).strftime("%y%m%d")
    outside = (now - timedelta(days=days + 1)).strftime("%y%m%d")
    kept = {
        f.path
        for f in cloud._publish_selection([run_file(inside), run_file(outside)], {}, now=now)
    }
    assert run_file(inside).path in kept
    assert run_file(outside).path not in kept


# ── the route itself ─────────────────────────────────────────────────────


def _passthru_pattern() -> re.Pattern[str]:
    """The production routing rule, read from the deploy config it lives in.

    `/privacy` has no file of its own in the static build: it is a
    client-side route served by the SPA fallback. It is therefore reachable
    only if this regex does *not* claim it for FastAPI.
    """
    text = UPSUN_CONFIG.read_text(encoding="utf-8")
    m = re.search(r"^\s*'(\^/\([a-z0-9|/]+\).*)':\s*$", text, re.MULTILINE)
    assert m, "no passthru rule found in .upsun/config.yaml"
    return re.compile(m.group(1))


def test_the_privacy_route_exists_and_the_spa_serves_it():
    assert PRIVACY_PAGE.is_file()
    pattern = _passthru_pattern()
    # Positive control: the rule really is the API passthru, so a failure
    # below means "the route got claimed", not "the regex was misread".
    assert pattern.search("/v1/dashboard/terms-status")
    assert pattern.search("/auth/github/start")
    # …and neither /privacy nor its sibling legal page is claimed by it.
    assert not pattern.search("/privacy")
    assert not pattern.search("/terms")


def test_the_notice_links_only_to_pages_that_exist():
    """A dangling legal link is a real defect: /legal-notice is still being
    drafted, and a notice that points at a 404 is worse than one that
    doesn't point at all."""
    text = PRIVACY_PAGE.read_text(encoding="utf-8")
    linked = set(re.findall(r"resolve\('(/[a-z0-9-]*)'\)", text))
    assert linked, "the notice links to nothing internal at all"
    for route in sorted(linked):
        if route == "/":
            assert (FRONTEND_ROUTES / "+page.svelte").is_file()
            continue
        assert (FRONTEND_ROUTES / route.lstrip("/") / "+page.svelte").is_file(), (
            f"the privacy notice links to {route}, which has no route"
        )


def test_the_public_footers_reach_the_notice():
    footers = [
        REPO_ROOT / "src" / "frontend" / "src" / "lib" / "Landing.svelte",
        FRONTEND_ROUTES / "pricing" / "+page.svelte",
    ]
    for page in footers:
        text = page.read_text(encoding="utf-8")
        assert "resolve('/privacy')" in text, f"{page.name} does not link the notice"


def test_the_notice_is_not_an_acceptance_gate(app):
    """#569 is explicit that a privacy notice is not a contract. It must not
    fetch, POST, or record anything — no checkbox, no acceptance column, no
    backend route bearing its name."""
    text = PRIVACY_PAGE.read_text(encoding="utf-8")
    for forbidden in ("fetch(", "type=\"checkbox\"", "/v1/terms/accept", "$state("):
        assert forbidden not in text, f"the notice must not {forbidden}"
    paths = {getattr(r, "path", "") for r in app.routes}
    assert not [p for p in paths if "privacy" in p], (
        "the backend must not own a /privacy surface: the page is inert"
    )


def test_the_notice_never_promises_more_than_the_mechanism():
    """WORDING GUARD — this one cannot be turned red by a code change.

    "No part of brnrd reads your working tree" is true about the mechanism.
    "Your code never leaves your machine" is not: the corpus lane mirrors
    agent-written pages verbatim and those pages routinely quote code
    (SECURITY.md, driven under #417). The distance between those two
    sentences is the distance between an honest architecture and a
    misrepresentation, so the widening is worth pinning even though only a
    human edit can trip it.

    It is a *substring* guard and cannot tell an affirmation from a denial —
    which it demonstrated on its first run by failing the draft's own "it is
    not a promise that your code never leaves your machine". The page now
    borrows SECURITY.md's phrasing instead, so the reassuring sentence never
    appears on the page in any grammatical mood. That is the stronger shape
    anyway: nothing to quote out of context.
    """
    text = PRIVACY_PAGE.read_text(encoding="utf-8").lower()
    for overclaim in (
        "never leaves your machine",
        "your code never leaves",
        "we never see your code",
        "we cannot see your code",
        "no data ever leaves",
    ):
        assert overclaim not in text, f"the notice overclaims: {overclaim!r}"
    # …and the narrow, true version is present with its caveat attached.
    assert "reads your working tree" in text
    assert "never as a guarantee about what" in text
    assert "verbatim" in text
