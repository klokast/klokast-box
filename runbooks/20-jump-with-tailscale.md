> Historical development runbook.
> The jump-VM path documented here was part of an earlier recovery and access
> model. It is kept for lessons learned and manual reference, not as the target
> steady-state architecture. The current design prefers direct management-plane
> access during development and aims long term for unattended template-based VM
> provisioning.

# the jump server
# will be connected to the `br-lan` bridge of the OPNsense VM
# wil be receiving Tailscale SSH connections, to manage OPNsense and Podman VMs
# again need kernel extraction trick because the host runs PVH without QEMU

## get the image
cd /mnt/dom0_data/xen_images                   #TODO is dom0_data stored in SSD? (just to be sure we don't waste RAM)
wget http://mirrors.hust.edu.cn/alpine/v3.23/releases/x86_64/alpine-virt-3.23.3-x86_64.iso -O alpine-virt.iso
wget https://dl-cdn.alpinelinux.org/alpine/v3.23/releases/x86_64/alpine-virt-3.23.3-x86_64.iso.sha256
sha256sum alpine-virt-3.23.3-x86_64.iso.sha256
rm alpine-virt-3.23.3-x86_64.iso.sha256
mv alpine-virt-3.23.3-x86_64.iso alpine-virt.iso

## extract vmlinux and initramfs 
mkdir -p /mnt/iso
mount -o loop alpine-virt.iso
cp /mnt/iso/boot/vmlinuz-virt /mnt/dom0_data/xen_images/
cp /mnt/iso/boot/initramfs-virt /mnt/dom0_data/xen_images/
umount /mnt/iso

## start the VM, first from iso

vi /etc/xen/jump.cfg
```
name = "jump"
type = "pvh"
memory = 1024
vcpus = 1

kernel = "/mnt/dom0_data/xen_images/vmlinuz-virt"
ramdisk = "mnt/dom0_data/xen_images/initramfs-virt"

# Tell Alpine to use the serial console and where to find the ISO
extra = "console=hvc0 alpine_dev=xvdb:iso9660 modules=loop,squashfs"

disk = [
    'phy:/dev/vg0/lv_jump,xvda,w',
    'file:/mnt/dom0_data/xen_images/alpine.iso,xvdb,r'
]

# Connect exclusively to the isolated LAN bridge
vif = [ 'bridge=br-lan,mac=00:16:3e:10:10:10' ]

on_crash = 'destroy'
on_reboot = 'restart'

```

lbu commit

## set up networking

ip link set eth0 up
ip addr add 10.10.10.10/24 dev eth0          # static IP
ip route add default via 10.10.10.1          # the opnsense LAN
ping -c 3 10.10.10.1                         # test
ping -c 3 1.1.1.1                            # test: pinging Cloudflare DNS server

## Launch the VM
xl create -c /etc/xen/jump.cfg
login is "root", no password.
setup-alpine                                 # hostname: yii-jump , static ip: 10.10.10.10
	Which one do you want to initialize?      # eth0
	Ip address for eth0?                      # 10.10.10.10
	Netmask?                                  # 255.255.255.0
	Gateway?                                  # 10.10.10.1
	Do you want to do any manual network configuration?        # n
	DNS domain name? (e.g 'bar.com') [my.domain]               # ???????????
	DNS nameserver(s)? 1.1.1.1                                 # Cloudflare DNS server
	Which disk(s) would you like to use?                       # xvda
	How would you like to use it?                              # sys
	Erase the above disk(s) and continue?                      # y

But then dont' reboot!!! Instead type `poweroff`

# Plug out the iso and reboot
vi /etc/xen/jump.cfg
```
name = "jump"
type = "pvh"
memory = 1024
vcpus = 1

kernel = "/mnt/dom0_data/xen_images/vmlinuz-virt"
ramdisk = "/mnt/dom0_data/xen_images/initramfs-virt"

# Tell Alpine to use the serial console and where to find the ISO
extra = "console=hvc0 root=/dev/xvda3 rw modules=ext4"

# The 10GB LVM drive
disk = [
    'phy:/dev/vg0/lv_jump,xvda,w',
]

# Connect exclusively to the isolated LAN bridge
vif = [ 'bridge=br-lan,mac=00:16:3e:10:10:10' ]

on_crash = 'destroy'
on_reboot = 'restart'

```
lbu commit
xl create -c /etc/xen/jump.cfg

# BUG: alpine downloaded a newer version of kernel than Xen, let's give xen the newer versions too
uname -r                          # 6.18.7-0-virt
ls /lib/modules/                  # 6.18.13-0-virt
poweroff                          # exiting the `yii-jump` VM, back to the host `yii`
apk add kpartx
mkdir -p /mnt/jump_boot
mount /dev/mapper/vg0-lv_jump1 /mnt/jump_boot
cp /mnt/jump_boot/vmlinuz-virt /mnt/dom0_data/xen_images/jump-kernel
cp /mnt/jump_boot/initramfs-virt /mnt/dom0_data/xen_images/jump-initramfs
umount /mnt/jump_boot
kpartx -d /dev/vg0/lv_jump

vi /etc/xen/jump.cfg              # and we point to the new files
```
name = "jump"
type = "pvh"
memory = 1024
vcpus = 1

kernel = "/mnt/dom0_data/xen_images/jump-kernel"
ramdisk = "/mnt/dom0_data/xen_images/jump-initramfs"

# Tell Alpine to use the serial console and where to find the ISO
extra = "console=hvc0 root=/dev/xvda3 rw modules=ext4"

# The 10GB LVM drive
disk = [
    'phy:/dev/vg0/lv_jump,xvda,w',
]

# Connect exclusively to the isolated opnsense LAN bridge
vif = [ 'bridge=br-lan,mac=00:16:3e:10:10:10' ]

on_crash = 'destroy'
on_reboot = 'restart'

```

lbu commit
xl create -c /etc/xen/jump.cfg

echo "http://mirrors.hust.edu.cn/alpine/latest-stable/main" > /etc/apk/repositories               # set up Tailscale access
echo "http://mirrors.hust.edu.cn/alpine/latest-stable/community" >> /etc/apk/repositories
echo "@edge http://mirrors.hust.edu.cn/alpine/edge/community" >> /etc/apk/repositories
apk update
apk add tailscale@edge
rc-update add tailscale default
rc-service tailscale start
tailscale up

rm /etc/motd

exit                      # close the yii-jump console session
ctrl ]                    # exit the console

ssh ryii          # from Mac, ssh via tailscale, into yii, as root. We havent removed tailscale from the host yet.
xl console jump

echo "net.ipv4.ip_forward = 1" >> /etc/sysctl.conf         # enable subnet routing on `yii-jump`
sysctl -p
tailscale up --advertise-routes=10.10.10.0/24              # let yii-jump advertise a route to the OPNsense LAN
     # then in tailscale dashboard, find `jump-yii`, and 3 dots icon  > edit route settings > approve the new route.
     
When the macbook is connected to the tailnet, it can now visit OPNsense dashboard on `https://10.10.10.1` (many security warnings about the wrong certificates)

# allow the connection from `yii-jump` to `yii`
ip addr add 192.168.100.1/24 dev br-back                # assign the ip
vi /etc/network/interfaces                              # make it permanent over reboot, add this at the end (replacing some lines)
```
auto br-back
iface br-back inet static
    address 192.168.100.1
    netmask 255.255.255.0
    bridge_ports none

```
lbu commit

# in yii-jump, open a network interface into the the backend network to be able to reach dom0 
vi /etc/xen/jump.cfg               # add this line: `'bridge=br-back,mac=00:16:3e:10:10:20'` in `vif`. This results in this:
```
name = "jump"
type = "pvh"
memory = 1024
vcpus = 1

kernel = "/mnt/dom0_data/xen_images/jump-kernel"
ramdisk = "/mnt/dom0_data/xen_images/jump-initramfs"

# Tell Alpine to use the serial console and where to find the ISO
extra = "console=hvc0 root=/dev/xvda3 rw modules=ext4"

# The 10GB LVM drive
disk = [
    'phy:/dev/vg0/lv_jump,xvda,w',
]

# Connect exclusively to the isolated LAN bridge
vif = [
    'bridge=br-lan,mac=00:16:3e:10:10:10',
    'bridge=br-back,mac=00:16:3e:10:10:20'
]

on_crash = 'destroy'
on_reboot = 'restart'

```
lbu commit
xl shutdown jump
xl create -c /etc/xen/jump.cfg

[logs back into `yii-jump`]

ip link                                  # double check that the newly created interface is name `eth1`
ip addr add 192.168.100.2/24 dev eth1
ip link set eth1 up                      # assign the IP
vi /etc/network/interfaces               # make it permanent in the VM, by adding the part about eth1 :
```
auto lo
iface lo inet loopback

auto eth0
iface eth0 inet static
	address 10.10.10.10
	netmask 255.255.255.0
	gateway 10.10.10.1

auto eth1
iface eth1 inet static
        address 192.168.100.2
        netmask 255.255.255.0

```

doas adduser -D -s /bin/ash codex

# install OpenSSH on `yii`, let it listen only to the `yii-jump` IP
apk add openssh
vi /etc/ssh/sshd_config
```
ListenAddress 192.168.100.1
PermitRootLogin yes
```

rc-update add sshd default
rc-service sshd start
TODO OUTPUT: 
```
yii:~# rc-update add sshd default
 * service sshd added to runlevel default
yii:~# rc-service sshd start
 * Caching service dependencies ...
 * You are binding an interface in ListenAddress statement in your sshd_config!
 * You must add rc_need="net.FOO" to your /etc/conf.d/sshd
 * where FOO is the interface(s) providing the following address(es):
 *   192.168.100.1                                                                                                              [ ok ]
 * Setting system clock using the hardware clock [UTC] ...                                                                      [ ok ]
ssh-keygen: generating new host keys: RSA ECDSA ED25519 
 * Starting sshd ... 
```

# from inside `yii-jump`:
tailscale up --advertise-routes=10.10.10.0/24,192.168.100.0/24
TODO OUTPUT:
```
yii-jump:~# tailscale up --advertise-routes=10.10.10.0/24,192.168.100.0/24
Warning: IPv6 forwarding is disabled.
Subnet routes and exit nodes may not work correctly.
See https://tailscale.com/s/ip-forwarding
Warning: UDP GRO forwarding is suboptimally configured on eth0, UDP forwarding throughput capability will increase with a configuration change.
See https://tailscale.com/s/ethtool-config-udp-gro
```

In tailscale dashboard, at 'yii-jump', "edit route settings" and toggle the button.
 #TODO OUTPUT: key is set to reset after a few months, which will lock us out then

# on macbook
ssh-keygen -t ed25519 -f ~/.ssh/one_to_yii -C "one_to_yii"     # on macbook, generate a key pair
sudo chmod 600 ~/.ssh/one_to_yii
sudo chmod 644 ~/.ssh/one_to_yii.pub

# from yii
adduser -D -s /bin/ash -h /home/one one     # I don't use `-G wheel` because I still want the `one` group to be created as primary group of this user
passwd one                                  # set a strong password. `adduser -D` created locked account, not good for ssh
addgroup ssh
adduser one ssh
adduser one wheel
adduser root ssh
vi /etc/ssh/sshd_config                     # then add this line `AllowGroups ssh` and `PasswordAuthentication no`   
rc-service sshd restart
groups one                                  # check his groups

mkdir -p /home/one/.ssh                     # put the public of macbook into yii
chown one:one /home/one/.ssh
chmod 700 /home/one/.ssh
echo "paste_your_public_key_here" >> /home/one/.ssh/authorized_keys
chown one:one /home/one/.ssh/authorized_keys
chmod 600 /home/one/.ssh/authorized_keys
lbu include /home                          # don't forget to add new files/folders outside /etc to the scope of lbu ! 
                                            # Hidden files (starting with .) are backed up if their parent directory is included
lbu commit


# back to yii
vi /etc/ssh/sshd_config                     # copy from the config file in sovi.live repo
rc-update add sshd default
rc-service sshd restart
lbu commit

# ssh into yii as user 'one' still using 'tailscale ssh'
doas delgroup root ssh       # remove ability of root to ssh via OpenSSH (was anyway blocked by `PermitRootLogin no`)
doas rc-service sshd restart
doas cp /etc/securetty /etc/securetty.bak       # first a backup
doas sh -c 'echo > /etc/securetty'
doas chmod 600 /etc/securetty
doas lbu commit

 #TODO more restrictions on the root user:
 #TODO `doas passwd -l root`
 #TODO PAM restriction for services including console: `doas vi /etc/pam.d/login` 
		 ```
		 auth    required       pam_listfile.so onerr=succeed item=user sense=deny file=/etc/deniedusers
		 ```
		Create /etc/deniedusers with "root" inside: `doas sh -c 'echo root > /etc/deniedusers'`
		Permissions: `doas chmod 600 /etc/deniedusers`
 #TODO encrypt the disk: LUKS via `doas apk add cryptsetup` (but first need the remote KVM to type password remotely when reboot) 
 #TODO set a GRUB password (doas grub-mkpasswd-pbkdf2 and edit `/etc/grub.d/00_header`
 #TOTO disable single-user mode without password: edit `/etc/inittab` or kernel params

# ssh from MacBook into yii as user 'one' (one@192.168.100.1), now using openssh
doas ls /root                      #TEST confirm that 'one' got root access. Here should be no output.
ls /root                           #TEST should output: `ls: can't open '/root': Permission denied`
doas rc-service tailscale stop     # remove tailscale from the Xen host `yii`
doas apk del tailscale
doas rm -rf /var/lib/tailscale
doas lbu commit

 # then in Tailscale web dashboard: remove the `yii` machine 
 # then in macbook `~/.ssh/config` remove the `yii` tailscale entries

doas rm /etc/motd                  # remove the MOTD!
doas lbu commit

# Lost root password of OPNsense? --> reset it from the xl console in dom0 
ssh yii                            # ssh into `yii` from macbook via OpenSSH from within the tailnet
doas console opnsense              # then enter credentials
     # select 3: "reset the root password"
     # y    to confirm
     # 0 to logout : DON'T FORGET THIS. console sessions don't log out automatically!!
Ctrl ]                             # exit the console
doas lbu commit
exit                               # exit ssh

# fix slow latency of ssh
tailscale ping yii-jump      # from macbook, output show slow & via DERP: `pong from yii-jump (100.83.93.59) via DERP(hkg) in 451ms`
 # Set up NAT forwarding of Tailscale UDP 41641 on the Residential Gateway and in OPNsense when this site otherwise falls back to DERP.

## NAT forwarding in OPNsense
https://10.10.10.1           # from the macbook, log into OPNsense GUI
	Firewall > NAT > Destination NAT
	Description: Tailscale traffic between WAN and JUMP server
	Interface: WAN
	Version: IPv4
	Protocol: UDP
	Destination address: WAN address
	Destination port: Single port or range - 41641
	Translation: Redirect Target IP: Single host or network : 10.10.10.10    # this is the (hardcoded) jump server IP
	Options: Firewall rule: "register rule"

In OPNsense, we can see the IP of the jump server:
	Interfaces > diagnostics > Neighbors > Automatic Discovery > Discovered Hosts > see the 2 lines for LAN interface

# opening both IPv6 firewalls (Residential Gateway & OPNsense) for "tailscale ssh" traffic to reach the jump server
 # because IPv4 traffic is blocked by the ISP CGNAT, we must use IPv6
 # In Huawei router, menu 1.2.1 shows my IP is 10.xx.xx.xx, means I'm behind CGNAT, and need IPv6.
 # In IPv6 there is no NAT like in IPv4, but we have to open the firewalls.
 #TODO as now udp port 41641 is open, should we add some monitoring of that port? Can we change the Tailscale port if we notice we are under attack?

## OPNsense

OPNsense GUI > interfaces > WAN > 
	> IPv6 configuration type: DHCPv6
	> DHCPv6 client configuration 
			> tick "Request prefix only" 
			> Prefix delegation size: 64
			> tick "send prefix hint" 
			--> save and apply change
			
OPNsense GUI > Interfaces > LAN
	> IPv6 Configuration Type: "Track Interface (legacy)"
	> TRack IPv6 Interface 
			> Parent interface : WAN
			> Assign prefix ID: 0
			--> save and apply change

OPNsense GUI > firewall > Rules > WAN > + to add a new rule
	Action: pass
	TCP/IP version: IPv6
	Protocol: UDP
	Destination: single host or network: 2408:8207:6c72:10d1:216:3eff:fe10:1010/64
	Destination port range : other : from 41641 , to: 41641
	Description: Allow tailscale IPv6 inbound
	--> save, apply change
	
## Residential Gateway
 # menu 4.2.4, IPv6虚拟主机配置, direct translation "IPv6 Virtual Host Configuration", better English "IPv6 firewall pinhole", 

create an entry called "yii-jump-tailscale"
towards IP: 2408:8207:6c72:10d1:216:3eff:fe10:1010  (the jump server, beware the prefix is 10d1 not 10d0 !)
then at bottom add the line for UDP, ports 41641-41641 (this is Tailscale)

## inside yii-jump console:
rc-service tailscale restart     # this breaks the ssh pipe. ssh back to yii, then console back to yii-jump, then:
rc-service tailscale status      # check it's started.

tailscale ping yii-jump          # super quick now!

# make the VM be recreated upon host reboot, do this in `yii`

doas mkdir -p /etc/xen/auto
doas ln -s /etc/xen/jump.cfg /etc/xen/auto/jump.cfg


 #TODO a cron job in the admin agent, to check if there is a new version of Alpine. If so, I'll update manually the host OS. After I've done it once, I'll see if I automatize it, from inside the dom0 to query the current alpine version. Should this be done to internet (dom0 shouldn't talk to internet...) or to the jump server (it's running alpine, so it could just check the version running there)?
                                
