"""Fixed print image content; executes only in the isolated builder."""
import json
from pathlib import Path
import shutil
import subprocess


def put(root, name, content, mode=0o644):
    path = root/name
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        path.unlink()
    path.write_text(content)
    path.chmod(mode)


def configure(root, manifest, config, inputs):
    put(root, 'etc/hostname', config['hostname']+'\n')
    put(root, 'etc/hosts', '127.0.0.1 localhost\n::1 localhost\n')
    put(root, 'etc/network/interfaces', config['network_interfaces'])
    put(root, 'etc/resolv.conf', 'nameserver '+config['gateway']+'\n')
    put(root, 'etc/fstab', '/dev/xvda1 / ext4 ro 0 0\n/dev/xvda2 /var/lib/tailscale ext4 rw,nosuid,nodev,noexec 0 0\n')
    put(root, 'etc/mkinitfs/mkinitfs.conf', 'features="base ext4 virtio xen"\n')
    put(root, 'etc/inittab', '::sysinit:/sbin/openrc sysinit\n::sysinit:/sbin/openrc boot\n::wait:/sbin/openrc default\n::shutdown:/sbin/openrc shutdown\n')
    put(root, 'etc/conf.d/hostname', 'hostname="'+config['hostname']+'"\n')
    put(root, 'etc/conf.d/tailscale', 'TAILSCALED_OPTS="--port=41641 --state=/var/lib/tailscale/tailscaled.state"\n')
    for directory in (root/'etc/runlevels').glob('*'):
        if directory.is_dir():
            for path in directory.iterdir():
                path.unlink()
    for level, services in {'sysinit':['devfs','dmesg','mdev'],
            'boot':['hostname','modules','sysctl','bootmisc','localmount'],
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
    # Resolve runtime dependencies while APK is still available, then remove
    # the build-only package closure. Keep package metadata as inventory evidence.
    runtime=['alpine-baselayout','ifupdown-ng','busybox','busybox-openrc','openrc','linux-virt',
             'cups','cups-client','cups-filters','cups-openrc','tailscale','tailscale-openrc','nftables','ca-certificates','tzdata']
    put(root,'etc/apk/world','\n'.join(runtime)+'\n')
    subprocess.run(['apk','--root',str(root),'--no-network','--no-scripts','del',
                    'alpine-base','busybox-suid','python3','e2fsprogs','util-linux'],
                   check=True,capture_output=True,text=True)
    # Restore BusyBox applet links shadowed by the removed build packages.
    subprocess.run(['chroot',str(root),'/bin/busybox','--install','-s'],check=True,capture_output=True)
    # Native CUPS is the only application. No account can log in.
    for filename in ('passwd','shadow'):
        path=root/'etc'/filename
        lines=[]
        for line in path.read_text().splitlines():
            fields=line.split(':')
            if filename=='passwd': fields[-1]='/sbin/nologin'
            else: fields[1]='!'
            lines.append(':'.join(fields))
        put(root,'etc/'+filename,'\n'.join(lines)+'\n',0o600 if filename=='shadow' else 0o644)
    # Kernel packages retain their dependency metadata. Strip build-only APK
    # and mkinitfs payloads after the boot archive has already been generated.
    database=(root/'lib/apk/db/installed').read_text()
    stripped=[]
    for block in database.strip().split('\n\n'):
        if not any('P:'+name in block.splitlines() for name in ('apk-tools','mkinitfs')):
            continue
        directory=''
        for line in block.splitlines():
            if line.startswith('F:'): directory=line[2:]
            elif line.startswith('R:'):
                path=root/directory/line[2:]
                if path.is_file() or path.is_symlink(): path.unlink()
        stripped.append(next(line[2:] for line in block.splitlines() if line.startswith('P:')))
    put(root,'etc/klokast-print-stripped.json',json.dumps(stripped)+'\n')
    for directory in ('bin','sbin','usr'):
        for path in (root/directory).rglob('*'):
            if not path.is_symlink() and path.is_file():
                mode=path.stat().st_mode & 0o7777
                if mode & 0o6000: path.chmod(mode & ~0o6000)
    (root/'bin/su').unlink(missing_ok=True)
    for name in ('sbin/apk','usr/bin/python3','usr/sbin/sshd','usr/bin/podman','usr/bin/doas','bin/su'):
        path=root/name
        if path.exists() or path.is_symlink():
            raise RuntimeError('unexpected administrative executable in print runtime: '+name)
