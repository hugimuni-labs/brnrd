# Plan: cloud env — Fly Machines (first BYO adapter)

Status: accepted on 2026-05-26 (locked in PR #40 MR review; implementation
feedback may reshape the exact env-class spine).

Fly Machines is the first accepted cloud env for brr. It is not implemented
yet. The env ships as first-party code under `src/brr/envs/fly_machines/`,
gated by the `brr[fly]` extra per
[`decision-monorepo-structure.md`](decision-monorepo-structure.md), and follows
the `EnvBackend` protocol in [`design-env-interface.md`](design-env-interface.md).

The same module has two callers:

- A local daemon using the user's Fly account, with `FLY_API_TOKEN` or the
  configured token env in the daemon environment.
- The brnrd backend using a brnrd-owned Fly pool for managed-compute failover,
  or a subscriber-owned Fly credential for BYO managed compute. See the caller
  axis in [`research-cloud-envs.md`](research-cloud-envs.md) and
  [`subject-managed-mode.md`](subject-managed-mode.md).

Anything that needs a cloud account, CLI install, SDK, or provider token stays
outside the always-on daemon dependency graph. Installing `brr[fly]` opts into
the Fly dependency footprint.

## Current Implementation Contract

- Env name and package path: `fly_machines` at
  `src/brr/envs/fly_machines/`.
- Package extra: `brr[fly]`.
- Registry: built into the env lookup when the extra is installed; without the
  extra, selecting `fly_machines` should fail with a clear "install brr[fly]"
  message.
- Runtime target: a Fly Machine running the brr runner image.
- Warm-image cold start target: under 1 second.
- Typical cost target: under $0.01 for a 5-minute run on `shared-cpu-1x` /
  256 MB.
- Durability: the env obeys the same salvage rule as local isolated envs.
  Destroy the machine on clean success; preserve it on `error` / `conflict` and
  record the machine ID in `ctx.env_state` / task metadata for inspection.

The implementation should not change daemon core shape beyond optional-dep
registration, docs, and the env lookup surface. Without `brr[fly]`, the Fly SDK
or REST client stays off the dependency graph.

## Implementation Spine

1. **Runner image.** Split or extend the bundled Docker build so a
   Fly-reachable runner image exists, shared with
   [`plan-daemon-deployment-templates.md`](plan-daemon-deployment-templates.md).
   Publish it to Docker Hub or `registry.fly.io`.
2. **Prepare.** `FlyMachineEnv.prepare` creates a machine through Fly's Machines
   API with `auto_destroy: true`, the configured image, credentials from the
   selected token source, and repo checkout/bootstrap instructions. Persist the
   machine ID in `ctx.env_state["machine_id"]`.
3. **Invoke.** Run the runner command in the machine, likely through SSH over
   WireGuard first, and stream stdout/stderr back through the existing
   `runner.invoke_runner` trace path. `/exec` remains a viable narrower path if
   it proves sufficient.
4. **Finalize.** Push the branch from inside the machine through the configured
   git remote. Capture the response from invoke stdout; do not add a separate
   response-fetch step unless implementation proves it necessary. Delete the
   machine on `status=done`; preserve it on `error` or `conflict`.
5. **Tests and docs.** Unit-test the Fly REST interactions with mocks, gate one
   integration test on `FLY_API_TOKEN`, add
   `src/brr/envs/fly_machines/README.md`, and cross-link
   `src/brr/docs/envs.md`.

Expected implementation size remains roughly 300-400 LOC of env code, 150 LOC
of tests, 100 LOC of env README, plus docs/optional-dependency plumbing and the
shared image-publish workflow.

## Configuration Surface

The accepted config key follows the package/env name:

```ini
[env.fly_machines]
api_token_env = FLY_API_TOKEN
app_name = my-brr-runners
region = ord
image = brr/runner:latest
machine_size = shared-cpu-1x
```

Earlier sketches used `fly-machine`; the current spelling is
`fly_machines`, matching the package path and the rest of the env kb.

## Deferred Choices

- **Fly app ownership.** Ship a template path first: users can `flyctl launch`
  once and point brr at that app. Allow an existing app override for advanced
  users.
- **SSH vs `/exec`.** SSH is the preferred first path because it matches the
  future `ssh` env shape and handles long-running interactive behavior. `/exec`
  can handle narrow commands such as destroy/final cleanup if it is reliable.
- **Subscription-auth tarballs.** Pattern A option 2 from
  [`research-cloud-envs.md`](research-cloud-envs.md) is deferred. Repo checkout
  via token-backed git is the first ship; tarball-via-secret can land when a
  real user or managed-compute constraint needs it.

## Out Of Scope

- Modal, Daytona, E2B, and Codespaces envs. Each is a separate env, separate
  extra, and separate plan.
- Generic cloud-init bootstrap for vanilla VMs; that belongs to the future
  `ssh` env.
- Runtime user choice among cloud platforms in brnrd managed compute. brnrd
  operator policy chooses the managed pool; subscribers can BYO credentials for
  supported envs.

## Read Next

1. [`research-cloud-envs.md`](research-cloud-envs.md) for Fly Machines platform
   analysis, caller axis, and the credential/repo-transfer patterns.
2. [`design-env-interface.md`](design-env-interface.md) for the env protocol and
   registry surface.
3. [`subject-managed-mode.md`](subject-managed-mode.md) for the managed-compute
   caller and BYO-vs-managed distinction.
4. [`decision-monorepo-structure.md`](decision-monorepo-structure.md) for the
   first-party env location and `brr[fly]` extra.

## Lineage

- 2026-05-22 - Drafted as part of the managed-mode kb shape rollout, then
  framed as a separate `brr-env-fly-machines` PyPI plugin package.
- 2026-05-25 - Reframed into the monorepo: `src/brr/envs/fly_machines/`, gated
  by `brr[fly]`, with the same env class called by local daemon and brnrd
  server-side paths.
- 2026-06-29 - Compressed from accepted-plan scaffolding into current-state
  synthesis and reconciled the env key to `fly_machines`; no implementation
  shipped.
