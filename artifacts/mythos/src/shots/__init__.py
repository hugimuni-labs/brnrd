"""Shots: each is a generator ``fn(need, **kw)`` that steps its physics every
local frame and yields ``(k, rgb8)`` only for the local indices the edit needs.

Physics runs continuously inside a shot even while the edit is cut away to an
insert, so returning to a shot returns to a world that kept moving.

    cosmos.py    web → disk → corona → crystal       (energy without names)
    plate.py     strike → rest → vibrate → observe   (one continuous world)
    inherit.py   capture → press → conduct → thread → wire

Screen anchors shared across cuts live here: a match cut preserves one
property, and when that property is *position* the two shots agree on it by
name rather than by coincidence.
"""
from __future__ import annotations

# The first ignition in the web lands where the disk's hottest inner edge is.
P_IGNITE = (1290.0, 430.0)
# The lensed photon arc's apex lands where the first coronal arch stands.
P_ARCH = (900.0, 380.0)
