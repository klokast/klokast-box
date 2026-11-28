"""Shared sealed-appliance content; executes only in the isolated builder."""
import json
from pathlib import Path
import subprocess

# No runtime may contain these administrative executables.
ADMINISTRATIVE = ('sbin/apk','usr/bin/python3','usr/sbin/sshd','usr/bin/podman','usr/bin/doas','bin/su')


def put(root, name, content, mode=0o644):
    path = root/name
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        path.unlink()
    path.write_text(content)
    path.chmod(mode)


def seal(root, kind, runtime):
    # Resolve runtime dependencies while APK is still available, then remove
    # the build-only package closure. Keep package metadata as inventory evidence.
    put(root,'etc/apk/world','\n'.join(runtime)+'\n')
    subprocess.run(['apk','--root',str(root),'--no-network','--no-scripts','del',
                    'alpine-base','busybox-suid','python3','e2fsprogs','util-linux'],
                   check=True,capture_output=True,text=True)
    # Restore BusyBox applet links shadowed by the removed build packages.
    subprocess.run(['chroot',str(root),'/bin/busybox','--install','-s'],check=True,capture_output=True)
    # No account can log in.
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
    put(root,'etc/klokast-'+kind+'-stripped.json',json.dumps(stripped)+'\n')
    for directory in ('bin','sbin','usr'):
        for path in (root/directory).rglob('*'):
            if not path.is_symlink() and path.is_file():
                mode=path.stat().st_mode & 0o7777
                if mode & 0o6000: path.chmod(mode & ~0o6000)
    (root/'bin/su').unlink(missing_ok=True)
    for name in ADMINISTRATIVE:
        path=root/name
        if path.exists() or path.is_symlink():
            raise RuntimeError('unexpected administrative executable in '+kind+' runtime: '+name)
    return sealed_packages(root, stripped)


def sealed_packages(root, stripped):
    """Return the name and version of each package that the sealed image keeps.

    The build result records them, because the appliance has no login. A package
    whose payload was stripped is not listed.
    """
    packages = {}
    for block in (root/'lib/apk/db/installed').read_text().strip().split('\n\n'):
        fields = dict(line.split(':', 1) for line in block.splitlines() if line[1:2] == ':' and line[:1] in 'PV')
        if 'P' in fields and fields['P'] not in stripped: packages[fields['P']] = fields.get('V', '')
    return packages
