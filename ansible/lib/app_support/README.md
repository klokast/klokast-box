# Shared application helpers

This is the authoritative catalog for shared application deployment helpers.
The ownership and authority rules are in
[Shared application helpers](../../../doc/architecture.md#shared-application-helpers).

## Available interfaces

- [Tailscale SSH transport](#tailscale-ssh-transport): an executable adapter and
  a Python function for Ansible transport settings.

Only library interfaces listed as available in this catalog are supported for
shared application use. Other modules in `ansible/lib/` are internal Platform
code.

### Tailscale SSH transport

Sources: [`platform-tailscale-ssh`](../../bin/platform-tailscale-ssh) and
[`app_support.tailscale_ssh`](tailscale_ssh.py). The executable is also listed
in the [CLI index](../../../doc/cli-tools.md#platform-and-controller-wrappers).

Run on the active controller as `smith`, with the caller's existing Tailnet
access. The adapter requires POSIX `sh` and `tailscale` on `PATH`. The Python
function requires only Python's standard library; its consumers also require
Ansible. Neither interface selects an application, inventory, or target, or
grants additional authority. Remote commands use the selected target account
and existing Tailscale SSH policy.

**Executable interface:**

```sh
/usr/local/bin/platform-tailscale-ssh -l neo -- boxa-torrent sh -s < script.sh
```

Arguments are SSH options, an optional `--`, one destination, and optional
remote command arguments. A destination can be a hostname, address, or
`user@host`. `-l USER` and `-o User=USER` select the remote user. Matching outer
single or double quotes are removed from usernames, including Ansible's
`User="neo"` form. Repeated usernames must agree with each other and with any
user in the destination. Without an explicit user, Tailscale selects its default.

Options and operands must be separate arguments. `-o` accepts `KEY=VALUE` with
a non-empty value. Keys are case-sensitive. These compatibility options are
consumed but have no effect; Tailscale SSH supplies the connection behavior:

- Operand flags: `-i`, `-F`, `-S`, `-b`, `-c`, `-m`, `-p`.
- Flags without operands: `-C`, `-t`, `-tt`, `-T`, and `-v` repeated within one
  argument, such as `-vvv`.
- `-o` keys: `ControlMaster`, `ControlPersist`, `ControlPath`,
  `StrictHostKeyChecking`, `UserKnownHostsFile`, `ConnectTimeout`,
  `KbdInteractiveAuthentication`, `PreferredAuthentications`,
  `PasswordAuthentication`, `GSSAPIAuthentication`, `BatchMode`, `IdentityFile`,
  `IdentitiesOnly`, and `Port`.

In particular, these options do not configure a port, timeout, private key,
known-hosts file, or terminal allocation. Unknown flags or keys, missing
operands or destinations, and conflicting usernames fail with code `255` and
an error on stderr before Tailscale is invoked. Arguments after the destination
are remote command arguments, not adapter options.

The adapter executes `tailscale ssh` without evaluating the command locally.
It preserves command argument boundaries, stdin, stdout, stderr, and the child
exit status. It writes no success output or command log, creates no temporary
files, and makes no connection before argument validation succeeds. Tailscale
then connects to the caller-selected destination and runs the remote command.

**Python interface:** `ansible_environment(base_env, *, executable=None)`
returns a new environment dictionary. It sets `ANSIBLE_SSH_EXECUTABLE`, disables
multiplexing with `ANSIBLE_SSH_ARGS`, clears `ANSIBLE_SSH_COMMON_ARGS`, selects
`piped` transfers, and sets `ANSIBLE_SSH_USETTY=false`. Other entries, including
`ANSIBLE_SSH_EXTRA_ARGS`, are preserved. Inventory and task variables can take
precedence over Ansible environment settings; callers must use compatible
transport settings there too.

The default executable is `/usr/local/bin/platform-tailscale-ssh`. An explicit
absolute executable path supports isolated tests. An unavailable executable
raises `OSError` with controller-convergence instructions. There is no fallback
to the source executable. The function does not modify its input, write files,
or start processes.

Repository application tools add the repository's `ansible/lib` to their
Python import path. Installed callers use `/usr/local/lib/klokast` instead:

```python
from app_support.tailscale_ssh import ansible_environment

env = ansible_environment(os.environ)
env["ANSIBLE_CONFIG"] = str(app_ansible_config)
env["ANSIBLE_ROLES_PATH"] = app_role_path
subprocess.run(app_playbook_command, env=env, check=True)
```

The controller role's `development-tools.yml` tasks install the executable
as root-owned mode `0755` and Python modules under
`/usr/local/lib/klokast/app_support` as mode `0644`, in a mode `0755` directory.
Controller convergence and development setup use these tasks. Controller
verification checks the installed command and module without a connection.
Source identity follows the repository commit and Platform release; there is
no independent helper version. Converge controller tooling before using updated
application callers. Application installation and removal do not manage helpers.

Consumers: Household VPN's `household-vpnctl` and Torrent's `torrentctl`.
Tests: [`test_app_support_tailscale_ssh.py`](../../tests/test_app_support_tailscale_ssh.py)
and [`test_app_tailscale_transport.py`](../../tests/test_app_tailscale_transport.py).
They use stub commands and isolated application copies without network access.

## Source locations

| Location | Purpose |
| --- | --- |
| `ansible/lib/app_support/` | Shared Python helpers for application deployment tools. |
| `ansible/bin/` | Executable helpers used from shell, Python, or Ansible. |
| `ansible/tests/` | Shared helper behavior tests. |
| `apps/<app>/` | Application callers, configuration, policy, and lifecycle code. |

Paths in this table are relative to the repository root. Add executable helpers
to the existing CLI index and link their entries here. Keep each interface
description in one place.

## When to share code

Before adding a helper, check this catalog and the existing Platform commands.
Use existing interfaces when they provide the required behavior.

Extract shared code when at least two concrete consumers need the same behavior.
Similar source text alone is insufficient. Keep small copies when their intended
behavior differs.

Keep inputs explicit. Do not add application-name branches to a shared helper.
Leave application decisions with the caller. Prefer an executable interface
when callers use different languages; use a Python module when callers need
Python functions. Do not add a separate service or package registry for reuse.

## Catalog entry requirements

For each available helper, document:

- Purpose and canonical source path.
- Supported command or import interface, inputs, outputs, and failure behavior.
- Execution location, account, required authority, and dependencies.
- Side effects, network access, and temporary-file cleanup, where applicable.
- Installation or packaging method and source version identification.
- Current consumers, relevant behavior tests, and a short usage example.

Record planned helpers separately from available interfaces. Add a helper to
the available catalog only when its implementation and delivery are present.

## Delivery and changes

Controller helpers are delivered through the Platform controller toolchain.
Their installation is independent of application installation and removal.
Use the existing repository commit and Platform release process to identify
their source; see [Platform lifecycle](../../../doc/platform-lifecycle.md).
Update the owning Ansible installation tasks when adding executable helpers.

If a helper must run inside an application VM or container, package the required
code into that application's artifact from the canonical source. Record the
source version in the artifact's build metadata. The target must not import
code from another application's directory or a mutable controller checkout.
A helper fix requires rebuilding and delivering the affected artifacts.

For a helper change, check every listed consumer and run the relevant helper
and consumer tests. Test the intended independent use: an application must be
able to use the helper without installing another application. Update affected
callers and delivery tasks with interface changes. No separate SDK versioning
process is required.
