# MacBook tools

This directory contains MacBook tools and [setup instructions](runbooks/10-macbook-preparation.md).

## Application commands with `kk`

Run `kk` from the public source checkout on the operator MacBook:

```text
kk [--instance PATH] APPLICATION COMMAND [ARGUMENTS...]
kk platform [--help]
kk doctor [--install]
kk --help
```

`PATH` is the local private Instance worktree. Set `KLOKAST_INSTANCE` to use
that worktree by default. An explicit `--instance PATH` takes precedence.
Application commands require this selection. Help, `platform`, and `doctor` do not.

For example, with Music and Torrent declared present in Instance:

```sh
export KLOKAST_INSTANCE="$HOME/private/klokast/instance"
kk torrent open --to boxb
kk music upload --from "$HOME/Documents/music" --to boxb
kk --instance "$KLOKAST_INSTANCE" torrent status --to boxb
```

The application name is the exact key under `apps` in `klokast-instance.json`.
Its `desired-state` must be `present`. Instance is desired state; it does not
prove that a service is running. The application tool handles connectivity
and execution. See [Instance desired state](../doc/klokast-instance-specification.md).

Keep the private worktree synchronized with ordinary Git:

```sh
git -C "$KLOKAST_INSTANCE" pull --ff-only
```

`kk` reads the selected local file on each invocation. It does not fetch Git
changes or contact the controller to select an application. It checks the
fields needed for dispatch. Use `klokast check --instance PATH` to validate
the full Instance before committing desired-state changes.

## Application client contract

An application exposes its MacBook commands through this executable:

```text
apps/<application-name>/bin/<application-name>-client
```

`kk` has no application list or application-specific command table. It uses
the Instance name to select this fixed path inside the application's directory.
Instance cannot supply an executable path or shell code. `doctor` is reserved
for the MacBook prerequisite check. `platform` is reserved for the human
Platform interface. Neither command dispatches to an application client.
See [application names](../doc/klokast-instance-specification.md#application-names)
for the application naming restriction.

The client receives the command and all remaining arguments unchanged. Options
after the application name belong to the client. For example,
`kk torrent --help` requests Torrent client help. The client owns command
validation, target selection, credentials, remote access, and errors.

`kk` preserves standard input, standard output, standard error, and the client
exit status. It preserves the environment and sets these client variables:

| Variable | Value |
| --- | --- |
| `KLOKAST_INSTANCE` | Absolute path of the selected private Instance worktree. |
| `KLOKAST_TAILNET_SUFFIX` | `tailscale.tailnet-dns-name` from that Instance. |

The Instance DNS name takes precedence over a caller's `KLOKAST_TAILNET_SUFFIX`.
When a client runs directly, its own environment rules apply.

Missing declarations, absent applications, invalid dispatch inputs, and missing
or non-executable client tools cause an expressive error with exit status `2`.
An application can be present without exposing a client interface. `kk` then
reports that the interface is unavailable.

Keep client code, tests, and command descriptions in the owning application.
See [Music](../apps/music/README.md) and [Torrent](../apps/torrent/README.md).
The [application catalog](../apps/README.md) links to controller maintenance
instructions. Foundation automation follows [User Services](../doc/architecture.md#user-services).

## Platform commands with `kk platform`

`kk platform` is the human command interface for the production Platform.
`kk doctor` checks local MacBook prerequisites; `kk platform` addresses Platform
administration. Authority follows the existing
[human authority model](../doc/threat-model.md#human-authority).

`kk platform`, `kk platform -h`, and `kk platform --help` show help and exit
with status `0`. The help lists future use cases. Other arguments report that
Platform commands are not implemented and exit with status `2`.

This interface currently provides only help. It does not read Instance or
connect to the controller. Platform operations and human authentication are
not implemented yet. Future command syntax is not defined.

## Other MacBook tools

- `bin/kk doctor [--install]` checks prerequisites and can install supported
  missing Python dependencies. Mac scripts use the Bash supplied by macOS.
- `bin/prepare-private-instance-worktree` clones the private Instance with
  ordinary Git. `bin/publish-private-instance` validates and optionally publishes edits.
- `bin/install-tailscale-oauth` sends root-only Tailscale OAuth files to the active controller.
- From Mac/client machines, use OpenSSH (`ssh`, `rsync -e ssh`) for Tailnet SSH
  paths. Do not use `tailscale ssh` as an rsync remote shell; rsync passes OpenSSH
  flags such as `-l`. Wrappers can use `tailscale status` to discover the active Tailnet IP.
