#!/usr/bin/env node
// `npx brnrd` — a bootstrapping installer, not a port and not an ephemeral run.
//
// Why this exists: brnrd's audience is people who already run AI coding tools,
// and those ship through npm. They have Node. They very often do NOT have `uv`
// or `pipx` — so a launcher that hands off to `uvx` and shrugs otherwise is
// useless to exactly the person it was written for. (uv is not installable from
// npm either; there is no official @astral-sh/uv package.)
//
// So this does the honest thing: it creates a real, durable brnrd install in a
// managed virtualenv and execs it. First run installs; every run after that is
// a spawn. Because the venv is durable rather than throwaway, `brnrd daemon
// install` works from here too — a service unit points at a directory that will
// still exist tomorrow.
//
// It never downloads a Python and never pipes a script into a shell. Python is a
// hard requirement of a Python program; if it isn't there, we say so plainly.
// `uv`, when present, is used purely as an accelerator — same venv, same result,
// less waiting.

const { spawnSync } = require("node:child_process");
const { existsSync, mkdirSync } = require("node:fs");
const { homedir, platform } = require("node:os");
const { join } = require("node:path");

const PYPI = "brnrd";
const VERSION = require("../package.json").version; // launcher and payload move together
const WINDOWS = platform() === "win32";

// Durable, XDG-ish, and *not* inside node_modules — npx's cache is disposable
// and a daemon service must not be pointed at it.
const HOME = process.env.BRNRD_HOME
  || join(process.env.XDG_DATA_HOME || join(homedir(), ".local", "share"), "brnrd");
const VENV = join(HOME, "venv");
const BIN = join(VENV, WINDOWS ? "Scripts" : "bin");
const EXE = join(BIN, WINDOWS ? "brnrd.exe" : "brnrd");
const PY = join(BIN, WINDOWS ? "python.exe" : "python");

const run = (cmd, args, opts = {}) =>
  spawnSync(cmd, args, { stdio: "inherit", ...opts });

const quiet = (cmd, args) =>
  spawnSync(cmd, args, { stdio: ["ignore", "pipe", "ignore"], encoding: "utf8" });

const found = (cmd) => quiet(WINDOWS ? "where" : "which", [cmd]).status === 0;

function systemPython() {
  for (const [cmd, args] of [["python3", []], ["python", []], ["py", ["-3"]]]) {
    if (!found(cmd)) continue;
    const probe = quiet(cmd, [...args, "-c", "import sys;print(sys.version_info[:2])"]);
    if (probe.status === 0) return [cmd, args];
  }
  return null;
}

function installed() {
  if (!existsSync(EXE)) return null;
  const probe = quiet(EXE, ["--version"]);
  return probe.status === 0 ? probe.stdout.trim() : null;
}

function bootstrap() {
  mkdirSync(HOME, { recursive: true });
  const uv = found("uv");

  if (!existsSync(PY)) {
    console.error(`brnrd: first run — creating ${VENV}`);
    if (uv) {
      // uv can fetch a managed CPython, so this path works even with no system
      // Python at all. It is an accelerator, never a requirement.
      if (run("uv", ["venv", VENV]).status !== 0) return false;
    } else {
      const py = systemPython();
      if (!py) {
        console.error(
          [
            "",
            "brnrd is a Python program, and no Python was found on this machine.",
            "",
            "  macOS:   brew install python",
            "  Debian:  sudo apt install python3 python3-venv",
            "  or:      https://www.python.org/downloads/",
            "",
            "Then run `npx brnrd` again — or skip this launcher entirely:",
            "",
            "  pip install brnrd",
            "",
          ].join("\n")
        );
        return false;
      }
      const [cmd, args] = py;
      if (run(cmd, [...args, "-m", "venv", VENV]).status !== 0) {
        console.error(
          "\nbrnrd: `python -m venv` failed. On Debian/Ubuntu the venv module is a\n"
            + "separate package: sudo apt install python3-venv\n"
        );
        return false;
      }
    }
  }

  console.error(`brnrd: installing ${PYPI}==${VERSION}`);
  const spec = `${PYPI}==${VERSION}`;
  const install = uv
    ? run("uv", ["pip", "install", "--python", PY, "--quiet", spec])
    : run(PY, ["-m", "pip", "install", "--quiet", "--upgrade", spec]);
  if (install.status !== 0) {
    console.error(`\nbrnrd: could not install ${spec}. Try: pip install brnrd\n`);
    return false;
  }
  return true;
}

const args = process.argv.slice(2);
const current = installed();

// The launcher's version is the contract: `npx brnrd@0.1.0` must give you
// brnrd 0.1.0, not whatever happens to be newest on PyPI.
if (!current || !current.includes(VERSION)) {
  if (!bootstrap()) process.exit(1);
}

const result = run(EXE, args);
process.exit(result.status === null ? 1 : result.status);
