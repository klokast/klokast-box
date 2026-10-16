"""Shared controller and runner guest configuration for creation and replacement."""
from pathlib import Path
import sys
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
            'bootstrap_source': topology['zones']['bak']['dom0_ipv4_address'] if role == 'air' else zone['dom0_ipv4_address'],
            'public_key': (Path.home() / '.ssh/github-klokast-codex.pub').read_text().strip(),
            'agent_uid': uid, 'agent_gid': gid}

