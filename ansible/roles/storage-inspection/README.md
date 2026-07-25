# storage-inspection

Read-only storage diagnosis for managed Platform hosts.

The role collects kernel block-device, mount, `blkid`, and LVM reports without
installing packages or changing device state. It publishes these facts:

- `storage_inspection_disks`
- `storage_inspection_partitions`
- `storage_inspection_physical_volumes`
- `storage_inspection_volume_groups`
- `storage_inspection_logical_volumes`
- `storage_inspection_mounts`
- `storage_inspection_managed_layout`

`storage_inspection_managed_layout` contains the discovered Platform SSD disk,
EFI partition, LVM partition, volume group, VG size/free bytes, confidence, and
warnings. Destructive playbooks should require `confidence == "exact"` before
using the discovered values.
