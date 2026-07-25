# Resolver

- to list stale devices from the Tailscale network, run `/usr/local/sbin/ts-devices-list`

- to remove stale devices from the Tailscale network, run `/usr/local/sbin/ts-device-delete-stale`

- To update Tailscale Access Control policy:
  1. Read Tailscale API documentation on `https://tailscale.com/api`.
  2. Edit public topology and grants in `policy.hujson.j2`; keep family
     identities in `~/private/klokast/deployment.yml`.
  3. Render with `ansible/bin/render-tailscale-policy --deployment-config
     ~/private/klokast/deployment.yml --output
     ~/private/klokast/tailscale-policy.hujson`.
  4. Validate with `sudo /usr/local/sbin/ts-policy-validate
     /home/smith/private/klokast/tailscale-policy.hujson`.
  5. Apply only after review with `sudo /usr/local/sbin/ts-policy-apply
     /home/smith/private/klokast/tailscale-policy.hujson`.
  6. `sudo /usr/local/sbin/ts-policy-pull` imports the live API policy into
     that private path for comparison; it never writes identities into Git.

- To diagnose connectivity issues:
  - check Tailscale Access Control in
  - run `tailscale status`
  - check latencies between machines
  - check that the traffic is not routed via a DERP server

- To modify the code of the narrow Tailscale sudo wrappers:
  1. edit, commit and push the code in git in `klokast-ops/tailscale/bin`
  2. tell the human to follow instructions in `klokast-ops/runbooks/05-tailscale-on-ops.md` to deploy the updated code onto the deployment server, as user `neo`.

- To rotate the Tailscale OAuth credentials that are used to enroll tagged Tailscale machines, tell the human follow the instructions in `klokast-ops/runbooks/40-tailscale-wrapper-setup-policy.md` to edit `/etc/klokast/tailscale-policy.env`

- for questions about how the user can work with the Tailscale Access Control policy, first read `klokast-ops/runbooks/40-tailscale-wrapper-setup-policy.md`.

# Tailscale secrets and wrappers in the deployment server

## source code
The git repository, in `klokast/klokast-box/klokast-ops/tailscale/bin`, contains the editable source code of the Tailscale wrappers.
- `ts-devices-list`
- `ts-device-delete-stale`
- `ts-policy-apply`
- `ts-policy-pull`
- `ts-policy-validate`
- `ts-authkey-mint`
- `ts-authkey-nextcloud`
- `ts-authkey-ops`
- `ts-authkey-infra`
- `ts-authkey-infra-agent` (legacy tag name)
- `ts-authkey-airunner`
- `ts-authkey-immich`
- `ts-authkey-music`
- `ts-authkey-music-upload`
- `ts-authkey-print`
- `ts-authkey-streamer`
- `ts-authkey-vm` for `<box>-torrent` with `tag:vm,tag:torrent`
- `ts-authkey-vm` for `<box>-household-vpn` with `tag:vm,tag:household-vpn`
- `ts-authkey-usr`
This source code is editable by user `codex`. It must not be executed with sudo directly from the repo.

## executable scripts
- Executable root-owned copies of the wrappers are installed in the deployment server under `/usr/local/sbin/`.
- Secrets are in the development server in `/etc/klokast/tailscale-policy.env`
- The wrappers and secrets got installed manually in the deployment server by user `neo`, as per `klokast-box/klokast-ops/runbooks/00-hetzner-tailscale-mosh-tmux-codex.md` and `klokast/klokast-ops/runbooks/40-tailscale-wrapper-setup-policy.md`.
- This ensures that the key is not readable by user `codex`, but that `codex` can still read and write the Tailscale Access Control policy.

# Tailscale policy
- Edit the public template for topology/grant changes and the private
  deployment file for family membership. The wrappers accept only
  `/home/smith/private/klokast/tailscale-policy.hujson`.

# using the tailscale scripts
- run the stale-device cleanup playbook first in dry-run mode for one target host
- only run the apply command after the dry-run shows the exact offline blocker selected for deletion
- for whole-box decommission, prefer `ansible/bin/decommission-box --box duh` from the repo root instead of calling the wrappers directly

# device lifecycle delete scope
- the managed delete path supports exact-name stale devices tagged `tag:vm`, `tag:dom0`, `tag:bootstrap`, `tag:ops`, `tag:infra`, `tag:airunner`, `tag:back`, `tag:dmz`, `tag:iot`, `tag:streamer`, `tag:usr`, `tag:nextcloud`, `tag:immich`, `tag:music`, `tag:music-upload`, `tag:print`, `tag:torrent`, and `tag:household-vpn`
- the Ansible role owns device classification; it lists devices through `ts-devices-list`, proves online/offline state from local `tailscale status --json`, and deletes only `stale_exact`
- documented sudoers entries for `codex` live in `klokast-ops/runbooks/41-tailscale-wrapper-devices.md`

# Onboarding of machines to Tailscale

To onboard a machine to Tailscale: don't run commands manually. Rather, run a suitable Ansible playbook. This way, `tailscale up` runs on the target machine. It must not run locally on the deployment server.

The Ansible controller (`tag:ops`) contains root-only Tailscale wrappers. If
the infra agent runs elsewhere, that host uses `tag:infra` and only gets
the explicit SSH path to `tag:ops`.
An in-Platform AI runner container uses `tag:airunner`: operators may SSH to
it as `agent` or `neo`, and it may SSH to `tag:ops` as `smith`.
The target model is OAuth-backed one-off auth-key minting: a wrapper validates
the purpose, hostname, and requested tag set, then prints a short-lived,
single-use `tskey-auth-...` value. App users and app sandboxes must not read
OAuth material or call these minting paths directly.

The `ts-authkey-*` wrappers are root-owned OAuth-backed minting wrappers. They
validate purpose, hostname, and the complete advertised tag set, then mint a
short-lived, single-use auth key through `ts-authkey-mint`.
`--check-config` must be run with the same hostname and tags as the real
enrollment call; it verifies that the OAuth credential can request an
`auth_keys` token scoped to that exact tag set.

Reusable file-backed auth keys under `/etc/tailscale-auth/` are transitional
TCB debt only. They must not be installed on the in-Platform `<box>-ops`
controller.

Legacy transitional setup:

1. Human admin creates reusable pre-authorized Tailscale auth keys. Only the `tag:bootstrap` key is ephemeral.
  Tailscale web console > Settings > Personal Settings > Keys > Generate Auth Key > `tskey-auth-*` string

2. `neo` ssh into `ops` deployment server and saves these keys root-only:

```
# replace with the right key names:
sudo vi /etc/tailscale-auth/ts-auth-*.authkey
sudo chown root:root /etc/tailscale-auth/ts-auth-*.authkey
sudo chmod 0600 /etc/tailscale-auth/ts-auth-*.authkey
```

3. User `neo` exposes to user `codex` only the narrow wrapper commands stored in `/usr/local/sbin/`:

|---------------------------|----------------------|------------------------------------------------------------------|
|           Key             |      Wrapper         | Machines to onboard      / playbook, role, task                  |
|in `/etc/tailscale-auth/`  |in `/usr/local/sbin/` |                                                                  |
|---------------------------|----------------------|------------------------------------------------------------------|
|`ts-auth-bootstrap.authkey`|`ts-authkey-bootstrap`|`bootstrap`   / 12,`diskless-apkovl`,`apkovl-bootstrap-tailscale` |
|`ts-auth-dom0.authkey`     |`ts-authkey-dom0`     |`dom0`        / ??,`tailscale-handoff`,`main`                     |
|`ts-auth-vm.authkey`       |`ts-authkey-vm`       |`router`      / 30,
|                           |                      |`dmz`,`back`,`iot`,`usr`                                 |
|`ts-auth-ops.authkey`      |`ts-authkey-ops`      |`<box>-ops`   / 65,`ops-controller`                      |
|[legacy file not created]  |`ts-authkey-infra`      |standalone infra-agent host                            |
|[legacy file not created]  |`ts-authkey-airunner` |`<box>-ops-airunner` / 68,`ops-airunner`                  |
|`ts-auth-dmz.authkey`      |`ts-authkey-dmz`      |`dmz` containers                                                  |
|`ts-auth-back.authkey`     |`ts-authkey-back`     |`back` containers                                                 |
|`ts-auth-iot.authkey`      |`ts-authkey-iot`      |`iot` containers                                                  |
|[legacy file not created]  |`ts-authkey-usr`      |`usr` containers                                                |
|`ts-auth-nextcloud.authkey`|`ts-authkey-nextcloud`|Nextcloud private ingress as `next` / `tag:nextcloud`             |
|`ts-auth-immich.authkey`   |`ts-authkey-immich`   |Immich private ingress as `photos` / `tag:immich`                 |
|`ts-auth-music.authkey`    |`ts-authkey-music`    |Music private ingress as `<box>-music` / `tag:music`              |
|`ts-auth-music-upload.authkey`|`ts-authkey-music-upload`|Music upload ingress as `<box>-music-upload` / `tag:music-upload`|
|[legacy file not created]  |`ts-authkey-print`    |Print ingress as `<box>-print` / `tag:print`                      |
|[legacy file not created]  |`ts-authkey-streamer` |Raspberry Pi streamer as `<box>-streamer` / `tag:streamer`        |
|---------------------------|----------------------|------------------------------------------------------------------|

- The code of the wrappers is in `~/src/klokast/klokast-box/klokast-ops/tailscale/bin`.
- The OAuth-backed wrappers must be called with explicit hostname and tags, for example:
  ```sh
  sudo /usr/local/sbin/ts-authkey-vm --hostname k001-usr-alice --tags tag:vm,tag:user-shell-alice
  sudo /usr/local/sbin/ts-authkey-usr --hostname k001-usr-shell --tags tag:usr
  sudo /usr/local/sbin/ts-authkey-ops --hostname k001-ops --tags tag:ops
  sudo /usr/local/sbin/ts-authkey-ops --hostname k001-ops --tags tag:ops,tag:infra
  sudo /usr/local/sbin/ts-authkey-infra --hostname codex --tags tag:infra
  sudo /usr/local/sbin/ts-authkey-airunner --hostname k002-ops-airunner --tags tag:airunner
  sudo /usr/local/sbin/ts-authkey-music --hostname k002-music --tags tag:music
  sudo /usr/local/sbin/ts-authkey-music-upload --hostname k002-music-upload --tags tag:music-upload
  sudo /usr/local/sbin/ts-authkey-print --hostname k002-print --tags tag:print
  sudo /usr/local/sbin/ts-authkey-streamer --hostname k002-streamer --tags tag:streamer
  ```

- Here the names of the variables in the `klokast-box/ansible/inventory/group_vars/*.yml` files, that define the paths to the wrappers (`/usr/local/sbin/ts-authkey-*`):
  - `bootstrap.yml`: `diskless_first_boot_authkey_wrapper`
  - `dom0.yml`:      `tailscale_handoff_authkey_wrapper`
  - `router.yml`:    `vm_tailscale_authkey_wrapper`
  - `backend.yml`:   `vm_tailscale_authkey_wrapper`
  - `dmz.yml`:       `vm_tailscale_authkey_wrapper`
  - `iot.yml`:       `vm_tailscale_authkey_wrapper`
  - `ops.yml`:       `vm_tailscale_authkey_wrapper`
  - `usr.yml`:       `vm_tailscale_authkey_wrapper`

 For reference, here are legacy manual command shapes. Normal onboarding
 should go through Ansible, not these commands.
   - dom0 bootstrap from the ops server:
  ```sh
  TS_AUTH_KEY="$(sudo /usr/local/sbin/ts-authkey-bootstrap --hostname duh-bootstrap --tags tag:bootstrap)"
  ssh root@duh-bootstrap.example.ts.net "tailscale up --auth-key='${TS_AUTH_KEY}' --hostname=duh-bootstrap --advertise-tags=tag:bootstrap --ssh"
  unset TS_AUTH_KEY
  ```
  - steady-state dom0 handoff from the ops server:
  ```sh
  TS_AUTH_KEY="$(sudo /usr/local/sbin/ts-authkey-dom0 --hostname duh-dom0 --tags tag:dom0)"
  ssh root@duh-dom0.example.ts.net "tailscale up --auth-key='${TS_AUTH_KEY}' --hostname=duh-dom0 --ssh"
  unset TS_AUTH_KEY
  ```
  - backend VM from the ops server:
  ```sh
  TS_AUTH_KEY="$(sudo /usr/local/sbin/ts-authkey-vm --hostname yii-bak --tags tag:vm)"
  ssh -J neo@yii-dom0.example.ts.net neo@192.168.100.10 "doas tailscale up --auth-key='${TS_AUTH_KEY}' --hostname=yii-bak --advertise-tags=tag:vm --ssh"
  unset TS_AUTH_KEY
  ```
  - router VM from the guest console or another trusted path:
  ```sh
  TS_AUTH_KEY="$(sudo /usr/local/sbin/ts-authkey-vm --hostname duh-router --tags tag:vm)"
  doas tailscale up --auth-key="${TS_AUTH_KEY}" --hostname=duh-router --advertise-tags=tag:vm --ssh
  unset TS_AUTH_KEY
  ```
