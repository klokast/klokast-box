# install OPNsense as pure PVH without QEMU

apk add bzip2
cd /mnt/dom0_data/xen_images/
wget https://mirrors.pku.edu.cn/opnsense/releases/mirror/OPNsense-26.1-dvd-amd64.iso.bz2
bunzip2 OPNsense-*.bz2             # takes 5 min...
mv OPNsense-*.iso opnsense.iso

modprobe isofs
mkdir -p /mnt/iso
mount -o loop /mnt/dom0_data/xen_images/opnsense.iso /mnt/iso
cp /mnt/iso/boot/kernel/kernel /mnt/dom0_data/xen_images/opnsense-kernel
umount /mnt/iso

vi /etc/xen/opnsense.cfg         # a new version...
```
name = "opnsense"
type = "pvh"
memory = 4096
vcpus = 2

# Boot directly from the extracted FreeBSD kernel
kernel = "/mnt/dom0_data/xen_images/opnsense-kernel"

# Tell FreeBSD to use the Xen serial console, and where to find the ISO filesystem
extra = "vfs.root.mountfrom=cd9660:/dev/xbd1 ro console=hvc0"

# Disks: 
#   - xvda is the LVM drive (xbd0 in FreeBSD) 
#   - xvdb is the ISO (xbd1 in FreeBSD)
disk = [
    'phy:/dev/vg0/lv_opnsense,xvda,w',
    'file:/mnt/dom0_data/xen_images/opnsense.iso,xvdb:cdrom,r'
]

# Network interfaces with predictable MACs for easy identification
vif = [
    'bridge=br-wan,mac=00:16:3e:00:00:01',
    'bridge=br-lan,mac=00:16:3e:00:00:02',
    'bridge=br-dmz,mac=00:16:3e:00:00:03',
    'bridge=br-back,mac=00:16:3e:00:00:04'
]

on_crash = 'destroy'
on_reboot = 'restart'
```

xl create -c /etc/xen/opnsense.cfg

At the login prompt type: installer
Then password: opnsense

This launches the installation wizard:
- accept the default keymap
- choice between ZFS and UFS --> select UFS (we don't need filesystem snapshots, we prefer to increase performance.)
- select the xbd0 drive (this is the 32GB LVM volume)
- "Continue with a recommended swap partion of size 8GB" --> no, we don't want swap. (to avoid sensitive data go onto the SSD, and to not freeze 8GB of RAM for nothing, as the system runs from RAM!)
- "Root password: change root password" --> yes do this
- Then the same screen come back and select: "Complete Install Confirm and exit" 
- last screen: choice between `reboot now: reboot system` and `halt now: power down system` --> select "halt now" 

and after some activity, it comes back to the alpine dom0 prompt. (If stuck in the VM, could use Ctrl ] to exit.


vi /etc/xen/opnsense.cfg             # we update to remove the installation drive, and a few changes
```
name = "opnsense"
type = "pvh"
memory = 4096
vcpus = 2

# Boot directly from the extracted FreeBSD kernel
kernel = "/mnt/dom0_data/xen_images/opnsense-kernel"

# Tell FreeBSD to use the Xen serial console
# and mount the installed UFS partition
extra = "vfs.root.mountfrom=ufs:/dev/xbd0p3 console=hvc0"

# Disks: xvda is the 32 GB LVM drive
disk = [
    'phy:/dev/vg0/lv_opnsense,xvda,w',
]

# Network interfaces with predictable MACs for easy identification
vif = [
    'bridge=br-wan,mac=00:16:3e:00:00:01',
    'bridge=br-lan,mac=00:16:3e:00:00:02',
    'bridge=br-dmz,mac=00:16:3e:00:00:03',
    'bridge=br-back,mac=00:16:3e:00:00:04'
]

on_crash = 'destroy'
on_reboot = 'restart'

```

lbu commit

# basic setup of OPNsense

xl create -c /etc/xen/opnsense.cfg           # boot the OPNsense VM

select 1 (assign interfaces)
want to configure LAGGs now -> n
want to configure VLANs now -> n
WAN interface name          -> xn0
LAN interface name          -> xn1
Optional 1 interface        -> xn2      # the DMZ
Optional 2 interface        -> xn3
Optional 3 interface        -> keep blank and enter
do you want to proceed?     --> yes 


ctrl ]                                       # exit the VM (but keep it running) and go back to the alpine Dom0 prompt
xl console opnsense                          # come back to the VM. (it will look like not working... Need to press enter a second time to revive the menu view

select  2) Set interface IP address
select interface 1-LAN 
Enter the number of the interface to configure: 1
Configure IPv4 address LAN interface via DHCP? [y/N] n
Enter the new LAN IPv4 address. Press <ENTER> for none: 10.10.10.1
Enter the new LAN IPv4 subnet bit count (1 to 32): 24
For a WAN, enter the new LAN IPv4 upstream gateway address. For a LAN, press <ENTER> for none --> ENTER
Configure IPv6 address LAN interface via WAN tracking? [Y/n] n
Configure IPv6 address LAN interface via DHCP6? [y/N] n
Enter the new LAN IPv6 address. Press <ENTER> for none: ENTER 
Do you want to enable the DHCP server on LAN? [y/N] n
Do you want to change the web GUI protocol from HTTPS to HTTP? [y/N] n
Do you want to generate a new self-signed web GUI certificate? [y/N] y
Restore web GUI access defaults? [y/N] n

outputs: 
```
    https://10.10.10.1
    https://[2408:8207:6c72:10d1:216:3eff:fe00:2]


 LAN (xn1)       -> v4: 10.10.10.1/24
                    v6: 2408:8207:6c72:10d1:216:3eff:fe00:2/64
 OPT1 (xn2)      -> 
 OPT2 (xn3)      -> 
 WAN (xn0)       -> v4/DHCP4: 192.168.1.21/24
                    v6/DHCP6: 2408:8207:6c72:10d0:216:3eff:fe00:1/64
```

# make the VM be recreated upon host reboot

doas mkdir -p /etc/xen/auto
doas ln -s /etc/xen/opnsense.cfg /etc/xen/auto/opnsense.cfg


# [ don't! ] temporary tunnel to the web GUI via the host, just the time to click one option to fix bug in OPNsense
# unnecessary finally as the option is pre-clicked now already

on mini pc host:
ip addr add 10.10.10.2/24 dev br-lan

on macbook: ssh -L 8443:10.10.10.1:443 ryii
then on macbook, with Google Chrome (not Safari!) go to https://localhost:8443

log in as root

Interfaces > Settings > check "disable hardware checksum offload" (was already so!)

exit the macbook tunnel

ip addr del 10.10.10.2/24 dev br-lan
