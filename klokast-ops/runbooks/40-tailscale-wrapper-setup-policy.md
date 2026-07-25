This runbook explains how user `neo` should set up the deployment server so user `codex` can safely pull, validate, and optionally apply the Tailscale tailnet policy, and mint validated one-off machine auth keys, without directly accessing the Tailscale OAuth secret.

In short:
- user `neo` creates the secrets file root-only in the deployment server: `/etc/klokast/tailscale-policy.env`.
- installed scripts are root-owned: `/usr/local/sbin/ts-policy-*` and
  `/usr/local/sbin/ts-authkey-*`
- as `neo`, to check sudo rights:
  - `sudo visudo -c`
  - `sudo -l -U codex`
  - to refresh sudo credentials: `sudo -v`

# 1. In the Tailscale admin console, Human creates a Tailscale OAuth client

with enough rights to allow the wrapper scripts to pull, validate and apply the local policy:

Trust credentials > Add credential > OAuth
  - `policy_file`: read and write
  - `auth_keys`: read and write
  - `devices:core` read
  - `devices:posture_attributes` read and write
 > generate credential > Client ID, Client secret

For machine enrollment, Tailscale also requires tag selection on the OAuth
client. At minimum, the current in-Platform ops migration needs an OAuth client
that can request the `auth_keys` scope and mint `tag:ops` keys. If the same
credential will also enroll the remote infra-agent host, select
`tag:infra` too. If one deployment OAuth client is used for all
`ts-authkey-*` wrappers, also select the managed child tags, or select an owner
tag whose `tagOwners` rules allow it to mint those child tags.

Validate the installed credential before running `provision-ops-vm`:

```
sudo /usr/local/sbin/ts-authkey-ops --check-config --hostname k002-ops --tags tag:ops
```

Expected output:

```
ok
```

This check requests an OAuth token scoped to the exact requested tags. If it
fails with "requested tags ... are invalid or not permitted", the OAuth
credential's tag selection in the Tailscale admin console is still incomplete
for that wrapper.

# 2. Human stores the OAuth credentials on the deployment server
- Don't mix up names. This folder contains another key in `tailscale-devices.env`, for other purposes.
- as user `neo`, create the secret directory outside the git local repository: `sudo install -d -o root -g root -m 0755 /etc/klokast`
- Create the secret file: `sudo nano /etc/klokast/tailscale-policy.env`
- Add the secrets inside:
```
TAILNET_ID="your-tailnet-id"
TS_OAUTH_CLIENT_ID="your-oauth-client-id"
TS_OAUTH_CLIENT_SECRET="your-oauth-client-secret"
```
- Lock the file down:
```
sudo chown root:root /etc/klokast/tailscale-policy.env
sudo chmod 0600 /etc/klokast/tailscale-policy.env
```
- Verify that user `codex` cannot read the file:
  - `sudo ls -l /etc/klokast/tailscale-policy.env`
  - Expected output: `-rw------- 1 root root ... /etc/klokast/tailscale-policy.env`

# 3. Human installs the root-owned wrapper scripts

```
sudo su
cd /home/codex/src/klokast/klokast-box/klokast-ops

sudo install -o root -g root -m 0755 tailscale/bin/ts-policy-pull /usr/local/sbin/ts-policy-pull

sudo install -o root -g root -m 0755 tailscale/bin/ts-policy-validate /usr/local/sbin/ts-policy-validate

sudo install -o root -g root -m 0755 tailscale/bin/ts-policy-apply /usr/local/sbin/ts-policy-apply

sudo install -o root -g root -m 0755 tailscale/bin/ts-authkey-mint /usr/local/sbin/ts-authkey-mint
sudo install -o root -g root -m 0755 tailscale/bin/ts-authkey-bootstrap /usr/local/sbin/ts-authkey-bootstrap
sudo install -o root -g root -m 0755 tailscale/bin/ts-authkey-dom0 /usr/local/sbin/ts-authkey-dom0
sudo install -o root -g root -m 0755 tailscale/bin/ts-authkey-vm /usr/local/sbin/ts-authkey-vm
sudo install -o root -g root -m 0755 tailscale/bin/ts-authkey-ops /usr/local/sbin/ts-authkey-ops
sudo install -o root -g root -m 0755 tailscale/bin/ts-authkey-infra /usr/local/sbin/ts-authkey-infra
sudo install -o root -g root -m 0755 tailscale/bin/ts-authkey-infra-agent /usr/local/sbin/ts-authkey-infra-agent
sudo install -o root -g root -m 0755 tailscale/bin/ts-authkey-back /usr/local/sbin/ts-authkey-back
sudo install -o root -g root -m 0755 tailscale/bin/ts-authkey-dmz /usr/local/sbin/ts-authkey-dmz
sudo install -o root -g root -m 0755 tailscale/bin/ts-authkey-iot /usr/local/sbin/ts-authkey-iot
sudo install -o root -g root -m 0755 tailscale/bin/ts-authkey-streamer /usr/local/sbin/ts-authkey-streamer
sudo install -o root -g root -m 0755 tailscale/bin/ts-authkey-nextcloud /usr/local/sbin/ts-authkey-nextcloud
sudo install -o root -g root -m 0755 tailscale/bin/ts-authkey-immich /usr/local/sbin/ts-authkey-immich
sudo install -o root -g root -m 0755 tailscale/bin/ts-authkey-print /usr/local/sbin/ts-authkey-print
```

Verify: `ls -l /usr/local/sbin/ts-policy-* /usr/local/sbin/ts-authkey-*`
Expected output:
```
-rwxr-xr-x 1 root root ... /usr/local/sbin/ts-policy-apply
-rwxr-xr-x 1 root root ... /usr/local/sbin/ts-policy-pull
-rwxr-xr-x 1 root root ... /usr/local/sbin/ts-policy-validate
```

# 4. Add narrow sudo permissions for `codex`

- Create a sudoers file (still as user `root`): `visudo -f /etc/sudoers.d/codex-tailscale-policy`
- Add:
```
codex ALL=(root) NOPASSWD: /usr/local/sbin/ts-policy-pull
codex ALL=(root) NOPASSWD: /usr/local/sbin/ts-policy-validate /home/smith/private/klokast/tailscale-policy.hujson
codex ALL=(root) NOPASSWD: /usr/local/sbin/ts-policy-apply /home/smith/private/klokast/tailscale-policy.hujson
codex ALL=(root) NOPASSWD: /usr/local/sbin/ts-authkey-bootstrap *
codex ALL=(root) NOPASSWD: /usr/local/sbin/ts-authkey-dom0 *
codex ALL=(root) NOPASSWD: /usr/local/sbin/ts-authkey-vm *
codex ALL=(root) NOPASSWD: /usr/local/sbin/ts-authkey-ops *
codex ALL=(root) NOPASSWD: /usr/local/sbin/ts-authkey-infra *
codex ALL=(root) NOPASSWD: /usr/local/sbin/ts-authkey-infra-agent *
codex ALL=(root) NOPASSWD: /usr/local/sbin/ts-authkey-back *
codex ALL=(root) NOPASSWD: /usr/local/sbin/ts-authkey-dmz *
codex ALL=(root) NOPASSWD: /usr/local/sbin/ts-authkey-iot *
codex ALL=(root) NOPASSWD: /usr/local/sbin/ts-authkey-streamer *
codex ALL=(root) NOPASSWD: /usr/local/sbin/ts-authkey-nextcloud *
codex ALL=(root) NOPASSWD: /usr/local/sbin/ts-authkey-immich *
codex ALL=(root) NOPASSWD: /usr/local/sbin/ts-authkey-print *
```
- save then check:
```
chmod 0440 /etc/sudoers.d/codex-tailscale-policy
visudo -c                  # syntax checker
```

`codex` must not have broad sudo access:
- The output should include:
  - `/etc/sudoers: parsed OK`
  - `/etc/sudoers.d/codex-tailscale-policy: parsed OK`
- Bad output:
```
codex ALL=(root) NOPASSWD: /usr/bin/curl *
codex ALL=(root) NOPASSWD: /bin/bash *
codex ALL=(root) NOPASSWD: /usr/bin/env *
codex ALL=(root) NOPASSWD: ALL
```
- Good output:
```
codex ALL=(root) NOPASSWD: /usr/local/sbin/ts-policy-pull
codex ALL=(root) NOPASSWD: /usr/local/sbin/ts-policy-validate /home/smith/private/klokast/tailscale-policy.hujson
codex ALL=(root) NOPASSWD: /usr/local/sbin/ts-policy-apply /home/smith/private/klokast/tailscale-policy.hujson
codex ALL=(root) NOPASSWD: /usr/local/sbin/ts-authkey-ops *
codex ALL=(root) NOPASSWD: /usr/local/sbin/ts-authkey-infra *
```
