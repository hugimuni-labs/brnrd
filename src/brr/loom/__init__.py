"""The loom feed — ``brnrd loom``, the daemon's first local listener.

design-the-loom.md §3/§4/§20/§22: the screen is rebuilt every beat from the
frame's own files. This package is the mechanical half of that screen:

- :mod:`brr.loom.state` — ``build()``, the ``GET /loom/state.json`` contract,
  pure reads of ``<repo>/.brr`` and the account home;
- :mod:`brr.loom.server` — a stdlib HTTP listener bound to ``127.0.0.1``
  serving the page under ``static/``, the state, a bench file and an SSE
  stream on the beat.

``static/`` is the page's (a sibling hand builds it); nothing here writes.
"""
