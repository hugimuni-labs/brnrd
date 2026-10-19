# Incremental loom fold: phase 1 measurement

Measured on 2026-10-17 against main `f375e645`. This is a disconnected copy,
with three fake held body locks, 50 empty room outboxes and two fake connector
supervisors. Both versions use the same frozen snapshot and the same committed
`loom_home_idle.py` harness. The larger case adds 32,000 inert renewal facts.
Each warmup adds one router fact in all four final runs.

| Facts after warmup | Main idle CPU / tick | Incremental idle CPU / tick | Main renewal CPU / tick | Incremental renewal CPU / tick |
|---|---:|---:|---:|---:|
| 16,069 | 129.1 ms | 32.2 ms | 406.2 ms | 36.0 ms |
| 48,069 | 321.4 ms | 33.5 ms | 853.8 ms | 33.6 ms |

Idle means 20 unprofiled production ticks after two warmups and a profiling
interval. Renewal means five ticks, each including one durable production
append and the tick which observes it. The renewal is a semantic no-op; this
measures reader, reducer, projection lookup and writer costs together. It does
not measure arbitrary arriving letters or large dependency fanout. CPU seconds
in the four final logs are respectively 2.582/0.643 and 6.427/0.669 for idle,
and 2.031/0.180 and 4.269/0.168 for renewal. Machine load varied; these are
observations, not a live-utilization promise.

Reproduce on an authorized local home (the harness copies it first):

```sh
PYTHONPATH=src python bench/loom_home_idle.py HOME --full-tick --ticks 20 --changed-ticks 5
PYTHONPATH=src python bench/loom_home_idle.py HOME --full-tick --ticks 20 --changed-ticks 5 --extra 32000
```

No real spawn, signal, connector child, transport send or gauge collector is
allowed by the harness. Gauge timestamps are refreshed in the private copy;
lease time is held fixed after warmup. Production authority and physical body
lock checks still execute. No live runtime was edited or restarted.

Startup, removed/replaced/truncated streams and unproven causal extensions
still replay. The fast-path proof deliberately rejects a new priority at or
below any prior priority, or a newly resolved old missing predecessor. No
checkpoint or SQLite state exists. The ledger remains authoritative.
