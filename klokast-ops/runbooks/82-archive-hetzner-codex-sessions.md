# Archive Hetzner Codex Sessions

Use this before decommissioning a Codex/ops cloud host whose conversations may
contain sensitive Platform details or accidental secrets.

## Execution locus

Run the archive from the active Ansible controller:

```sh
tailscale ssh smith@k002-ops
cd ~/src/klokast/klokast-box
git pull --ff-only
```

Do not run the archive on `vultr-ops` as `agent`. Cloud infra-agent hosts are
remote terminals only and must not become the durable store for Platform-private
conversation history.

## Tailscale access

The controller must be allowed to SSH to the retiring source machine as the
source Unix user. For the old Hetzner machine named `codex`, that means:

```text
src: tag:ops
dst: tag:ops
user: codex
```

Keep this access only as long as needed for the archive. Validate and apply
policy changes from the controller with the root-owned policy wrappers, then
remove the temporary access after the archive has been verified.

## Archive command

First inspect the plan:

```sh
ansible/bin/archive-codex-sessions \
  --source-machine codex \
  --source-user codex \
  --dry-run-plan
```

Then run the transfer and source cleanup:

```sh
ansible/bin/archive-codex-sessions \
  --source-machine codex \
  --source-user codex \
  --delete-source
```

The archive is written under:

```text
/home/smith/private/klokast/codex-archives/<source>/<timestamp>/
```

The wrapper pulls into a `.incoming` directory, writes `SHA256SUMS` and
`manifest.json`, verifies the checksums, atomically publishes the archive, and
only then deletes the exact source files listed in `source-files.nul`.

Included by default:

```text
~/.codex/sessions/
~/.codex/history.jsonl
~/.codex/logs*.sqlite*
~/.codex/state*.sqlite*
~/.codex/goals*.sqlite*
~/.codex/log/
~/.codex/shell_snapshots/
```

Excluded by design:

```text
~/.codex/auth.json
~/.codex/config.toml
~/.codex/cache/
~/.codex/tmp/
~/.codex/.tmp/
~/.codex/skills/
```

## Verification

On the controller:

```sh
cd /home/smith/private/klokast/codex-archives/codex/<timestamp>
sha256sum -c SHA256SUMS
find . -perm /077 -print
test ! -e codex/auth.json
test ! -e codex/config.toml
```

On the source, the wrapper verifies that no included conversation files remain.
It may leave an empty `~/.codex` directory and non-archived auth/config/cache
files behind.
