Klokast is an automated deployment for a secure multisite homelab.
In this `README.md` you find the instructions for the human admin. The AI agent rather reads `AGENTS.md`.

# 1. Hardware
Get one or more mini-PCs.
Get, ideally, one NanoKVM for each mini-PC.
Connect the devices.

# 2. Git
1. Create a GitHub account
2. Fork the Klokast project

For brokered app automation, create a GitHub App for the Klokast organization
instead of using a long-lived personal token. The static-site Secret Authority
pilot stores that app private key root-only on `<box>-ops` and mints short-lived
installation tokens only inside checked action wrappers. See
`doc/secret-authority.md`.

The private instance repository uses a different, temporary GitHub App with
Administration permission and no Contents permission. The human authors and
pushes that private repository from a trusted workstation. Approved airunners
continue to author and push the public `klokast/klokast-box` repository. See
the private-instance bootstrap procedure in `doc/secret-authority.md`.

# 3. Hetzner
1. Create a Hetzner account.
2. Generate one API key.

# 4. Build the deployment server
From a Macbook:
1. install `brew`
2. `brew install klokast` : this should create and provision the deployment server (using terraform and Ansible). Secret Authority approval on the trusted Mac uses Apple system OpenSSH with a native, Touch ID-protected CryptoTokenKit identity. The package must not replace this signer path with an ambient or long-running SSH agent or another OpenSSH build. A private Apple agent can run only for the duration of one identity-selection and signing operation.

Optional cloud bootstrap host or cloud-based `airunner`:
- `klokast-ops/bin/provision-vultr-ops` creates a cloud-based host named `<cloud>-ops` (where `<cloud>` is, for example, `vultr` or `hetzner`).
- During initial bootstrap, the host can run both the coding agent and the controller. It then has `tag:ops` and holds controller-private state.
- After the controller moves to `<box>-ops`, fence its former controller authority. A retained cloud coding runner uses `tag:infra` and must not store Platform private state or Tailscale OAuth material.
- The cloud host belongs to the TCB while it exists. No `<cloud>-ops` host is running in the current deployment.
- It does not retain a Go toolchain. Dependency maintenance may use a
  checksum-pinned temporary toolchain that is removed afterward; deployable
  `klokast` binaries come only from the controller-owned Xen builder.

# 5. Tailscale
Create the Tailscale account and install deployment-server wrappers. The secure
target is root-owned OAuth-backed wrappers that mint short-lived, single-use
auth keys after validating purpose, hostname, and tags. Any reusable auth-key
file is transitional TCB debt and must stay root-only outside git and outside
app sandboxes.

The target in-platform controller splits local automation accounts:
`smith` owns infrastructure credentials and apply authority;
`minion` runs app install/verify workflows without access to those
credentials.

1. Create a Tailscale account.
2. Onboard laptop and deployment server
3. On the deployment server, install the root-owned machine-onboarding wrappers described in `tailscale/AGENTS.md`:
- `ts-authkey-mint`
- `ts-authkey-bootstrap`
- `ts-authkey-dom0`
- `ts-authkey-vm`
- `ts-authkey-ops`
- `ts-authkey-infra`
- `ts-authkey-infra-agent` (legacy tag name)
- `ts-authkey-back`
- `ts-authkey-dmz`
- `ts-authkey-iot`
- `ts-authkey-streamer`
- `ts-authkey-nextcloud`
- `ts-authkey-immich`
- `ts-authkey-music`
- `ts-authkey-music-upload`
- `ts-authkey-print`
4. Install the corresponding scoped OAuth material outside git on the
   deployment server, in `/etc/klokast/tailscale-policy.env`. Transitional
   reusable auth-key files under `/etc/tailscale-auth/` are legacy only.
5. If you want repo-driven policy pull/validate/apply, also create a separate Tailscale OAuth client and install the `ts-policy-*` wrappers as described in `tailscale/tailscale-policy-wrapper-setup-for-neo.md`
6. If you want Ansible to remove stale offline Tailscale machines that block VM hostnames, create a separate device-lifecycle OAuth client and install the `ts-devices-list` and `ts-device-delete-stale` wrappers as described in `klokast-ops/runbooks/41-tailscale-wrapper-devices.md`.

# 6. Build the Debian Live bootstrap ISO
Build one generic Debian Live bootstrap ISO from the `bootstrap-live-builder`
Podman container on `yii-bak`. It must not contain the box name or the
Tailscale auth key.

```sh
cd /home/codex/src/klokast/klokast-box
ansible/bin/bootstrap-live-iso all --resources-registry path/to/platform-resources.yml
```

The builder is a privileged, short-lived exception. Before `build-builder` or
`all`, enable `bootstrap-iso-debian` in a private platform-resource registry
based on `ops/platform-resources.example.yml`, with `privileged_approval`,
`expires_at`, and `cleanup_required`.

That converges the builder, builds the ISO inside the `yii-bak` Podman builder
container, transfers the ISO and SHA512 checksum directly from the builder container
to `root@oob.<tailnet>.ts.net:/data/`, then stops and removes the builder.
The tailnet MagicDNS suffix comes from `tailscale status --json`; the NanoKVM
Tailscale IP is not hardcoded.

The ISO boots into Debian Live, gets DHCP, publishes `kk.local` and
`klokast.local` on the LAN through mDNS, and serves the onboarding portal.
At boot, paste a `tskey-auth-*` key into the portal. The key itself does not
carry tags; the portal runs `tailscale up --ssh --hostname=<box>-bootstrap
--advertise-tags=tag:bootstrap --auth-key=<key>`. The key must be generated by
a tailnet Owner/Admin/Network admin that can assign `tag:bootstrap`.
If Tailscale assigns a suffixed bootstrap name, the portal asks for a new box
name without asking for the auth key again and rejects names visible in
`tailscale status --json`.

# 7. Provision the first box

Load the generic Debian Live bootstrap ISO into the NanoKVM. This can be done
through the NanoKVM UI or from the deployment server:

```sh
BOOTSTRAP_ISO_URL='https://downloads.example.invalid/klokast-bootstrap-debian-trixie-amd64.iso'
ansible/bin/nanokvm-virtual-media --status
ansible/bin/nanokvm-virtual-media --upload-url "$BOOTSTRAP_ISO_URL" \
  --as klokast-bootstrap-debian-trixie-amd64.iso
ansible/bin/nanokvm-virtual-media --load klokast-bootstrap-debian-trixie-amd64.iso --verify-sha512-sidecar
```

Boot the mini-PC from the generic Debian Live bootstrap ISO. From a machine on
the same DHCP LAN, open `http://kk.local/` or `http://klokast.local/`, enter
the box name such as `k001`, and paste the `tskey-auth-*` key. Confirm it
joined Tailscale as `<box>-bootstrap`, then run from the deployment server:

```sh
cd /home/codex/src/klokast
klokast-box/ansible/bin/provision-box --box <box>
```

The script renders a temporary box inventory, then runs the numbered playbooks
through dom0, Xen, router, and Podman VM provisioning. It has operator gates
before wiping the SSD. Before phase `14`, it unloads the NanoKVM ISO
programmatically so the mini-PC boots Alpine diskless from the SSD. To bypass
only the phase `11` SSD wipe prompt after verifying the target box and disk,
pass `--yes`:

```sh
klokast-box/ansible/bin/provision-box --box <box> --yes
```

If NanoKVM SSH is unavailable, use the manual fallback:

```sh
klokast-box/ansible/bin/provision-box --box <box> --manual-iso-detach
```

To inspect the plan or resume after a fixed failure:

```sh
klokast-box/ansible/bin/provision-box --box <box> --dry-run-plan
klokast-box/ansible/bin/provision-box --box <box> --from 20
```

To wipe and reinstall an existing managed box in one operator flow, use:

```sh
ansible/bin/reinstall-box --box <box>
```

That helper reads NanoKVM `--status`, selects the newest valid Klokast Debian
bootstrap ISO unless `--bootstrap-iso` is provided, loads it with
`--verify-sha512-sidecar`, runs `decommission-box --final-action reboot`,
waits while the operator enrolls the rebooted ISO as `<box>-bootstrap`, then
runs `provision-box`.

# 8. Create the in-Platform ops controller

The `vultr-ops` cloud VPS is an approved Codex/OpenAI runner.
The in-Platform `<box>-ops` VM is the trusted infrastructure controller for TCB
automation and private Platform state. It dispatches reviewed `klokast` CLI
builds to a short-lived networkless Xen builder; it does not run the CLI's Go
compiler or tests itself.

After the box has dom0, router, and the Alpine VM template, make sure the
current controller has `/etc/klokast/tailscale-policy.env` and
`/etc/klokast/tailscale-devices.env`. The OAuth client stored in
`/etc/klokast/tailscale-policy.env` must be able to request the `auth_keys`
scope and mint `tag:ops` auth keys. Verify before migration:

```sh
sudo /usr/local/sbin/ts-authkey-ops --check-config --hostname k002-ops --tags tag:ops
```

The playbook installs the checked-in Tailscale wrappers locally before
enrolling `<box>-ops`, then run:

```sh
ansible/bin/provision-ops-vm --box <box>
```

`<box>-ops` clones the public source repository over HTTPS and does not hold a
GitHub credential. Coding airunners keep separate write-enabled SSH identities.
Deployment-specific configuration stays under
`/home/smith/private/klokast/`, outside the public checkout.

The same run also migrates `/home/codex/private/klokast/` into
`/home/smith/private/klokast/`, copies the root-only Tailscale OAuth
files into `/etc/klokast/`, and removes legacy reusable auth-key files from the
new controller.

The resulting VM has two local automation accounts:

- `smith`: infrastructure authority, private state, wrappers, builders,
  `platform-resources apply`, dom0/router authority.
- `minion`: app installation and verification without infrastructure
  credentials.

The current in-Platform runner is an `agent`-owned container on `k002-ops`.
This controller-container placement is a supported steady state:

```sh
ansible/bin/converge-ops-airunner --box k002 --instance candidate
# After Mac-side candidate Mosh verification:
ansible/bin/converge-ops-airunner \
  --box k002 --instance canonical --require-candidate-ready
# After Mac-side canonical Mosh verification:
ansible/bin/retire-ops-airunner-candidate --box k002
```

Codex auth, GitHub access, `smith@k002-ops` remote-terminal access, session
archiving, and negative secret-read checks must pass from
`k002-ops-airunner`. Multiple runners can be approved at once, but keep the set
small because each runner is a persistent Control TCB authority. A dedicated
`<box>-airunner` Xen VM is optional hardening. It is not a required migration
target. During blue-green container replacement, the candidate is
`<box>-ops-airunner-candidate`; both containers temporarily share `/home/agent`,
so do not mutate Git or Codex state from both at once.
The shared home persists the managed Git checkout, runner-owned GitHub key,
Codex state, and tmux configuration across container replacement; these files
are not baked into the image. Existing checkouts are validated but never
updated or reset by airunner convergence.

# 9. Decommission one box

To remove one managed box from the live platform without deleting its inventory
or repo metadata, run from the ops server:

```sh
cd /home/codex/src/klokast/klokast-box
ansible/bin/decommission-box --box duh
```

The script stops every Xen guest on `duh-dom0`, removes stale offline
Tailscale identities for non-dom0 `duh-*` machines, wipes the SSD from
diskless `duh-dom0`, runs the requested final action, and removes stale offline
Tailscale identities for every remaining `duh-*` machine.

To stop before the destructive dom0 SSD wipe:

```sh
ansible/bin/decommission-box --box duh --to 92 -- -vv
ansible/bin/decommission-box --box duh --from 94 -- -vv
```

To inspect the selected phases and gates without running anything:

```sh
ansible/bin/decommission-box --box duh --dry-run-plan
```

By default the box powers off after the SSD wipe. If NanoKVM is already set to
boot the bootstrap ISO, reboot after the wipe instead:

```sh
ansible/bin/decommission-box --box duh --final-action reboot -- -vv
```

Decommission is dom0-only. It does not use or recreate a `duh-bootstrap`
machine, and it does not recreate the managed GPT/EFI/LVM layout.
