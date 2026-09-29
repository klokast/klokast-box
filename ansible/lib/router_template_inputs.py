"""Controller-side router-only build staging. Outputs are qualification evidence."""
from pathlib import Path
import json
import tarfile

import router_updates
import vm_template_inputs
from platform_updates import UpdateError


def personalization_fixture(repo, manifest):
    """Render a synthetic topology from the same templates as router convergence."""
    from jinja2 import Environment, StrictUndefined
    import router_personalize
    env = Environment(undefined=StrictUndefined, autoescape=False, keep_trailing_newline=True,
                      trim_blocks=True)
    env.filters['bool'] = bool
    variables = {'router_wan_interface': 'eth0', 'router_local_lan_enabled': False,
                 'router_ap_uplink_enabled': False,
                 'router_internal_interfaces': ['eth' + str(i) for i in range(1, 6)],
                 'router_dns_upstreams': ['192.0.2.53'], 'router_dhcp_hosts': [],
                 'router_dhcp_ranges': [{'name': 'iot', 'interface': 'eth3',
                    'start': '198.18.3.50', 'end': '198.18.3.150', 'router': '198.18.3.1'}]}
    for i, zone in enumerate(('dmz', 'backend', 'iot', 'usr', 'ops'), 1):
        variables.update({f'router_{zone}_interface': 'eth' + str(i),
                          f'router_{zone}_ipv4_address': f'198.18.{i}.1',
                          f'router_{zone}_ipv4_netmask': '255.255.255.0'})
    files = {'etc/hostname': 'boxa-router\n',
             'etc/hosts': '127.0.0.1 localhost\n::1 localhost\n',
             'etc/resolv.conf': 'nameserver 192.0.2.53\n',
             'etc/sysctl.conf': 'net.ipv4.ip_forward=1\nnet.ipv6.conf.all.forwarding=0\n',
             'etc/klokast/overlay-ipv6.nft': '# Ops IPv6 downstream is disabled.\n',
             'etc/klokast/app-resources/router-forward.nft': '# No synthetic application rules.\n',
             'etc/klokast/app-resources/router-forward.d/000-empty.nft': '# Empty placeholder so nft include globs always match.\n'}
    for target, source in (('etc/network/interfaces', 'interfaces.j2'), ('etc/dhcpcd.conf', 'dhcpcd.conf.j2'),
                           ('etc/dnsmasq.conf', 'dnsmasq.conf.j2'), ('etc/nftables.nft', 'nftables.nft.j2')):
        files[target] = env.from_string((Path(repo) / 'ansible/roles/router/templates' / source).read_text()).render(variables)
    value = {'kind': 'klokast.router-personalization.v1', 'box': 'boxa', 'role': 'router',
             'inputs_sha256': manifest['inputs_sha256'], 'files': files,
             'packages': {p['name']: p['version'] for p in manifest['packages']}}
    router_personalize.validate(value)
    return value


def stage(source, work, profile, engine, guest):
    import router_update_controller as transport
    source, work, guest = Path(source), Path(work), Path(guest)
    manifest = transport.load(source / 'inputs.json')
    router_updates.validate_inputs(manifest, profile, engine)
    vm_template_inputs.verify_inputs(source, manifest, expected_profile=router_updates.PROFILE)
    if not work.is_dir() or work.is_symlink() or any(work.iterdir()):
        raise UpdateError('router build staging requires a new empty directory')
    repo = guest.parents[4]
    fixture = work / 'personalization.json'
    fixture.write_text(json.dumps(personalization_fixture(repo, manifest), sort_keys=True) + '\n')
    capsule = work / 'capsule.tar'
    with tarfile.open(capsule, 'x', format=tarfile.USTAR_FORMAT) as archive:
        for relative in ['inputs.json', *['keys/' + name for name in sorted(manifest['keys'])],
                         *[p['file'] for p in manifest['packages']], manifest['tailscale']['file'],
                         manifest['tailscale']['openrc_file']]:
            archive.add(source / relative, arcname=relative, recursive=False)
        archive.add(guest, arcname='guest.py', recursive=False)
        archive.add(repo / 'ansible/lib/router_personalize.py', arcname='router_personalize.py', recursive=False)
        archive.add(repo / 'ansible/lib/router_service_probe.py', arcname='router_service_probe.py', recursive=False)
        archive.add(repo / 'ansible/lib/router_finalize.py', arcname='router_finalize.py', recursive=False)
        archive.add(fixture, arcname='personalization.json', recursive=False)
    # Native APK extraction is scriptless and unprivileged on the controller.
    # The template's package scripts and filesystem tools run only inside Xen.
    boot = vm_template_inputs.bootstrap(source, work / 'boot', guest, expected_profile=router_updates.PROFILE)
    vm_template_inputs.verify_inputs(source, manifest, expected_profile=router_updates.PROFILE)
    return manifest, {'sha256': vm_template_inputs.sha256(capsule), 'bytes': capsule.stat().st_size}, boot


def release(candidate, manifest, profile, engine, box, operation, *, approved_engine):
    if approved_engine != engine:
        raise UpdateError('router release requires the exact controller-approved engine commit')
    if (not isinstance(candidate, dict) or
            candidate.get('kind') != 'klokast.router-template-candidate.v1' or
            candidate.get('box') != box or candidate.get('role') != 'router' or
            candidate.get('operation_id') != operation or
            candidate.get('inputs_sha256') != manifest['inputs_sha256'] or
            candidate.get('replacement_authorized') is not False):
        raise UpdateError('router template result belongs to another operation or target')
    personalized = candidate.get('personalization_test')
    if (not isinstance(personalized, dict) or personalized.get('success') is not True or
            personalized.get('operation_id') != operation or
            personalized.get('inputs_sha256') != manifest['inputs_sha256'] or
            personalized.get('kernel_release') != candidate['kernel_release'] or
            personalized.get('tests') != dict.fromkeys(('personalization', 'exact_packages',
                'identity_absent', 'service_syntax', 'native_services'), True)):
        raise UpdateError('router release requires matching native personalization and service evidence')
    finalization = personalized.get('finalization')
    if not isinstance(finalization, dict) or finalization.get('kind') != 'klokast.router-finalization.v1':
        raise UpdateError('router release requires native first-contact package retirement')
    value = router_updates.seal({'kind': router_updates.RELEASE, 'profile': router_updates.PROFILE,
        'engine_commit': engine, 'inputs': manifest, 'kernel_release': candidate['kernel_release'],
        'artifacts': {k: v['sha256'] for k, v in candidate['artifacts'].items()},
        'generic_tests': candidate['generic_tests'], 'runtime_packages': finalization.get('packages'),
        'runtime_tests': finalization.get('tests')})
    router_updates.validate_release(value, profile, engine)
    return value
