#!/usr/bin/env node
// `npx brnrd` — a launcher, not a port.
//
// brnrd is a Python tool published on PyPI. This package exists so that the
// npx habit lands somewhere honest instead of on a 404 or a squatter: it hands
// off to a Python runner and otherwise says exactly what to install. It never
// downloads a Python, never curl|sh's a toolchain, and never pretends to be a
// Node implementation of brnrd.
//
// Preference order is deliberate: `uvx` (fastest, ephemeral, no state) →
// `pipx run` (same shape, wider install base) → instructions. A daemon service
// must NOT be installed from an ephemeral environment — `daemon install` would
// point a systemd/LaunchAgent unit at a directory that gets garbage-collected —
// so that one subcommand is refused with the real install line instead.

const { spawnSync } = require("node:child_process");

const args = process.argv.slice(2);
const PYPI = "brnrd";

function has(cmd) {
  const probe = process.platform === "win32" ? "where" : "which";
  return spawnSync(probe, [cmd], { stdio: "ignore" }).status === 0;
}

function tell(lines) {
  for (const line of lines) console.error(line);
}

// An ephemeral env cannot host a long-lived service.
if (args[0] === "daemon" && (args[1] === "install" || args[1] === "uninstall")) {
  tell([
    "brnrd: `daemon install` needs a real installation, not an ephemeral one.",
    "",
    "  pip install brnrd && brnrd daemon install",
    "",
    "(npx/uvx run brnrd from a throwaway environment — a service unit pointing",
    "at one would break the moment it is cleaned up.)",
  ]);
  process.exit(1);
}

const runner = has("uvx")
  ? ["uvx", [PYPI, ...args]]
  : has("pipx")
    ? ["pipx", ["run", PYPI, ...args]]
    : null;

if (!runner) {
  tell([
    "brnrd is a Python tool. This npx launcher needs a Python runner to hand off to.",
    "",
    "  pip install brnrd        # the normal install",
    "  uvx brnrd                # zero-install run (https://docs.astral.sh/uv/)",
    "",
    "Install either `uv` or `pipx`, or just use pip — all three end up in the",
    "same place.",
  ]);
  process.exit(1);
}

const [cmd, cmdArgs] = runner;
const result = spawnSync(cmd, cmdArgs, { stdio: "inherit" });
process.exit(result.status === null ? 1 : result.status);
