# The envoy's account-home shims

The X envoy's post/reply/delete/read mechanics live in this repo now
(`src/brr/envoy_x.py`, covered by tests and the gate) instead of as
free-standing scripts in the account home. `x-post.py` / `x-read.py`
here are the **installed shims** — thin wrappers with nothing but their
own directory, importing the product module.

**Install** (parent's call, not this PR's — nothing under
`account/` in the account home is touched by this change):

1. Ensure `brr` is importable where these run (the account home's own
   install of this project).
2. Copy both files into `<account-home>/account/`, replacing the
   existing `x-post.py` / `x-read.py` there.
3. Leave `x-brnrd-resident.env`, `x-post-log.jsonl`, `x-refresh.py`,
   `x-read-state.json` exactly where they already sit, beside the
   shims — the mechanics resolve every path relative to the shim's own
   directory (`Paths.in_dir`), so nothing else moves.

CLI shape is byte-compatible with the originals: the sweep contracts
and schedule prompts cite an interpreter *where brr is importable* —
typically `<repo>/.venv/bin/python3 ~/brnrd/account/x-post.py "text"` —
and these shims honor that invocation unchanged. The system `python3` on
the PATH often cannot import `brr`; that is usually the issue, not the
package. The shims report that error legibly if it occurs.

## The browser envoy (`x-browser.py`)

`src/brr/envoy_x_browser.py` is a second, independent lane: a persistent,
human-logged-in browser session (Playwright/Chromium) for the verbs X's
API forbids — see that module's docstring for the guardrails (kill
switch, hourly cap, the disarmed `send`, headed-only writes). `x-browser.py`
here is its installed shim, same shape as the two above.

**Install:**

1. Playwright is *not* a runtime dependency of this project (opt-in
   `browser` extra in `pyproject.toml`, since most adopters never touch
   this envoy) — `pip install playwright && playwright install chromium`
   wherever `brr` is importable for these scripts.
2. Copy `x-browser.py` into `<account-home>/account/`, beside the other
   shims.
3. **`chmod 700 x-browser.py`** after copying — git records only the
   executable bit, not the full mode, so a fresh checkout lands more
   permissive than intended; this file drives a browser tied to a live
   logged-in session and should not be group/world-readable.
4. One-time human step: run `<python-with-brr> x-browser.py login`
   (where `<python-with-brr>` is an interpreter where `brr` is importable,
   usually `<repo>/.venv/bin/python3`) — this opens a headed browser on a
   fresh persistent profile (`account/x-browser-profile/`, also excluded
   from this repo's own git tracking by `account.py`'s `GITIGNORE` — a
   leaked profile dir is the whole account) — log in by hand, then it
   verifies and reports which handle is logged in. The profile persists
   after that; no further login step until the session expires.
5. `send` stays inert until *both* `--confirm` (per call) and
   `BRR_X_BROWSER_SEND=1` (environment) are set — nothing posts by
   installing this.
