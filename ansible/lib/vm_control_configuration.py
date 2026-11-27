"""Shared controller and runner guest configuration for creation and replacement."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import platform_resource_model as model

REPO = Path(__file__).resolve().parents[2]

def guest_config(box, role, uid=1004, gid=1004, *, view=None):
    topology = model.load_topology(repo_root=REPO)
    zone = topology['zones']['usr'] if role == 'air' else topology['control_zones']['ops']
    if view is None:
        import platform_source
        view = platform_source.snapshot()["rendered"]["projection"]
    box_config = view['registry']['boxes'][box]
    if 'bootstrap_dns' not in box_config:
        raise RuntimeError('Instance projection lacks country DNS policy; update the controller CLI first')
    address = topology['air']['ipv4_address'] if role == 'air' else zone['vm_ipv4_address']
    sys.path.insert(0, str(REPO / 'ansible/roles/vm-template-builder/files'))
    import ops_network
    return {**({'uplink': ops_network.uplink(box)} if role == 'ops' else {}),
            'kind': 'klokast.infrastructure-config.v1', 'box': box, 'role': role,
            'bridge': zone['bridge'], 'bootstrap_dns': model.bootstrap_dns(box_config),
            'address': address + '/' + str(zone['router_ipv4_prefix']), 'gateway': zone['router_ipv4_address'],
            # The dom0 address of the controller link; no first-contact key exists.
            **({'bootstrap_source': zone['dom0_ipv4_address']} if role == 'ops' else {}),
            'agent_uid': uid, 'agent_gid': gid}



def runner_files(box, public, *, repo=REPO, inventory=None):
    """Render the runner files that depend on Instance values.

    The image finalizer writes them before the first boot, so no step after boot
    changes the runner root. The render runs on the controller and does not
    connect to a runner.
    """
    with tempfile.TemporaryDirectory(prefix='air-candidate-') as directory:
        work = Path(directory)
        arguments, result = work / 'arguments.json', work / 'runner-files.json'
        arguments.write_text(json.dumps({'air_candidate_result': str(result),
                                         'air_candidate_bootstrap_dns': public['bootstrap_dns']}))
        # The playbook output goes to the operation log, not to the JSON result.
        completed = subprocess.run(
            ['ansible-playbook', '-i', str(inventory or repo / 'ansible/execution-inventory/hosts'),
             str(repo / 'ansible/playbooks/74-air-candidate-render.yml'), '--limit', box + '-air',
             '-e', '@' + str(arguments)],
            cwd=repo, env=dict(os.environ, ANSIBLE_CONFIG=str(repo / 'ansible/ansible.cfg')),
            stdout=sys.stderr, stderr=sys.stderr, timeout=300)
        if completed.returncode or not result.is_file():
            raise RuntimeError('rendering the runner files of ' + box + '-air failed; '
                               'read the 74-air-candidate-render.yml output above')
        return json.loads(result.read_text())
