#!/bin/sh
# Install the brnrd command onto PATH. POSIX sh: macOS /bin/sh and Debian dash.
# Safe to run twice. BRNRD_INSTALL_DRY_RUN=1 prints the commands and does not
# run them. Probes still run, so a second run prints upgrade rather than install.
# --dev (or BRNRD_INSTALL_DEV=1) installs a checkout of main that the loom
# follows instead of the PyPI release: sh -s -- --dev when piped.
# The uv bootstrap is not a pipe. POSIX sh has no pipefail, and sh exits 0
# on an empty script, which would hide a failed curl.
set -eu

dry=0
if [ "${BRNRD_INSTALL_DRY_RUN:-}" = "1" ]; then
  dry=1
fi
dev=0
if [ "${BRNRD_INSTALL_DEV:-}" = "1" ]; then
  dev=1
fi
for arg in "$@"; do
  case "$arg" in
    --dev) dev=1 ;;
    *) printf '%s\n' "Unknown option ${arg}; the only option is --dev." >&2; exit 1 ;;
  esac
done
REPO=https://github.com/hugimuni-labs/brnrd
DEV_DIR=${BRNRD_DEV_DIR:-${HOME}/.brnrd/src}

fail() {
  printf '%s\n' "$1" >&2
  exit 1
}

show() {
  printf '%s\n' "$1"
}

warn() {
  printf 'warning: %s\n' "$1" >&2
}

run_named() {
  name=$1
  shift
  if [ "$dry" -eq 1 ]; then
    show "$name"
    return 0
  fi
  # Piped, the script is this shell's stdin: a child reading it would eat the rest.
  if ! "$@" </dev/null; then
    fail "Failed to run ${name}; run it by hand."
  fi
}

has_pkg() {
  printf '%s\n' "$2" | grep -E "^$1([[:space:]]|$)" >/dev/null 2>&1
}

install_with_uv() {
  uvbin=$(command -v uv) || fail "uv was not found; run uv tool install brnrd by hand."
  list=$("$uvbin" tool list 2>/dev/null) || fail "uv tool list failed; run uv tool install brnrd by hand, or uv tool upgrade brnrd if it is already installed."
  if has_pkg brnrd "$list"; then
    run_named "uv tool upgrade brnrd" "$uvbin" tool upgrade brnrd
  else
    run_named "uv tool install brnrd" "$uvbin" tool install brnrd
  fi
  uv_path "$uvbin"
}

uv_path() {
  uvbin=$1
  if [ "$dry" -eq 1 ]; then
    show "uv tool update-shell"
    return 0
  fi
  bindir=$("$uvbin" tool dir --bin) || fail "uv tool dir --bin failed; run uv tool update-shell by hand."
  # A rerun finds the profile already written: uv says so and exits 1.
  if ! said=$("$uvbin" tool update-shell 2>&1); then
    case "$said" in
      *"already up-to-date"*) ;;
      *) show "Could not update the shell profile. Add this line: export PATH=\"${bindir}:\$PATH\"" ;;
    esac
  elif [ -n "$said" ]; then
    show "$said"
  fi
  case ":$PATH:" in
    *":${bindir}:"*) ;;
    *)
      PATH="${bindir}:$PATH"
      export PATH
      show "Add this line if new terminals cannot find brnrd: export PATH=\"${bindir}:\$PATH\""
      ;;
  esac
}

install_with_pipx() {
  list=$(pipx list --short 2>/dev/null) || fail "pipx list --short failed; run pipx install brnrd by hand, or pipx upgrade brnrd if it is already installed."
  if has_pkg brnrd "$list"; then
    run_named "pipx upgrade brnrd" pipx upgrade brnrd
  else
    run_named "pipx install brnrd" pipx install brnrd
  fi
  if [ "$dry" -eq 1 ]; then
    show "pipx ensurepath"
    return 0
  fi
  bindir="${HOME}/.local/bin"
  if ! pipx ensurepath; then
    show "Could not update the shell profile. Add this line: export PATH=\"${bindir}:\$PATH\""
  fi
  case ":$PATH:" in
    *":${bindir}:"*) ;;
    *)
      if [ -d "$bindir" ]; then
        PATH="${bindir}:$PATH"
        export PATH
      fi
      show "Add this line if new terminals cannot find brnrd: export PATH=\"${bindir}:\$PATH\""
      ;;
  esac
}

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
  fail "The uv installer finished but uv is not on PATH; add \$HOME/.local/bin to PATH and run uv tool install brnrd by hand."
}

show_version() {
  if [ "$dry" -eq 1 ]; then
    show "brnrd --version"
    return 0
  fi
  if ! command -v brnrd >/dev/null 2>&1; then
    fail "brnrd is not on PATH after install; run brnrd --version by hand once the tool bin directory is on PATH."
  fi
  brnrd --version || fail "brnrd --version failed; run it by hand."
}

# What --dev's editable uv tool collides with: another brnrd command on PATH, a
# pipx release in the same bin directory (uv refuses to overwrite it), and a loom
# this user runs from an interpreter other than uv's brnrd tool.
preflight_other_installs() {
  ubin="${HOME}/.local/bin"
  utools="${HOME}/.local/share/uv/tools"
  if command -v uv >/dev/null 2>&1; then
    ubin=$(uv tool dir --bin 2>/dev/null </dev/null) || ubin="${HOME}/.local/bin"
    utools=$(uv tool dir 2>/dev/null </dev/null) || utools="${HOME}/.local/share/uv/tools"
  fi
  if command -v pipx >/dev/null 2>&1; then
    plist=$(pipx list --short 2>/dev/null </dev/null) || plist=""
    if has_pkg brnrd "$plist"; then
      warn "pipx has a brnrd release installed, and its brnrd command collides with this install; run pipx uninstall brnrd."
    fi
  fi
  found=$(command -v brnrd 2>/dev/null) || found=""
  if [ -n "$found" ] && [ "$found" != "${ubin}/brnrd" ]; then
    warn "brnrd on PATH is ${found}, not ${ubin}/brnrd, so it shadows this install; remove it or put ${ubin} first on PATH."
  fi
  me=$(id -u 2>/dev/null) || return 0
  procs=$(ps -U "$me" -o pid=,args= 2>/dev/null) || return 0
  printf '%s\n' "$procs" | grep 'brr\.loom\.runtime loom' | grep -vF "${utools}/brnrd/" | while read -r pid args; do
    warn "a loom from another install is running (pid ${pid}: ${args%% *}); loom install --dev replaces the loom service, but a loom started by hand keeps running beside it: stop it with that install's brnrd loom stop, or kill ${pid}."
  done
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
  if [ "$dev" -eq 0 ]; then
    return 0
  fi
  preflight_other_installs
  if ! command -v claude >/dev/null 2>&1 && ! command -v codex >/dev/null 2>&1; then
    warn "neither claude nor codex is on PATH, and loom install picks the loom's body from them; install and sign in to one first."
  fi
  if ! command -v gh >/dev/null 2>&1; then
    warn "gh is not installed, and following main reads CI through it; install the GitHub CLI and run gh auth login."
  elif ! gh auth status >/dev/null 2>&1; then
    warn "gh auth status fails, and following main reads CI through it; run gh auth login."
  fi
}

# The rule the loom applies before it moves a checkout (DevSource.refusal):
# on main, no uncommitted change to a tracked file, fast-forward only.
dev_checkout() {
  if [ ! -e "$DEV_DIR" ]; then
    run_named "git clone ${REPO} ${DEV_DIR}" git clone "$REPO" "$DEV_DIR"
    return 0
  fi
  if [ ! -e "${DEV_DIR}/.git" ]; then
    fail "${DEV_DIR} exists and is not a git checkout: not touched; move it away or set BRNRD_DEV_DIR to another directory."
  fi
  branch=$(git -C "$DEV_DIR" symbolic-ref -q --short HEAD || true)
  if [ -n "$branch" ] && [ "$branch" != "main" ]; then
    fail "checkout ${DEV_DIR} is on branch ${branch}, not main: not touched; switch it back with git -C ${DEV_DIR} switch main and run this again."
  fi
  dirty=$(git -C "$DEV_DIR" status --porcelain --untracked-files=no) || fail "git status failed in ${DEV_DIR}; check it by hand."
  if [ -n "$dirty" ]; then
    count=$(printf '%s\n' "$dirty" | wc -l | tr -d ' ')
    first=$(printf '%s\n' "$dirty" | sed -n '1s/^ *//p')
    fail "checkout ${DEV_DIR} has ${count} uncommitted change(s) (${first}): not touched; commit or stash them and run this again."
  fi
  run_named "git -C ${DEV_DIR} pull --ff-only origin main" git -C "$DEV_DIR" pull --ff-only origin main
}

brnrd_named() {
  name="brnrd $*"
  if [ "$dry" -eq 1 ]; then
    show "$name"
    return 0
  fi
  if ! "${bindir}/brnrd" "$@" </dev/null; then
    fail "Failed to run ${name}; run it by hand."
  fi
}

install_dev() {
  git --version >/dev/null 2>&1 || fail "git is missing (on macOS: xcode-select --install); install it and run this again."
  if ! command -v uv >/dev/null 2>&1; then
    bootstrap_uv
  fi
  dev_checkout
  uvbin=$(command -v uv || echo uv)
  run_named "uv tool install --editable ${DEV_DIR}" "$uvbin" tool install --editable "$DEV_DIR"
  uv_path "$uvbin"
  brnrd_named loom install --dev --dry-run
  brnrd_named loom install --dev
  brnrd_named loom status
}

preflight

if [ "$dev" -eq 1 ]; then
  install_dev
  show "Next: brnrd setup"
  exit 0
fi

if command -v uv >/dev/null 2>&1; then
  install_with_uv
elif command -v pipx >/dev/null 2>&1; then
  install_with_pipx
else
  bootstrap_uv
  if [ "$dry" -eq 1 ]; then
    show "uv tool install brnrd"
    show "uv tool update-shell"
  else
    install_with_uv
  fi
fi

show_version
show "Next: brnrd setup"
