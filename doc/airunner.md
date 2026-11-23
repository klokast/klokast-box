# AI runner

This document describes the mechanisms of the Platform AI runner `<box>-air`.
The [architecture](architecture.md#box-air) owns its role, authority and trust
boundary. Operator commands are in the
[runner runbook](../klokast-ops/runbooks/07-codex-on-ops.md).

## Virtual machine

- Xen guest name `air`; Alpine Linux; 2 vCPUs, 4096 MiB RAM, a 50 GiB root LV
  and a separate 50 GiB data LV after lifecycle migration.
- Runs Codex and Claude Code directly as unprivileged `agent`. Preserve its
  numeric UID and GID and `/home/agent` during migration. `neo` provides
  maintenance and recovery; root password login is locked.
- Uses the `usr` bridge and reserved address `192.168.175.11`. The resource
  compiler rejects workload allocations at this address.
- Uses exactly `tag:airunner` and a fresh single-use enrollment credential from
  the existing controller broker. Never transfer another runner's Tailscale state.

## Image

The runner uses the existing isolated Alpine builder with profile
`air-alpine-v1`. Image reuse binds the native Codex and Claude Code versions
and artifact checksums. Images contain no credentials or machine identity.

The runner is the test locus for unreviewed source. The image contains the
Alpine `ansible` and Python packages that the controller profile also
declares, from the same Alpine repositories. Run syntax checks and repository
tests here as `agent`. The image has no Node.js, npm, pip, C compiler, zsh, GNU
wget or container runtime: no runner workflow uses them. The profile check
refuses a runner profile that requests Podman. These tests use no inventory
secrets, controller state or network path to Platform hosts. A runner check
does not qualify a change: the controller can select a newer
`community.general` than the Alpine package.

Claude Code comes from Anthropic's stable APK repository. The controller trusts
only the signing key pinned in `ansible/lib/claude_artifact.py` for that index;
native APK checks the package against it. The builder extracts only the binary
to `/opt/claude`, outside the Alpine package database.

The latest stable Codex and Claude Code packages are selected for every new
image. CLI and native app-server packages follow the qualified image; retained
user installers cannot silently keep an older runtime selected. Build version
records are evidence, not desired-state pins. A candidate older than an
installed runtime or a newly published stable release of either tool is refused
before shutdown. Retain all discussion, authentication and configuration state.
Local reconstruction uses native resume and retained files, independent of
cross-machine storage adapters.

## Network and enrollment

The Platform compiler derives network rules from Instance runner placement.
Existing managed firewall includes have reserved Platform ownership. Permit
DNS, web and Tailscale transport, operator SSH/Mosh, and controller maintenance.
New runner images contain the SSH client for Git access, but no OpenSSH server.
New guests enroll directly in Tailscale on their first networked boot. Image
construction and filesystem personalization stay networkless. First creation uses the
[common enrollment transport](architecture.md#shared-guest-image-and-enrollment)
with the `ts-authkey-airunner` broker.

Normal boots and replacement images preserve this runner's Tailscale state and
do not enroll again. An uncertain enrollment result requires recovery of the
exact recorded attempt, not a new credential or OpenSSH fallback. See
[runner creation and recovery](platform-deploy.md#runner-creation-and-recovery).

## Controller access in development

In development, the image-managed Claude Code settings allow
`tailscale ssh smith@<box>-ops` for each controller of the verified Instance
without a prompt. Codex has no equivalent rule. The `air-workspace` role reads
the controllers with `platform-source controllers`, which refuses outside
development, so a production runner cannot receive this rule.

Controller credentials and private state stay on `ops`. Instance Git
credentials stay on their owning controller. Runner-visible archives use a
local read-only copy. No AI service daemon is required.

<a id="runner-git-credentials"></a>

## Git credentials

Each runner generates and retains its own Ed25519 deploy key
`/home/agent/.ssh/github-klokast-codex`, owned by `agent`, with private-key mode
`0600`. The key has access to the public implementation repository only. The
runner home retains no other SSH key and no SSH client settings. Images contain
no Git credentials. Same-runner replacement retains the key with the home
volume; a new runner generates a new key. No command copies keys between
runners.

The active development controller registers the public key as a write-enabled
deploy key on `klokast/klokast-box` through the root-owned `ksa-air-git` command.
It checks validated Instance runner placement before registration. The command
has a fixed repository and never returns credentials. Its dedicated GitHub App
has only repository Administration write and Metadata read, installed only on
that public repository. The App credential is kept in the root-owned mode
`0600` file `/etc/klokast/secret-authority/air-git.json`, outside Git. The broker
uses PyGithub to mint short-lived installation tokens, limits mutation tokens
to the fixed repository, and revokes those tokens after use. Only public keys,
key IDs, fingerprints and results enter provisioning output and audit records.

The attacker in scope is a compromised runner or dependency. It can misuse
that runner's public-source write access, but must not receive the App key,
repository administration token, another runner's key, or Instance credentials.
Public source writes do not admit a production Platform release. The controller
is the credential trust boundary; development `smith` remains privileged.

Provisioning checks the App capability before creating the VM. After enrollment
and network closure it bootstraps Git, registers the key, and checks a dry-run
push as `agent`. Missing credentials, denied registration or failed push access
stop provisioning. Repeated registration preserves the same key. A conflicting
key for the same runner stops the operation until its old grant is revoked.

Retirement revokes the runner's managed deploy key after stopping the VM.
The broker can revoke by runner name while that VM is offline or absent from
Instance. For compromise, revoke first, fence the runner through the existing
VM lifecycle, and rebuild with a new key. Removing the App installation does
not revoke its deploy keys; revoke those explicitly at GitHub if the controller
is unavailable. Same-controller replacement preserves the App credential.
Controller promotion requires installing that controller's own App credential;
do not copy credentials between controllers. Keep recovery material in the
human password manager. Setup and recovery commands are in the
[runner Git runbook](../klokast-dev/runbooks/26-air-github-app.md).

## Workspace

Each runner has its own `agent` tmux workspace and its own Codex and Claude Code
discussions on its retained home. No command moves a discussion or a workspace
to another runner, and no runner trusts data from another runner. `kk air`
opens the runner that the device preference or `kk air --box BOX` selects; it
skips a [closed](#runner-closure) runner. An image replacement saves the
workspace of its runner and restores it on the same runner.

### Runner closure

Only an operation that stops the runner closes it: a runner replacement or a
dom0 update. The active controller runs it under the existing VM operation
lock. Its final activity check, under the runner's workspace lock, writes the
closure: the operation ID and its owner, `air` or `dom0`. The closure refuses
new connections. It survives root replacement on retained storage and does not
expire. The operation removes it when it ends, and restores the saved
workspace. The dom0 journals decide when a closure has ended: before a new
runner replacement or dom0 update starts, the controller removes a closure of
the same owner when that journal has no pending operation on the box. A
closure of the other owner stays until its own operation resumes. Nothing
reserves a runner before that final check.

<a id="runner-activity"></a>

### Activity check

Manual and nightly replacement use the same live activity check. It waits
only for running agents, because replacement ends every other process. These
block replacement: a Codex turn or background terminal, a Claude Code client
that is not idle, and agent work outside a turn. An attached tmux client does
not block. The final check writes the [closure](#runner-closure), which refuses
a new connection, and then detaches each client with a message that names
`kk status`. Agent work outside a turn is a process that a Claude Code client
started and that still runs, for example a background Bash command, or a Codex
process without an open native thread, for example `codex exec`. Native
idle-thread and background-terminal checks distinguish idle Codex clients from
running work. Shells, editors, other programs, Claude Code's own supervisor,
PTY and spare hosts, idle tmux servers and the probe itself do not block. Each
Claude Code client's own PID record, bound to the process start time, reports
its session, its status and the background job of a parked conversation; a
missing or reused record blocks. Claude Code writes the status after the first
turn, so a client without a status blocks only when it has a conversation: a
transcript with a user message.

Replacement can proceed as soon as the live checks report idle. Recent use does
not impose a delay. Before a stop, recheck activity on the target runner under
its workspace lock and save its workspace on retained storage. Updating an idle
runner can briefly make that runner unavailable. A healthy spare runner is not
required; another box, router or VPN cannot block this replacement. The target
still requires its own verified image, identity, retained storage, network and
recovery checks. This protects operator work from accidental concurrent
maintenance; it is not a boundary against controller root or a malicious
runner account.

Nightly execution skips an air guest that the activity check reports busy.
Check before preparation and again before shutdown. Never interrupt a turn to
permit replacement. Manual requests use the same checks through the active
controller.

### Workspace checkpoint

Same-machine replacement saves the tmux workspace, closes the idle Claude Code
panes and restarts each one with native `claude --resume`. A pane that shows
the agent view after its conversation moved to the background resumes that
parked conversation. A client without a conversation, for example on the
session picker, closes, and its pane restores as a shell. A pane with another
program also restores as a shell; a shell pane outside the retained home
restores in the home. The checkpoint closes the tmux panes first, then stops the
idle background sessions with `claude daemon stop --any` and records their IDs,
then ends idle Claude Code clients outside tmux. Replacement does not restart
these clients; their transcripts stay on the retained disk, `claude agents`
lists the stopped background sessions and `claude --resume` opens them.

The local tmux workspace is saved on retained storage and restored after
replacement and reboot verification. AIR replacements with local workspace
checkpoints verify tmux restoration before accepting the new generation. An
interrupted restore keeps the VM operation pending and the
[runner closed](#runner-closure). Resume that exact VM operation. If the closure
has no checkpoint, the runner cannot recreate it. Completion then releases the
runner and records `workspace: not restored: checkpoint missing` in the job
result, the job reason and the log. Native histories stay on the retained disk;
reopen them with `codex resume` or `claude --resume`. Scheduled AIR work skips
a closed runner and cannot retire its recovery generation. The MacBook and
runner update clients use the same controller transaction; see
[runner commands](../klokast-ops/runbooks/07-codex-on-ops.md#air-image-updates).

## Retained home

Replacement retains `/home/agent`, same-machine Tailscale state, and the runner
archive copy. Keep the archive bind read-only. The runner home keeps only
declared state: the work in `src`, the Codex and Claude Code discussions and
logins, the workspace saves, the runner Git key and known hosts, the GitHub CLI
login, the controller contacts, the shell history and the terminal
descriptions. `AIR_HOME` in `vm_infrastructure_storage.py` is the exact list. At
each boot, before any login, the storage service removes every other home entry
and follows no link. Thus a change to `~/.bashrc` or another home file does not
survive a reboot or a replacement. The `air-workspace` role installs the shell,
tmux and Git settings as root-owned files in `/etc`; change that role to make a
setting persistent. Preserve numeric account identities and leave `agent`
outside privileged groups. Reconstruct native tools and managed configuration
from the qualified image and frozen inputs.

The [common lifecycle](platform-updates.md#retained-controller-and-runner-state)
owns the retained data LV, its migration and root retirement.

## Migration from runner containers

Migration preserves runner-owned repositories, uncommitted work, sessions,
configuration, skills and the same operator's runner credentials. Stop source
writers before the final copy and validate SQLite databases and ownership before
destination use. Before destination writes, a failed cutover leaves or restores
the source. After destination writes, keep both copies and reconcile before
rollback. Retired container data and its service definition remain offline
rollback material. Controller placement and naming do not change with runner
migration.
