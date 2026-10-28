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


def shard_items(items: list, spec: str) -> list:
    """Every Nth item for ``PYTEST_SHARD=k/N`` (1-based), in collection order.

    CI's backend job runs as a matrix of N shards (ci-speed, 2026-10-28).
    Collection order is the same on every shard and every xdist worker, so
    the shards partition the suite: each test runs on exactly one.
    """
    index, _, total = spec.partition("/")
    k, n = int(index), int(total)
    if not 1 <= k <= n:
        raise ValueError(f"PYTEST_SHARD={spec!r}: want k/N with 1 <= k <= N")
    return [item for i, item in enumerate(items) if i % n == k - 1]


def pytest_collection_modifyitems(config, items):
    spec = (os.environ.get("PYTEST_SHARD") or "").strip()
    if not spec:
        return
    keep = shard_items(items, spec)
    keep_ids = {id(item) for item in keep}
    config.hook.pytest_deselected(items=[item for item in items if id(item) not in keep_ids])
    items[:] = keep
