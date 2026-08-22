#!/usr/bin/env sh
# CauseGraph installer — downloads the prebuilt release for your OS/arch and sets it up.
# No Go, no build. Needs Python 3 (preinstalled on macOS/Linux) + curl + tar.
#
#   curl -fsSL https://raw.githubusercontent.com/NitinKumar004/causegraph/main/install.sh | sh
#
# Then:  cg up      (or ~/.local/share/causegraph/<name>/scripts/cg up)
set -eu

REPO="NitinKumar004/causegraph"
DEST="${CAUSEGRAPH_PREFIX:-$HOME/.local/share/causegraph}"

os="$(uname -s | tr '[:upper:]' '[:lower:]')"
case "$os" in darwin|linux) ;; *) echo "unsupported OS: $os (macOS/Linux only)" >&2; exit 1;; esac
arch="$(uname -m)"
case "$arch" in x86_64|amd64) arch=amd64;; arm64|aarch64) arch=arm64;; *) echo "unsupported arch: $arch" >&2; exit 1;; esac

command -v python3 >/dev/null 2>&1 || { echo "Python 3 is required (not found on PATH)." >&2; exit 1; }

echo "finding the latest CauseGraph release..."
tag="$(curl -fsSL "https://api.github.com/repos/$REPO/releases/latest" \
  | sed -n 's/.*"tag_name":[[:space:]]*"\([^"]*\)".*/\1/p' | head -1)"
[ -n "$tag" ] || { echo "no published release found for $REPO yet." >&2; exit 1; }

name="causegraph-${tag}-${os}-${arch}"
url="https://github.com/$REPO/releases/download/${tag}/${name}.tar.gz"
echo "downloading ${name}.tar.gz ..."
mkdir -p "$DEST"
curl -fsSL "$url" | tar -xz -C "$DEST"

app="$DEST/$name"
CG="$app/scripts/cg"
echo "installed to $app"

# convenience symlink if ~/.local/bin exists
[ -d "$HOME/.local/bin" ] && ln -sf "$CG" "$HOME/.local/bin/cg" && echo "linked: cg -> ~/.local/bin/cg"

# turnkey: set it up to run automatically (recorder + dashboard as login services)
echo
echo "setting up CauseGraph to run automatically..."
if "$CG" setup; then
  :
else
  echo
  echo "auto-setup was skipped — start it yourself with:  $CG up" >&2
fi
