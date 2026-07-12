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
// A working system Python remains the fastest first-run path. When neither
// Python nor uv exists, the launcher downloads a checksum-pinned uv release and
// lets uv provision CPython. Both stay under BRNRD_HOME; no shell installer,
// system mutation, or PATH change is involved.

const { spawnSync } = require("node:child_process");
const { createHash } = require("node:crypto");
const {
  chmodSync,
  createWriteStream,
  existsSync,
  mkdirSync,
  readFileSync,
  renameSync,
  rmSync,
  writeFileSync,
} = require("node:fs");
const https = require("node:https");
const { arch, homedir, platform } = require("node:os");
const { basename, join } = require("node:path");
const { pipeline } = require("node:stream/promises");
const { Transform } = require("node:stream");
const { gunzipSync, inflateRawSync } = require("node:zlib");

const PYPI = "brnrd";
const VERSION = require("../package.json").version; // launcher and payload move together
const UV_RELEASE = require("../uv-assets.json");
const WINDOWS = platform() === "win32";

// Durable, XDG-ish, and *not* inside node_modules — npx's cache is disposable
// and a daemon service must not be pointed at it.
const HOME = process.env.BRNRD_HOME
  || join(process.env.XDG_DATA_HOME || join(homedir(), ".local", "share"), "brnrd");
const VENV = join(HOME, "venv");
const BIN = join(VENV, WINDOWS ? "Scripts" : "bin");
const EXE = join(BIN, WINDOWS ? "brnrd.exe" : "brnrd");
const PY = join(BIN, WINDOWS ? "python.exe" : "python");
const UV_DIR = join(HOME, "uv", UV_RELEASE.version);
const UV_EXE = join(UV_DIR, WINDOWS ? "uv.exe" : "uv");
const UV_PYTHONS = join(HOME, "python");
const CACHE = join(HOME, "cache");
const CHILD_ENV = {
  ...process.env,
  PIP_CACHE_DIR: join(CACHE, "pip"),
  UV_CACHE_DIR: join(CACHE, "uv"),
  UV_PYTHON_INSTALL_DIR: UV_PYTHONS,
};

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

function missingPython() {
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

function assetForHost() {
  let key = `${platform()}-${arch()}`;
  if (platform() === "linux") {
    const glibc = process.report?.getReport?.().header?.glibcVersionRuntime;
    key += glibc ? "-gnu" : "-musl";
  }
  return UV_RELEASE.assets[key] || null;
}

function download(url, destination, expectedHash, redirects = 0) {
  return new Promise((resolve, reject) => {
    const request = https.get(url, { headers: { "User-Agent": "brnrd-npm-launcher" } }, (response) => {
      if (response.statusCode >= 300 && response.statusCode < 400 && response.headers.location) {
        response.resume();
        if (redirects >= 5) return reject(new Error("too many redirects"));
        return resolve(download(response.headers.location, destination, expectedHash, redirects + 1));
      }
      if (response.statusCode !== 200) {
        response.resume();
        return reject(new Error(`HTTP ${response.statusCode}`));
      }

      const hash = createHash("sha256");
      const hashingStream = new Transform({
        transform(chunk, _encoding, callback) {
          hash.update(chunk);
          callback(null, chunk);
        },
      });
      pipeline(response, hashingStream, createWriteStream(destination, { flags: "wx" }))
        .then(() => {
          const actual = hash.digest("hex");
          if (actual !== expectedHash) {
            reject(new Error(`SHA256 mismatch (expected ${expectedHash}, got ${actual})`));
          } else {
            resolve();
          }
        })
        .catch(reject);
    });
    request.setTimeout(30000, () => request.destroy(new Error("download timed out")));
    request.on("error", reject);
  });
}

function extractTarGz(archive, destination) {
  const tar = gunzipSync(readFileSync(archive));
  for (let offset = 0; offset + 512 <= tar.length;) {
    const header = tar.subarray(offset, offset + 512);
    if (header.every((byte) => byte === 0)) break;
    const name = header.subarray(0, 100).toString().replace(/\0.*$/, "");
    const size = parseInt(header.subarray(124, 136).toString().replace(/\0.*$/, "").trim(), 8) || 0;
    const start = offset + 512;
    if (basename(name) === "uv") {
      writeFileSync(destination, tar.subarray(start, start + size), { flag: "wx", mode: 0o755 });
      return;
    }
    offset = start + Math.ceil(size / 512) * 512;
  }
  throw new Error("uv executable missing from release archive");
}

function extractZip(archive, destination) {
  const zip = readFileSync(archive);
  for (let offset = 0; offset + 46 <= zip.length;) {
    const signature = zip.readUInt32LE(offset);
    if (signature !== 0x02014b50) {
      offset += 1;
      continue;
    }
    const method = zip.readUInt16LE(offset + 10);
    const compressedSize = zip.readUInt32LE(offset + 20);
    const nameLength = zip.readUInt16LE(offset + 28);
    const extraLength = zip.readUInt16LE(offset + 30);
    const commentLength = zip.readUInt16LE(offset + 32);
    const localOffset = zip.readUInt32LE(offset + 42);
    const name = zip.subarray(offset + 46, offset + 46 + nameLength).toString();
    if (basename(name) === "uv.exe") {
      if (zip.readUInt32LE(localOffset) !== 0x04034b50) throw new Error("invalid uv zip archive");
      const localNameLength = zip.readUInt16LE(localOffset + 26);
      const localExtraLength = zip.readUInt16LE(localOffset + 28);
      const start = localOffset + 30 + localNameLength + localExtraLength;
      const compressed = zip.subarray(start, start + compressedSize);
      const executable = method === 0 ? compressed : method === 8 ? inflateRawSync(compressed) : null;
      if (!executable) throw new Error(`unsupported zip compression method ${method}`);
      writeFileSync(destination, executable, { flag: "wx" });
      return;
    }
    offset += 46 + nameLength + extraLength + commentLength;
  }
  throw new Error("uv executable missing from release archive");
}

async function managedUv() {
  if (existsSync(UV_EXE)) return UV_EXE; // verified before its atomic rename

  const asset = assetForHost();
  if (!asset) throw new Error(`no pinned uv build for ${platform()}/${arch()}`);
  mkdirSync(UV_DIR, { recursive: true });
  const suffix = `${process.pid}-${Date.now()}`;
  const archive = join(UV_DIR, `.download-${suffix}`);
  const executable = join(UV_DIR, `.uv-${suffix}`);
  const url = `https://github.com/astral-sh/uv/releases/download/${UV_RELEASE.version}/${asset.archive}`;
  console.error(`brnrd: downloading uv ${UV_RELEASE.version} (${Math.ceil(asset.size / 1024 / 1024)} MB)`);
  try {
    await download(url, archive, asset.sha256);
    if (asset.archive.endsWith(".zip")) extractZip(archive, executable);
    else extractTarGz(archive, executable);
    if (!WINDOWS) chmodSync(executable, 0o755);
    renameSync(executable, UV_EXE);
    return UV_EXE;
  } finally {
    rmSync(archive, { force: true });
    rmSync(executable, { force: true });
  }
}

async function bootstrap() {
  mkdirSync(HOME, { recursive: true });
  let uv = found("uv") ? "uv" : null;

  if (!existsSync(PY)) {
    console.error(`brnrd: first run — creating ${VENV}`);
    const py = systemPython();
    if (py) {
      const [cmd, args] = py;
      if (run(cmd, [...args, "-m", "venv", VENV], { env: CHILD_ENV }).status !== 0) {
        console.error(
          "\nbrnrd: `python -m venv` failed. On Debian/Ubuntu the venv module is a\n"
            + "separate package: sudo apt install python3-venv\n"
        );
        return false;
      }
    } else {
      try {
        uv = uv || await managedUv();
      } catch (error) {
        console.error(`brnrd: could not download a verified uv: ${error.message}`);
        return missingPython();
      }
      console.error(`brnrd: provisioning Python ${UV_RELEASE.python}`);
      const provision = run(
        uv,
        [
          "python", "install", "--install-dir", UV_PYTHONS,
          "--no-bin", "--no-registry", "--no-config", UV_RELEASE.python,
        ],
        { env: CHILD_ENV }
      );
      if (provision.status !== 0) return missingPython();
      const create = run(
        uv,
        ["venv", "--python", UV_RELEASE.python, "--managed-python", "--no-project", "--no-config", VENV],
        { env: CHILD_ENV }
      );
      if (create.status !== 0) return missingPython();
    }
  }

  console.error(`brnrd: installing ${PYPI}==${VERSION}`);
  const spec = `${PYPI}==${VERSION}`;
  const install = uv
    ? run(uv, ["pip", "install", "--python", PY, "--quiet", "--no-config", spec], { env: CHILD_ENV })
    : run(PY, ["-m", "pip", "install", "--quiet", "--upgrade", spec], { env: CHILD_ENV });
  if (install.status !== 0) {
    console.error(`\nbrnrd: could not install ${spec}. Try: pip install brnrd\n`);
    return false;
  }
  return true;
}

async function main() {
  const args = process.argv.slice(2);
  const current = installed();

  // The launcher's version is the contract: `npx brnrd@0.1.0` must give you
  // brnrd 0.1.0, not whatever happens to be newest on PyPI.
  if (!current || !current.includes(VERSION)) {
    if (!await bootstrap()) process.exit(1);
  }

  const result = run(EXE, args);
  process.exit(result.status === null ? 1 : result.status);
}

main().catch((error) => {
  console.error(`brnrd: bootstrap failed: ${error.message}`);
  process.exit(1);
});
