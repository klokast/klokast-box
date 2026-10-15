#!/usr/bin/python3
"""Native retained-router fixture. Runs only in disposable networkless Xen guests."""
import grp
import hashlib
from importlib.machinery import SourceFileLoader
import importlib.util
import json
import os
from pathlib import Path
import pwd
import re
import subprocess
import sys
import uuid

import router_personalize as recipe
import router_service_probe as native
import vm_router_state as state

HELPER = '/usr/local/libexec/vm_router_image_test.py'
FIXTURE = Path('/usr/local/libexec/router-fixture.json')
PROOF = Path('/etc/klokast-router-fixture.json')


def run(argv, *, check=True):
    result = subprocess.run(argv, capture_output=True, text=True, timeout=90)
    if check and result.returncode:
        raise RuntimeError('router fixture command failed: '+str(argv[0])+': '+result.stderr[-1200:])
    return result


def identity():
    machine = json.loads(Path('/var/lib/tailscale/tailscaled.state').read_text())['_machinekey']
    paths = ['var/lib/dhcpcd/duid', 'var/lib/dhcpcd/secret', 'etc/machine-id']
    paths += [str(p).lstrip('/') for p in Path('/etc/ssh').glob('ssh_host_*')]
    return {'machine_key':hashlib.sha256(machine.encode()).hexdigest(),
            'files':{p:hashlib.sha256(Path('/'+p).read_bytes()).hexdigest() for p in paths}}


def accounts():
    return {'dnsmasq_uid':pwd.getpwnam('dnsmasq').pw_uid, 'dnsmasq_gid':grp.getgrnam('dnsmasq').gr_gid,
            'tailscale_gid':grp.getgrnam('tailscale').gr_gid}


def seed():
    state.isolated()
    config = json.loads(FIXTURE.read_text()); recipe.validate(config)
    for name, content in config['files'].items():
        recipe.put(Path('/'), name, content, recipe.file_modes(config['files'])[name])
    # Native daemons generate the test state. There is no enrollment or real NIC.
    native.guard(); native.WORK.mkdir(mode=0o700)
    native.network()
    run(['nft', '-f', '/etc/nftables.nft'])
    dhcp = native.dhcp_dns()
    component = json.loads(Path('/etc/klokast-router-inputs.json').read_text())['tailscale']
    tailscale = native.tailscale_state(component)
    run(['ssh-keygen', '-A'])
    state.write(Path('/'), 'etc/machine-id', uuid.uuid4().hex.encode()+b'\n')
    contract = {'kind':state.KIND, 'box':'boxa', 'filesystem_uuid':str(uuid.uuid4()), 'accounts':accounts()}
    proof = {'contract':contract, 'identity':identity(), 'files':state.evidence(state.snapshot(Path('/'),contract))}
    state.write(Path('/'), str(PROOF).lstrip('/'), json.dumps(proof,sort_keys=True).encode())
    return {'seed':proof, 'native_services':dict(dhcp,**tailscale)}


def finalize(operation, mode):
    state.isolated()
    run(['mount','-t','ext4','-o','ro,noload','/dev/xvdd','/mnt'])
    try:
        proof = json.loads((Path('/mnt')/str(PROOF).lstrip('/')).read_text())
    finally:
        run(['umount','/mnt'])
    config = json.loads(FIXTURE.read_text())
    value = {'kind':'klokast.vm-service-finalize.v1','box':'boxa','role':'router',
        'root_partition':'','accounts':{'neo':[1000,1000]},'source_files':{},
        'router_state':proof['contract'],'router_storage':{'mode':mode,'seed_id':operation},
        'router_configuration':config}
    loader = SourceFileLoader('router_fixture_finalizer','/usr/local/libexec/klokast-service-finalize')
    module = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name,loader))
    loader.exec_module(module)
    module.finalize(module.validate(value))
    run(['mount','-t','ext4','/dev/xvde','/srv/retained'])
    try:
        files = state.evidence(state.snapshot(Path('/srv/retained'),proof['contract'],retained=True))
        if mode == 'migrate' and files != proof['files']:
            raise RuntimeError('router migration changed native state bytes or numeric ownership')
        if mode == 'attach' and Path('/srv/retained/dnsmasq/latest-fixture').read_text() != 'latest\n':
            raise RuntimeError('second router root lost the latest retained write')
    finally:
        run(['umount','/srv/retained'])
    state.write(Path('/'),str(PROOF).lstrip('/'),json.dumps(proof,sort_keys=True).encode())
    # Only disposable clones receive these hooks. The published image has none.
    early = '#!/sbin/openrc-run\ndepend() { need localmount; before networking; }\nstart() { '+HELPER+' --network; }\n'
    late = '#!/sbin/openrc-run\ndepend() { after networking dhcpcd dnsmasq nftables tailscale; }\nstart() { '+HELPER+' --boot; }\n'
    for name, content in (('router-fixture-network',early),('router-fixture-check',late)):
        recipe.put(Path('/'),'etc/init.d/'+name,content,0o755)
        (Path('/etc/runlevels/default')/name).symlink_to('/etc/init.d/'+name)
    return {'mode':mode,'copy_verified':True,'contract':proof['contract']}


def network():
    state.isolated()
    native.WORK.mkdir(mode=0o700)
    native.network()
    run(['ip','netns','exec','router-probe-upstream','dnsmasq','--conf-file=/dev/null',
         '--interface=wanpeer','--bind-interfaces','--port=0',
         '--dhcp-range=198.19.0.50,198.19.0.100,5m','--dhcp-option=3,198.19.0.1',
         '--pid-file=/run/router-service-probe/upstream.pid',
         '--dhcp-leasefile=/run/router-service-probe/upstream.leases'])


def boot(negative):
    # The fixture network service creates only synthetic links after guard().
    if negative:
        for name in ('networking','dhcpcd','dnsmasq','tailscale'):
            if run(['rc-service',name,'status'],check=False).returncode == 0:
                raise RuntimeError('router service started without its retained filesystem: '+name)
        for path in ('var/lib/tailscale/tailscaled.state','var/lib/dhcpcd/duid','var/lib/misc/dnsmasq.leases'):
            if Path('/'+path).exists():
                raise RuntimeError('router generated state on its OS root after storage failure')
        return {'refused':True, 'root_state_absent':True}
    state.verify()
    proof = json.loads(PROOF.read_text())
    if identity() != proof['identity']:
        raise RuntimeError('router identity changed across replacement or reboot')
    for name in ('networking','dhcpcd','dnsmasq','nftables','tailscale'):
        run(['rc-service',name,'status'])
    run(['nft','-c','-f','/etc/nftables.nft'])
    lease = Path('/srv/retained/dnsmasq/dnsmasq.leases')
    if 'router-probe-client' not in lease.read_text():
        raise RuntimeError('OpenRC dnsmasq lost the retained lease')
    answer = run(['busybox','nslookup','router-probe-client','198.18.3.1']).stdout
    if '198.18.3.' not in answer:
        raise RuntimeError('OpenRC DNS cannot resolve the retained lease')
    Path('/srv/retained/dnsmasq/latest-fixture').write_text('latest\n')
    return {'identity':proof['identity'],'filesystem_uuid':proof['contract']['filesystem_uuid'],
            'boot_id':Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
            'services':True,'leases':True,'storage':True}


def main():
    pid1 = os.getpid() == 1
    if pid1:
        for name, fs in (('proc','proc'),('sys','sysfs'),('dev','devtmpfs')):
            if not os.path.ismount('/'+name): run(['mount','-t',fs,fs,'/'+name])
        run(['mount','-o','remount,rw','/']); run(['mdev','-s'])
    args = dict(v.split('=',1) for v in Path('/proc/cmdline').read_text().split() if '=' in v)
    operation, inputs = args.get('klokast_operation',''),args.get('klokast_inputs','')
    if (os.geteuid() != 0 or not re.fullmatch('[0-9a-f]{24}',operation) or
            not re.fullmatch('[0-9a-f]{64}',inputs) or
            json.loads(Path('/etc/klokast-template.json').read_text()).get('inputs_sha256') != inputs or
            args.get('klokast_router_fixture') != '1' or
            Path('/sys/hypervisor/type').read_text().strip() != 'xen' or
            Path('/sys/hypervisor/uuid').read_text().strip() == '00000000-0000-0000-0000-000000000000'):
        raise RuntimeError('router qualification requires its exact disposable Xen assignment')
    if sys.argv[1:] == ['--network']:
        network(); return
    stage = args['klokast_router_stage']
    result = {'kind':'klokast.router-image-fixture.v1','operation_id':operation,'inputs_sha256':inputs,
              'stage':stage,'success':False,'production_data_used':False}
    try:
        if sys.argv[1:] == ['--boot']:
            result.update(boot(stage in ('missing','substitute')))
        elif stage == 'seed': result.update(seed())
        elif stage in ('migrate','attach'): result.update(finalize(operation,stage))
        else: raise RuntimeError('unknown router qualification stage')
        result['success'] = True
    except Exception as error:
        result['error'] = str(error)[:2000]; print(result['error'],flush=True)
    os.sync()
    with open('/dev/xvdc','wb',buffering=0) as stream:
        stream.write(json.dumps(result,sort_keys=True).encode()+b'\0'); os.fsync(stream.fileno())
    if pid1: run(['mount','-o','remount,ro','/'],check=False)
    run(['poweroff','-f'],check=False)


if __name__ == '__main__': main()
