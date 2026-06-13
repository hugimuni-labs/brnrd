"""GitHub App endpoints for brnrd."""

from __future__ import annotations

import hashlib
import hmac
from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, Request

router = APIRouter(prefix="/api/github", tags=["github-app"])


def _signature_ok(secret: str, body: bytes, signature: str | None) -> bool:
    if not secret or not signature:
        return False
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(f"sha256={digest}", signature)


@router.get("/callback")
def github_app_callback(
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    error_description: str | None = None,
) -> dict[str, str | None]:
    """Accept GitHub App user-authorization callbacks.

    This is intentionally a safe landing endpoint for the newly registered
    GitHub App. The actual OAuth exchange can be wired in a later slice.
    """
    if error:
        raise HTTPException(status_code=400, detail=error_description or error)
    return {"status": "ok", "code": code, "state": state}


@router.get("/setup")
def github_app_setup(
    installation_id: str | None = None,
    setup_action: str | None = None,
) -> dict[str, str | None]:
    """Accept GitHub App post-install redirects."""
    return {
        "status": "ok",
        "installation_id": installation_id,
        "setup_action": setup_action,
    }


@router.post("/webhook")
async def github_app_webhook(
    request: Request,
    x_hub_signature_256: Annotated[str | None, Header()] = None,
    x_github_event: Annotated[str | None, Header()] = None,
) -> dict[str, str | None]:
    """Accept GitHub App webhooks at the public URL configured in GitHub."""
    body = await request.body()
    settings = request.app.state.settings
    if settings.github_webhook_secret and not _signature_ok(
        settings.github_webhook_secret,
        body,
        x_hub_signature_256,
    ):
        raise HTTPException(status_code=401, detail="Invalid GitHub signature")
    return {"status": "ok", "event": x_github_event}
