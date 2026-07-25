The playbooks are in `klokast-box/ansible/playbooks/`
The roles are in `klokast-box/ansible/roles/`
The orchestration script is `klokast-box/ansible/bin/decommission-box`

# 90-box-decommission-preflight.yml
Controller preflight and inventory validation

- Preflight the box decommission flow from the controller
  - Assert required decommission variables are set
  - Assert the decommission wipe source is dom0
  - Check required controller executables
  - Check required Tailscale wrappers
  - Assert the Tailscale wrappers exist on the controller
  - Assert the inventory contains the dom0 and VM hosts for this box
  - Check controller SSH reachability to dom0 before decommission stop phase
- Inspect the target dom0 storage layout before decommission
  - Run `roles/storage-inspection` read-only on `<box>-dom0`
  - Assert `/` is `tmpfs`
  - Assert the managed SSD layout is discovered exactly from the live box
  - Validate the discovered wipe target is an NVMe namespace with EFI and LVM partitions

# 91-box-decommission-stop-guests.yml
Stop Xen guests on the target dom0

- Stop every Xen guest on the selected dom0 host
  - Stop Xen guests in dependency order: IoT, DMZ, backend, router
  - Collect the remaining Xen domains after decommission stop
  - Assert that no selected guest domains remain for the selected box

# 92-box-decommission-tailscale-vm-identities.yml
Cleanup stale non-dom0 Tailscale identities

- Remove stale offline non-dom0 Tailscale identities for the decommissioned box
  - Review and optionally delete stale non-dom0 `BOX-*` Tailscale identities: `roles/tailscale-box-stale-devices`

# 94-box-decommission-dom0-wipe.yml
Wipe the SSD from diskless dom0

- Wipe the managed SSD from diskless dom0 and run the final action
  - Assert the requested final action is supported
  - Run `roles/storage-inspection` read-only immediately before wiping
  - Bind the wipe target variables from the discovered managed SSD layout
  - Ensure dom0-side wipe prerequisite packages are available in RAM
  - Assert dom0 root is RAM-backed before wiping the SSD
  - Assert the target disk exists
  - Verify required wipe commands are available before unmounting SSD state
  - Fail when the target disk is missing
  - Assert the target disk looks like the expected NVMe install target
  - Collect remaining Xen domains before dom0 wipe
  - Assert no Xen guests remain before dom0 wipe
  - Show dom0 wipe target state
  - Unmount all target-disk and managed-VG mountpoints
  - Deactivate and remove the managed volume group
  - Remove filesystem and partition signatures from the SSD
  - Schedule the final action after dom0 SSD wipe

# 95-box-decommission-finalize-dom0.yml
Remove final stale box Tailscale identities

- Remove final stale Tailscale identities after dom0 wipe
  - Review and optionally delete every stale `BOX-*` Tailscale identity: `roles/tailscale-box-stale-devices`
