Tasks that don't call a role are written in bracket, as for example `(hostname)`. Such tasks are typically built-in Ansible modules or bash commands.

# Prerequisite
The mini PC has booted from the Debian bootstrap iso, that:
- Installs `python3` and `tailscale`
- Includes all necessary packages, as packages must be signed with a key trusted by `/etc/apk/keys`. A plain `apk index` output is not sufficient.
- Is generic: it does not embed the node name or Tailscale auth key.
- Gets DHCP, publishes `kk.local` and `klokast.local` over mDNS, and serves
  the HTTP onboarding portal.
- Onboards the bootstrap machine into Tailscale after the operator enters the
  node name and `tskey-auth-*` in the portal (hostname: `<node>-bootstrap`,
  tag: `tag:bootstrap`)
- Keeps the Debian live bootstrap identity separate from the later steady-state
  dom0 hostname.
- Was built as per `klokast/klokast-box/apps/bootstrap-iso-debian/build-bootstrap-iso.md`
- Was deployed as per `klokast/klokast-box/apps/bootstrap-iso-debian/builder-container.md`
- Uses a dedicated known-hosts file.

# 10-bootstrap-access.yml
Gather facts about the host after it booted from the Debian bootstrap iso.

- `bootstrap-tailscale`
  - Get the standard Ansible facts with `gather_facts: true`
  - Run `tailscale status` to validate that the host is accessible via Tailscale.
  - Run `tailscale ip` and print the output.

# 11-bootstrap-disk-layout.yml
Wipe the SSD, partition it, then check for success.

- `disk-layout`
  - Check if SSD is safe to wipe & partition: SSD exist? NVMe type? Unmounted? Any VG?
  - Wipe SSD
  - Create GPT, partitions, EFI filesystem, PV, VG.

- `disk-layout-verification`
  - Check success of partionning:
  - Collect outputs of `lsblk`, `blkid`, `pvs`, `vgs`, `parted`.
  - EFI and LVM partition devices exist?
  - EFI partition has the expected label and vfat type?
  - LVM partition is registered as a PV?
  - PV belongs to the expected VG?
  - Expected VG exists?
  - Note we don't check GPT label, exact partition boundaries, ESP/LVM flags.

# 12-bootstrap-diskless-build.yml
On the Debian bootstrap host, download the Alpine image from internet, then prepare on the SSD the Alpine diskless boot artifacts:  kernel and other files from the downloaded ISO, GRUB configuration, apkovl overlay, tailscale identity, packages, UEFI bootloader.
 #BUG we better include the boot artefacts into the Debian iso, to remove failure modes related to internet connectivity.

- `diskless-prereqs`
  - Get the standard Ansible facts with `gather_facts: true`.
  - Get diskless-boot variables from `inventory/group_vars/bootstrap.yml`.
  - Install the bootstrap-host packages needed for diskless boot prep.
  - Ensure `efi_mount_path` exists.
  - Unmount any stale mount of `efi_partition_device`.
  - Mount the EFI partition at `efi_mount_path`.
  - Ensure the EFI boot directories exist.

- `diskless-seed-source`
  - Set the seed source facts and local extraction paths.
  - Install tools to download the ISO (`curl`) and extract files from it (`xorriso`).
  - Ensure `{{ alpine_diskless_seed_remote_directory }}` exists.
  - Download the signed Alpine standard ISO checksum from `{{ alpine_diskless_seed_mirror }}`.
  - Reuse an already downloaded ISO when its checksum is valid, otherwise resume
    a partial download from `{{ alpine_diskless_seed_mirror }}/v<major.minor>/releases/<arch>/alpine-standard-<release>-<arch>.iso`.
  - Let `curl` choose the fastest usable address family by default; set
    `alpine_diskless_seed_curl_extra_args` to `["--ipv4"]` or `["--ipv6"]`
    for site-specific routing behavior.
  - From the Alpine ISO, extract the first-boot seed artifacts: `vmlinuz-lts`, `initramfs-lts`, `modloop-lts`, `apks/<arch>`.
  - Assert existence of the extracted boot files and of the package repository directory.

- `diskless-bootloader`
  - Mount the SSD EFI partition `{{ efi_boot_directory }}`.
  - Write the diskless boot artifacts onto the EFI partitition in the SSD: `vmlinuz-lts`, `initramfs-lts`, and `modloop-lts`.
  - List current UEFI boot entries.
  - Remove stale named UEFI entries for legacy bootloader IDs, but keep the generic fallback entry.
  - Remove stale on-disk legacy EFI bootloader directories.
  - Run `grub-install` with `--bootloader-id={{ efi_bootloader_id }}`.
  - Copy `grubx64.efi` to the fallback `BOOTX64.EFI` path.

- `diskless-boot-repository`
  - Ensure the separate runtime cache `{{ apk_cache_path }}` exists.
  - Ensure `{{ apk_boot_repository_path }}` exists.
  - Reset the `apks/<arch>` boot repository before reseeding.
  - Copy the extracted `apks/<arch>` tree onto the SSD.
  - Restore the canonical signed `APKINDEX.tar.gz`.
  - Remove legacy `.boot_repository` markers from the EFI root and cache root.
  - Create `.boot_repository` only under `apks/`.

- `diskless-apkovl`
  - Build the first `{{ node_hostname }}.apkovl.tar.gz` in a temporary workspace under `/tmp`. (We don't mutate the Debian live carrier. We don't call `lbu commit`.)
  - Stage the base overlay content: hostname, `/etc/.default_boot_services`, loopback-only network config, first-boot APK repository
  path, APK cache symlink, minimal `world`, `lbu.conf`, protected `lbu` paths.
  - Stage the `klokast-diskless-bootstrap` service for the first SSD boot.
  - That first-boot helper brings up networking, switches `/etc/apk/repositories` to the normal managed repos, installs `python3`,
  `tailscale`, and `tailscale-openrc`, then runs `tailscale up --ssh` so phase 20 can reconnect over Tailscale SSH without `sshd`.
  - Mint a one-use bootstrap auth key through the ops wrapper on the controller and stage that key temporarily on the EFI media, so the first SSD boot can enroll itself into the temporary `tag:bootstrap` identity as `{{ bootstrap_tailscale_hostname }}`. (A later phase removes that staged key and replaces the temporary identity with the steady-state dom0 identity).
  - Install the managed UEFI bootloader paths
  - Archive the staged tree into `{{ apkovl_archive_path }}` and replace it only if the content changed.

- `diskless-grub`
  - Render the initial GRUB configuration `{{ efi_grub_directory }}/grub.cfg`.
  - Create the initial Alpine RAM-mode entry using `alpine_dev=LABEL={{ efi_partition_label }}` and `modloop=/boot/modloop-lts`.
  - Print the resulting diskless boot path state.

- `efi-grub-loader`
  - Render managed GRUB loader configs under both the node-specific EFI path and the generic `EFI/BOOT` fallback path.
  - Both loader configs search for `LABEL={{ efi_partition_label }}` and hand off to `{{ efi_grub_directory }}/grub.cfg`.

# 13-bootstrap-diskless-verify.yml
Verify the staged Alpine diskless boot state on the SSD before reboot: apkovl contents, EFI boot files, rendered GRUB configuration.

check that the canonical signed index is present, check that `alpine-base` is present, Verify explicitly `.boot_repository`. As it's a hidden file, a naive directory listing or default `ansible.builtin.find` pass can miss it and produce a false-negative boot-readiness check.

- `diskless-install-verification`
  - Check that the EFI partition is mounted at `efi_mount_path` with the expected filesystem type.
  - Collect an EFI artifact listing for diagnostics.
  - Check that all required diskless paths exist: `vmlinuz-lts`, `initramfs-lts`, `modloop-lts`, `grub.cfg`, `EFI/{{ efi_bootloader_id }}/grubx64.efi`, `EFI/{{ efi_bootloader_id }}/grub.cfg`, fallback `BOOTX64.EFI`, fallback `grub.cfg`, `.boot_repository`, `APKINDEX.tar.gz`, `{{ apkovl_archive_name }}`.
  - Check the staged boot repository under `apks/<arch>`:
    - `.boot_repository` is present
    - the canonical signed `APKINDEX.tar.gz` is present
    - at least one `.apk` is present
    - `alpine-base-*.apk` is present
    - extract `APKINDEX` and check that it indexes all packages from `diskless_first_boot_world_packages`
  - Check the staged apkovl content:
    - hostname matches `node_hostname`
    - loopback network config is present
    - `/etc/apk/repositories` matches `diskless_first_boot_repositories`
    - `/etc/apk/world` matches `diskless_first_boot_world_packages`
    - `LBU_MEDIA` in `lbu.conf` matches `lbu_media_name`
    - protected `lbu` paths include `tailscale_persist_paths + diskless_lbu_include_paths`
    - normalize `lbu` entries before comparing: remove the leading `/` from expected paths, and strip `+` / `!` prefixes from stored
entries
    - apkovl contains `.default_boot_services`
    - apkovl contains the `/etc/apk/cache` symlink to the managed cache path
    - apkovl contains the `klokast-diskless-bootstrap` init script and its runlevel symlink
    - apkovl contains `/var/lib/tailscale`
    - the staged first-SSD-boot bootstrap auth key exists on the EFI media
  - Check the staged GRUB config:
    - collect `grub.cfg`
    - collect and verify the node-specific and fallback EFI GRUB loader configs
    - if `diskless_alpine_repo_kernel_param` is set, check that it appears in GRUB
    - if the debug entry is enabled, check that the debug title and `debug_init` are present
    - check that `apkvol=` is not present
  - Print the collected verification state for diagnostics.

# 14-bootstrap-reboot-into-diskless.yml
- Unload the NanoKVM ISO through `ansible/bin/nanokvm-virtual-media` before
  reboot, otherwise the machine can boot the Debian live carrier again.
- Remove the cached bootstrap Tailscale SSH host key on the controller before reconnecting to the first Alpine SSD boot.
- Reboot into diskless Alpine, from SSD

# 15-bootstrap-diskless-debug.yml
- Debug variant of playbook 12 and 13 with extra first-boot diagnostics, that writes a debug log onto EFI.
- Render a dedicated debug GRUB entry as the default boot choice

- `diskless-prereqs`

- `diskless-seed-source`

- `diskless-apkovl`

- `diskless-grub`

- `diskless-install-verification`
