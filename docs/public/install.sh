#!/bin/sh
# One install: an editable checkout, with an independently prepared interpreter.
# --dev and BRNRD_INSTALL_DEV remain accepted aliases. Reruns never move HEAD.
set -eu
dry=${BRNRD_INSTALL_DRY_RUN:-0}
for arg in "$@"; do
  case "$arg" in
    --dev) : ;;
    *) printf 'Unknown option %s; the only compatibility option is --dev.\n' "$arg" >&2; exit 1 ;;
  esac
done
REPO=https://github.com/hugimuni-labs/brnrd
ROOT=${BRNRD_ROOT:-${HOME}/.brnrd}
DEV_DIR=${BRNRD_DEV_DIR:-${ROOT}/src}
fail() { printf '%s\n' "$1" >&2; exit 1; }
show() { printf '%s\n' "$1"; }
warn() { printf 'warning: %s\n' "$1" >&2; }
run_named() {
  name=$1
  shift
  if [ "$dry" = 1 ]; then show "$name"; return 0; fi
  "$@" </dev/null || fail "Failed: ${name}. The previous interpreter is retained."
}

# All collision and checkout checks precede bootstrap, clone or environment writes.
if command -v pipx >/dev/null 2>&1; then
  packages=$(pipx list --short </dev/null) || fail 'Cannot check pipx installs; run pipx list --short and retry.'
  if printf '%s\n' "$packages" | grep -E '^brnrd([[:space:]]|$)' >/dev/null; then
    fail 'pipx brnrd collides with this install. Run: pipx uninstall brnrd; then rerun this installer.'
  fi
fi
if command -v npm >/dev/null 2>&1; then
  npmroot=$(npm root -g </dev/null) || fail 'Cannot check npm installs; run npm root -g and retry.'
  if [ -e "${npmroot}/brnrd/package.json" ]; then
    fail 'npm brnrd collides with this install. Run: npm uninstall -g brnrd; then rerun this installer.'
  fi
fi
git --version >/dev/null 2>&1 || fail 'git is missing (on macOS: xcode-select --install); install it and retry.'
if [ -e "$DEV_DIR" ] || [ -L "$DEV_DIR" ]; then
  [ -e "${DEV_DIR}/.git" ] || fail "${DEV_DIR} is not a git checkout; move it aside or set BRNRD_DEV_DIR."
  origin=$(git -C "$DEV_DIR" remote get-url origin) || fail "Cannot read origin in ${DEV_DIR}."
  case "$origin" in
    https://github.com/hugimuni-labs/brnrd|https://github.com/hugimuni-labs/brnrd.git|git@github.com:hugimuni-labs/brnrd.git) : ;;
    *) fail "${DEV_DIR} has a foreign origin; not touched. Set BRNRD_DEV_DIR to an upstream checkout." ;;
  esac
  git -C "$DEV_DIR" rev-parse --verify HEAD >/dev/null || fail "${DEV_DIR} has no HEAD; not touched."
fi
bootstrap_uv() {
  if [ "$dry" -eq 1 ]; then
    show "curl -LsSf https://astral.sh/uv/install.sh | sh"
    return 0
  fi
  tmp=$(mktemp) || fail "Could not create a temporary file; run curl -LsSf https://astral.sh/uv/install.sh | sh by hand."
  trap 'rm -f "$tmp"' EXIT
  if ! curl -LsSf -o "$tmp" https://astral.sh/uv/install.sh; then
    fail "Downloading the uv installer failed; run curl -LsSf https://astral.sh/uv/install.sh | sh by hand."
  fi
  if ! sh "$tmp"; then
    fail "The uv installer failed; run curl -LsSf https://astral.sh/uv/install.sh | sh by hand."
  fi
  rm -f "$tmp"
  trap - EXIT
  if command -v uv >/dev/null 2>&1; then
    return 0
  fi
  for dir in "${HOME}/.local/bin" "${HOME}/.cargo/bin"; do
    if [ -x "${dir}/uv" ]; then
      PATH="${dir}:$PATH"
      export PATH
      return 0
    fi
  done
  fail "The uv installer finished but uv is not on PATH; add \$HOME/.local/bin to PATH and rerun this installer."
}

preflight() {
  if [ -n "${PYTHONPATH:-}" ]; then
    warn "PYTHONPATH is set (${PYTHONPATH}), so brnrd may run another checkout's code instead of this install; unset it here and in your shell profile."
  fi
  for unit in "${HOME}/Library/LaunchAgents/dev.brnrd.brr.plist" "${XDG_CONFIG_HOME:-${HOME}/.config}/systemd/user/brr.service"; do
    if [ -e "$unit" ]; then
      warn "an old brnrd daemon is installed (${unit}) and runs beside the loom; remove it with brnrd daemon uninstall."
    fi
  done
  if ! command -v claude >/dev/null 2>&1 && ! command -v codex >/dev/null 2>&1; then
    warn "neither claude nor codex is on PATH, and loom install picks the loom's body from them; install and sign in to one first."
  fi
  if ! command -v gh >/dev/null 2>&1; then
    warn "gh is not installed, and following main reads CI through it; install the GitHub CLI and run gh auth login."
  elif ! gh auth status >/dev/null 2>&1; then
    warn "gh auth status fails, and following main reads CI through it; run gh auth login."
  fi
}


preflight
if ! command -v uv >/dev/null 2>&1; then bootstrap_uv; fi
uvbin=$(command -v uv || printf uv)
if [ ! -e "$DEV_DIR" ]; then
  run_named "git clone --single-branch --branch main ${REPO} ${DEV_DIR}" git clone --single-branch --branch main "$REPO" "$DEV_DIR"
else
  show "Keeping checkout HEAD and branch in ${DEV_DIR}"
fi
if [ "$dry" = 1 ]; then
  candidate="${ROOT}/envs/new-install"
else
  mkdir -p "${ROOT}/envs"
  candidate=$(mktemp -d "${ROOT}/envs/install-XXXXXXXX")
fi
run_named "uv venv ${candidate}" "$uvbin" venv "$candidate"
run_named "uv pip install --python ${candidate}/bin/python --editable ${DEV_DIR}" "$uvbin" pip install --python "${candidate}/bin/python" --editable "$DEV_DIR"
# -I excludes a shell/body's PYTHONPATH and user site. Never dispatch via PATH.
run_named "${candidate}/bin/python -I -m brr.loom.runtime.install_checkout ${DEV_DIR}" "${candidate}/bin/python" -I -m brr.loom.runtime.install_checkout "$DEV_DIR"
if [ "$dry" = 1 ]; then
  show 'uv tool update-shell (shim PATH)'
else
  UV_TOOL_BIN_DIR="${ROOT}/bin" "$uvbin" tool update-shell </dev/null || warn 'Shell profile not changed; add the PATH line below if needed.'
fi
show "Add to your shell profile: export PATH=\"${ROOT}/bin:\$PATH\""
show "Next: ${ROOT}/bin/brnrd setup"
