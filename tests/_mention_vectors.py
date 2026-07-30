"""Shared test vectors for the fence-aware mention matcher (#879 member 5).

There is exactly one implementation now
(``brr.gates.github.parse.find_mention``) — brnrd already imported from
``brr.gates.github`` before this landed (``routers/webhooks.py``'s
``gh_parse``), so the two previously-drifted, hand-rolled predicates
(cloud's un-fenced casefold, the gate's case-sensitive substring check)
were replaced with one shared function rather than two copies kept honest
by a shared table. This table still lives here and is imported from both
``test_github_gate.py`` (the gate call site) and ``test_brnrd_github.py``
(the cloud call site) so a wiring regression in either caller is caught
locally, even though the underlying predicate is one piece of code.
"""

MENTION = "@brr-bot"

# (label, body, expected-match)
MENTION_VECTORS = [
    ("plain mention", f"{MENTION} please look at this", True),
    ("fenced block", f"discuss:\n```\n{MENTION} mentioned here\n```", False),
    ("inline code span", f"see `{MENTION}` in code", False),
    ("blockquote", f"> {MENTION} quoted from elsewhere", False),
    ("different casing", f"{MENTION.upper()} shout-mention", True),
    ("substring of a longer handle", f"{MENTION}-2 is a different bot", False),
    ("start of body", f"{MENTION} kicks things off", True),
    ("end of body", f"this ends with {MENTION}", True),
    ("empty body", "", False),
    ("only a fenced block", f"```\n{MENTION}\n```", False),
]

MENTION_VECTOR_IDS = [label for label, _body, _expected in MENTION_VECTORS]
