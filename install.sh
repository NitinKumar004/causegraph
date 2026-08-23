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

# Pin an exact release with CAUSEGRAPH_TAG; otherwise resolve the latest.
tag="${CAUSEGRAPH_TAG:-}"
if [ -z "$tag" ]; then
  echo "finding the latest CauseGraph release..."
  tag="$(curl -fsSL "https://api.github.com/repos/$REPO/releases/latest" \
    | sed -n 's/.*"tag_name":[[:space:]]*"\([^"]*\)".*/\1/p' | head -1)"
fi
[ -n "$tag" ] || { echo "no published release found for $REPO yet." >&2; exit 1; }

name="causegraph-${tag}-${os}-${arch}"
BASE="${CAUSEGRAPH_BASE_URL:-https://github.com/$REPO/releases/download/${tag}}"

# Download the archive AND its checksum list to a temp dir, VERIFY, then extract. We never
# pipe straight into tar — unverified bytes must never touch the filesystem as an executable.
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
echo "downloading ${name}.tar.gz ..."
curl -fsSL "$BASE/${name}.tar.gz" -o "$tmp/${name}.tar.gz"
curl -fsSL "$BASE/SHA256SUMS"     -o "$tmp/SHA256SUMS"

# 1) INTEGRITY (hard gate): the archive's sha256 must match the release's SHA256SUMS.
if command -v sha256sum >/dev/null 2>&1; then
  got="$(sha256sum "$tmp/${name}.tar.gz" | awk '{print $1}')"
elif command -v shasum >/dev/null 2>&1; then
  got="$(shasum -a 256 "$tmp/${name}.tar.gz" | awk '{print $1}')"
else
  echo "no sha256 tool (sha256sum/shasum) found — cannot verify the download." >&2; exit 1
fi
want="$(grep "[ /]${name}.tar.gz\$" "$tmp/SHA256SUMS" | awk '{print $1}' | head -1)"
[ -n "$want" ] || { echo "no checksum for ${name}.tar.gz in SHA256SUMS — refusing to install." >&2; exit 1; }
if [ "$got" != "$want" ]; then
  echo "CHECKSUM MISMATCH for ${name}.tar.gz — refusing to install." >&2
  echo "  expected $want" >&2
  echo "  got      $got" >&2
  exit 1
fi
echo "checksum verified"

# 2) AUTHENTICITY (best available): if the GitHub CLI supports attestations, require GitHub's
# keyless build-provenance to verify — this catches a fully-tampered release that also rewrote
# SHA256SUMS. Skip with CAUSEGRAPH_SKIP_ATTESTATION=1 (offline/mirror installs).
if [ "${CAUSEGRAPH_SKIP_ATTESTATION:-0}" != "1" ] && command -v gh >/dev/null 2>&1 && gh attestation --help >/dev/null 2>&1; then
  if gh attestation verify "$tmp/${name}.tar.gz" --repo "$REPO" >/dev/null 2>&1; then
    echo "build provenance verified (GitHub attestation)"
  else
    echo "PROVENANCE VERIFICATION FAILED for ${name}.tar.gz — refusing to install." >&2
    echo "  the checksum matched but GitHub's build-provenance did not verify." >&2
    echo "  if you are installing from a mirror, re-run with CAUSEGRAPH_SKIP_ATTESTATION=1" >&2
    exit 1
  fi
else
  echo "note: install the GitHub CLI (gh) for cryptographic provenance verification; using checksum only." >&2
fi

mkdir -p "$DEST"
tar -xzf "$tmp/${name}.tar.gz" -C "$DEST"

app="$DEST/$name"
CG="$app/scripts/cg"
echo "installed to $app"

# convenience symlink if ~/.local/bin exists
[ -d "$HOME/.local/bin" ] && ln -sf "$CG" "$HOME/.local/bin/cg" && echo "linked: cg -> ~/.local/bin/cg"

# turnkey: set it up to run automatically (recorder + dashboard as login services).
# CAUSEGRAPH_NO_SETUP=1 skips this (scripted/CI installs that don't want a login service).
if [ "${CAUSEGRAPH_NO_SETUP:-0}" = "1" ]; then
  echo "skipping auto-setup (CAUSEGRAPH_NO_SETUP=1) — start it with:  $CG up"
else
  echo
  echo "setting up CauseGraph to run automatically..."
  if "$CG" setup; then
    :
  else
    echo
    echo "auto-setup was skipped — start it yourself with:  $CG up" >&2
  fi
fi
