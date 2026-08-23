#!/usr/bin/env bash
# render-homebrew-formula.sh — fill the Homebrew formula template with a version and the
# per-arch sha256s from a release's SHA256SUMS. Prints the finished formula to stdout.
#
#   scripts/render-homebrew-formula.sh 0.1.0 dist/SHA256SUMS > causegraph.rb
#
# The version is WITHOUT the leading "v" (archives are named causegraph-v<version>-<os>-<arch>).
set -euo pipefail
VERSION="${1:?usage: render-homebrew-formula.sh <version-without-v> [SHA256SUMS]}"
SUMS="${2:-dist/SHA256SUMS}"
TMPL="$(cd "$(dirname "$0")/.." && pwd)/packaging/homebrew/causegraph.rb.tmpl"

[ -f "$SUMS" ] || { echo "SHA256SUMS not found: $SUMS" >&2; exit 1; }
[ -f "$TMPL" ] || { echo "template not found: $TMPL" >&2; exit 1; }

sha_for() { # $1 = os-arch (e.g. darwin-arm64); matches the archive line in SHA256SUMS
  grep "causegraph-v${VERSION}-$1\.tar\.gz\$" "$SUMS" | awk '{print $1}' | head -1
}

out="$(cat "$TMPL")"
out="${out//@VERSION@/$VERSION}"
for t in darwin-arm64 darwin-amd64 linux-amd64 linux-arm64; do
  token="@SHA_$(printf '%s' "$t" | tr 'a-z-' 'A-Z_')@"   # darwin-arm64 -> @SHA_DARWIN_ARM64@
  sha="$(sha_for "$t")"
  [ -n "$sha" ] || { echo "no sha256 for causegraph-v${VERSION}-$t.tar.gz in $SUMS" >&2; exit 1; }
  out="${out//$token/$sha}"
done
printf '%s\n' "$out"
