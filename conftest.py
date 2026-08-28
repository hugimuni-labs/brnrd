"""Repository-wide pytest bootstrap.

The backend integration suite routinely builds two- and three-repository
account topologies to exercise routing, pairing, publish scope, ledgers, and
dashboard behaviour. Those fixture shapes are not assertions about the
commercial Free offer.

Keep the suite's ambient repo headroom deliberately above those fixture
needs, then let the dedicated limits tests pin the production Free contract
explicitly. Setting this before pytest imports the test modules matters:
``Settings`` captures environment-backed dataclass defaults at import time.

This is intentionally an assignment rather than ``setdefault``: a developer
shell carrying production-like BRNRD settings must not change the topology
available to unrelated integration fixtures. Tests that exercise repo limits
pass their limit to ``Settings`` explicitly.
"""

from __future__ import annotations

import os


os.environ["BRNRD_LIMIT_FREE_REPOS"] = "10"
