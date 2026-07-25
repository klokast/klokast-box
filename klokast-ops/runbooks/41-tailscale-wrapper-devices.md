# Tailscale Device Lifecycle Wrapper Setup for `neo`

This runbook explains how user `neo` should set up the deployment server so user
`codex` and Ansible can list Tailscale devices and remove stale offline
blockers without directly accessing the Tailscale OAuth secret.

# 1. Create a dedicated Tailscale OAuth client

In the Tailscale admin console, create a separate OAuth client for device lifecycle automation.

Grant only the device lifecycle permission needed by the wrappers:

```text
devices:core
```

When creating the OAuth client, select every tag that this narrow delete path
is allowed to manage:

```text
tag:ops
tag:vm
tag:dom0
tag:bootstrap
tag:back
tag:dmz
tag:iot
tag:streamer
tag:usr
tag:nextcloud
tag:immich
tag:music
tag:music-upload
tag:torrent
```

This scope can list devices and remove machines, so do not reuse the policy
wrapper credential for it.

# 2. Store the OAuth credentials outside git

Create the secret directory if needed:

```bash
sudo install -d -o root -g root -m 0755 /etc/klokast
```

Create the device lifecycle secret:

```bash
sudo nano /etc/klokast/tailscale-devices.env
```

Put this inside:

```bash
TAILNET_ID="your-tailnet-id"
TS_OAUTH_CLIENT_ID="your-device-oauth-client-id"
TS_OAUTH_CLIENT_SECRET="your-device-oauth-client-secret"
```

Lock the file down:

```bash
sudo chown root:root /etc/klokast/tailscale-devices.env
sudo chmod 0600 /etc/klokast/tailscale-devices.env
```

`codex` must not be able to read this file.

# 3. Install the wrapper scripts as root-owned commands

From the ops repository:

```bash
cd ~/src/klokast/klokast-box/klokast-ops
sudo install -o root -g root -m 0755 tailscale/bin/ts-devices-list /usr/local/sbin/ts-devices-list
sudo install -o root -g root -m 0755 tailscale/bin/ts-device-delete-stale /usr/local/sbin/ts-device-delete-stale
```

Verify:

```bash
ls -l /usr/local/sbin/ts-devices-list /usr/local/sbin/ts-device-delete-stale
```

# 4. Add narrow sudo permissions for `codex`

Create a sudoers file:

```bash
sudo visudo -f /etc/sudoers.d/codex-tailscale-devices
```

Add:

```sudoers
codex ALL=(root) NOPASSWD: /usr/local/sbin/ts-devices-list
codex ALL=(root) NOPASSWD: /usr/local/sbin/ts-device-delete-stale --id * --hostname * --tag tag\:vm
codex ALL=(root) NOPASSWD: /usr/local/sbin/ts-device-delete-stale --id * --hostname * --tag tag\:dom0
codex ALL=(root) NOPASSWD: /usr/local/sbin/ts-device-delete-stale --id * --hostname * --tag tag\:bootstrap
codex ALL=(root) NOPASSWD: /usr/local/sbin/ts-device-delete-stale --id * --hostname * --tag tag\:ops
codex ALL=(root) NOPASSWD: /usr/local/sbin/ts-device-delete-stale --id * --hostname * --tag tag\:nextcloud
codex ALL=(root) NOPASSWD: /usr/local/sbin/ts-device-delete-stale --id * --hostname * --tag tag\:immich
codex ALL=(root) NOPASSWD: /usr/local/sbin/ts-device-delete-stale --id * --hostname * --tag tag\:music
codex ALL=(root) NOPASSWD: /usr/local/sbin/ts-device-delete-stale --id * --hostname * --tag tag\:music-upload
codex ALL=(root) NOPASSWD: /usr/local/sbin/ts-device-delete-stale --id * --hostname * --tag tag\:streamer
codex ALL=(root) NOPASSWD: /usr/local/sbin/ts-device-delete-stale --id * --hostname * --tag tag\:torrent
codex ALL=(root) NOPASSWD: /usr/local/sbin/ts-device-delete-stale --id * --hostname * --tag tag\:usr
```

Save, then check:

```bash
sudo chmod 0440 /etc/sudoers.d/codex-tailscale-devices
sudo visudo -c
```

Do not give `codex` broad sudo access to `curl`, shell interpreters, or editable repo scripts.

# 5. Test the wrappers

As `codex`, the list wrapper should return device JSON:

```bash
sudo -n /usr/local/sbin/ts-devices-list
```

The delete wrapper is intentionally narrow. It re-fetches the device list,
checks local `tailscale status --json`, and refuses deletion unless the target
device id is proven offline, has the requested short hostname, and has the
requested tag:

```bash
sudo -n /usr/local/sbin/ts-device-delete-stale --id DEVICE_ID --hostname duh-usr --tag tag:vm
```

If `ts-devices-list` fails with HTTP `401`, the stored OAuth client ID/secret
is not usable. Recreate the dedicated device lifecycle OAuth client, replace
`/etc/klokast/tailscale-devices.env`, and retry. Do not copy the policy
wrapper credential into this file; it should not have device deletion scope.

Prefer the Ansible playbook for normal use:

```bash
cd ~/src/klokast/klokast-box/ansible
ansible-playbook -i inventory/hosts.yml playbooks/68-vm-tailscale-stale-machines.yml --limit duh-iot
ansible-playbook -i inventory/hosts.yml playbooks/68-vm-tailscale-stale-machines.yml --limit duh-iot \
  -e tailscale_stale_machine_apply=true \
  -e 'tailscale_stale_machine_confirm=delete stale tailscale machines for duh'
```

The repo-supported box decommission flow also relies on these wrappers:

```bash
cd ~/src/klokast/klokast-box
ansible/bin/decommission-box --box duh --dry-run-plan
ansible/bin/decommission-box --box duh --to 92 -- -vv
ansible/bin/decommission-box --box duh --from 93 -- -vv
```

That flow deletes stale offline identities for:

- `tag:vm`: `duh-router`, `duh-bak`, `duh-dmz`, `duh-iot`, optional `duh-usr`
- `tag:dom0`: `duh-dom0`
- `tag:bootstrap`: `duh-bootstrap`
- `tag:streamer`: app-managed Raspberry Pi streamer endpoints such as `duh-streamer`
