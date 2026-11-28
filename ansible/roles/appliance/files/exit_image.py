"""Fixed exit image content; executes only in the isolated builder."""
import json
import shutil
import appliance_common
from appliance_common import put

# Exit clients reach only public destinations. Tailscale CGNAT addresses are
# the clients themselves; the router and the household LAN are private.
NON_PUBLIC = ('0.0.0.0/8', '10.0.0.0/8', '100.64.0.0/10', '127.0.0.0/8', '169.254.0.0/16',
              '172.16.0.0/12', '192.168.0.0/16', '198.18.0.0/15', '224.0.0.0/4', '240.0.0.0/4')
WEB_TCP = (80, 443)
WEB_UDP = (443,)


def configure(root, manifest, config, inputs):
    put(root, 'etc/hostname', config['hostname']+'\n')
    put(root, 'etc/hosts', '127.0.0.1 localhost\n::1 localhost\n')
    put(root, 'etc/network/interfaces', config['network_interfaces'])
    put(root, 'etc/resolv.conf', 'nameserver '+config['gateway']+'\n')
    put(root, 'etc/fstab', '/dev/xvda1 / ext4 ro 0 0\n/dev/xvda2 /var/lib/tailscale ext4 rw,nosuid,nodev,noexec 0 0\n')
    put(root, 'etc/modules', 'tun\nxen-netfront\n')
    put(root, 'etc/mkinitfs/mkinitfs.conf', 'features="base ext4 virtio xen"\n')
    put(root, 'etc/inittab', '::sysinit:/sbin/openrc sysinit\n::sysinit:/sbin/openrc boot\n::wait:/sbin/openrc default\n::shutdown:/sbin/openrc shutdown\n')
    put(root, 'etc/conf.d/hostname', 'hostname="'+config['hostname']+'"\n')
    for directory in (root/'etc/runlevels').glob('*'):
        if directory.is_dir():
            for path in directory.iterdir():
                path.unlink()
    for level, services in {'sysinit':['devfs','dmesg','dev','mdev','hwdrivers'],
            'boot':['hostname','modules','localmount'],
            'default':['exit-appliance'], 'shutdown':['killprocs','mount-ro']}.items():
        for service in services:
            if service == 'exit-appliance' or (root/'etc/init.d'/service).exists():
                path=root/'etc/runlevels'/level/service
                path.parent.mkdir(parents=True,exist_ok=True)
                path.symlink_to('/etc/init.d/'+service)
    # The runtime service owns the complete boot sequence, with no remote shell.
    put(root,'etc/init.d/exit-appliance', (inputs/'exit-appliance.openrc').read_text(),0o755)
    put(root,'usr/local/libexec/exit-start', (inputs/'exit-start').read_text(),0o755)
    put(root,'usr/local/libexec/exit-selftest', (inputs/'exit-selftest').read_text(),0o755)
    put(root,'etc/exit-appliance.conf','EXIT_HOSTNAME='+config['hostname']+'\n',0o600)
    put(root,'etc/nftables.nft', firewall(config))
    put(root,'etc/klokast-exit-image.json',json.dumps(manifest,sort_keys=True)+'\n')
    for name in ('machine-id','ssh'):
        path=root/'etc'/name
        if path.is_dir(): shutil.rmtree(path)
        else: path.unlink(missing_ok=True)


def firewall(config):
    private = ', '.join(NON_PUBLIC)
    return f'''flush ruleset
table inet filter {{
 chain input {{
  type filter hook input priority 0; policy drop;
  iifname "lo" accept
  ct state established,related accept
  ct state invalid drop
  ip protocol icmp accept
  meta l4proto ipv6-icmp accept
  iifname "eth0" udp dport 41641 accept
  # Exit DNS: clients query the Tailscale peer API, which has no fixed port.
  # tailscaled is the only listener; its own grant filter runs first.
  iifname "tailscale0" tcp dport 1024-65535 accept
 }}
 chain forward {{
  type filter hook forward priority 0; policy drop;
  tcp flags syn tcp option maxseg size set rt mtu
  ct state established,related accept
  ct state invalid drop
  iifname "tailscale0" oifname "eth0" ip daddr {{ {private} }} drop
  iifname "tailscale0" oifname "eth0" tcp dport {{ {', '.join(map(str, WEB_TCP))} }} accept
  iifname "tailscale0" oifname "eth0" udp dport {{ {', '.join(map(str, WEB_UDP))} }} accept
 }}
 chain output {{
  type filter hook output priority 0; policy drop;
  oifname "lo" accept
  ct state established,related accept
  ip daddr {config['gateway']} udp dport 53 accept
  ip daddr {config['gateway']} tcp dport 53 accept
  oifname "eth0" ip daddr {{ {private} }} drop
  oifname "eth0" tcp dport 443 accept
  oifname "eth0" udp dport {{ 3478, 41641 }} accept
  oifname "tailscale0" accept
 }}
}}
table ip nat {{
 chain postrouting {{
  type nat hook postrouting priority 100; policy accept;
  oifname "eth0" ip saddr 100.64.0.0/10 masquerade
 }}
}}
'''


def seal(root, manifest):
    # Kernel forwarding through nftables is the only service; tailscaled is upstream.
    return appliance_common.seal(root,'exit',['alpine-baselayout','ifupdown-ng','busybox-mdev-openrc','busybox','busybox-openrc','openrc',
                                       'linux-virt','nftables','ca-certificates','tzdata'])
