# Plan: cloud-runner env — Fly Machines (first BYO adapter)

First implementation of a Dimension B cloud-runner env adapter.
Ships as a plugin (`brr-env-fly-machines` pip package), not a
built-in, following the rule from
[`design-env-interface.md`](design-env-interface.md): anything that
needs an account, a CLI install, or an SDK install belongs in a
plugin.

## Status

**Not started.** Foundational reference:
[`research-cloud-runner-patterns.md`](research-cloud-runner-patterns.md)
§ Fly Machines covers the per-platform delta and the open
questions.

## Goals

- A working `fly-machine` env that brr daemons (host, BYO, or
  managed-mode) can configure with one config block and a Fly API
  token.
- Cold start under 1 second (warm-image case); per-task cost under
  $0.01 for a typical 5-minute task on `shared-cpu-1x` / 256 MB.
- Salvage rule observed: machine destroyed on clean `status=done`;
  preserved on `error` / `conflict`, with the machine ID surfaced
  in `task.meta`.

## Done definition

- `brr-env-fly-machines` package on PyPI, registered under the
  `brr.envs` entry-point group as `fly-machine`.
- `brr` core unchanged (the plugin is genuinely opt-in).
- `brr/runner` Docker image variant published to a Fly-reachable
  registry (Docker Hub or `registry.fly.io`).
- Documentation in the plugin's own README, with a
  `src/brr/docs/envs.md` cross-link added in brr core listing
  `brr-env-fly-machines` as the first cited plugin env.
- Tests: prepare creates machine, invoke streams output, finalize
  destroys on success and preserves on failure. Integration test
  gated on `FLY_API_TOKEN` being present in the env.

## Steps

1. **Image variant.** Split the bundled
   [`src/brr/Dockerfile`](../src/brr/Dockerfile) into `brr/daemon`
   and `brr/runner` variants (shared with
   [`plan-daemon-deployment-templates.md`](plan-daemon-deployment-templates.md)).
   Publish `brr/runner:latest` to Docker Hub.
2. **`FlyMachineEnv.prepare`.** `POST /v1/apps/{app}/machines` with
   `config.image = brr/runner:latest`, `auto_destroy: true`, env
   block carrying credential vars per
   [`research-cloud-runner-patterns.md`](research-cloud-runner-patterns.md)
   Pattern A. Repo via `git clone https://${TOKEN}@…` in the
   machine's entrypoint (Pattern B option 1). Persist machine ID
   to `ctx.env_state["machine_id"]`.
3. **`FlyMachineEnv.invoke`.** SSH via WireGuard or `POST .../exec`
   to run the runner command. Stream stdout / stderr back to the
   host trace via the existing `runner.invoke_runner` plumbing.
4. **`FlyMachineEnv.finalize`.** Push the branch from inside the
   machine via the git remote (Pattern C option 1). Response file
   captured from invoke stdout — no separate fetch step needed.
   `DELETE /v1/apps/{app}/machines/{id}` on `status=done`;
   preserve on `status ∈ {error, conflict}` and surface
   `machine_id` in `task.meta` for user inspection / cleanup.
5. **Configuration plumbing.** New `.brr/config` section:

   ```ini
   [env.fly-machine]
   api_token_env = FLY_API_TOKEN
   app_name = my-brr-runners
   region = ord                      ; optional; default = nearest
   image = brr/runner:latest         ; override for custom images
   machine_size = shared-cpu-1x      ; default
   ```

6. **Tests.** Mock the Fly REST API for unit tests; one
   integration test gated on `FLY_API_TOKEN`.
7. **README + docs cross-link.** Plugin README covers setup; brr
   core's `src/brr/docs/envs.md` gets a small "Plugin envs"
   section listing `brr-env-fly-machines` as the first cited
   example.

## Estimate

~300-400 LOC plugin code + ~150 LOC tests + ~100 LOC plugin
README. brr-core changes: ~50 LOC docs section + the image-publish
workflow (shared with deployment templates).

## Open questions before starting

- **Custom Fly app or per-user app?** If brr ships a Fly app
  template (`fly.toml`) that users `flyctl launch` once, fewer
  config knobs are needed; if brr uses the user's existing Fly
  app, the adapter is more flexible. Probably ship both with the
  template path as the default.
- **WireGuard SSH vs `/exec` API.** The `/exec` endpoint is
  cleaner but the SSH path is more flexible (supports
  long-running interactive use). Likely SSH for invoke (matches
  the `ssh` env pattern), `/exec` for destroy.
- **Subscription auth users.** Pattern A option 2 (tarball via
  secret) is more operational complexity than the first ship
  warrants. Defer; document as a known limitation; add when a
  user asks.

## Out of scope

- Modal / Daytona / E2B adapters — those are separate plugins,
  separate plans, separate timelines.
- Codespaces — fast-follow with its own plan
  (`plan-env-codespaces.md`).
- The cloud-init bootstrap of vanilla VMs — that's the `ssh` env
  in brr core, not a Fly-specific concern.

## Read next

1. [`research-cloud-runner-patterns.md`](research-cloud-runner-patterns.md)
   § Fly Machines for the per-platform analysis and open
   questions.
2. [`design-env-interface.md`](design-env-interface.md) for the
   env Protocol this plugin implements.
3. [`subject-managed-mode.md`](subject-managed-mode.md) →
   Dimension B for the strategic context.

## Lineage

- 2026-05-22 — drafted as part of the managed-mode KB shape
  rollout.
