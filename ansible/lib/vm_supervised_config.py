"""Render the fixed shared-VM personalization files for a supervised cutover."""

from pathlib import Path

import vm_config_audit as audit


TASKS = {
    'etc/hostname': ('ansible/roles/podman-guest-clone/tasks/main.yml',
                     'Persist the Podman guest hostname'),
    'etc/hosts': ('ansible/roles/podman-guest-clone/tasks/main.yml',
                  'Persist the Podman guest hosts file'),
    'etc/network/interfaces': ('ansible/roles/podman-guest-clone/tasks/main.yml',
                               'Persist the Podman guest network interfaces'),
    'etc/containers/containers.conf.d/10-klokast-network.conf':
        ('ansible/roles/podman-host/tasks/main.yml',
         'Prefer the nftables firewall backend when Podman needs host firewall integration'),
    'etc/klokast/overlay-ipv6-input.nft':
        ('ansible/roles/podman-vm-firewall/tasks/main.yml',
         'Ensure the disabled overlay IPv6 input include exists'),
    'etc/klokast/app-resources/vm-input.nft':
        ('ansible/roles/podman-vm-firewall/tasks/main.yml',
         'Ensure Podman VM app-resource include file exists'),
}
TEMPLATES = {
    'etc/nftables.nft': 'ansible/roles/podman-vm-firewall/templates/nftables.nft.j2',
    'etc/containers/registries.conf': 'ansible/roles/podman-host/templates/registries.conf.j2',
    'etc/init.d/klokast-podman-runroot-cleanup':
        'ansible/roles/podman-host/templates/klokast-podman-runroot-cleanup.init.j2',
}


def render(repo, host, variables, resolv_conf):
    """Use checked recipes and the current Tailscale-managed resolver content."""
    if (not isinstance(resolv_conf, str) or not 1 <= len(resolv_conf.encode()) <= 65536 or
            '\0' in resolv_conf or not resolv_conf.endswith('\n') or
            'nameserver 100.100.100.100\n' not in resolv_conf):
        raise audit.ConfigAuditError('guest resolver does not have the supported Tailscale configuration')
    env, context = audit.render_context(host, variables)
    result = {'etc/resolv.conf': resolv_conf}
    for target, (source, task) in TASKS.items():
        content, _source = audit.task_content(repo, source, task)
        result[target] = env.from_string(content).render(context)
    for target, source in TEMPLATES.items():
        result[target] = env.from_string((Path(repo) / source).read_text()).render(context)
    if (set(result) != {'etc/resolv.conf', *TASKS, *TEMPLATES} or
            result['etc/hostname'] != host + '\n' or
            any(not isinstance(value, str) or not value or '\0' in value or
                len(value.encode()) > 65536 for value in result.values())):
        raise audit.ConfigAuditError('supervised personalization recipe is incomplete')
    return result
