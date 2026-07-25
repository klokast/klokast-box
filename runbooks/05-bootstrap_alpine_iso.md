
# initial alpine bootstrap, booting from USB

Several options:

## Custom bootstrap ISO

Custom boostrap ISO based on `Debian Live`,
as described in `apps/bootstrap-iso-debian/README.md`.

Booting that ISO by itself is non-destructive. The target SSD is only modified
after you start destructive Ansible phases such as `11-bootstrap-disk-layout.yml`.

Operator smoke test:

1. Ensure no other machine is still online in Tailscale as `duh-dom0`.
2. Attach `ansible/artifacts/bootstrap-iso-debian/klokast-bootstrap-duh-dom0-<debian-release>-amd64.iso`
   over KVM or USB and boot it.
3. Wait for DHCP and Tailscale to come up.
4. From the control plane:

```sh
ssh root@duh-dom0.example.ts.net
hostname
tailscale status
python3 --version
apk policy tailscale python3
getent passwd neo
doas -n true
```

If you only want to validate bootstrap connectivity and inventory before the
destructive install, stop after:

```sh
ansible-playbook -vv ansible/playbooks/10-bootstrap-access.yml -l duh-bootstrap
```

## Manual commands from the Alpine live ISO

- Connect NanoKVM 3 cables: usb C to power, usb C to mini PC, HDMI to mini PC.
- Download the Alpine Live ISO into NanoKVM: NanoKVM web interface: `Terminal` icon -> `NanoKMV terminal` ->
```
cd ../data
wget http://mirrors.hust.edu.cn/alpine/v3.23/releases/x86_64/alpine-standard-3.23.3-x86_64.iso
ls
reboot
```
(Reboot seems necessary for NanoKVM image screen to see the new iso file.)
- Load that ISO as bootable drive: NanoKVM web interface -> `images` icon --> select the new image.
- Power on the mini PC
```
setup-alpine -q
echo "http://mirrors.hust.edu.cn/alpine/latest-stable/main" > /etc/apk/repositories
echo "http://mirrors.hust.edu.cn/alpine/latest-stable/community" >> /etc/apk/repositories
apk add --no-cache --latest tailscale
rc-update add tailscale default
rc-service tailscale start
tailscale up --ssh --advertise-tags=tag:bootstrap

ssh root@alpine       # from the MacBook
```
## From Alpine live ISO with tailscale Auth Key

In Tailscale dashboard, Access Control -> Tailscale SSH -> allow `group:operators`
 to ssh into `tag:bootstrap` as user `root`.

Also, create a Tailscale Auth Key: `tag:bootstrap`, ephemeral, pre-approved, reusable.

Build the Debian Live custom ISO, load it into NanoKVM, boot from it.
It's a normal Debian Live, but with Tailscale automatic onboarding with a ephemeral key.

On MacBook, `vi ~/.ssh/config`:
```
Host *
    AddKeysToAgent yes
    UseKeychain yes

Host bootstrap
    Hostname duh-dom0
    User root
```

Use the Debian Live system to format the SSD partition and save the apkovl file into it:
```
ssh bootstrap

mkfs.vfat -F 32 -n EFI /dev/nvme0n1p1
mkdir -p /mnt/efi
mount /dev/nvme0n1p1 /mnt/efi
```

On Macbook, create the apkovl files, then send to the Debian Live SSD.
Don't forget the steps after this back on the miniPC Debian Live!
```
./build-duh-bootstrap-apkovl.sh --authkey tskey-auth-xxxxxxxxxxx
scp bootstrap.apkovl.tar.gz bootstrap:/mnt/efi/alpine/
```

On Debian Live:
```
sync
find /mnt/efi -maxdepth 2 -type f | sort
umount /mnt/efi
```

Boot on Alpine standard, press `e` to reach the grub parameters, add this at the end of the linux entry (remove "quiet")
`ip=dhcp apkovl=nvme0n1p1:/bootstrap.apkovl.tar.gz`
So it looks like this:
```
setparams 'linux tls'

linux       /boot/vmlinuz-lts modloop=/boot/modloop-lts modules=loop,squashfs,sd-mod,usb-storage ip=dhcp apkovl=nvme0n1p1:/bootstrap.apkovl.tar.gz
initrd      /boot/initramfs-lts
```

# rescue commands when booting from gParted

In the GParted terminal, commands to:
- disables both SSD boot-repo markers
- moves every root-level *.apkovl* out of the EFI root
- leaves the files preserved under _disabled/

```
  sudo -i

  dosfsck -a /dev/nvme0n1p1 || true

  mkdir -p /mnt/nvme
  mount /dev/nvme0n1p1 /mnt/nvme    # if already mounted error then `findmnt /dev/nvme0n1p1`

  mkdir -p /mnt/nvme/_disabled

  [ -e /mnt/nvme/.boot_repository ] && \
    mv /mnt/nvme/.boot_repository /mnt/nvme/_disabled/boot_repository_root.off

  [ -e /mnt/nvme/cache/.boot_repository ] && \
    mv /mnt/nvme/cache/.boot_repository /mnt/nvme/_disabled/boot_repository_cache.off

  for f in /mnt/nvme/*.apkovl*; do
    [ -e "$f" ] || continue
    mv "$f" /mnt/nvme/_disabled/
  done

  find /mnt/nvme -maxdepth 2 \( -name '.boot_repository*' -o -name '*.apkovl*' \) -print

  sync
  umount /mnt/nvme
  poweroff
```

Then, using the remove KVM interface:
1. remove the GParted USB
2. insert the Alpine USB
3. boot Alpine USB normally (see previous section)
