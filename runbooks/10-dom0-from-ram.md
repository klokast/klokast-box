> Historical development runbook.
> This describes a manual dom0 bootstrap path that was useful while proving the
> platform design. It is not the target operator workflow. The long-term goal is
> unattended dom0 installation from the custom bootstrap ISO, followed by
> unattended VM materialization from versioned Alpine templates. Tailscale in
> this runbook is also part of the current development management plane, not a
> permanent architectural requirement.

We are installing:
- Alpine Linux, bare metal, diskless (running from RAM)
- with persistence on the SSD
- one user only: `neo`, who belongs to the wheel group 
- making it the dom0 of the Xen hypervisor
- with the bridges to run several VMs
- hostname: `yii-dom0`
- access via Tailscale

# Pre-requisite: tailscale account is created, macbook is connected to it.

On Macbook, download "standard x86_64" Alpine Linux from `https://alpinelinux.org/downloads/` or better use the quick mirror:
		http://mirrors.hust.edu.cn/alpine/v3.23/releases/x86_64/alpine-standard-3.23.3-x86_64.iso
		
Download the sha256 from `https://alpinelinux.org/downloads/`

shasum -a 256 alpine-standard-3.23.3-x86_64.iso        # check checksum in mac
sha256sum alpine-standard-3.23.3-x86_64.iso           # or better do it in alpine

- With BalenaEtcher, burn the image into a usb stick.
	- The black SopraSteria usb stick works.
	- The big 126 GB Tesla usb stick works.
	- The small metal usb stick *doesn't* work.
- Boot the minipc from USB

root				                   # is the login, no password.

parted -a optimal /dev/nvme0n1 mklabel gpt                          # partition the SSD
parted -a optimal /dev/nvme0n1 mkpart primary fat32 1MiB 1GiB
parted -a optimal /dev/nvme0n1 name 1 EFI
parted -a optimal /dev/nvme0n1 set 1 esp on
parted -a optimal /dev/nvme0n1 mkpart primary ext4 1GiB 100%
parted -a optimal /dev/nvme0n1 set 2 lvm on
mkfs.fat -F32 -n ALPINE_EFI /dev/nvme0n1p1
pvcreate /dev/nvme0n1p2
vgcreate vg0 /dev/nvme0n1p2

mkdir -p /media/nvme                                                # setup GRUB bootlaoder
mount /dev/nvme0n1p1 /media/nvme
cp -a /media/cdrom/* /media/nvme/
grub-install --target=x86_64-efi --efi-directory=/media/nvme --boot-directory=/media/nvme/boot --bootloader-id=alpine

vi /media/nvme/boot/grub/grub.cfg
```
set timeout=3

menuentry "Alpine Linux (RAM Mode)" {
    linux /boot/vmlinuz-lts modules=loop,squashfs,sd-mod,usb-storage,nvme quiet modloop=/boot/modloop-lts alpine_dev=LABEL=ALPINE_EFI
    initrd /boot/initramfs-lts
}

```
rc-update add lvm boot
mkdir -p /media/nvme/cache
setup-apkcache /media/nvme/cache
setup-lbu nvme
lbu commit
sync
umount /media/nvme
                                       # remove the usb!
reboot

echo "http://mirrors.hust.edu.cn/alpine/latest-stable/main" > /etc/apk/repositories
echo "http://mirrors.hust.edu.cn/alpine/latest-stable/community" >> /etc/apk/repositories

apk add --no-cache --latest tailscale                       # setup Tailscale
rc-update add tailscale default
rc-service tailscale start
doas tailscale up --ssh --advertise-tags=tag:prod,tag:dom0
 # then follow instruction from the terminal to visit a tailscale webpage 

 # then in macbook edit `~/.ssh/config` :
 # and, if needed, delete old records in `~/.ssh/known_hosts`
```
Host ryii
   Hostname yii
   User root
   
Host yii-dom0
   Hostname yii
   User neo
```

ssh ryii                         # as `root` user

apk update
apk add --no-cache --latest doas e2fsprogs
adduser -D -s /bin/ash neo
adduser neo wheel
echo "permit nopass :wheel" > /etc/doas.d/doas.conf

lbu include /var/lib/tailscale   # include tailscale keys into lbu persistence
lbu include /var/log             # add to the scope of what `lbu commit` makes persistent. Otherwise only /etc
lbu status                       # to see what has been added to the scope of lbu commit
vi /etc/lbu/lbu.conf             # uncomment and edit last row to let lbu keep a few backups `BACKUP_LIMIT=10`

apk del dosfstools parted efibootmgr
rm -f /etc/apk/cache	            # ensure apk never use its cache

doas passwd -l root              # invalidates the password of root by prepending ! to the hash

rm /etc/motd                     # delete the message of the day
touch /etc/motd                  # recreate an empty file, to avoid automatic recreation upon reboot

lbu commit -v                    # -v for verbose

lvcreate -L 20 G -n lv_dom0_data vg0
lvcreate -L 20G -n lv_dom0_data vg0
lvcreate -L 32G -n lv_opnsense vg0
lvcreate -L 10G -n lv_jump vg0
lvcreate -L 50G -n lv_podman_dmz vg0
lvcreate -L 100G -n lv_podman_back vg0
mkfs.ext4 /dev/vg0/lv_dom0_data
echo "/dev/vg0/lv_dom0_data /mnt/dom0_data ext4 rw,relatime 0 2" >> /etc/fstab

`/etc/fstab` is now like this (I just deleted 2 first rows, of cdrom and usbdisk)
```
UUID=A076-ED3E    /media/nvme    vfat    noauto,ro 0 0
/dev/vg0/lv_dom0_data /mnt/dom0_data ext4 rw,relatime 0 2
```

 # BEWARE we don't want to use UUIDs in `/etc/fstab`, so that we can restore the system easilyto another drive if needed, then only use names, as UUIDs would be different.

mount -a
mkdir -p /mnt/dom0_data/logs
mkdir -p /mnt/dom0_data/xen_images
rc-service syslog stop
cp -a /var/log/* /mnt/dom0_data/logs/
rm -rf /var/log
ln -s /mnt/dom0_data/logs /var/log

lbu commit

rc-service syslog start
apk del e2fsprogs
lbu commit -v
apk add xen xen-hypervisor      # we don't install xen-qemu and ovmf-xen, as we will only build PVH VMs (don't put "--no-cache" !)
mount /dev/nvme0n1p1 /media/nvme           # no worry in case of "resource busy" error, it just means it's already mounted.
mount -o remount,rw /media/nvme            # need the read-write
cp /boot/xen.gz /media/nvme/boot/
ls /media/nvme/boot/                       # for check

vi /media/nvme/boot/grub/grub.cfg
```
set timeout=3
set default=0

menuentry "Alpine Linux (Xen, from RAM)" {
    insmod multiboot2
    multiboot2 /boot/xen.gz dom0_mem=1024M,max:1024M loglvl=all guest_loglvl=all
    module2 /boot/vmlinuz-lts modules=loop,squashfs,sd-mod,usb-storage,nvme quiet modloop=/boot/modloop-lts alpine_dev=LABEL=ALPINE_EFI
    module2 /boot/initramfs-lts
}
```

rc-update add xenstored default
rc-update add xenconsoled default
rc-update add xendomains default
lbu commit -v
cd /
sync
umount /media/nvme                    error is ok: "umount: can't unmount /media/nvme: Resource busy"
umount -l /media/nvme
reboot

[back via tailscale ssh]

xl list
df -h

# installing the virtual bridges

apk add bridge-utils
modprobe bridge
echo "bridge" >> /etc/modules
vi /etc/network/interfaces
```
auto lo
iface lo inet loopback

auto eth0
iface eth0 inet manual

auto br-wan
iface br-wan inet dhcp
    bridge-ports eth0

auto br-lan
iface br-lan inet manual
    bridge-ports none

auto br-dmz
iface br-dmz inet manual
    bridge-ports none

auto br-back
iface br-back inet manual
    bridge-ports none
```

lbu commit
