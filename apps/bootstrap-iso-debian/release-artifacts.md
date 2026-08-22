# Releasing bootstrap artifacts

Use a private GitHub Release to preserve bootstrap artifacts that are too large
for normal git history. Do not commit ISO images or seed tarballs into the repo.

GitHub Free private repositories are acceptable for this workflow: GitHub
Release assets can be large binary files, while regular repository files over
100 MiB are blocked. Keep the repository private unless there is an explicit
decision to publish the bootstrap ISO publicly.

References:
- <https://docs.github.com/en/repositories/releasing-projects-on-github/about-releases>
- <https://docs.github.com/en/repositories/working-with-files/managing-large-files/about-large-files-on-github>
- <https://docs.github.com/en/get-started/learning-about-github/githubs-plans>

## Inputs

Stage the built artifacts on the ops server under an ignored directory such as
`tmp/`:

```text
klokast-bootstrap-debian-trixie-amd64.iso
klokast-bootstrap-debian-trixie-amd64.iso.sha512
alpine-standard-3.23.3-x86_64.tar.gz
alpine-standard-3.23.3-x86_64.tar.gz.sha512
```

The ISO and Alpine seed are expected to be secret-free. They must not contain a
box name, Tailscale auth key, OAuth material, GitHub token, SSH private key, or
inventory secret.

The builder container is not required after the artifacts have been copied to
ops and checksum-verified. Keep the ops copy until the GitHub Release has been
uploaded, downloaded back, and verified.

## Create the manifest

First generate the tracked manifest:

```sh
cd /home/codex/src/klokast/klokast-box
ansible/bin/bootstrap-live-iso-release \
  --source-dir tmp \
  --tag bootstrap-iso-debian-2026-05-05 \
  --title "Bootstrap ISO Debian trixie / Alpine 3.23.3" \
  --used-for boxa \
  --iso-built-at 2026-05-05T01:53:00Z \
  --seed-built-at 2026-05-02T00:33:00Z \
  --manifest-out apps/bootstrap-iso-debian/releases/bootstrap-iso-debian-2026-05-05.json \
  --dry-run
```

Review and commit the generated manifest before publishing the release.

## Publish the release

After the manifest commit has been pushed and the worktree is clean:

```sh
ansible/bin/bootstrap-live-iso-release \
  --source-dir tmp \
  --tag bootstrap-iso-debian-2026-05-05 \
  --title "Bootstrap ISO Debian trixie / Alpine 3.23.3" \
  --used-for boxa \
  --iso-built-at 2026-05-05T01:53:00Z \
  --seed-built-at 2026-05-02T00:33:00Z
```

The helper creates and pushes an annotated tag, creates the GitHub Release,
uploads the ISO, seed tarball, normalized checksums, and generated JSON
manifest, then downloads the assets into `.run/` and verifies `SHA512SUMS`.

Use `gh release view <tag>` to inspect the published asset list.

## Recovery use

Download the release assets from a machine with repository access:

```sh
gh release download bootstrap-iso-debian-2026-05-05 \
  --repo klokast/klokast-box \
  --dir /tmp/klokast-bootstrap-release
cd /tmp/klokast-bootstrap-release
sha512sum -c SHA512SUMS
```

Upload the ISO and its `.sha512` sidecar to the NanoKVM data directory, then
load the ISO through the NanoKVM UI as described in `feed-bootstrap-iso.md`.
The Alpine seed tarball is an optional offline companion; the current phase 12
workflow still downloads and verifies the Alpine standard ISO unless it is
later changed to consume the release seed directly.
