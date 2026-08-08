# Nextcloud

The old root-level `80-nextcloud.yml` private proof-of-concept was removed.
Nextcloud automation now lives under `apps/nextcloud/` and is launched through
`apps/nextcloud/bin/nextcloudctl` with explicit active master and passive
backup box names.

# 80-platform-resources.yml

Applies platform-owned app resource policy from a deployment registry compiled
by `ansible/bin/platform-resources`. This is the preferred control-plane path
for app work. It renders router and Podman VM firewall include files from
symbolic app zone declarations, then records apply provenance under
`/etc/klokast/platform-resources/`.

The wrapper uses Ansible for router-side convergence and target-local
`tailscale ssh` execution for steady-state Podman VMs. Podman VMs are expected
to rely on Tailscale SSH rather than raw OpenSSH after their baseline is
converged.

The TCB boundary for this flow is defined in `doc/architecture.md`. App
install playbooks verify and consume applied resources; they do not own raw
topology, Tailnet ownership, dom0 state, or privileged builder placement.

- `router`: keeps the router baseline converged, including the stable
  `/etc/klokast/app-resources/router-forward.nft` compatibility anchor, then
  applies keyed router-forward snippets under `router-forward.d/`.
- `podman-vm-firewall`: owns the Podman VM firewall baseline and stable
  `/etc/klokast/app-resources/vm-input.nft` compatibility anchor; platform
  resources fail closed if that baseline is missing and apply keyed VM-input
  snippets under `vm-input.d/`.
- `app-resources-verification`: checks persisted include files and the live
  nftables ruleset for every expected compiled rule.

App install playbooks must not mutate router or VM firewall policy directly.
They should verify that required app resources are already applied.

# 81-platform-resources-verify.yml

Verification-only variant of `80-platform-resources.yml`. It checks the
persisted and live platform-resource rules without reconverging router or
Podman VM baselines.


# 81-bootstrap-iso-alpine-builder.yml

Removed. The Alpine `mkimage` bootstrap ISO builder was replaced by the
portal-enabled Debian live builder in `82-bootstrap-iso-debian-builder.yml`.

# 82-bootstrap-iso-debian-builder.yml

Deploys the Debian-live bootstrap builder workload on the backend Podman host.
The build helper produces a generic, secret-free Debian bootstrap ISO with a compiled Go onboarding portal. It does not accept node names or Tailscale auth keys as build inputs.

- Deploy the bootstrap ISO builder on the `yii-bak` Podman host.
- Validate the Podman baseline first, then exposes the host-side builder entrypoint for stage-1 images.
- Prepare the parallel Debian-live bootstrap builder on `yii-bak`. It keeps the repo/output workspace under the backend admin account, but the actual build container is a privileged
rootful exception because Debian `live-build` requires mount-capable chroot stages. It builds two artifacts:
  - the Debian live stage-1 ISO
  - the Alpine diskless seed tarball later consumed by `ansible/playbooks/12-bootstrap-diskless-build.yml`
That split keeps the migration narrow: replace the fragile stage-1 carrier without forcing a full dom0-install pipeline rewrite at the same time.
- The builder workload does not store the bootstrap auth key in inventory. After the service is up, the operator uses `/usr/local/sbin/bootstrap-live-builder-build` on `yii-bak` to build the generic ISO and optional Alpine seed. The operator pastes the raw bootstrap auth key only into the booted portal.
- The builder container has its own Tailscale identity tagged `tag:back`, enrolled through the existing `/usr/local/sbin/ts-authkey-back` wrapper, so artifact upload can follow the narrow `tag:back -> tag:oob` policy instead of widening the shared `tag:vm` VM policy.
- Artifact upload to NanoKVM runs from inside the builder container through the full NanoKVM MagicDNS name, for example `root@oob.<tailnet>.ts.net:/data/`. The tailnet suffix comes from `tailscale status --json`; the Tailscale IP is not hardcoded.

# Dedicated App VMs

Some applications request dedicated Debian PVH app VMs through the
`per_user_app_vm` resource type. The compiler renders the VM inventory,
firewall grants, and Tailnet tags from the app manifest plus the private
deployment registry. App-specific automation then consumes the approved grant;
it does not mutate dom0/router/firewall baselines directly.

`79-platform-app-vms.yml` handles the per-user Debian app VM lifecycle:

- `debian-app-vm-image-builder` runs on `<box>-bak`, builds per-guest Debian
  ext4 root images with host-local `debootstrap`, and serves artifacts
  temporarily on the backend bridge.
- `debian-app-vm-rootfs` runs on `<box>-dom0`, fetches the builder manifest and
  artifacts over the local backend bridge, verifies SHA256 checksums, writes the
  ext4 image to the target LV, resizes it, and copies kernel/initramfs to dom0
  Xen image storage.
- The backend artifact service and its temporary live nftables rule are stopped
  before app VM Tailscale enrollment starts.

This backend builder path is transitional. During the build, the selected
`<box>-bak` is TCB-adjacent because it produces trusted app-VM images. The
target placement is `<box>-ops` or a short-lived builder VM controlled by
`<box>-ops`.

# Future storage-aware application playbooks

Application playbooks that need placement decisions from available box SSD
capacity should run `roles/storage-inspection` against the candidate dom0 hosts.
The role is read-only and publishes structured facts for disks, partitions,
volume groups, logical volumes, mounts, and the managed Platform SSD layout.
