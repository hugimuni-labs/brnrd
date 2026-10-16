#!/bin/sh
# Install the brnrd command onto PATH. POSIX sh: macOS /bin/sh and Debian dash.
# Safe to run twice. BRNRD_INSTALL_DRY_RUN=1 prints the commands and does not
# run them. Probes still run, so a second run prints upgrade rather than install.
# The uv bootstrap is not a pipe. POSIX sh has no pipefail, and sh exits 0
# on an empty script, which would hide a failed curl.
set -eu

dry=0
if [ "${BRNRD_INSTALL_DRY_RUN:-}" = "1" ]; then
  dry=1
fi

fail() {
  printf '%s\n' "$1" >&2
  exit 1
}

show() {
  printf '%s\n' "$1"
}

run_named() {
  name=$1
  shift
  if [ "$dry" -eq 1 ]; then
    show "$name"
    return 0
  fi
  if ! "$@"; then
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
  if [ "$dry" -eq 1 ]; then
    show "uv tool update-shell"
    return 0
  fi
  bindir=$("$uvbin" tool dir --bin) || fail "uv tool dir --bin failed; run uv tool update-shell by hand."
  if ! "$uvbin" tool update-shell; then
    show "Could not update the shell profile. Add this line: export PATH=\"${bindir}:\$PATH\""
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
