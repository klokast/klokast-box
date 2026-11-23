"""Name the configuration that a replacement root receives after boot.

The image identity names the qualified image. This identity names the public
recipe and the declared inputs that the post-boot configuration applies to the
same root. An update replaces the root when either identity differs from the
installed root. Mechanisms that change state in place own their inputs, so
those inputs are not part of this identity: the controller checkout
(controller-checkout-update) and the Platform resource rules on routers and
controllers (platform-resources).
"""
import hashlib
import json
from pathlib import Path
import subprocess

import platform_resource_model as model
import platform_source
from platform_updates import digest

REPO = Path(__file__).resolve().parents[2]
ROLE_DIRECTORY = 'ansible/roles/'
GROUP_VARS = 'ansible/inventory-policy/group_vars/'
NETWORK = ('ansible/roles/vm-template-builder/files/ops_network.py',
           'ansible/roles/vm-template-builder/files/ops_network_guest.py')

# Each recipe lists the post-boot playbooks, the complete roles they import, and the
# files that those roles copy from outside their own directory. A repository test
# parses the playbooks and fails when a reference is missing here.
RECIPES = {
    'shared': {
        'playbooks': {'69-dmz-configure.yml': None},
        'roles': ('alpine-mirror', 'ops-network', 'podman-host', 'podman-vm-firewall', 'vm-boot-assignment', 'vm-dns'),
        'files': NETWORK,
    },
    'air': {
        'playbooks': {'74-vm-infrastructure-configure.yml': 'air'},
        'roles': ('air-verification', 'air-workspace', 'ops-network', 'podman-vm-firewall', 'vm-dns', 'vpn-egress-client'),
        'files': NETWORK + ('ansible/bin/air-update', 'ansible/bin/ops-controller-ha') + tuple(
            'ansible/lib/' + name for name in (
                'air_image_workspace.py', 'air_workspace.py', 'codex_live.py', 'codex_workspace.py',
                'platform_proxy.py')),
    },
    'ops': {
        'playbooks': {'74-vm-infrastructure-configure.yml': 'ops'},
        'roles': ('alpine-mirror', 'ops-controller', 'ops-controller-verification', 'ops-data', 'ops-network',
                  'podman-vm-firewall', 'router-boot-assignment', 'tailscale-client', 'vm-base',
                  'vm-boot-assignment', 'vm-dns', 'vpn-egress-client'),
        # The playbook and ops-controller import only these task files of other roles.
        'files': NETWORK + ('ansible/roles/ops-replacement/tasks/configure.yml',
                            'ansible/roles/ops-replacement/tasks/preflight.yml',
                            'ansible/roles/platform-update-discovery/tasks/main.yml',
                            'ansible/lib/alpine_mirrors.py', 'ansible/lib/github_metadata.py',
                            'ansible/update-profiles/ops-alpine-v1.json',
                            'klokast-ops/secret-authority/bin/ksa-github-metadata',
                            'klokast-ops/tailscale/bin/ts-authkey-mint',
                            'klokast-ops/tailscale/policy.hujson.j2') + tuple(
            'ansible/bin/' + name for name in (
                'ops-controller-ha', 'platform-ansible', 'platform-inventory', 'platform-known-hosts',
                'platform-maintenance', 'platform-registry', 'platform-source', 'platform-tailscale-ssh')),
    },
    'vpn-egress': {
        'playbooks': {'69-vpn-egress-runtime.yml': None},
        'roles': ('vm-dns', 'vpn-egress-runtime'),
        'files': (),
    },
    'router': {
        'playbooks': {'74-router-candidate-render.yml': None},
        'roles': ('router', 'router-boot-assignment', 'router-verification'),
        'files': (),
    },
}
RECIPE_OF = {'bak': 'shared', 'dmz': 'shared', 'iot': 'shared', 'air': 'air', 'ops': 'ops',
             'vpn-egress': 'vpn-egress', 'router': 'router'}


def file_digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def recipe(role, *, repo=REPO):
    """Hash the public files that configure a root of this role after boot."""
    if role not in RECIPE_OF:
        raise RuntimeError('no configuration recipe is declared for role ' + role)
    spec = RECIPES[RECIPE_OF[role]]
    names = ['ansible/playbooks/' + name for name in spec['playbooks']] + list(spec['files'])
    for name in spec['roles']:
        directory = repo / (ROLE_DIRECTORY + name)
        if not directory.is_dir():
            raise RuntimeError('configuration recipe names a missing role: ' + name)
        names += [str(path.relative_to(repo)) for path in directory.rglob('*')
                  if path.is_file() and '__pycache__' not in path.parts]
    files = {}
    for name in sorted(set(names)):
        path = repo / name
        if not path.is_file():
            raise RuntimeError('configuration recipe names a missing file: ' + name)
        files[name] = file_digest(path)
    return files


def host_inventory(host, graph, *, repo=REPO):
    """Select the Instance values and group policy that Ansible gives this host."""
    hostvars = graph.get('_meta', {}).get('hostvars', {})
    if host not in hostvars:
        raise RuntimeError('Instance inventory does not declare host ' + host)
    groups = {name for name, row in graph.items() if name != '_meta' and host in row.get('hosts', [])}
    while True:
        parents = {name for name, row in graph.items()
                   if name != '_meta' and name not in groups and groups & set(row.get('children', []))}
        if not parents:
            break
        groups |= parents
    groups.add('all')
    policy = {name: file_digest(repo / (GROUP_VARS + name + '.yml'))
              for name in sorted(groups) if (repo / (GROUP_VARS + name + '.yml')).is_file()}
    return {'hostvars': hostvars[host], 'groups': {name: graph.get(name, {}).get('vars', {}) for name in sorted(groups)},
            'group_vars': policy}


def instance_inventory():
    value = json.loads(platform_source.as_controller(
        [str(platform_source.BINARY), 'inventory', '--instance', str(platform_source.INSTANCE), '--json']))
    if value.get('valid') is not True:
        raise RuntimeError('Instance inventory is invalid; the configuration identity is unknown')
    return value['projection']['inventory']


def declared(box, role, *, repo=REPO):
    """Return the declared values that the post-boot configuration of this root applies."""
    if RECIPE_OF.get(role) == 'shared':
        import dmz_provisioning
        public, runtime = dmz_provisioning.configuration(box, repo=repo, role=role)
        return {'creation': public, 'runtime': runtime}
    if role in ('air', 'ops'):
        from vm_control_configuration import guest_config
        view = platform_source.snapshot()['rendered']['projection']
        value = {'creation': guest_config(box, role, view=view)}
        if role == 'ops':
            import ops_replacement_inputs as inputs
            import vm_control_toolchains
            value['toolchains'] = vm_control_toolchains.resolve()
            value['instance_repository'] = inputs.run(['git', '-C', inputs.INSTANCE, 'remote', 'get-url', 'origin'])
        return value
    if role == 'vpn-egress':
        import vpn_egress_provisioning
        snapshot = platform_source.snapshot()
        topology = model.load_topology(repo_root=repo)
        config = model.registry_box_configs(snapshot['rendered']['projection']['registry'], topology)[box]
        return {'vpn': vpn_egress_provisioning.configuration_signature(box, config, topology, repo=repo)}
    if role == 'router':
        import platform_resource_compiler as compiler
        compiled = json.loads(subprocess.run([str(repo / 'ansible/bin/platform-resources'), 'show'],
                                             check=True, capture_output=True, text=True, timeout=180).stdout)
        # App include files are excluded: platform-apply changes them in place.
        return {'router': compiler.box_access_router_vars(compiled, box)}
    raise RuntimeError('no declared configuration is defined for role ' + role)


def compute(box, role, *, repo=REPO):
    """Return the SHA-256 that names the configuration of a new root for box-role."""
    return digest({'kind': 'klokast.vm-configuration-identity.v1', 'box': box, 'role': role,
                   'recipe': recipe(role, repo=repo),
                   'inventory': host_inventory(box + '-' + role, instance_inventory(), repo=repo),
                   'declared': declared(box, role, repo=repo)})
