#!/usr/bin/env node
// The brnrd launcher: npm's way to the one install. brnrd has a single install kind, a git checkout the
// resident can change (docs/public/install.sh). This package carries a copy of that installer (prepack
// copies it in), runs it once, pinned to this package's own release tag, and from then on hands every
// call to the shim it made:
//
//   npm install -g brnrd   → a `brnrd` command on PATH, this file behind it
//   npx brnrd <command>    → the same install; the shim it leaves is `~/.brnrd/bin/brnrd`
//
// BRNRD_REF=v<version> makes the installer clone that release on a branch `stable` and set
// `git config brnrd.update stable`, so the loom follows releases, not main. Nothing is piped from the
// network: the installer is the file in this package.

const { spawnSync } = require("node:child_process");
const { existsSync, mkdirSync, writeFileSync } = require("node:fs");
const { homedir, platform } = require("node:os");
const { join } = require("node:path");

const VERSION = require("../package.json").version;
const INSTALLER = join(__dirname, "..", "install.sh");
const ROOT = process.env.BRNRD_ROOT || join(homedir(), ".brnrd");
const SHIM = join(ROOT, "bin", "brnrd");
// What launchers up to 0.7.x built: a venv with brnrd from PyPI. Left alone; said once.
const OLD_VENV = join(
  process.env.BRNRD_HOME || join(process.env.XDG_DATA_HOME || join(homedir(), ".local", "share"), "brnrd"),
  "venv",
);
const OLD_TOLD = join(ROOT, ".npm-old-venv-told");

// npx installs into a disposable `_npx/<hash>/` directory; a global install does not. The path, not
// an environment variable a parent shell may have leaked (#1084).
const VIA_NPX = __dirname.split(/[\\/]/).includes("_npx");

// ^C belongs to the child: both share the foreground process group, so a no-op handler keeps node
// alive through the child's own handling, and the child's exit status still decides ours.
process.on("SIGINT", () => {});

const run = (cmd, args, env = process.env) => spawnSync(cmd, args, { stdio: "inherit", env });

function main() {
  if (platform() === "win32") {
    console.error(
      "brnrd runs on macOS and Linux; on Windows, run it inside WSL:\n"
        + "  wsl --install, then in the WSL shell: npm install -g brnrd",
    );
    return 1;
  }
  if (!existsSync(SHIM)) {
    console.error(`brnrd: first run, installing v${VERSION} into ${ROOT} (a git checkout you can change)`);
    const install = run("sh", [INSTALLER], { ...process.env, BRNRD_REF: `v${VERSION}` });
    if (install.status !== 0) return install.status === null ? 1 : install.status;
    if (!existsSync(SHIM)) {
      console.error(`brnrd: the installer finished but ${SHIM} is missing; rerun it: sh ${INSTALLER}`);
      return 1;
    }
  }
  if (existsSync(OLD_VENV) && !existsSync(OLD_TOLD)) {
    console.error(`brnrd: ${OLD_VENV} is an older launcher's install and is no longer used; remove it when you like.`);
    try {
      mkdirSync(ROOT, { recursive: true });
      writeFileSync(OLD_TOLD, `${OLD_VENV}\n`);
    } catch {
      // Said again next time; never a reason to fail the command.
    }
  }
  // brnrd prints its own next steps; under npx it spells them `npx brnrd …`.
  const result = run(SHIM, process.argv.slice(2), VIA_NPX ? { ...process.env, BRNRD_LAUNCHER: "npx" } : process.env);
  return result.status === null ? 1 : result.status;
}

process.exit(main());
