> Historical development runbook.
> This file captures the manual migration path from OPNsense to Alpine router
> VM. Keep it as a low-level reference, but treat the Ansible code and facts as
> the current source of truth. The long-term goal is unattended dom0 + VM
> rollout from code and templates rather than repeating this manual sequence.

doas lvcreate -L 2G -n lv_router vg0

doas vi /etc/xen/router.cfg
```
name = "router"
type = "pvh"
memory = 512
vcpus = 1

kernel = "/mnt/dom0_data/xen_images/vmlinuz-virt"
ramdisk = "/mnt/dom0_data/xen_images/initramfs-virt"

# Alpine to use serial console and boot from ISO
extra = "console=hvc0 alpine_dev=/dev/xvdb:iso9660 modules=loop,squashfs"

disk = [
    'phy:/dev/vg0/lv_router,xvda,w',
    'file:/mnt/dom0_data/xen_images/alpine-virt.iso,xvdb,r'
]

# Only connect internal networks for now.
# Using temporary MACs to avoid ARP conflicts
vif = [
    'bridge=br-lan,mac=00:16:3e:10:11:02',
    'bridge=br-dmz,mac=00:16:3e:10:11:03',
    'bridge=br-back,mac=00:16:3e:10:11:04',
]

on_crash = 'destroy'
on_reboot = 'restart'

```

doas lbu commit
doas xl create -c /etc/xen/router.cfg

setup-alpine
	Which one do you want to initialize?[eth0] 
	Ip address for eth0? (10.10.10.2
	Netmask? [255.0.0.0] 
	Gateway?  10.10.10.1
	Which one do you want to initialize? done
	Do you want to do any manual network configuration? n
	DNS domain name? [enter]
	DNS nameserver(s)? 1.1.1.1
	Which timezone are you in? (or '?' or 'none') [UTC]
	HTTP/FTP proxy URL? [none] 
	Which NTP client to run?[busybox] 
	Enter mirror number or URL: 79
	Setup a user? [no] 
	Which ssh server? [openssh] 
	Allow root ssh login? yes
	Enter ssh key or URL for root [none] 
	Which disk(s) would you like to use? xvda
	How would you like to use it? sys
	WARNING: Erase the above disk(s) and continue? y
	
poweroff

doas apk add --no-cache --latest --virtual .build-deps kpartx
doas kpartx -a /dev/vg0/lv_router
doas mkdir -p /mnt/router_boot
doas mount /dev/mapper/vg0-lv_router1 /mnt/router_boot
doas cp /mnt/router_boot/vmlinuz-virt /mnt/dom0_data/xen_images/router-kernel
doas cp /mnt/router_boot/initramfs-virt /mnt/dom0_data/xen_images/router-initramfs
doas umount /mnt/router_boot/
doas kpartx -d /dev/vg0/lv_router
doas vi /etc/xen/router.cfg
```
name = "router"
type = "pvh"
memory = 512
vcpus = 1

kernel = "/mnt/dom0_data/xen_images/router-kernel"
ramdisk = "/mnt/dom0_data/xen_images/router-initramfs"

# mount the root partition read-write
extra = "console=hvc0 root=/dev/xvda3 rw modules=ext4"

# LVM drive only, not ISO
disk = [
    'phy:/dev/vg0/lv_router,xvda,w'
]

vif = [
    'bridge=br-lan,mac=00:16:3e:10:11:02',
    'bridge=br-dmz,mac=00:16:3e:10:11:03',
    'bridge=br-back,mac=00:16:3e:10:11:04',
]

on_crash = 'destroy'
on_reboot = 'restart'

```
doas lbu commit
doas xl create -c /etc/xen/router.cfg
doas xl list
doas xl console router
```

```
apk update
apk add --no-cache --latest doas dnsmasq nftable tailscale
adduser -D -s /bin/ash neo
adduser neo wheel
echo "permit nopass :wheel" > /etc/doas.d/doas.conf
passwd neo

echo "http://mirrors.hust.edu.cn/alpine/latest-stable/main" > /etc/apk/repositories
echo "http://mirrors.hust.edu.cn/alpine/latest-stable/community" >> /etc/apk/repositories

rc-update add tailscale default
tailscale up --ssh --advertise-tags=tag:prod,tag:vm
exit                # exit the session
ctrl ]              # close the console, back to `yii-dom0`
```

```
ssh yii-router      # as user neo. Add `doas` prefix in following commands

rm /etc/motd
touch /etc/motd
rc-update add dnsmasq default
rc-update add nftables default
echo "net.ipv4.ip_forward = 1" >> /etc/sysctl.conf
echo "net.ipv6.conf.all.forwarding = 1" >> /etc/systctl.conf
```

`sysctl -p`
```
`vi /etc/network/interfaces`
```
auto lo
iface lo inet loopback

# WAN / br-wan
auto eth0
iface eth0 inet dhcp
iface eth0 inet6 dhcp

# LAN / br-lan
auto eth1
iface eth1 inet static
    address 10.10.10.1
    netmask 255.255.255.0
    post-up ip -6 route add 2408:8207:6c74:3f92::/64 dev eth1 metric 1
# this rule above will break if the ISP changes the IPv6 prefix! 

# DMZ / bf-dmz
auto eth2
iface eth2 inet static
    address 192.168.200.1
    netmask 255.255.255.0

# BACK / br-back
auto eth3
iface eth3 inet static
    address 192.168.100.254
    netmask 255.255.255.0
    
```

`vi /etc/dnsmasq.conf`
```
domain-needed
bogus-priv

interface=eth1
interface=eth2
interface=eth3
bind-interfaces

server=1.1.1.1
server=1.0.0.1

dhcp-range=set:lan,10.10.10.50,10.10.10.150,24h
dhcp-option=tag:lan,option:router,10.10.10.1

dhcp-host=00:16:3e:10:10:10,10:10:10:10,yii-jump

```

vi /etc/nftables.nft
```
#!/usr/sbin/nft -f
flush ruleset

table inet filter {
    chain input {
        type filter hook input priority 0; policy drop;

        # Allow established/related connections
        ct state { established, related } accept

        # Allow loopback
        iifname "lo" accept

        # Allow internal network traffic to reach the router
        iifname { "eth1", "eth2", "eth3" } accept

        # Tailscale Pinhole (IPv4 & IPv6) - redirect is handled in nat table
        iifname "eth0" udp dport 41641 accept

        # Allow Ping
        ip protocol icmp accept
        ip6 nexthdr icmpv6 accept
    }

    chain forward {
        type filter hook forward priority 0; policy drop;

        ct state { established, related } accept
       
        # Allow LAN to WAN
        iifname "eth1" oifname "eth0" accept
       
        # Allow Tailscale port forward traffic to jump server
        iifname "eth0" oifname "eth1" ip daddr 10.10.10.10 udp dport 41641 accept
    }

    chain output {
        type filter hook output priority 0; policy accept;
    }
}

table ip nat {
    chain prerouting {
        type nat hook prerouting priority -100; policy accept;
       
        # Port forward UDP 41641 from WAN to yii-jump (10.10.10.10)
        iifname "eth0" udp dport 41641 dnat to 10.10.10.10
    }

    chain postrouting {
        type nat hook postrouting priority 100; policy accept;
       
        # Masquerade (NAT) traffic leaving WAN
        oifname "eth0" masquerade
    }
}

```

poweroff
////////////////////////////////////////////////////

log in via physical keyboard

vi /etc/xen/router.cfg
```
name = "router"
type = "pvh"
memory = 512
vcpus = 1
kernel = "/mnt/dom0_data/xen_images/router-kernel"
ramdisk = "/mnt/dom0_data/xen_images/router-initramfs"
extra = "console=hvc0 root=/dev/xvda3 rw modules=ext4"
disk = [
    'phy:/dev/vg0/lv_router,xvda,w'
]
vif = [
    'bridge=br-wan,mac=00:16:3e:10:11:01', 
    'bridge=br-lan,mac=00:16:3e:10:11:02',
    'bridge=br-dmz,mac=00:16:3e:10:11:03',
    'bridge=br-back,mac=00:16:3e:10:11:04',
]
on_crash = 'destroy'
on_reboot = 'restart'

```

doas lbu commit
doas xl create -c /etc/xen/router.cfg                        # launch the new Alpine router VM
doas rm /etc/xen/auto/opnsense.cfg                           # remove the old opnsense auto start link
doas ln -s /etc/xen/router.cfg /etc/xen/auto/router.cfg      # make alpine router automatically start upon reboot
doas lbu commit                                              # make persistent upon reboot

doas lvremove /dev/vg0/lv_opnsense                           # remove the old opnsense VM volume and files
doas rm /mnt/dom0_data/xen_images/opnsense.iso 
doas rm /mnt/dom0_data/xen_images/opnsense-kernel 
doas rm /etc/xen/opnsense.cfg 
doas lbu commit

doas rm /etc/xen/xlexample.*                                 # cleanup
doas lbu commit

# Fix the slow latency of Tailscale SSH from macbook to `yii` 
 # Reminder: this site's IPv4 CGNAT forces Tailscale SSH through a distant DERP relay. To fix the latency, configure IPv6 Prefix Delegation (DHCPv6-PD) on `yii-router` and open a narrow pinhole in the RG IPv6 firewall.

## Install and configure Prefix Delegation with dhcpcd

 #TODO move router to running from RAM?
 #TODO create new user, install doas
 #TODO Remove MOTD from the VMs (delete file, then recreate empty one with touch)
 #TODO adjust the pinhole IPv6 firewall on RG, so only traffic coming from tailscale (100.xxx.xx.xx) i allowed through.
 #TODO review every machine and remove unnecessary packages added for installation. Define a baseline list for each machine.
 #TODO remove the welcome messages (ssh and console?) `Welcome to Alpine Linux 3.23  --- Kernel 6.18.13-0-virt on x86_64 (/dev/hvc0)`
 #TODO as so many alpine VMs all update from the repo... Better set up a local cache for apk updates / upgrades?

doas xl console router
apk update
apk add --no-cache --latest dhcpcd
rc-update add dhcpcd default
vi /etc/dhcpcd.conf
```
duid
persistent
vendorclassid
option domain_name_servers, domain_name, domain_search
option static_routes, classless_static_routes
option interface_mtu
option host_name
require dhcp_server_identifier
slaac private
noipv6rs
interface eth0
    ipv6rs
    ia_na 1
    ia_pd 2 eth1/0
interface eth1
    slaac private
```

vi /etc/dnsmasq.conf                # Add the last 3 rows
```
domain-needed
bogus-priv

interface=eth1
interface=eth2
interface=eth3
bind-interfaces

server=1.1.1.1
server=1.0.0.1

dhcp-range=set:lan,10.10.10.50,10.10.10.150,24h
dhcp-option=tag:lan,option:router,10.10.10.1

dhcp-host=00:16:3e:10:10:10,10:10:10:10,yii-jump

# enable IPv6 Router Advertisements (SLAAC)
enable-ra
dhcp-range=::,constructor:eth1,slaac,64,12h

```

vi /etc/nftables.nft           # adding the pinhole in the firewall
```
#!/usr/sbin/nft -f             # add this one line: `iifname "eth0" oifname "eth1" udp dport 41641 accept`
flush ruleset

table inet filter {
    chain input {
        type filter hook input priority 0; policy drop;

        # Allow established/related connections
        ct state { established, related } accept

        # Allow loopback
        iifname "lo" accept

        # Allow internal network traffic to reach the router
        iifname { "eth1", "eth2", "eth3" } accept

        # Tailscale Pinhole (IPv4 & IPv6) - redirect is handled in nat table
        iifname "eth0" udp dport 41641 accept

        # Allow Ping
        ip protocol icmp accept
        ip6 nexthdr icmpv6 accept
    }

    chain forward {
        type filter hook forward priority 0; policy drop;

        ct state { established, related } accept
       
        # Allow LAN to WAN
        iifname { "eth1", "eth2", "eth3" } oifname "eth0" accept
       
        # Allow Tailscale port forward traffic to jump server
        iifname "eth0" oifname "eth1" ip daddr 10.10.10.10 udp dport 41641 accept

        # Allow Tailscale IPv6 Pinhole to Jump Server
        iifname "eth0" oifname "eth1" udp dport 41641 accept
    }

    chain output {
        type filter hook output priority 0; policy accept;
    }
}

table ip nat {
    chain prerouting {
        type nat hook prerouting priority -100; policy accept;
       
        # Port forward UDP 41641 from WAN to yii-jump (10.10.10.10)
        iifname "eth0" udp dport 41641 dnat to 10.10.10.10
    }

    chain postrouting {
        type nat hook postrouting priority 100; policy accept;
       
        # Masquerade (NAT) traffic leaving WAN
        oifname "eth0" masquerade
    }
}

```

rc-service dhcpcd start           # apply... 
rc-service dnsmasq restart
nft -f /etc/nftables.nft
exit 
ctrl ]

doas xl console jump
rc-service tailscale restart


 # in router:
vi /etc/nftables.nft               # Adding a rule for nftables for Tailscale port forwarding
```
#!/usr/sbin/nft -f
flush ruleset

table inet filter {
    chain input {
        type filter hook input priority 0; policy drop;

        # Allow established/related connections
        ct state { established, related } accept

        # Allow loopback
        iifname "lo" accept

        # Allow internal network traffic to reach the router
        iifname { "eth1", "eth2", "eth3" } accept

        # Tailscale Pinhole (IPv4 & IPv6) - redirect is handled in nat table
        iifname "eth0" udp dport 41641 accept

        # Allow Ping
        ip protocol icmp accept
        ip6 nexthdr icmpv6 accept
    }

    chain forward {
        type filter hook forward priority 0; policy drop;

        ct state { established, related } accept

        # Allow ICMPv6 forwarding for neighbor discovery and path MTU, required by Tailscale
        ip6 nexthdr icmpv6 accept

        # Fix the MTU Blackhole (MSS Clamping)
        tcp flags syn tcp option maxseg size set rt mtu
       
        # Allow LAN to WAN
        iifname { "eth1", "eth2", "eth3" } oifname "eth0" accept
       
        # Allow Tailscale NAT port forward to jump server
        iifname "eth0" oifname "eth1" ip daddr 10.10.10.10 udp dport 41641 accept

        # Allow Tailscale IPv6 Pinhole to Jump Server
        iifname "eth0" oifname "eth1" udp dport 41641 accept
    }

    chain output {
        type filter hook output priority 0; policy accept;
    }
}

table ip nat {
    chain prerouting {
        type nat hook prerouting priority -100; policy accept;
       
        # Port forward UDP 41641 from WAN to yii-jump (10.10.10.10)
        iifname "eth0" udp dport 41641 dnat to 10.10.10.10
    }

    chain postrouting {
        type nat hook postrouting priority 100; policy accept;
       
        # Masquerade (NAT) traffic leaving WAN
        oifname "eth0" masquerade
    }
}

```

## setup ipv6 forwarding in the jump server

vi /etc/sysctl.conf         # in `jump`, adding the 2 last lines, so that ipv6 forwarding is working
```
# content of this file will override /etc/sysctl.d/*
net.ipv4.ip_forward = 1

net.ipv6.conf.all.forwarding = 1
net.ipv6.conf.eth0.accept_ra = 2
```

sysctl -p                           # apply the change
rc-service networking restart       # restart the network

echo "nameserver 223.5.5.5" > /etc/resolv.conf      ## use AliDNS on the jump server

# TODO - optimize UDP traffic by grouping packets for handling by the CPU
 # in `jump-router` : (beware between -k and -K !!!)

sysctl net.ipv4.ip_forward              #check if routing is activated. should be yes for IPv4: `net.ipv4.ip_forward = 1`
sysctl net.ipv6.conf.all.forwarding     # and also for IPv6: `net.ipv6.conf.all.forwarding = 1`

apk add ethtool
ethtool -K eth0 rx-udp-gro-forwarding on rx-gro-list on           # this is non permanent over reboot...

ethtool -k eth0 | grep -E 'rx-udp-gro-forwarding|rx-gro-list'      # check that the kernel applied it
```                                                                # expected output if activated:
rx-udp-gro-forwarding: on
rx-gro-list: on
```

ethtool -K eth0 rx-udp-gro-forwarding off rx-gro-list off         # to swap from on to off, to desactivate the optimization

to make it permanent over reboot, must include this in this part (for example) of `/etc/network/interfaces`, so it always runs the commands after reboot. This needs for any interface that does forwarding. For example, each of `yii-router` interfaces.
```
auto eth0
iface eth0 inet dhcp
    post-up ethtool -K eth0 rx-udp-gro-forwarding on rx-gro-list on
```








