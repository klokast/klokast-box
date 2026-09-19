"""Compare fixed shared-VM configuration with checked Ansible recipes.

This is read-only evidence. It neither approves machine inputs nor authorizes
adoption. Guest contents and controller-private inventory stay on the active
controller; reports contain only paths, checksums, and match results.
"""
import hashlib
import json
from pathlib import Path
import re
import shlex
import subprocess

import yaml


PATHS = (
    '/etc/apk/arch', '/etc/apk/repositories', '/etc/hostname', '/etc/hosts',
    '/etc/network/interfaces', '/etc/fstab', '/etc/subuid', '/etc/subgid',
    '/etc/doas.d/doas.conf', '/etc/motd',
    '/etc/containers/containers.conf.d/10-klokast-network.conf',
    '/etc/containers/registries.conf',
    '/etc/klokast/app-resources/router-forward.d/000-empty.nft',
    '/etc/klokast/app-resources/vm-input.d/000-empty.nft',
    '/etc/klokast/app-resources/router-forward.nft',
    '/etc/klokast/app-resources/vm-input.nft',
    '/usr/local/libexec/klokast-app-resources-reconcile',
    '/etc/init.d/klokast-podman-runroot-cleanup', '/etc/nftables.nft',
)
HASH = re.compile(r'[0-9a-f]{64}')


class ConfigAuditError(RuntimeError):
    pass


def sha(value):
    return hashlib.sha256(value).hexdigest()


def environment():
    from jinja2 import Environment, StrictUndefined
    result = Environment(undefined=StrictUndefined, autoescape=False,
                         keep_trailing_newline=True, trim_blocks=True)
    result.filters.update(to_json=json.dumps, quote=shlex.quote)
    return result


def task_content(repo, relative, name):
    source = Path(repo) / relative
    def walk(tasks):
        for task in tasks:
            if task.get('name') == name:
                yield task
            for child in ('block', 'rescue', 'always'):
                if isinstance(task.get(child), list):
                    yield from walk(task[child])
    matches = list(walk(yaml.safe_load(source.read_text())))
    if len(matches) != 1 or 'ansible.builtin.copy' not in matches[0]:
        raise ConfigAuditError('fixed recipe task is missing or ambiguous: ' + name)
    content = matches[0]['ansible.builtin.copy'].get('content')
    if not isinstance(content, str):
        raise ConfigAuditError('fixed recipe task has no literal content: ' + name)
    return content, relative


def render(repo, host, variables):
    """Return exact file bytes and public source path for supported files."""
    if (not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,30}-(?:dmz|iot)', host) or
            variables.get('node_hostname') not in (host, '{{ node_name }}-' + host.rsplit('-', 1)[1]) or
            variables.get('node_domain_role') != host.rsplit('-', 1)[1]):
        raise ConfigAuditError('checked inventory does not name the selected guest')
    env = environment()
    context = dict(variables)
    context['podman_clone_hostname'] = host
    context['podman_clone_network_interfaces'] = env.from_string(
        variables['vm_bootstrap_network_interfaces']).render(variables)
    context['podman_host_runner_user'] = 'neo'
    context['podman_host_subid_start'] = 100000
    context['podman_host_subid_count'] = 65536
    for key in ('podman_vm_firewall_input_tcp_rules', 'podman_vm_firewall_input_udp_rules',
                'podman_vm_firewall_forward_egress_interfaces'):
        rows = variables.get(key, [])
        if not isinstance(rows, list):
            raise ConfigAuditError('checked firewall input has an unsupported type')
        context[key] = [
            {name: env.from_string(value).render(variables) if isinstance(value, str) else value
             for name, value in row.items()} if isinstance(row, dict) else row
            for row in rows
        ]
    result = {}
    def add(path, value, source):
        if path not in PATHS or path in result or not isinstance(value, str) or not value:
            raise ConfigAuditError('fixed recipe has a duplicate or empty file')
        result[path] = {'sha256': sha(value.encode()), 'source': source,
                        'source_sha256': sha((Path(repo) / source).read_bytes())}
    recipe = [
        ('/etc/apk/repositories', 'ansible/roles/vm-base/tasks/main.yml',
         'Ensure Alpine repositories are configured'),
        ('/etc/doas.d/doas.conf', 'ansible/roles/vm-base/tasks/main.yml',
         'Configure doas for the wheel group'),
        ('/etc/motd', 'ansible/roles/vm-base/tasks/main.yml',
         'Ensure the VM MOTD is configured'),
        ('/etc/hostname', 'ansible/roles/podman-guest-clone/tasks/main.yml',
         'Persist the Podman guest hostname'),
        ('/etc/hosts', 'ansible/roles/podman-guest-clone/tasks/main.yml',
         'Persist the Podman guest hosts file'),
        ('/etc/network/interfaces', 'ansible/roles/podman-guest-clone/tasks/main.yml',
         'Persist the Podman guest network interfaces'),
        ('/etc/fstab', 'ansible/roles/podman-template-rootfs/tasks/main.yml',
         'Seed Podman template filesystem table'),
        ('/etc/containers/containers.conf.d/10-klokast-network.conf',
         'ansible/roles/podman-host/tasks/main.yml',
         'Prefer the nftables firewall backend when Podman needs host firewall integration'),
        ('/etc/klokast/app-resources/router-forward.d/000-empty.nft',
         'ansible/roles/app-resources/tasks/main.yml',
         'Ensure app-resource include glob sentinels exist'),
        ('/etc/klokast/app-resources/vm-input.d/000-empty.nft',
         'ansible/roles/app-resources/tasks/main.yml',
         'Ensure app-resource include glob sentinels exist'),
        ('/etc/klokast/app-resources/router-forward.nft',
         'ansible/roles/app-resources/tasks/main.yml',
         'Retire legacy aggregate app-resource include files'),
        ('/etc/klokast/app-resources/vm-input.nft',
         'ansible/roles/app-resources/tasks/main.yml',
         'Retire legacy aggregate app-resource include files'),
    ]
    for path, source, name in recipe:
        content, source = task_content(repo, source, name)
        add(path, env.from_string(content).render(context), source)
    fixed = 'ansible/update-profiles/shared-alpine-v1.json'
    add('/etc/apk/arch', 'x86_64\n', fixed)
    source = 'ansible/roles/podman-host/tasks/main.yml'
    for name in ('subuid', 'subgid'):
        add('/etc/' + name, 'neo:100000:65536\n', source)
    for path, source in (
        ('/etc/containers/registries.conf', 'ansible/roles/podman-host/templates/registries.conf.j2'),
        ('/etc/init.d/klokast-podman-runroot-cleanup',
         'ansible/roles/podman-host/templates/klokast-podman-runroot-cleanup.init.j2'),
        ('/etc/nftables.nft', 'ansible/roles/podman-vm-firewall/templates/nftables.nft.j2'),
    ):
        add(path, env.from_string((Path(repo) / source).read_text()).render(context), source)
    source = 'ansible/roles/app-resources/files/reconcile-app-resources.py'
    add('/usr/local/libexec/klokast-app-resources-reconcile',
        (Path(repo) / source).read_text(), source)
    if set(result) != set(PATHS):
        raise ConfigAuditError('fixed recipe coverage is incomplete')
    return result


def parse_guest_hashes(output):
    result = {}
    for line in output.splitlines():
        parts = line.split()
        if len(parts) != 2 or not HASH.fullmatch(parts[0]) or parts[1] not in PATHS or parts[1] in result:
            raise ConfigAuditError('guest configuration hash response is incomplete or unsafe')
        result[parts[1]] = parts[0]
    if set(result) != set(PATHS):
        raise ConfigAuditError('guest configuration hash response lacks a fixed path')
    return result


def guest_hashes(host):
    script = 'doas sha256sum ' + ' '.join(PATHS) + '\n'
    try:
        response = subprocess.run(['tailscale', 'ssh', 'neo@' + host, 'sh', '-s'],
                                  input=script, text=True, capture_output=True, timeout=45)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ConfigAuditError('selected guest configuration hash probe failed') from error
    if response.returncode or len(response.stdout) > 8192:
        raise ConfigAuditError('selected guest configuration hash probe failed')
    return parse_guest_hashes(response.stdout)


def report(host, source_commit, qualification_sha256, expected, observed, approved_engine):
    if set(expected) != set(PATHS) or set(observed) != set(PATHS):
        raise ConfigAuditError('fixed configuration coverage differs')
    if not HASH.fullmatch(qualification_sha256):
        raise ConfigAuditError('qualification report identity is invalid')
    rows = [{'path': path, 'expected_sha256': expected[path]['sha256'],
             'observed_sha256': observed[path], 'source': expected[path]['source'],
             'source_sha256': expected[path]['source_sha256'],
             'match': expected[path]['sha256'] == observed[path]}
            for path in PATHS]
    value = {'kind': 'klokast.vm-config-comparison.v1', 'host': host,
             'source_commit': source_commit, 'approved_engine': approved_engine,
             'qualification_sha256': qualification_sha256,
             'authority': 'comparison-only', 'rows': rows}
    value['report_sha256'] = sha(json.dumps(value, sort_keys=True, separators=(',', ':')).encode())
    return value
