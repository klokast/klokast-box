# MacBook tools

This directory contains MacBook tools and [setup instructions](runbooks/10-macbook-preparation.md).

## Application commands with `kk`

Run `kk` from the public source checkout on the operator MacBook:

```text
kk [--instance PATH] COMMAND [ARGUMENTS...]
kk app APPLICATION COMMAND [ARGUMENTS...]
kk app --list
kk status
kk platform handoff --session SESSION_ID --from BOX --to BOX
kk update BOX-ROLE [--status | --resume OPERATION_ID] [--detach]
kk update --list
kk promote BOX-ROLE [--resume OPERATION_ID]
kk promote --list
kk promote [BOX-ROLE] --status
kk air [--box BOX | --prefer BOX,BOX | --status]
kk doctor [--install]
kk setup [--instance REPOSITORY_URL]
kk --help
```

`PATH` is the local private Instance worktree. Set `KLOKAST_INSTANCE` to use
that worktree by default. An explicit `--instance PATH` takes precedence.
Application, runner and maintenance commands require this selection. Help, `doctor`, and `setup` do not.

Use [`kk setup`](runbooks/10-macbook-preparation.md#install-macbook-cli-dependencies)
to update the public checkout, install supported prerequisites, clone the private
Instance, and configure the shell. For setup only, `--instance` takes a GitHub
repository URL. For other commands, the global option takes a local worktree path.

For example, with Music and Torrent declared present in Instance:

```sh
export KLOKAST_INSTANCE="$HOME/src/private-klokast/klokast-instance"
kk app torrent open --to boxb
kk app music upload --from "$HOME/Documents/music" --to boxb
kk --instance "$KLOKAST_INSTANCE" app torrent status --to boxb
kk app --list
```

The application name is the exact key under `apps` in `klokast-instance.json`.
Its `desired-state` must be `present`. `kk app --list` shows each declared
application, its declared state, and if it supplies a client. Instance is desired state; it does not
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
Instance cannot supply an executable path or shell code. Only `kk app`
dispatches to an application client. An application name is thus never in the
`kk` command position, and no name is reserved: a new `kk` command cannot
collide with an application.

The client receives the command and all remaining arguments unchanged. Options
after the application name belong to the client. For example,
`kk app torrent --help` requests Torrent client help. The client owns command
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

## Runner and maintenance commands

Use `kk update BOX-ROLE` to request a safe image update of one VM from the
active controller, for example `kk update k001-dmz`. `ROLE` is `air`, `bak`,
`dmz`, `iot`, `router`, `vpn-egress` or `ops`. All roles use the same
controller job and the same [replacement workflow](../doc/platform-updates.md#common-vm-replacement)
as the nightly schedule. An unchanged image causes health checks only.
`BOX-ops` selects the standby controller; the active controller refuses its
own replacement. The command shows the stages and prints one line when the
stage changes, until the job ends. Press Ctrl-C to stop following, or use
`--detach` to return after the request. Use `--status` to inspect progress or
`--resume OPERATION_ID` to retry the same request. The controller job survives
MacBook disconnection. For `BOX-air`, detach from the runner so its idle
workspace can be saved, replaced and restored. The public checkout of each
controller must contain the `vm-image-update` command before the first request.
See [request authority](../doc/architecture.md#vm-image-update-requests).

Use `kk update --list` to list the accepted targets. The command reads only the
selected Instance worktree; it does not connect to a controller. `DECLARED` is
the declared runtime state, not an observation. The list includes a target
that is declared stopped and the active controller, with a note that gives the
reason why an update request is refused. Below the table, the command prints
the `kk promote` command that makes the standby active. The controller target
check remains
the authority for each request.

Use `kk promote BOX-ROLE` to make one declared target the active holder of a
role that only one box holds at a time. `kk promote k001-ops` runs the
[planned controller switch](../doc/platform-deploy.md#controller-recovery) on
the other controller. `kk promote k002-air` runs the
[workspace move](../klokast-ops/runbooks/07-codex-on-ops.md) on the active
controller; `kk air` then opens that runner. A device preference
(`kk air --prefer`) cannot move the workspace. The command prints an operation
ID; use `--resume OPERATION_ID` after an interruption. `kk promote --list`
shows each target with its observed state. `kk promote --status` lists the
unfinished operations that each declared controller records: target,
operation ID, start time, time of the last recorded progress, state and
detail. The state is `running`, `failed` (the record contains the error) or
`interrupted` (the process stopped and recorded no error). The command also
prints the resume command of each operation that is not running. Add
`BOX-ROLE` to show one target. A record made before this command existed has
an unknown start time. The public checkout of each controller must contain
the `promote-status` command. An application with a primary box
must supply its own client command with the same form:
`kk app APPLICATION promote BOX`. See
[request authority](../doc/architecture.md#promotion-requests).

Use `kk status` for a read-only summary. It shows the role that each declared
controller reports, the state of each runner and the workspace location, the
unfinished promote operations, and the update jobs that are running, failed
or interrupted. For each stopped operation it prints the command that resumes
it. The exit status is `0` when no item needs attention and `1` when an item
needs attention or a source cannot be read. `kk promote --status` uses the
same exit status. The update jobs come from `vm-image-update list` on the
active controller: for each target, the latest job when it is not complete.

`kk air` opens the selected native runner through Mosh and tmux. Device
preferences and selection stay local. `kk platform handoff` transfers one
selected discussion through this device. See [native runner operations](../klokast-ops/runbooks/07-codex-on-ops.md)
for commands, prerequisites, supported storage formats and recovery limits.
These maintenance commands use the existing [development controller authority](../doc/platform-syscalls.md).
They do not implement production release admission. Help needs no Instance.

## Other MacBook tools

- `bin/kk doctor [--install]` checks prerequisites and can install supported
  missing Python dependencies. Mac scripts use the Bash supplied by macOS.
- `bin/prepare-private-instance-worktree` clones the private Instance with
  ordinary Git. `bin/publish-private-instance` validates and optionally publishes edits.
- `bin/install-tailscale-oauth` installs independent root-only Tailscale OAuth files on their owning controller.
- From Mac/client machines, use OpenSSH (`ssh`, `rsync -e ssh`) for Tailnet SSH
  paths. Do not use `tailscale ssh` as an rsync remote shell; rsync passes OpenSSH
  flags such as `-l`. Wrappers can use `tailscale status` to discover the active Tailnet IP.
