# Homebrew support for CauseGraph

CauseGraph is distributed through a **custom Homebrew tap** (not homebrew-core, which has a
notability bar and requires building from source). The tap ships the same self-contained,
checksum-verified release archive the `curl … install.sh` path uses.

Once set up, users install with:

```sh
brew install NitinKumar004/causegraph/causegraph
# or:  brew tap NitinKumar004/causegraph && brew install causegraph
```

and upgrade with `brew upgrade causegraph`.

---

## One-time setup (maintainer)

### 1. Create the tap repository

Homebrew taps must be named `homebrew-<tap>`. Create a **public** repo:

```
NitinKumar004/homebrew-causegraph
```

Add a `Formula/` directory. That's it — the formula file is pushed automatically by CI on
each release (see below). To seed it once by hand from an existing release:

```sh
# from this repo, after a release exists:
curl -fsSL https://github.com/NitinKumar004/causegraph/releases/download/v0.1.0/SHA256SUMS -o /tmp/SHA256SUMS
scripts/render-homebrew-formula.sh 0.1.0 /tmp/SHA256SUMS > causegraph.rb
# then commit causegraph.rb to homebrew-causegraph/Formula/causegraph.rb
```

### 2. Give CI push access to the tap

The release workflow renders the formula from the release's `SHA256SUMS` and pushes it to the
tap. Because that's a *different* repo, `github.token` can't push there — add a token:

1. Create a fine-grained **Personal Access Token** with **Contents: read/write** on
   `homebrew-causegraph` only.
2. In this repo: **Settings → Secrets and variables → Actions → New repository secret**,
   name it `TAP_TOKEN`, paste the PAT.

That's all. The next `git tag vX.Y.Z && git push origin vX.Y.Z` will build the archives,
publish the GitHub Release, and update `homebrew-causegraph/Formula/causegraph.rb`. Without
`TAP_TOKEN` the tap step is skipped (the rest of the release still runs).

---

## How it works

- `scripts/package.sh` builds the per-platform archives **and** `SHA256SUMS`.
- `scripts/render-homebrew-formula.sh <version> <SHA256SUMS>` fills
  `packaging/homebrew/causegraph.rb.tmpl` with the version and each arch's sha256.
- The formula installs the archive under `libexec` (preserving `bin/ engine/ vendor/ ui/
  scripts/`) and drops a `bin/cg` **exec shim**. A shim — not a symlink — is required so the
  `cg` wrapper resolves its `ROOT` to `libexec` (a symlink would point it at Homebrew's prefix).
- `depends_on "python@3.12"` ensures a Python ≥3.10 is present; the archive vendors networkx,
  so there is nothing to `pip install`.

## Testing the formula locally

```sh
scripts/package.sh v0.1.0                       # build archives + SHA256SUMS into dist/
scripts/render-homebrew-formula.sh 0.1.0 dist/SHA256SUMS > causegraph.rb
brew install --build-from-source ./causegraph.rb   # or: brew audit --new ./causegraph.rb
cg --help
```
