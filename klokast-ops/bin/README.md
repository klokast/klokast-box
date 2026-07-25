# `kk` CLI target

This directory will contain the first `kk` command for Klokast.

For now, `kk` is a small shell wrapper around:

- `git`
- `ssh`
- `tailscale`
- `terraform`
- `ansible`
- local helper scripts

If it grows, it can later move to a dedicated `klokast-cli` repository.

## Repositories

```text
klokast/
├── homebrew-klokast   # Homebrew tap; installs kk
└── klokast-box        # provisions physical Klokast boxes
    └── klokast-ops    # creates/operates the deployment server
```

`homebrew-klokast` should contain the install formula, not the main CLI logic.
That formula must depend on Homebrew `openssh`, because Secret Authority
approval signing uses OpenSSH FIDO/YubiKey key types and macOS' bundled
`ssh-keygen` may not provide the required FIDO provider.

## Vocabulary

```text
ops    = deployment/control server
box    = physical Klokast appliance: mini PC + host OS + VMs + services
host   = OS running directly on the box
vm     = virtual machine running on a box
zone   = network/security boundary
stack  = deployable service group
```

## Command shape

Preferred pattern:

```bash
kk <domain> <action> [target] [flags]
```

Examples:

```bash
kk ops up
kk box up box001
kk vm up router001
kk zone apply dmz
kk tailscale policy pull
```

## MVP commands

```bash
kk help
kk version
kk doctor

kk ops up
kk ops status
kk ops ssh

kk box list
kk box up box001
kk box status box001
kk box ssh box001

kk tailscale policy pull
kk tailscale policy validate
kk tailscale policy apply

kk repo status
kk repo sync
```

## Current direct wrappers

These are not yet folded into `kk`:

- `provision-vultr-ops`: create or converge the `vultr-ops` infra-agent host.
- `provision-vultr-coder-guest`: create or converge a locked guest coding
  account such as `ml-agent` on `vultr-ops`, plus its private GitHub repo.

## Later commands

```bash
kk ops plan
kk ops apply
kk ops destroy

kk box bootstrap box001
kk box apply box001
kk box reboot box001
kk box facts collect box001

kk vm up router001
kk vm ssh router001
kk vm exec router001 -- ip addr

kk zone apply dmz
kk zone test dmz

kk stack deploy monitoring
kk stack status monitoring
```

## Safety rules

- Friendly commands: `up`, `status`, `ssh`
- IaC-style commands: `plan`, `apply`
- Destructive commands: `destroy`, `wipe`, `delete`
- Destructive commands must ask for confirmation unless `--yes` is passed.

## First implementation

Start with:

```text
bin/
├── README.md
└── kk
```

The initial `kk` can be a shell script using:

```bash
set -euo pipefail
```

It should:

- fail clearly
- print useful errors
- avoid storing secrets
- detect missing tools
- detect missing repos
- use explicit paths

## Expected workspace

```text
~/src/klokast/
├── homebrew-klokast
└── klokast-box
    └── klokast-ops
```

## Current preferred interface

```bash
kk ops up
kk box up box001
kk vm up router001
kk zone apply dmz
kk stack deploy monitoring
```
