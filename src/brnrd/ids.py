"""Opaque identifier and secret minting."""

from __future__ import annotations

import secrets

_PAIR_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"


def _rid(prefix: str, nbytes: int = 12) -> str:
    return f"{prefix}_{secrets.token_hex(nbytes)}"


def account_id() -> str:
    return _rid("acc")


def repo_id() -> str:
    return _rid("repo")


def token_id() -> str:
    return _rid("tok")


def daemon_id() -> str:
    return _rid("dmn")


def event_id() -> str:
    return _rid("ev")


def activity_id() -> str:
    return _rid("act")


def pair_request_id() -> str:
    return _rid("pair")


def config_change_request_id() -> str:
    return _rid("cfgreq")


def runner_wake_request_id() -> str:
    return _rid("wake")


def run_stop_request_id() -> str:
    return _rid("stopreq")


def terms_acceptance_id() -> str:
    return _rid("ta")


def channel_route_id() -> str:
    return _rid("chan")


def github_installation_id() -> str:
    return _rid("ghinst")


def github_installed_repo_id() -> str:
    return _rid("ghrepo")


def tg_pair_code_id() -> str:
    return _rid("tgpair")


def subscription_id() -> str:
    return _rid("sub")


def credit_bucket_id() -> str:
    return _rid("bkt")


def billing_ledger_id() -> str:
    return _rid("blg")


def api_key() -> str:
    return "bk_" + secrets.token_urlsafe(32)


def session_token() -> str:
    return "bs_" + secrets.token_urlsafe(32)


def daemon_token() -> str:
    return "bd_" + secrets.token_urlsafe(32)


def poll_secret() -> str:
    return secrets.token_urlsafe(24)


def pair_approve_secret() -> str:
    """Proof that an approve came from the daemon that *started* the pairing.

    Deliberately not the poll secret. The poll secret is the daemon's own
    credential — presenting it returns the minted daemon token — and this
    value is designed to travel to a *browser*, in a URL the human pastes.
    Two secrets, two blast radii: leaking this one costs the pairing, not
    the token.

    256-bit, unlike the ~20-bit human-typable ``pair_code``: this is the
    value the approval decision now rests on, so it has to be the one that
    is not enumerable.
    """
    return secrets.token_urlsafe(32)


def pair_code() -> str:
    return "BR-" + "".join(secrets.choice(_PAIR_ALPHABET) for _ in range(4))


def tg_pair_code() -> str:
    return "TG-" + "".join(secrets.choice(_PAIR_ALPHABET) for _ in range(4))
