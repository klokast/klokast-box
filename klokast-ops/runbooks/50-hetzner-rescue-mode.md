Here the instructions to rescue a Hetzner instance that cannot boot or `sudo` anymore: reboot in rescue mode, mount the file system, edit, reboot in normal mode.

[`https://docs.hetzner.com/cloud/servers/getting-started/rescue-system/`]

1. Open Hetzner Cloud web UI: `https://console.hetzner.cloud/`
2. Select the project.
3. Click the affected server (hostname: `hetzner-ops`).
4. In the server page, look at the top menu bar with tabs like Overview, Graphs, Networking, etc. > click `Enable Rescue`.
5. Select the ssh public key from MacBook: `xiaoju_codex_hetzner`
6. Actions > shutdown
7. Wait a while... then "Power on"

6. Choose linux64. (Enable Linux 64 bit rescue)

8. Click Enable rescue & power cycle: the instance will reboot into rescue mode.
9. ssh in
  ```
  ssh -i ~/.ssh/xiaoju_codex_hetzner root@<public-ip>
  lsblk -f
  ```
12. Find the server root partition. On many Hetzner cloud servers it is `/dev/sda1` or `/dev/vda1`.
13. Remove the failing file:
  ```
  mkdir -p /mnt/sysroot
  mount /dev/sda1 /mnt/sysroot
  ls /mnt/sysroot/etc/sudoers.d
  rm /mnt/sysroot/etc/sudoers.d/codex-tailscale-devices
  sync
  reboot
  ```
14. From Macbook, ssh normally as `neo`. `sudo` works again.
  ```
  mosh neo@hetzner-ops
  sudo su
  ```
