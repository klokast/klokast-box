"""DMZ and IoT configuration and fixed data mounts inside the isolated guest."""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess

import vm_infrastructure_personalize as base
from vm_guest_identity import preserve_identity

DATA = Path('/srv/retained')
PODMAN = Path('/home/neo/.local/share/containers')


def validate_uuid(value):
    if not isinstance(value, str) or not re.fullmatch(r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}', value):
        raise RuntimeError('shared guest data requires its recorded filesystem UUID')
    return value


def filesystem_uuid(output):
    # BusyBox blkid prints a full record on releases without util-linux blkid.
    value=output.strip()
    if len(value.splitlines())==1:
        match=re.fullmatch(r'.*\bUUID="([0-9a-f-]{36})".*',value)
        if match:value=match[1]
    return validate_uuid(value)


def directory(path, mode, uid=0, gid=0):
    if path.is_symlink() or (path.exists() and not path.is_dir()):
        raise RuntimeError('shared guest storage directory has an unsafe type: '+str(path))
    path.mkdir(parents=True, exist_ok=True)
    path.chmod(mode)
    os.chown(path,uid,gid)


def storage(value, *, initialize):
    role=value.get('role','dmz')
    if role not in ('dmz','iot'):raise RuntimeError('persistent storage requires DMZ or IoT')
    expected=validate_uuid(value['data_uuid'])
    device='/dev/xvde'
    if Path('/sys/class/block/xvde/ro').read_text().strip() != '0':
        raise RuntimeError('shared guest data must be attached at the recorded writable data slot')
    actual=subprocess.run(['blkid','-s','UUID','-o','value',device],capture_output=True,text=True,timeout=30)
    # BusyBox returns success with no output for a blank device; util-linux returns 2.
    if actual.returncode in (0,2) and not actual.stdout.strip() and initialize:
        base.run(['mkfs.ext4','-F','-U',expected,device])
    elif actual.returncode or filesystem_uuid(actual.stdout) != expected:
        raise RuntimeError('shared guest data filesystem differs; refusing to format or mount it')
    base.run(['e2fsck','-f','-n',device])
    directory(DATA,0o711)
    base.run(['mount','-t','ext4',device,str(DATA)])
    try:
        DATA.chmod(0o711)
        marker=DATA/('platform-'+role+'-storage.json')
        evidence={'kind':'klokast.'+role+'-storage.v1','box':value['box'],'role':role,'filesystem_uuid':expected}
        if marker.is_symlink() or (marker.exists() and json.loads(marker.read_text()) != evidence):
            raise RuntimeError('shared guest data ownership marker selects a different assignment')
        for name in ('podman','apps'):
            path=DATA/name
            if path.exists() and (path.stat().st_uid,path.stat().st_gid)!=(1000,1000):
                raise RuntimeError('shared guest data directory numeric ownership differs: '+name)
            directory(path,0o700,1000,1000)
        marker.write_text(json.dumps(evidence,sort_keys=True)+'\n');marker.chmod(0o600)
        directory(PODMAN,0o000)
        base.put('etc/fstab','/dev/xvda / ext4 defaults 0 1\nUUID='+expected+' /srv/retained ext4 defaults 0 2\n'
                 '/srv/retained/podman /home/neo/.local/share/containers none bind 0 0\n')
        service='klokast-'+role+'-storage'
        base.put('etc/'+service+'.json',json.dumps(evidence,sort_keys=True)+'\n',0o600)
        base.put('etc/conf.d/tailscale','no_logs_no_support=yes\ncommand_args="--state=/srv/retained/platform-tailscale-state"\nrc_need="localmount nftables '+service+'"\n')
        base.put('etc/init.d/'+service, '''#!/sbin/openrc-run
# A failed data mount must not create a replacement Podman store on the root.
depend() { need localmount; before tailscale; }
start() {
    /usr/bin/python3 /usr/local/libexec/vm_dmz_personalize.py --verify '''+role+'''
}
''',0o755)
        base.run(['rc-update','add',service,'boot'])
        return evidence
    except Exception:
        base.run(['umount',str(DATA)])
        raise


def finalize(value, digest, *, initialize, source=None, expected_machine=None):
    value=base.validate(value)
    if value['role'] not in ('dmz','iot'):raise RuntimeError('shared personalization requires DMZ or IoT')
    base.finalize(value,digest)
    evidence=storage(value,initialize=initialize)
    try:
        if source is not None:
            machine=source/'etc/machine-id'
            actual=hashlib.sha256(machine.read_bytes()).hexdigest() if machine.exists() else None
            if actual != expected_machine:
                raise RuntimeError('shared guest machine identity changed after preflight')
            saved=DATA/'platform-tailscale-state'
            state=saved if saved.exists() else None
            # Existing DMZ guests use Tailscale SSH and may have no OpenSSH host keys.
            preserve_identity(source,Path('/'),tailscale_source=state,require_ssh=False)
            if state is None:
                shutil.copy2('/var/lib/tailscale/tailscaled.state',saved)
                saved.chmod(0o600)
            base.run(['rc-update','del','sshd','default'])
            Path('/home/neo/.ssh/authorized_keys').unlink(missing_ok=True)
        # Podman user namespace ownership must not change with OS generations.
        for name in ('subuid','subgid'):
            base.put('etc/'+name,'neo:100000:65536\n')
        os.sync()
    finally:
        base.run(['umount',str(DATA)])
    return evidence


def verify(role='dmz'):
    if role not in ('dmz','iot'):raise RuntimeError('storage verification requires DMZ or IoT')
    evidence=json.loads(Path('/etc/klokast-'+role+'-storage.json').read_text())
    if evidence.get('kind')!='klokast.'+role+'-storage.v1' or evidence.get('role')!=role:
        raise RuntimeError('shared guest persistent storage contract is absent')
    expected=validate_uuid(evidence['filesystem_uuid'])
    mounts=[line.split() for line in Path('/proc/mounts').read_text().splitlines()]
    data=[row for row in mounts if row[1]==str(DATA)]
    bound=[row for row in mounts if row[1]==str(PODMAN)]
    if len(data)!=1 or len(bound)!=1 or data[0][2]!='ext4' or bound[0][2]!='ext4':
        raise RuntimeError('shared guest data or Podman bind mount is absent')
    if filesystem_uuid(base.run(['blkid','-s','UUID','-o','value',data[0][0]]))!=expected:
        raise RuntimeError('shared guest mounted data UUID differs')
    if not os.path.samefile(DATA/'podman',PODMAN):
        raise RuntimeError('shared guest Podman storage is not the persistent data directory')
    if json.loads((DATA/('platform-'+role+'-storage.json')).read_text())!=evidence:
        raise RuntimeError('shared guest data ownership differs from its root assignment')
    return evidence


if __name__ == '__main__':
    import sys
    if sys.argv[1:] not in (['--verify'],['--verify','dmz'],['--verify','iot']):
        raise SystemExit('Expected --verify [dmz|iot]')
    print(json.dumps(verify(sys.argv[2] if len(sys.argv)==3 else 'dmz'),sort_keys=True))
