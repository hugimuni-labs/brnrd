# Native accounting captures — 2026-10-02

Three actual brnrd runs per Shell on the maintainer's machine. Each run has
provenance.json with run id, source basename, attested Core and measured terms.

native.jsonl is a usage-only projection of the original Claude transcript,
Codex rollout or Vibe completion journal. Counters, message/action ids and
record nesting are copied unchanged. Prompt text, tool arguments, reasoning,
host paths and journal hash chains are removed. The prefix contains the first
five distinct billed requests; streaming repeats remain. Zero placeholder
Claude messages and synthetic rows are present where the original had them.

boundaries.jsonl projects the first twelve original hook records to timestamp,
phase, context, spend, quota and subagent. These old records have no per-request
cache accounting; cumulative draw is not a substitute. spend.json is copied
when it existed; its old Claude boot stamp can be zero because it selected a
placeholder. Codex/Vibe had no spend.json boot stamp, so absence is retained.
The Vibe adapter's real usage sidecar is copied as vibe-usage.json.

Tests derive the new boot/baseline projection from these native records and
also verify that the old records alone produce unknown terms. No prompt or
model request is sent by the tests.
