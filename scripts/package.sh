#!/usr/bin/env bash
# package.sh — build self-contained release archives.
#
# Each archive needs only Python 3 to RUN (no Go, no pip, no internet): it bundles
# the platform's cged binary, the engine source, the UI, and a vendored copy of
# networkx (pure-python, so one copy works everywhere).
#
#   make build          # once, to create the venv that we vendor networkx from
#   scripts/package.sh [version]     # -> dist/causegraph-<version>-<os>-<arch>.tar.gz
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VERSION="${1:-dev}"
DIST="$ROOT/dist"

NX="$("$ROOT/engine/.venv/bin/python" -c 'import networkx,os;print(os.path.dirname(networkx.__file__))' 2>/dev/null || true)"
if [ -z "$NX" ] || [ ! -d "$NX" ]; then
  echo "networkx not found in the engine venv — run 'make build' first" >&2
  exit 1
fi

rm -rf "$DIST"; mkdir -p "$DIST"
TARGETS="darwin/arm64 darwin/amd64 linux/amd64 linux/arm64"
for t in $TARGETS; do
  os="${t%/*}"; arch="${t#*/}"
  name="causegraph-${VERSION}-${os}-${arch}"
  stage="$DIST/$name"
  echo "==> $name"
  mkdir -p "$stage/bin" "$stage/engine" "$stage/vendor" "$stage/scripts"
  # 1. platform recorder binary (cgo-free -> static, portable)
  ( cd "$ROOT/daemon" && GOOS="$os" GOARCH="$arch" CGO_ENABLED=0 go build -trimpath -o "$stage/bin/cged" ./cmd/cged )
  # 2. engine source (no venv, no bytecode)
  rsync -a --exclude '__pycache__' --exclude '*.pyc' "$ROOT/engine/causegraph" "$stage/engine/"
  # 3. vendored deps (pure-python networkx) + UI + launcher + docs
  cp -R "$NX" "$stage/vendor/networkx"
  cp -R "$ROOT/ui" "$stage/ui"
  cp "$ROOT/scripts/cg" "$stage/scripts/cg"; chmod +x "$stage/scripts/cg"
  [ -f "$ROOT/README.md" ] && cp "$ROOT/README.md" "$stage/README.md"
  cat > "$stage/RUN.txt" <<'TXT'
CauseGraph — run it:

    ./scripts/cg up

Requires Python 3.10+ (preinstalled on macOS and most Linux). Nothing else to
install. `cg up` starts a background recorder and opens the live UI in your
browser. `cg down` stops it; `cg service install` makes it start on every login.
TXT
  ( cd "$DIST" && tar -czf "$name.tar.gz" "$name" && rm -rf "$name" )
  echo "    dist/$name.tar.gz"
done

# SHA256SUMS over every archive — the installer verifies the download against this before
# extracting, so a corrupted or tampered archive is refused (integrity). Standard
# `sha256sum -c` format so it's verifiable by hand too.
( cd "$DIST" && { command -v sha256sum >/dev/null 2>&1 && sha256sum ./*.tar.gz || shasum -a 256 ./*.tar.gz; } > SHA256SUMS )
echo "    dist/SHA256SUMS"
echo "done — archives in dist/"
