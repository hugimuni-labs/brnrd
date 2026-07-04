"""Current Planned State (CPS) view — plan + decision ledger snapshots.

Renders the CS5 (`plans/<repo>/active.md`, `plans/_cross-repo/active.md`)
and CS7 (`ledger/decisions.md`) account-dominion files that connected
daemons mirror via `PUT /v1/daemons/plans` (see `brnrd.routers.daemons`).
No new backend shape beyond that mirror — this module only reads what's
already on `Repo`/`Account` and renders it as-is, per
kb/plan-brnrd-dashboard-mvp.md "Gap: Current Planned State view" (ship
plain, skin later).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from brnrd.auth import get_db
from brnrd.models import Account

from .routes import _account_id, _render, _repos, _time_label

router = APIRouter(tags=["web"])


@router.get("/plans", response_class=HTMLResponse)
def plans_page(request: Request, db: Session = Depends(get_db)):
    account_id = _account_id(request, db)
    if account_id is None:
        return RedirectResponse(url="/login?next=/plans", status_code=303)
    account = db.get(Account, account_id)
    if account is None:
        return RedirectResponse(url="/login?next=/plans", status_code=303)

    repos = _repos(db, account.id)
    repo_plans = [
        {
            "repo": repo,
            "plan_md": repo.plan_md or "",
            "updated_label": _time_label(repo.plan_updated_at),
        }
        for repo in repos
        if (repo.plan_md or "").strip()
    ]
    cross_repo_plan_md = (account.cross_repo_plan_md or "").strip()
    decision_ledger_md = (account.decision_ledger_md or "").strip()

    return _render(
        request,
        "plans.html",
        {
            "body_class": "dashboard-page",
            "title": "brnrd plans",
            "logged_in": True,
            "account": account,
            "repos": repos,
            "repo_plans": repo_plans,
            "cross_repo_plan_md": cross_repo_plan_md,
            "decision_ledger_md": decision_ledger_md,
            "plans_updated_label": _time_label(account.plans_updated_at),
            "has_any_plan": bool(repo_plans or cross_repo_plan_md or decision_ledger_md),
        },
    )
