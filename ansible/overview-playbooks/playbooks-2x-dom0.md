# 20-dom0-base-bootstrap.yml
Use the bootstrap `root` Tailscale identity to converge the steady-state dom0 base before the identity handoff.
- Refresh the controller bootstrap known-hosts entry, then assert phase 20 is really connected to the Alpine SSD boot enrolled as `{{ bootstrap_tailscale_hostname }}` with `{{ bootstrap_tailscale_tag }}`.
- `alpine-base`: detect the live EFI mountpoint, remount it read-write if needed, repoint `/etc/apk/cache` and `LBU_BACKUPDIR`, exclude the EFI and dom0 data mountpoints from `lbu`, repair `/` to mode `0755`, keep only `{{ apkovl_archive_name }}` at the EFI root by moving stale `*.apkovl*` files into `_disabled/`, rewrite the steady-state non-Xen `grub.cfg`, configure Alpine repositories, remove the temporary first-boot bootstrap helper, and configure `/etc/motd`.
- `dom0-apk-policy`: install the complete runtime and recovery package set, replace checksum constraints and other drift with the exact `dom0_world_packages` list, remove maintenance-only packages, install the APK transaction guard, and remove the RAM-only unlock.
- `system-identity`: set `/etc/hostname` and the live hostname to `node_hostname`, create the managed local admin user from `dom0_local_users` with a per-box console password hash loaded from controller-private state, lock the `root` password, track the admin home path in `lbu`, and write `permit nopass :wheel`.
- `efi-grub-loader`: render managed GRUB loader configs under both the node-specific EFI path and the generic `EFI/BOOT` fallback path, each handing off through `LABEL={{ efi_partition_label }}` to the canonical `boot/grub/grub.cfg`.
- `(flush_handlers)`: run `apk cache sync` and `lbu commit -d` so the new base state is persisted before phase 21 changes the Tailscale identity.

Diskless persistence invariant: `lbu` must not archive mounted persistent
volumes. `/mnt/dom0_data` stores Xen images and other LVM-backed dom0 data, so
the protected paths file must contain `-mnt/dom0_data` and must not contain
`+mnt/dom0_data`.

# 21-dom0-tailscale-handoff.yml
Replace the temporary bootstrap Tailscale identity with the steady-state dom0 identity.
- Refresh the controller bootstrap known-hosts entry, then run the handoff from the bootstrap `root` session.
- `tailscale-handoff`: inspect the current Tailscale state; if the host is still on the bootstrap identity, read the steady-state auth key from the ops wrapper, free the final hostname if the bootstrap node is still using it, detect the live EFI mountpoint, and render a detached handoff script.
- `tailscale-handoff`: the script wipes `/var/lib/tailscale`, restarts Tailscale, runs `tailscale up --ssh --hostname={{ node_hostname }}` with the steady-state auth key, removes the staged bootstrap auth key from the EFI media, adds `+var/lib/tailscale` to `lbu`, and commits with `lbu commit -d`; then the controller refreshes the dom0 known-hosts entry and waits for reconnect as `{{ dom0_admin_user_name }}` on `{{ node_hostname }}.example.ts.net`.

# 22-dom0-base-verify.yml
Verify dom0 base state from the steady-state identity and finalize persisted Tailscale settings.
- Refresh the controller dom0 known-hosts entry, then gather facts from the steady-state host.
- `dom0-apk-policy`: converge the exact package world and the locked APK guard again from the steady-state dom0 identity. This path also repairs existing boxes that no longer have the bootstrap identity.
- `dom0-tailscale`: keep Tailscale running, restart it when the daemon version differs from the CLI version, prevent `udhcpc` from overwriting Tailscale-owned DNS, ensure Tailscale accepts Tailnet DNS and repairs overwritten `/etc/resolv.conf`, align the advertised hostname with `node_hostname`, and ensure `tailscale_persist_paths` are tracked by `lbu`.
- `(flush_handlers)`: run `apk cache sync` and `lbu commit -d` before verification.
- `dom0-base-verification`: detect the live EFI mountpoint and assert hostname, `/` mode `0755`, EFI mount and filesystem, `/etc/apk/cache`, `LBU_BACKUPDIR`, the single persisted `{{ apkovl_archive_name }}`, steady-state `grub.cfg`, managed EFI GRUB loader configs, populated `apks/` boot repository, installed `python3`, removed bootstrap helper, persisted Tailscale state, required `lbu` include/exclude paths, and a running Tailscale identity advertising `node_hostname`.
- `dom0-base-verification`: also assert that `{{ dom0_admin_user_name }}` has a usable local console password hash, belongs to `wheel`, can use the managed `doas` policy, and that the `root` password is locked.

# 23-dom0-xen-host.yml
Prepare dom0 storage and boot media for the first Xen reboot.
- `dom0-storage`: enable the `lvm` service, create `{{ dom0_data_lv_name }}` if missing with stale signatures wiped, format it as `ext4`, mount it on `{{ dom0_data_mount_path }}`, keep that mounted persistent volume outside `lbu`, create the persistent dom0 directories under it, and remove the obsolete `+var/log` `lbu` include.
- `xen-host`: detect the live EFI mountpoint, mount the EFI partition, copy `/boot/xen.gz` to `{{ xen_binary_target_path }}`, render the Xen `grub.cfg`, and enable `xenstored`, `xenconsoled`, and `xendomains`. Phase 20 already installed the Xen packages.
- `efi-grub-loader`: refresh the node-specific and fallback EFI GRUB loader configs after the Xen menu is rendered.
- `xen-host-verification`: assert `{{ dom0_data_mount_path }}` is mounted as `ext4`, the `apks/` boot repository still contains its marker, index, and packages, `{{ xen_binary_target_path }}` exists, the managed EFI GRUB loader configs are present, and the rendered GRUB contains both the Xen and rescue entries.

# 24-dom0-reboot-into-xen.yml
Re-check Xen boot readiness on the live diskless host, then reboot.
- `(Detect the current EFI mountpoint on the running diskless host)`.
- `(Assert the EFI partition is mounted before the Xen reboot)`.
- `(Override Xen verification paths to the mounted EFI partition)` so the verification role checks the real runtime mount.
- `xen-host-verification`: rerun the same pre-reboot Xen checks on storage, `apks/`, `xen.gz`, GRUB content, and EFI GRUB loader configs.
- `(reboot)`: reboot with `reboot_timeout: 1200`.

# 25-dom0-verify-xen-boot.yml
Confirm the reboot really landed in Xen and remote access survived it.
- Reconnect after the reboot on the steady-state dom0 identity.
- `(hostname)`: assert the runtime hostname is still `node_hostname`.
- `(xl info)`: require Xen runtime output containing `xen_major`.
- `(xl list)`: require `Domain-0` to be visible.
- `(dom0 data mount)`: require `{{ dom0_data_mount_path }}` to be mounted back as `ext4`.
- `(tailscale status --json)`: parse the status and require `BackendState` `Running`, `Self.HostName` `node_hostname`, and `{{ tailscale_handoff_expected_tag }}` on the host.

Xen may print `qemu-xen is unavailable, using qemu-xen-traditional instead`
while `xendomains` runs `xl create --quiet` for PVH guests. This is acceptable
only when the rendered guest configs remain `type = "pvh"` and contain no
device-model settings, and dom0 health reports no QEMU packages, binaries, or
processes. Do not add `device_model_version` to silence the message; PVH guests
should not use a device model.

# 26-dom0-network-bridges.yml
After Xen reboot, configure the bridges of `dom0`: detect the live WAN uplink, render the bridge file, verify the rendered result, and persist it.
- This playbook does not move the live uplink into `br-wan`; WAN-dependent guest start still requires dom0 to boot into that persisted bridge config.

- `network-bridges`: render the steady-state `dom0` bridge interfaces configuration. Phase 20 already installed the bridge tooling.
  - "Detect the live WAN uplink interface when auto-selection is enabled"
  - "Record the WAN uplink interface used for bridge rendering"
  - "Assert the WAN uplink interface was resolved to a physical NIC"
  - "Ensure bridge kernel module loads at boot"
  - "Render bridge network interfaces configuration"
- `network-bridges-verification`: verifiy that the rendered `dom0` bridge configuration matches the detected WAN uplink and expected bridge layout.
  - "Check the resolved WAN uplink interface path"
  - "Collect the rendered dom0 interfaces file"
  - "Check whether bridge-utils is installed"
  - "Collect bridge module boot-load entries"
  - "Assert the dom0 bridge configuration matches the detected uplink"
  - "Show verified dom0 bridge configuration summary"
- `(flush_handlers)`

# 27-dom0-reboot-into-bridges.yml
Reboot dom0 into the persisted bridge configuration before starting WAN-dependent guests.
- Record the current boot ID.
- Trigger a detached reboot through `doas`.
- Wait for Tailscale SSH to drop and reconnect.
- Assert the boot ID changed.
- Verify the default route uses `br-wan` and the WAN bridge has a physical port.
