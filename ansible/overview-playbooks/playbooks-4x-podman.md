- Tasks that don't call a role are written in bracket, as for example `(hostname)`. Such tasks are typically built-in Ansible modules or bash commands.
- The VM identity is the default service-network identity.
- User `root` is locked on the VMs during steady-state convergence.
- These playbooks target shared inventory groups. Run them box-scoped with `--limit <box_name>`, for example `--limit yii` or `--limit duh`.
- Current target path: build one sealed Alpine VIRT Podman template on dom0, clone it into `backend`, `dmz`, and `iot`, optionally remove stale Tailscale machine identities that block the VM names, then personalize identity/network/Tailscale state per guest. Compiler-managed Debian app VMs are handled separately by `79-platform-app-vms.yml`.
- The Podman VMs use the shared VM Tailscale identity tag `tag:vm`. Application containers can later use more specific service tags when they need their own tailnet identity.

# 40-vm-golden-image.yml
Build the sealed Alpine VIRT Podman template LV directly on dom0 without QEMU/HVM.

- `alpine-virt-assets`: download and verify the Alpine VIRT ISO, then extract `vmlinuz-virt`, `initramfs-virt`, the ISO package repository, and the modloop.
- `podman-template-rootfs`:
  - create `/dev/vg0/lv_podman_template`.
  - partition it with a boot partition and root partition `xvda3`.
  - install Alpine VIRT plus first-contact base packages into the mounted root.
  - seed PVH boot artifacts and kernel modules from the Alpine VIRT assets.
  - leave only generic/template identity: no hostname-specific Tailscale state, no SSH host keys, and loopback-only networking.
  - leave Tailscale and Podman installation to playbook 69, after each clone boots as a normal VM.
- `(Persist Podman golden-image build state on the diskless dom0 host)`.

# 41-vm-backend.yml
Clone, personalize, render, and boot the backend Podman VM from the template.

- `network-bridges` and `dom0-bridge-runtime`: keep dom0 bridge config persisted and live.
- `xen-guest-instance`: stop `bak` before clone work.
- `podman-guest-clone`: clone `/dev/vg0/lv_podman_template` into `/dev/vg0/lv_podman_bak`, set hostname `{{ node_name }}-bak`, seed backend static networking on `br-bak`, clear cloned machine state, and seed first-contact root SSH.
- `xen-guest-artifacts`: copy `bak-kernel` and `bak-initramfs` out of the cloned disk.
- `xen-guest`: render `/etc/xen/bak.cfg` in installed stage.
- `xen-guest-instance`: start `bak`.
- `(Persist backend guest state on the diskless dom0 host)`.

# 42-vm-dmz.yml
Clone, personalize, render, and boot the DMZ Podman VM from the template.

- `network-bridges` and `dom0-bridge-runtime`: keep dom0 bridge config persisted and live.
- `xen-guest-instance`: stop `dmz` before clone work.
- `podman-guest-clone`: clone `/dev/vg0/lv_podman_template` into `/dev/vg0/lv_podman_dmz`, set hostname `{{ node_name }}-dmz`, seed DMZ static networking on `br-dmz`, clear cloned machine state, and seed first-contact root SSH. Dom0 reaches the DMZ bootstrap address through the router.
- `xen-guest-artifacts`: copy `dmz-kernel` and `dmz-initramfs` out of the cloned disk.
- `xen-guest`: render `/etc/xen/dmz.cfg` in installed stage.
- `xen-guest-instance`: start `dmz`.
- `(Persist dmz guest state on the diskless dom0 host)`.

# 43-vm-iot.yml
Clone, personalize, render, and boot the IoT Podman VM from the template.

- `network-bridges` and `dom0-bridge-runtime`: keep dom0 bridge config persisted and live.
- `xen-guest-instance`: stop `iot` before clone work.
- `podman-guest-clone`: clone `/dev/vg0/lv_podman_template` into `/dev/vg0/lv_podman_iot`, set hostname `{{ node_name }}-iot`, seed IoT static networking on `br-iot`, clear cloned machine state, and seed first-contact root SSH.
- `xen-guest-artifacts`: copy `iot-kernel` and `iot-initramfs` out of the cloned disk.
- `xen-guest`: render `/etc/xen/iot.cfg` in installed stage.
- `xen-guest-instance`: start `iot`.
- `(Persist iot guest state on the diskless dom0 host)`.

# 44-vm-podman-guests-runtime.yml

Apply or verify controller-compiled runtime intent for existing shared guests.
`shared-xen-guests` maps public roles `bak`, `dmz`, and `iot` to their Xen
definitions, verifies that referenced bridges already exist, persists the
autostart and desired-state metadata changes with `lbu`, then starts or cleanly
stops each selected domain. It never renders or restarts dom0 networking.

# 68-vm-tailscale-stale-machines.yml
Dry-run first cleanup for stale offline Tailscale machine identities that block Podman VM names.

- `tailscale-stale-machines`:
  - now delegates to the generic `tailscale-stale-device` role.
  - list tailnet devices through `/usr/local/sbin/ts-devices-list`.
  - collect local `tailscale status --json` on the deployment server to prove online/offline state.
  - for each targeted Podman VM, classify exact-name matches into `stale_exact`, `online_exact`, `online_suffix`, and `unknown_exact`.
  - delete only offline `stale_exact` entries whose hostname exactly matches `{{ node_hostname }}` and whose tag matches `{{ vm_tailscale_authkey_expected_tag }}`.
  - report online exact-name machines and online suffixed machines such as `k001-bak-1`, but do not delete them.
  - keep exact-name machines when offline state cannot be proven from local Tailscale status.
  - delete selected stale machines only when `tailscale_stale_machine_apply=true` and `tailscale_stale_machine_confirm` matches `delete stale tailscale machines for {{ node_name }}`.
  - delete through `/usr/local/sbin/ts-device-delete-stale`, which re-fetches and verifies the target, including offline state from local Tailscale status, before calling the Tailscale device delete API.

# 69-vm-podman-hosts.yml
Enroll all Podman VMs into Tailscale over their first-contact SSH paths, then converge them as steady-state rootless Podman hosts.

- "Enroll Podman guests into Tailscale over their dom0 bootstrap SSH paths"
  - Check from the controller whether each steady-state Tailscale identity is already online with `tag:vm`; skip bootstrap for hosts already online.
  - For app VMs, require all expected tags, for example `tag:vm` and `tag:user-shell-admin`, before skipping bootstrap enrollment.
  - Clean bootstrap known-hosts entries and wait for root SSH through the dom0 bridge path.
  - `bootstrap-python-alpine`: ensure Python is available on first contact.
  - `vm-base`: create the `neo`/`doas` base access model while keeping root bootstrap access available for the enrollment step.
  - `tailscale-client`: install and start Tailscale.
  - `vm-tailscale-enrollment`: enroll the VM through `/usr/local/sbin/ts-authkey-vm`, set hostname, advertise `tag:vm`, and enable Tailscale SSH.
- "Wait for Podman guests to be reachable over steady-state Tailscale SSH"
  - `wait_for_connection`.
- "Converge Podman guests into steady-state rootless Podman hosts"
  - `tailscale-client`: keep Tailscale converged first.
  - `(reset_connection)`.
  - `vm-base`: lock root, keep the managed `neo` account, and remove bootstrap-only OpenSSH once Tailscale SSH is active.
  - `podman-host`: install and verify rootless Podman, subordinate IDs, cgroup v2, registry policy, and a real bind-mount/container APK probe.
  - `podman-vm-firewall`: enforce a VM-local nftables input baseline so published service ports must be declared explicitly.
  - `vm-egress-verification`: verify host HTTPS egress and container HTTPS egress.

# Legacy development scaffolds
Playbooks 45 through 64 are the older backend/dmz/iot installer-stage and transitional clone scaffolds. They remain useful as historical recovery references, but the current provisioning path for new shared Podman VMs is 40, 41, 42, 43, optionally 68, then 69.
