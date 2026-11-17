#!/bin/sh
# Clone brnrd at a release tag: the one install kind, a checkout, pinned for an image.
# checkout.sh <dest> [version]   version 0.7.1 → tag v0.7.1; empty → the newest vX.Y.Z tag.
# BRNRD_SOURCE_URL overrides the repository (tests).
set -eu
dest=$1
version=${2:-}
url=${BRNRD_SOURCE_URL:-https://github.com/hugimuni-labs/brnrd}
if [ -z "$version" ]; then
  # Releases only: three numbers, no suffix (a pre-release is never picked).
  version=$(git ls-remote --tags --refs "$url" 'refs/tags/v*' \
    | sed -n 's|.*refs/tags/v\([0-9][0-9]*\.[0-9][0-9]*\.[0-9][0-9]*\)$|\1|p' \
    | sort -t. -k1,1n -k2,2n -k3,3n | tail -n 1)
  [ -n "$version" ] || { echo "checkout.sh: no release tag at $url" >&2; exit 1; }
fi
git clone --quiet --branch "v${version#v}" "$url" "$dest"
echo "brnrd checkout: v${version#v} at $(git -C "$dest" rev-parse --short HEAD) in $dest"
