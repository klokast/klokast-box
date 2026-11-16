# MacBook tools

This directory contains MacBook tools and [setup instructions](runbooks/10-macbook-preparation.md).

## Application commands with `kk`

Run `kk` from the public source checkout on the operator MacBook:

```text
kk [--instance PATH] COMMAND [ARGUMENTS...]
kk app APPLICATION COMMAND [ARGUMENTS...]
kk status [TARGET]
kk platform handoff --session SESSION_ID --from BOX --to BOX
kk update BOX-ROLE [--resume OPERATION_ID] [--detach]
kk update BOX-ROLE --cancel
kk promote BOX-ROLE [--resume OPERATION_ID]
kk promote [BOX-ROLE] --status
kk logs [list MACHINE [--expired] | clean [--apply] | view MACHINE PATH | get MACHINE PATH [--to DIRECTORY]]
kk air [--box BOX | --prefer BOX,BOX | --status]
kk doctor [--install]
kk setup [--instance REPOSITORY_URL]
kk setup --controller BOX-ops [--check]
kk --help
```

`PATH` is the local private Instance worktree. Set `KLOKAST_INSTANCE` to use
that worktree by default. An explicit `--instance PATH` takes precedence.
Application, runner and maintenance commands require this selection. Help, `doctor`, and `setup` do not.

Each command that reads the Instance first fetches its GitHub `main`, so that
it never answers from a stale worktree. A clean worktree that is behind is
fast-forwarded, and the command says so. These conditions stop the command:
uncommitted changes, commits that are not on GitHub `main`, a branch other
than `main`, and a GitHub that the MacBook cannot reach. The fetch uses the
GitHub SSH access of the clone and never prompts.

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
kk status
```

The application name is the exact key under `apps` in `klokast-instance.json`.
Its `desired-state` must be `present`. `kk status` shows each declared
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

Use `kk update BOX-dom0` for a manual host update, including a controller
switch when required. It can reboot the entire box. See the
[dom0 update procedure](../doc/platform-updates.md#diskless-dom0-activation)
for session checks, terminal requirements, and recovery.

Use `kk update BOX-ROLE` to request a safe image update of one VM from the
active controller, for example `kk update k001-dmz`. `ROLE` is `air`, `bak`,
`dmz`, `iot`, `router`, `vpn-egress` or `ops`. All roles use the same
controller job and the same [replacement workflow](../doc/platform-updates.md#common-vm-replacement)
as the nightly schedule. An unchanged image causes health checks only.
`BOX-ops` selects the standby controller; the active controller refuses its
own replacement. The command shows the stages and prints one line when the
stage changes, until the job ends. Press Ctrl-C to stop following, or use
`--detach` to return after the request. Use `kk status BOX-ROLE` to inspect progress or
`--resume OPERATION_ID` to retry the same request. The controller job survives
MacBook disconnection. Use `kk update BOX-ROLE --cancel` to stop the job while
it still waits for its checks or for the shared update lock. A job that already
changes the VM, or resumes a recorded VM operation, cannot stop; the command
refuses and the job continues. A cancelled job leaves no unfinished operation. For `BOX-air`, detach from the runner so its idle
workspace can be saved, replaced and restored. The public checkout of each
controller must contain the `vm-image-update` command before the first request.
See [request authority](../doc/architecture.md#vm-image-update-requests).

Use `kk status` to list the machines. The controller target check remains
the authority for each request: it refuses a machine that is declared stopped
and the active controller. To update the active controller, first make the
standby active with `kk promote BOX-ops`.

Use `kk promote BOX-ROLE` to make one declared target the active holder of a
role that only one box holds at a time. `kk promote k001-ops` runs the
[planned controller switch](../doc/platform-deploy.md#controller-recovery) on
the other controller. `kk promote k002-air` runs the
[workspace move](../klokast-ops/runbooks/07-codex-on-ops.md) on the active
controller; `kk air` then opens that runner. A device preference
(`kk air --prefer`) cannot move the workspace. The command prints an operation
ID; use `--resume OPERATION_ID` after an interruption. `kk status` shows the
observed state of each controller and runner. `kk promote --status` lists the
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

Use `kk status` for a read-only summary. The report starts with Machines,
then Credentials, Applications, Controllers, Runners, Promotions and Updates.
Applications shows one column per declared box, from Instance placement.
`present` identifies an active placement; `passive` identifies a standby in an
active-passive placement. Other boxes show `absent`. These are declarations,
not live health checks. An application with one dedicated VM uses that VM's
hostname suffix as its display name, such as `print` for `print-server`.
Other applications keep their Instance name. `kk status NAME` accepts either
name; `kk app` still uses the Instance name. CLIENT is `available` or `none`
for a present application, and `-` for an absent application.
Credentials currently shows each reachable controller's checks, as in
`kk setup --controller BOX-ops --check`. Controllers shows each declared
controller's role, whether the active controller uses the published Instance,
and the last completed controller switch time. That time comes from the final
write of a completed switch record on either reachable controller; it is
`unknown` when neither has a completed record. It is not the marker change time.
Runners shows the workspace location. Updates combines unfinished update jobs
with the latest scheduled run of each reachable controller. It also shows the
active controller's scheduled switch and standby update result. Help contains
resume commands and failed run log paths. Summary gives the attention count
just before the report time footer. All times are UTC.

The machines are each dom0 and each declared VM of the `kk update` roles.
Machines with a common image profile form one group, with a blank line before
each group. Groups sort by their lowest reported package count, in ascending
order. Groups with no reported count come last. Within a group, machines sort
by role and then by target name. For each machine the active controller reads
the last update time, the Alpine release and the explicit package count.
This count includes installed packages named in `/etc/apk/world`, plus
Tailscale, Codex and Claude Code when installed outside APK. Dependencies are
excluded unless they are also explicit packages. Upstream tool versions come
from their version commands. The summary and target details use the same list.
The time of the last update is the last change of the root file system
identity, which a VM replacement writes, or of the package database, which an
in-place upgrade writes. A diskless dom0 installs its packages again at each
boot; for that machine the time can be the time of the last boot. The command
does not contact a machine that is declared stopped.

Use `kk status TARGET` for the details of one machine or one application. For
a machine it shows the time of the last update, the time at which the image
was built, the time of the last boot, the latest update job with the recorded
time of each check and stage, and each explicit package with its version and
the time at which the distribution built that version. An upstream tool has
an unknown build time when its version command does not supply one. The Platform records
no update time and no package list for an application; the command shows its
declared state and if it supplies a client. For each stopped operation it prints the command that resumes
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

## Log files with `kk logs`

`kk logs` shows the log files of all machines: for each machine the number of
files, the size, the expired files, the unregistered files and the large files.
Then it shows the largest groups and the operations journal of each
controller. The controllers apply the
[log retention policy](../ansible/ansible.md#log-retention); `kk` holds no rule.
Each controller reports its own files. The active controller reports dom0 and
the other VMs. The exit status is `1` when an item needs attention.

```sh
kk logs                                   # report
kk logs list k002-ops --expired           # files of one machine
kk logs clean                             # plan: the files that expire
kk logs clean --apply                     # delete them now
kk logs view k001-dom0 /var/log/messages  # read one file in vi -R
kk logs get k002-ops /var/lib/klokast/ops-nightly/20261116T190000.000000Z.log
```

`MACHINE` is a name from the report, for example `k001-ops` or `k002-dmz`.
`PATH` is an exact path from `kk logs list`; the controller refuses any other
file. `get` writes to `~/Downloads/klokast-logs/MACHINE/` by default and does
not replace an existing file; use `--to DIRECTORY` to select another directory.
`view` deletes its temporary copy when `vi` stops. Because file contents are
private Platform state, `view` and `get` run only on the operator MacBook.
The nightly run applies the same deletions; `clean --apply` does not have to
wait for it.

## Other MacBook tools

- `bin/kk doctor [--install]` checks prerequisites and can install supported
  missing Python dependencies. Mac scripts use the Bash supplied by macOS.
- `bin/prepare-private-instance-worktree` clones the private Instance with
  ordinary Git. `bin/publish-private-instance` validates and optionally publishes edits.
- `bin/install-tailscale-oauth` installs independent root-only Tailscale OAuth files on their owning controller.
- From Mac/client machines, use OpenSSH (`ssh`, `rsync -e ssh`) for Tailnet SSH
  paths. Do not use `tailscale ssh` as an rsync remote shell; rsync passes OpenSSH
  flags such as `-l`. Wrappers can use `tailscale status` to discover the active Tailnet IP.

## Controller credentials

Each controller holds its own provider and Git credentials. A planned switch
refuses a destination that lacks one of them. Use
`kk setup --controller BOX-ops --check` to show the report of one controller.
The read-only `controller-credentials status` command on that controller
makes the report; it shows no secret.

Use `kk setup --controller BOX-ops` to install the missing items. For each
item, the command shows the provider page and the exact settings, and then
reads the values at hidden prompts. For each Tailscale client, paste only the
client secret; the command reads the client ID from it. You do not need to
save the client secrets.

| Item | Procedure | What the command does |
| --- | --- | --- |
| Tailscale policy and devices clients | [Tailscale credentials](../klokast-ops/runbooks/40-tailscale-wrapper-setup-policy.md) | Writes two temporary `0600` files and runs `install-tailscale-oauth`. |
| GitHub public metadata token | [GitHub metadata](runbooks/27-github-metadata.md) | Runs `install-github-metadata-token`. |
| Instance read and write keys | [Instance Git access](../doc/platform-syscalls.md#development-instance-git-access) | Shows the public key. With your consent, it adds the key with the GitHub CLI; otherwise it opens the GitHub page. It runs the controller Instance Git steps. |

Create new provider clients and tokens for each controller. Do not reuse
the credentials of another controller. The command runs only on the operator
MacBook, because the secrets pass through it. A Freebox app token needs a
physical approval; the command reports it but cannot install it.

## Nightly time

Use `kk setup --update-time 04:30` to save and apply the daily UTC time.
Use `kk status --update-time` to show the saved time. Both require an existing
Instance selection. See [the Instance contract](../doc/klokast-instance-specification.md#nightly-update-time).
This setup option does not install MacBook software or accept a repository URL.
