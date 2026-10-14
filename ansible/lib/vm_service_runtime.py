"""Controller authority, transport and bounded health checks for service VMs."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time

import ops_replacement_inputs as inputs
import platform_source
import vm_service_probe

REPO = Path(__file__).resolve().parents[2]
ROOT = Path('/home/smith/private/klokast/vm-replacement-inputs')
ROLES = ('bak', 'dmz', 'iot', 'vpn-egress')


def resume_command(box, record):
    if record.get('kind')=='klokast.infrastructure-assignment.v1':
        return 'ansible/bin/platform-update provision --box '+box+' --role '+record['role']
    return 'ansible/bin/platform-update update --box '+box+' --role '+record['role']+' --resume '+record['operation_id']


def profile(role):
    if role not in ROLES: raise RuntimeError('unsupported service VM role')
    return 'vpn-egress-alpine-v1' if role == 'vpn-egress' else 'shared-alpine-v1'


def target(view, box, role):
    declared = view['instance']['boxes'].get(box)
    if declared is None or role not in ROLES: raise RuntimeError('service target is not declared')
    if role == 'vpn-egress':
        if not declared.get('vpn-egress'): raise RuntimeError('VPN gateway is not declared')
        return 'running'
    return declared.get('substrate', {}).get('shared-guests', {}).get(role, {}).get('runtime-state', 'running')


def authority(box, role):
    return target(platform_source.snapshot(), box, role)


def step(box, role, action, *, repo=REPO, inventory=None, operation='', payload=None, receipt=None, checksum=''):
    if authority(box, role) != 'running' and action != 'status':
        raise RuntimeError('service runtime intent changed; preserve the operation and inspect before resume')
    with tempfile.TemporaryDirectory(prefix='service-step-') as temporary:
        output = Path(temporary) / 'result.json'
        variables = {'service_box':box,'service_role':role,'service_action':action,'service_operation':operation,
                     'service_payload':payload or {},'service_receipt':receipt or {},'service_inputs_sha256':checksum,
                     'service_result_path':str(output)}
        inputs.save(Path(temporary) / 'variables.json', variables)
        result = subprocess.run(['ansible-playbook','-vv','-i',str(inventory or REPO/'ansible/execution-inventory/hosts'),
                 str(repo/'ansible/playbooks/74-vm-service-update.yml'),'--limit',box+'-dom0',
                 '-e','@'+str(Path(temporary)/'variables.json')],cwd=repo,
                 env=dict(os.environ,ANSIBLE_CONFIG=str(repo/'ansible/ansible.cfg')),timeout=4200)
        if result.returncode: raise RuntimeError('service step failed: '+action+'; inspect the private operation log')
        return json.loads(output.read_text())


def wait_probe(box, role):
    deadline = time.monotonic()+300
    while True:
        try: return vm_service_probe.probe(box, role)
        except (RuntimeError, subprocess.SubprocessError) as error:
            if time.monotonic()>=deadline: raise RuntimeError('service did not pass health checks within five minutes') from error
            time.sleep(5)


def prepare_failure_hint(box, role, operation, image):
    command = 'ansible/bin/platform-update update --box '+box+' --role '+role
    try:
        observed = step(box, role, 'status', operation=operation)
        if 'operation' in observed and observed['operation'] is None:
            # The producer records ownership before allocating a disk. A retry
            # still checks for pending work and competing jobs before preparing.
            return '; no protected operation was recorded; retry with: '+command+' --image '+image
    except (RuntimeError, OSError, ValueError, subprocess.SubprocessError):
        pass  # A lost response is not evidence that allocation never started.
    return '; resume with: '+command+' --resume '+operation
