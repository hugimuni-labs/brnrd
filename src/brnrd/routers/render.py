"""Public render surface for diffense packs.

``GET /r/{token}`` serves the self-contained diffense HTML for a pack the
producer's daemon relayed (see ``POST /v1/daemons/pack``). The pack lives
only in RAM behind the unguessable token and expires on a TTL, so this is
a **capability URL**: possession of the link grants the rendered view for
as long as the relay holds it, and nothing is persisted server-side.

No auth on this route by design — a reviewer opening the link from a PR
body is not necessarily a brnrd user. The token is the capability; the
TTL bounds exposure. (A future tightening could gate private-repo packs
behind the reviewer's brnrd session; for now the model matches the user
publishing their own data to their own PR.)

``GET /r?pack=<raw-gist-url>`` serves only the renderer shell. The
browser fetches the pack from the user's GitHub gist, so brnrd never sees
or stores those durable pack bytes. The token relay remains the private /
no-gist fallback.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse

router = APIRouter(tags=["render"])


@router.get("/r/{token}", response_class=HTMLResponse)
def render_pack(token: str, request: Request) -> HTMLResponse:
    pack = request.app.state.pack_relay.get(token)
    if pack is None:
        raise HTTPException(
            status_code=404, detail="review pack expired or not found"
        )
    from brr.diffense.render import render

    return HTMLResponse(render(pack))


@router.get("/r", response_class=HTMLResponse)
def render_pack_shell() -> HTMLResponse:
    """Serve the static renderer shell; the browser loads ``?pack=...``."""
    from brr.diffense.render import render_shell

    return HTMLResponse(render_shell())
