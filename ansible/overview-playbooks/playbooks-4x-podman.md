- Tasks that don't call a role are written in bracket, as for example `(hostname)`. Such tasks are typically built-in Ansible modules or bash commands.
- The VM identity is the default service-network identity.
- User `root` is locked on the VMs during steady-state convergence.
- These playbooks target shared inventory groups. Run them box-scoped with `--limit <box_name>`, for example `--limit yii` or `--limit duh`.
- BAK, DMZ and IoT use the qualified image path through playbooks 41–43. The older template and enrollment paths remain for their app-VM callers. Compiler-managed Debian app VMs use `79-platform-app-vms.yml`.
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
- `dom0-apk-policy`: permit the image tools only in RAM, then remove them and restore the exact world before persistence.

# 41-vm-backend.yml

Dispatch `platform-update provision --box BOX --role bak` on the active
controller. BAK uses the same qualified image, persistent storage, enrollment
and acceptance workflow as DMZ and IoT. Image preparation requires the target
box's own controller. An existing accepted guest is verified without replacement.

See [shared guest creation and reconstruction](../../doc/platform-updates.md#dmz-creation-and-reconstruction).

# 42-vm-dmz.yml

Dispatch `platform-update provision --box BOX --role dmz` on the active
controller. Image preparation runs on the target box's own controller.
Creation and replacement use the same image personalization and
`69-dmz-configure.yml`. Enrollment uses the tasks shared with VPN provisioning.
The provision-box phase 69 excludes BAK, DMZ and IoT.

See [DMZ creation and reconstruction](../../doc/platform-updates.md#dmz-creation-and-reconstruction)
for persistent storage, first transition and recovery commands.

# 43-vm-iot.yml

Dispatch `platform-update provision --box BOX --role iot` on the active
controller. It uses the same qualified image, persistent data configuration,
enrollment and acceptance workflow as DMZ. An accepted IoT VM is verified.
An absent VM is created; an interrupted creation resumes its frozen inputs.

See [shared guest creation and reconstruction](../../doc/platform-updates.md#dmz-creation-and-reconstruction).

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
  - report online exact-name machines and online suffixed machines such as `boxa-bak-1`, but do not delete them.
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
  - `podman-host`: install and verify rootless Podman, subordinate IDs, cgroup v2, and registry policy. The image download and container package probe require the explicit `podman_host_container_probe` test input.
  - `podman-vm-firewall`: enforce a VM-local nftables input baseline so published service ports must be declared explicitly.
  - `vm-egress-verification`: verify host HTTPS egress. The optional `podman-host` container package probe checks container egress with a temporary test image.
