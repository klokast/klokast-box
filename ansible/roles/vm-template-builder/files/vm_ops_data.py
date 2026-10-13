"""Mount controller data inside its VM; never copy it during replacement."""
import argparse
import json
import os
from pathlib import Path
import pwd
import re
import subprocess
import uuid

MOUNT = Path('/srv/retained')
TARGET = Path('/var/lib/klokast/download-sources')


def run(argv):
    return subprocess.check_output(argv, text=True, timeout=120).strip()


def verify(box):
    if not os.path.ismount(MOUNT) or not os.path.ismount(TARGET):
        raise RuntimeError('controller retained data or download-source bind is not mounted')
    identity = json.loads((MOUNT / 'platform-ops-data.json').read_text())
    if identity.get('box') != box:
        raise RuntimeError('controller retained data belongs to another box')
    expected = identity['filesystem_uuid']
    uuid.UUID(expected)
    observed = run(['blkid', '/dev/xvdb'])
    if ('UUID="' + expected + '"' not in observed or 'TYPE="ext4"' not in observed or
            os.stat(MOUNT).st_dev != os.stat('/dev/xvdb').st_rdev or
            not TARGET.samefile(MOUNT / 'download-sources')):
        raise RuntimeError('controller retained data mount identity differs')
    return dict(identity, mounted=True)


def configure(box, filesystem_uuid, device='/dev/xvdb', initialize=False):
    if not re.fullmatch('[a-z0-9][a-z0-9-]{0,30}', box) or device not in ('/dev/xvdb', '/dev/xvde'):
        raise RuntimeError('invalid controller data identity or device')
    uuid.UUID(filesystem_uuid)
    result = subprocess.run(['blkid', device], capture_output=True, text=True, timeout=30)
    if result.returncode not in (0, 2): raise RuntimeError('cannot inspect the controller data filesystem')
    if not result.stdout.strip():
        if not initialize: raise RuntimeError('retained controller data filesystem is missing; refuse formatting')
        run(['mkfs.ext4', '-F', '-U', filesystem_uuid, device])
    elif 'UUID="' + filesystem_uuid + '"' not in result.stdout or 'TYPE="ext4"' not in result.stdout:
        raise RuntimeError('controller data filesystem UUID or type differs; refuse formatting')
    for path in (MOUNT, TARGET):
        if path.is_symlink(): raise RuntimeError('controller retained mount path is a symlink')
        path.mkdir(parents=True, exist_ok=True)
    if not os.path.ismount(MOUNT):
        if any(MOUNT.iterdir()): raise RuntimeError('controller data mount would hide existing root files')
        run(['mount', '-t', 'ext4', device, str(MOUNT)])
    if os.stat(MOUNT).st_dev != os.stat(device).st_rdev:
        raise RuntimeError('mounted controller data device differs')
    marker = MOUNT / 'platform-ops-data.json'
    identity = {'box': box, 'filesystem_uuid': filesystem_uuid}
    if marker.exists():
        if marker.is_symlink() or json.loads(marker.read_text()) != identity:
            raise RuntimeError('controller data ownership marker differs')
    elif initialize:
        marker.write_text(json.dumps(identity) + '\n'); marker.chmod(0o600)
    else: raise RuntimeError('controller data ownership marker is missing')
    source = MOUNT / 'download-sources'
    source.mkdir(mode=0o700, exist_ok=True)
    if source.is_symlink(): raise RuntimeError('controller download-source state is a symlink')
    smith = pwd.getpwnam('smith')
    os.chown(source, smith.pw_uid, smith.pw_gid); source.chmod(0o700)
    if not os.path.ismount(TARGET):
        if any(TARGET.iterdir()): raise RuntimeError('download-source bind would hide existing root files')
        TARGET.chmod(0)
        run(['mount', '--bind', str(source), str(TARGET)])
    if not TARGET.samefile(source): raise RuntimeError('controller download-source bind points elsewhere')
    lines = Path('/etc/fstab').read_text().splitlines()
    expected = [f'UUID={filesystem_uuid} {MOUNT} ext4 defaults 0 2', f'{source} {TARGET} none bind 0 0']
    for target, line in zip((str(MOUNT), str(TARGET)), expected):
        existing = [row for row in lines if len(row.split()) > 1 and row.split()[1] == target]
        if existing and existing != [line]: raise RuntimeError('controller data fstab entry differs')
        if not existing: lines.append(line)
    Path('/etc/fstab').write_text('\n'.join(lines) + '\n')
    return {'box': box, 'filesystem_uuid': filesystem_uuid, 'mounted': True}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--box', required=True)
    parser.add_argument('--uuid')
    parser.add_argument('--initialize', action='store_true')
    parser.add_argument('--verify', action='store_true')
    args = parser.parse_args()
    if not args.verify and not args.uuid: parser.error('--uuid is required for setup')
    print(json.dumps(verify(args.box) if args.verify else configure(args.box, args.uuid, initialize=args.initialize)))
