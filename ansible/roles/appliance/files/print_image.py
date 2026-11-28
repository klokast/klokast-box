"""Fixed print image content; executes only in the isolated builder."""
import json
from pathlib import Path
import shutil
import appliance_common
from appliance_common import put


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
            'boot':['hostname','modules','sysctl','localmount'],
            'default':['print-appliance'], 'shutdown':['killprocs','mount-ro']}.items():
        for service in services:
            if service == 'print-appliance' or (root/'etc/init.d'/service).exists():
                path=root/'etc/runlevels'/level/service
                path.parent.mkdir(parents=True,exist_ok=True)
                path.symlink_to('/etc/init.d/'+service)
    # The runtime service owns the complete boot sequence, with no remote shell.
    put(root,'etc/init.d/print-appliance', (inputs/'print-appliance.openrc').read_text(),0o755)
    put(root,'usr/local/libexec/print-start', (inputs/'print-start').read_text(),0o755)
    put(root,'usr/local/libexec/print-selftest', (inputs/'print-selftest').read_text(),0o755)
    put(root,'etc/print-appliance.conf',
        'PRINT_HOSTNAME='+config['hostname']+'\nPRINT_URI=ipp://'+config['printer']+'/ipp/print\n',0o600)
    put(root,'etc/nftables.nft', firewall(config))
    # Preserve pristine package defaults; /etc/cups is volatile at runtime.
    seed=root/'usr/share/print-appliance/cups'
    shutil.copytree(root/'etc/cups',seed)
    files=seed/'cups-files.conf'
    settings=[line for line in files.read_text().splitlines() if not line.startswith(('SystemGroup ', 'Printcap '))]
    files.write_text('\n'.join(settings)+'\nSystemGroup root\nPrintcap /run/cups/printcap\n')
    cups=(inputs/'cupsd.conf').read_text()
    put(root,'usr/share/print-appliance/cups/cupsd.conf',cups)
    put(root,'etc/klokast-print-image.json',json.dumps(manifest,sort_keys=True)+'\n')
    for name in ('machine-id','ssh'):
        path=root/'etc'/name
        if path.is_dir(): shutil.rmtree(path)
        else: path.unlink(missing_ok=True)


def firewall(config):
    lan = f'ip saddr {config["lan_subnet"]} tcp dport 631 accept' if config['lan_subnet'] else ''
    return f'''flush ruleset
table inet filter {{
 chain input {{
  type filter hook input priority 0; policy drop;
  iifname "lo" accept
  ct state established,related accept
  ct state invalid drop
  ip protocol icmp accept
  meta l4proto ipv6-icmp accept
  iifname "tailscale0" tcp dport 631 accept
  iifname "eth0" udp dport 41641 accept
  iifname "eth0" {lan or 'counter drop'}
 }}
 chain forward {{ type filter hook forward priority 0; policy drop; }}
 chain output {{
  type filter hook output priority 0; policy drop;
  oifname "lo" accept
  ct state established,related accept
  ip daddr {config['gateway']} udp dport 53 accept
  ip daddr {config['gateway']} tcp dport 53 accept
  ip daddr {config['printer']} tcp dport 631 accept
  oifname "eth0" ip daddr {{ 10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16, 169.254.0.0/16 }} drop
  oifname "eth0" tcp dport 443 accept
  oifname "eth0" udp dport {{ 3478, 41641 }} accept
  oifname "tailscale0" accept
 }}
}}
'''


def seal(root, manifest):
    # Native CUPS is the only application.
    return appliance_common.seal(root,'print',['alpine-baselayout','ifupdown-ng','busybox-mdev-openrc','busybox','busybox-openrc','openrc','linux-virt',
                                        'cups','cups-client','cups-filters','cups-openrc','nftables','ca-certificates','tzdata'])
