#!/bin/sh
# One install: an editable checkout, with an independently prepared interpreter.
# --dev and BRNRD_INSTALL_DEV remain accepted aliases. Reruns never move HEAD.
# BRNRD_REF=vX.Y.Z clones that release (the npm launcher passes its own version) and sets
# git config brnrd.update stable in the new checkout, so the loom follows releases; default: main.
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
REF=${BRNRD_REF:-main}
release=0
case "$REF" in
  main) : ;;
  v[0-9]*.[0-9]*.[0-9]*) case "${REF#v}" in *[!0-9.]*) release=bad ;; *) release=1 ;; esac ;;
  *) release=bad ;;
esac
case "$release" in
  bad) printf 'BRNRD_REF=%s is neither main nor a release tag vX.Y.Z.\n' "$REF" >&2; exit 1 ;;
esac
candidate=''
# A failed run leaves no environment behind, unless current already names it.
drop_candidate() {
  [ "$dry" != 1 ] && [ -n "$candidate" ] && [ -d "$candidate" ] || return 0
  case "$candidate" in "${ROOT}/envs/install-"*) : ;; *) return 0 ;; esac
  if [ -e "${ROOT}/current" ] && [ "${ROOT}/current" -ef "$candidate" ]; then return 0; fi
  rm -rf "$candidate"
}
fail() { printf '%s\n' "$1" >&2; drop_candidate; exit 1; }
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
# The npm launcher that ships install.sh runs this installer and then the shim, so it is no collision;
# an older one installs its own copy of brnrd from PyPI beside this one.
npmshim=''
if command -v npm >/dev/null 2>&1; then
  npmroot=$(npm root -g </dev/null) || fail 'Cannot check npm installs; run npm root -g and retry.'
  if [ -e "${npmroot}/brnrd/package.json" ]; then
    [ -e "${npmroot}/brnrd/install.sh" ] ||
      fail 'npm brnrd collides with this install. Run: npm uninstall -g brnrd; then rerun this installer.'
    if prefix=$(npm prefix -g </dev/null 2>/dev/null); then npmshim="${prefix}/bin/brnrd"; fi
  fi
fi
# A machine already running from another checkout keeps it: refuse before any write.
real_dir() { (cd "$1" 2>/dev/null && pwd -P) || printf '%s' "$1"; }
running=''
if [ -f "${ROOT}/launcher-source" ]; then
  running=$(sed -n 's/.*"source": *"\([^"]*\)".*/\1/p' "${ROOT}/launcher-source")
fi
if { [ -z "$running" ] || [ "$(real_dir "$running")" = "$(real_dir "$DEV_DIR")" ]; } && [ -L "${ROOT}/current" ]; then
  target=$(real_dir "${ROOT}/current")
  case "${target}/" in
    "$(real_dir "${ROOT}/envs")"/*) : ;;
    *)
      if [ -x "${target}/bin/python" ]; then
        imported=$("${target}/bin/python" -I -c 'import brr, os; print(os.path.dirname(os.path.dirname(os.path.dirname(os.path.realpath(brr.__file__)))))' </dev/null 2>/dev/null) || imported=''
        if [ -n "$imported" ] && [ -e "${imported}/.git" ]; then running=$imported; fi
      fi
      ;;
  esac
fi
if [ -n "$running" ] && [ "$(real_dir "$running")" != "$(real_dir "$DEV_DIR")" ]; then
  fail "brnrd already runs from the checkout ${running}. This installer would clone ${DEV_DIR} and move the loom onto it, leaving that checkout and its commits behind; nothing was changed. To keep it, rerun with BRNRD_DEV_DIR=${running}"
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
  # A loom started by hand from another install keeps running beside the migrated service.
  me=$(id -u 2>/dev/null) || return 0
  procs=$(ps -U "$me" -o pid=,args= 2>/dev/null) || return 0
  printf '%s\n' "$procs" | grep 'brr\.loom\.runtime loom' | grep -vF "${ROOT}/" | while read -r pid args; do
    warn "a loom from another install is running (pid ${pid}: ${args%% *}); the installer moves the loom service, but a loom started by hand keeps running beside it: stop it with that install's brnrd loom stop, or kill ${pid}."
  done
}


preflight
if ! command -v uv >/dev/null 2>&1; then bootstrap_uv; fi
uvbin=$(command -v uv || printf uv)
if [ ! -e "$DEV_DIR" ]; then
  run_named "git clone --single-branch --branch ${REF} ${REPO} ${DEV_DIR}" git clone --single-branch --branch "$REF" "$REPO" "$DEV_DIR"
  if [ "$release" = 1 ]; then
    # A tag clone is a detached HEAD; the resident's changes want a branch. The loom follows releases from here.
    run_named "git -C ${DEV_DIR} checkout -b stable" git -C "$DEV_DIR" checkout -q -b stable
    run_named "git -C ${DEV_DIR} config brnrd.update stable" git -C "$DEV_DIR" config brnrd.update stable
    # A single-branch tag clone has no origin/main; the board and activation find upstream by it.
    run_named "git -C ${DEV_DIR} fetch origin main" git -C "$DEV_DIR" fetch -q origin +refs/heads/main:refs/remotes/origin/main
  fi
else
  show "Keeping checkout HEAD and branch in ${DEV_DIR}"
fi
if [ "$dry" = 1 ]; then
  candidate="${ROOT}/envs/new-install"
else
  mkdir -p "${ROOT}/envs"
  candidate=$(mktemp -d "${ROOT}/envs/install-XXXXXXXX")
fi
# brnrd needs Python >= 3.10 (pyproject's requires-python); without the bound uv takes the
# first python3 on PATH, which on a stock Mac is Xcode's 3.9, and the install below fails.
run_named "uv venv ${candidate} --python >=3.10" "$uvbin" venv "$candidate" --python '>=3.10'
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
# The service now runs the checkout; the person's shell must too, or it is service new, CLI old.
if [ "$dry" = 1 ]; then
  show 'check which brnrd a fresh login shell runs'
else
  login=${SHELL:-/bin/sh}
  found=$(env -i HOME="$HOME" USER="${USER:-}" LOGNAME="${LOGNAME:-}" SHELL="$login" "$login" -lc 'command -v brnrd' </dev/null 2>/dev/null) || found=''
  found=$(printf '%s\n' "$found" | sed -n '$p')
  if [ -n "$found" ] && ! [ "$found" -ef "${ROOT}/bin/brnrd" ] && ! { [ -n "$npmshim" ] && [ "$found" -ef "$npmshim" ]; }; then
    show "warning: a new login shell runs ${found}, not ${ROOT}/bin/brnrd: the loom service runs this checkout, but brnrd typed in a shell would run that older install."
    if "$uvbin" tool list </dev/null 2>/dev/null | grep -E '^brnrd([[:space:]]|$)' >/dev/null; then
      show "It is the packaged uv tool install. Remove it with: uv tool uninstall brnrd"
    else
      show "Remove ${found}, or put ${ROOT}/bin first on PATH in your shell profile."
    fi
  fi
fi
