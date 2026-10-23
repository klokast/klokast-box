"""Development-only repair of a pending router's read-only network verification.

The request, image, private inputs and every mutating native step stay frozen.
Only the read-only Ansible network verifier uses the current published source.
"""
import json
import os
from pathlib import Path
import re
import subprocess
import sys


def eligible(observed, box, operation, instance_commit):
    pending = observed.get('pending', [])
    if len(pending) != 1:
        raise RuntimeError('verification repair requires exactly one pending router operation')
    record = pending[0]
    assignment = observed.get('assignment') or {}
    if (record.get('box') != box or record.get('role') != 'router' or record.get('operation_id') != operation
            or assignment.get('operation_id') != operation or assignment.get('stage') != 'booted'
            or record.get('reboot_verification', {}).get('status') != 'complete'):
        raise RuntimeError('verification repair requires the exact booted router after successful reboot verification')
    if record.get('requested_configuration', {}).get('instance_commit') != instance_commit:
        raise RuntimeError('Instance changed since the router request; verification repair cannot change frozen policy')
    return record


def repair(box, operation, repo):
    import ops_replacement_inputs as inputs
    import platform_resource_runtime as runtime
    import platform_source
    import vm_service_runtime as service
    platform_source.require_controller()  # Includes the development-only gate.
    if not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,30}', box) or not re.fullmatch(r'[0-9a-f]{24}', operation):
        raise RuntimeError('verification repair requires a box and an exact operation ID')
    with runtime.vm_update_installation_lock():
        service.authority(box, 'router')
        revision = inputs.revision(repo, True)
        instance = inputs.revision(inputs.INSTANCE, True)
        observed = service.step(box, 'router', 'status', operation=operation)
        record = eligible(observed, box, operation, instance)
        work, _ = inputs.recover(record, configuration_key='service_configuration', image_key='service_image', root=service.ROOT)
        request = dict(box=box, operation=operation, record=record, verification_commit=revision, repo=str(repo), work=str(work))
        log_path = work/'verification-repair.log'
        with log_path.open('a+b') as log:
            os.chmod(log_path, 0o600)
            log.write(('Read-only verification repair from '+revision+'\n').encode()); log.flush()
            start = log.tell()
            result = subprocess.run([sys.executable, '-I', str(Path(__file__).resolve()), '--worker'],
                input=json.dumps(request), text=True, stdout=log, stderr=subprocess.STDOUT, timeout=1800)
            end = log.seek(0, os.SEEK_END); log.seek(max(start, end-8192)); tail = log.read().decode(errors='replace')
        if result.returncode:
            raise RuntimeError('router verification repair failed; inspect '+str(log_path))
        return json.loads(tail.strip().splitlines()[-1])


def worker(request):
    # Start with no current Platform modules loaded. Recover and import the
    # operation's checksummed frozen implementation for all privileged steps.
    snapshot_root = Path('/home/smith/private/klokast/vm-replacement-inputs')
    work = Path(request['work'])
    if work.parent != snapshot_root or not re.fullmatch(r'[0-9a-f]{24}', work.name):
        raise RuntimeError('verification repair requires the recorded private snapshot')
    repo = Path(request['repo'])
    sys.path.insert(0, str(work/'public/ansible/lib'))
    import ops_replacement_inputs as inputs
    import platform_source
    import vm_service_runtime as service
    import vm_service_probe as probe
    platform_source.require_controller()
    if repo != platform_source.REPO or inputs.revision(repo, True) != request['verification_commit']:
        raise RuntimeError('read-only verification source changed')
    recovered, variables = inputs.recover(request['record'], configuration_key='service_configuration', image_key='service_image', root=service.ROOT)
    if recovered != work:
        raise RuntimeError('verification repair selected a different frozen snapshot')
    options = dict(repo=work/'public', inventory=work/'inventory/hosts.json', operation=request['operation'], checksum=request['record']['inputs_sha256'])
    observed = service.step(request['box'], 'router', 'status', **options)
    record = eligible(observed, request['box'], request['operation'], inputs.revision(inputs.INSTANCE, True))
    config = variables['service_configuration']
    before = service.wait_probe(request['box'], 'router')
    probe.verify(config['before'], before, record)
    import tempfile
    with tempfile.TemporaryDirectory(prefix='router-verification-repair-') as temporary:
        args = Path(temporary)/'variables.json'
        inputs.save(args, dict(variables['router_verification'], router_update_box=request['box']))
        subprocess.run(['ansible-playbook','-i',str(work/'inventory/hosts.json'),
            str(repo/'ansible/playbooks/74-router-service-verify.yml'),'--limit',request['box']+'-router','-e','@'+str(args)],
            cwd=repo,env=dict(os.environ,ANSIBLE_CONFIG=str(repo/'ansible/ansible.cfg')),check=True,timeout=600)
    after = service.wait_probe(request['box'], 'router')
    checks = probe.verify(config['before'], after, record)
    if before['boot_id'] != after['boot_id'] or inputs.revision(repo, True) != request['verification_commit']:
        raise RuntimeError('router boot or verification source changed during checks')
    service.step(request['box'], 'router', 'checks', payload=checks, **options)
    service.step(request['box'], 'router', 'advance', **options)
    state = service.step(request['box'], 'router', 'advance', **options)
    if state['journal']['stage'] != 'complete':
        raise RuntimeError('router acceptance remains incomplete; use the recorded normal resume command')
    return dict(state='complete',box=request['box'],role='router',operation_id=request['operation'],
                image=record['image'],verification_commit=request['verification_commit'],repaired_verification=True)


if __name__ == '__main__':
    if sys.argv[1:] != ['--worker']:
        raise SystemExit('use platform-update repair-router-verification on the active development controller')
    try:
        print(json.dumps(worker(json.loads(sys.stdin.read(16*1024*1024))), sort_keys=True))
    except Exception as error:
        print('router verification repair: '+str(error), file=sys.stderr)
        raise SystemExit(1)
