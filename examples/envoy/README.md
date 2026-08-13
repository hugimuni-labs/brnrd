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
and schedule prompts that cite
`python3 ~/brnrd/account/x-post.py "text"` keep working unchanged.
