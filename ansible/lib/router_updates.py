"""Closed router release and decision contracts. No Platform mutations.

Evidence is not authority. Callers must read accepted assignments from protected
box state and policy from the verified Instance reader. A report cannot authorize
initial installation, adoption, or replacement.
"""
import datetime as dt
import hashlib
import re

import router_state
import router_generations
import router_records
import router_initial_installation
from platform_updates import UpdateError, branch_number, digest, fresh, timestamp
from platform_update_metadata import adjacent_stable_branch, newest_stable_branch

PROFILE = 'router-alpine-v2'
RELEASE = 'klokast.router-release.v2'
DECISION = 'klokast.router-update-check.v1'
HASH = re.compile(r'[0-9a-f]{64}')
NAME = re.compile(r'[a-zA-Z0-9][a-zA-Z0-9+_.-]*')
VERSION = re.compile(r'[a-zA-Z0-9][a-zA-Z0-9+_.~-]*')
BOX = re.compile(r'[a-z0-9][a-z0-9-]{0,30}')


def match(pattern, value):
    return isinstance(value, str) and pattern.fullmatch(value) is not None


def seal(value, field='receipt_sha256'):
    if field in value:
        raise UpdateError('cannot seal a record that already has a checksum')
    return {**value, field: digest(value)}


def closed(value, fields, label):
    if not isinstance(value, dict) or set(value) != set(fields.split()):
        raise UpdateError(label + ' has missing or unknown fields')


def verify_seal(value, field='receipt_sha256'):
    if (not isinstance(value, dict) or not match(HASH, value.get(field)) or
            value[field] != digest({k: v for k, v in value.items() if k != field})):
        raise UpdateError('record checksum does not match its contents')


def validate_profile(profile):
    closed(profile, 'kind profile architecture roles branch_policy state_contract packages '
           'repositories repository_origin release_metadata', 'router profile')
    if (profile['kind'] != 'klokast.vm-template-profile.v1' or profile['profile'] != PROFILE or
            profile['architecture'] != 'x86_64' or profile['roles'] != ['router'] or
            profile['branch_policy'] != 'tested-stable' or
            profile['state_contract'] != 'klokast.router-state.v1' or
            profile['repositories'] != ['main', 'community'] or
            profile['repository_origin'] != 'https://dl-cdn.alpinelinux.org/alpine' or
            profile['release_metadata'] != 'https://alpinelinux.org/releases.json'):
        raise UpdateError('unsupported router profile, architecture, or upstream origin')
    packages = profile['packages']
    if (not isinstance(packages, list) or not packages or
            any(not match(NAME, p) for p in packages) or len(set(packages)) != len(packages) or
            not {'alpine-base', 'linux-virt', 'mkinitfs', 'openssh',
                 'dhcpcd', 'dnsmasq', 'iproute2', 'nftables', 'python3', 'e2fsprogs'} <= set(packages)):
        raise UpdateError('router profile has an incomplete package request set')
    if {'tailscale', 'tailscale-openrc'} & set(packages):
        raise UpdateError('upstream Tailscale must not be requested from Alpine APK')


def validate_inputs(inputs, profile, engine):
    """Check receipt structure; native APK must separately verify payload bytes."""
    validate_profile(profile)
    closed(inputs, 'kind engine_commit profile profile_sha256 branch architecture world repositories '
           'keys indexes packages tailscale inputs_sha256', 'router inputs')
    verify_seal(inputs, 'inputs_sha256')
    branch_number(inputs['branch'])
    if (not match(re.compile(r'[0-9a-f]{40}'), engine) or inputs['engine_commit'] != engine or
            inputs['kind'] != 'klokast.vm-template-inputs.v1' or inputs['profile'] != PROFILE or
            inputs['profile_sha256'] != digest(profile) or inputs['architecture'] != 'x86_64' or
            inputs['world'] != sorted(profile['packages']) or inputs['repositories'] != [
                profile['repository_origin'] + '/' + inputs['branch'] + '/' + r
                for r in profile['repositories']]):
        raise UpdateError('router inputs differ from the engine, profile, or repositories')
    for field in ('keys', 'indexes'):
        values = inputs[field]
        if (not isinstance(values, dict) or not values or len(values) > 64 or
                any(not isinstance(k, str) or '/' in k or not match(HASH, v) for k, v in values.items())):
            raise UpdateError('router inputs have invalid signing key or index evidence')
    if len(inputs['indexes']) != 2:
        raise UpdateError('router inputs require both authenticated repository indexes')
    packages = inputs['packages']
    if not isinstance(packages, list) or not 1 <= len(packages) <= 512:
        raise UpdateError('router package closure is missing or too large')
    names = []
    for p in packages:
        closed(p, 'name version origin architecture file bytes sha256', 'router package')
        if (not match(NAME, p['name']) or not match(VERSION, p['version']) or
                not match(NAME, p['origin']) or p['architecture'] not in ('x86_64', 'noarch') or
                not match(HASH, p['sha256']) or type(p['bytes']) is not int or
                not 0 < p['bytes'] <= 512 * 1024 * 1024 or
                p['file'] != 'packages/' + p['name'] + '-' + p['version'] + '.apk'):
            raise UpdateError('router package has an invalid identity')
        names.append(p['name'])
    if names != sorted(set(names)) or not set(inputs['world']) <= set(names):
        raise UpdateError('router package closure is incomplete or duplicated')
    import router_tailscale
    router_tailscale.validate(inputs['tailscale'])


def effective_inputs(inputs):
    # APK index changes, signing-key rotation, and an announcement alone do not
    # change installed bytes. Engine and profile changes do affect the recipe.
    component = inputs['tailscale']
    return digest({**{k: inputs[k] for k in ('engine_commit', 'profile', 'profile_sha256',
                                           'branch', 'architecture', 'world', 'packages')},
                   'tailscale': {k: component[k] for k in ('version', 'sha256',
                                                           'tailscale_sha256', 'tailscaled_sha256',
                                                           'openrc_sha256')}})


def validate_release(receipt, profile, engine):
    closed(receipt, 'kind profile engine_commit inputs kernel_release artifacts generic_tests '
           'runtime_packages runtime_tests receipt_sha256', 'router release')
    verify_seal(receipt)
    validate_inputs(receipt['inputs'], profile, engine)
    if (receipt['kind'] != RELEASE or receipt['profile'] != PROFILE or
            receipt['engine_commit'] != engine or not match(VERSION, receipt['kernel_release'])):
        raise UpdateError('router release has a different engine, profile, or kernel')
    closed(receipt['artifacts'], 'os kernel initramfs', 'router artifacts')
    if any(not match(HASH, value) for value in receipt['artifacts'].values()):
        raise UpdateError('router release artifact hashes are invalid')
    closed(receipt['generic_tests'], 'identity_absent exact_packages upstream_tailscale kernel_modules openrc', 'generic tests')
    if any(value is not True for value in receipt['generic_tests'].values()):
        raise UpdateError('router generic template has not passed every native test')
    import router_finalize
    try:
        router_finalize.validate_packages({p['name']: p['version'] for p in receipt['inputs']['packages']},
                                          receipt['runtime_packages'], receipt['inputs']['world'])
    except ValueError as error:
        raise UpdateError(str(error)) from error
    closed(receipt['runtime_tests'], 'frozen_packages no_openssh_server locked_root pinned_world', 'router runtime tests')
    if any(value is not True for value in receipt['runtime_tests'].values()):
        raise UpdateError('router release lacks complete native runtime finalization evidence')


def dispatch(role):
    if role == 'router':
        return PROFILE
    if role in ('bak', 'dmz', 'iot'):
        return 'shared-alpine-v1'
    raise UpdateError('unsupported VM update role')


def unactivated_diagnostic_policy(schedule, box):
    """Use sealed Instance timing for a read-only router check without update authority."""
    if (not isinstance(schedule, dict) or set(schedule) != {'kind','policy','activated','replacement_ready'} or
            schedule.get('kind') != 'klokast.vm-update-schedule.v1' or
            schedule.get('activated') is not False or schedule.get('replacement_ready') is not False or
            not match(BOX, box) or
            not isinstance(schedule.get('policy'), dict)):
        raise UpdateError('unactivated router check requires verified Instance schedule intent')
    source = schedule['policy']
    if (source.get('branch-policy') != 'tested-stable' or
            type(source.get('branch-delay-days')) is not int or not 0 <= source['branch-delay-days'] <= 365 or
            type(source.get('report-max-age-hours')) is not int or not 24 <= source['report-max-age-hours'] <= 168 or
            not isinstance(source.get('targets'), dict)):
        raise UpdateError('Instance schedule has no supported router check timing')
    if any(not match(BOX, key) or not isinstance(roles, list) or
           len(roles) != len(set(roles)) or
           any(role not in ('bak', 'dmz', 'iot', 'router') for role in roles)
           for key, roles in source['targets'].items()):
        raise UpdateError('Instance schedule has unsupported target declarations')
    targets = {key: list(roles) for key, roles in source['targets'].items()}
    targets[box] = sorted(set(targets.get(box, [])) | {'router'})
    policy = {**source, 'targets':targets, 'enabled':False}
    return policy, digest({'kind':'klokast.router-unactivated-diagnostic.v1',
                           'box':box, 'schedule':schedule})


def rendered_includes(compiled, box):
    """Select reconstructable files from compiler output, never a running router."""
    if (not isinstance(compiled, dict) or compiled.get('compiler') != 'platform-resources' or
            not match(HASH, compiled.get('registry_sha256')) or
            not isinstance(compiled.get('box_configs'), dict) or box not in compiled['box_configs'] or
            not isinstance(compiled.get('app_resource_effective_files'), list)):
        raise UpdateError('router include inspection requires complete current resource compiler output')
    files = {
        '/etc/klokast/app-resources/router-forward.nft':
            '# Legacy aggregate include retired by keyed platform-resources.\n',
        '/etc/klokast/app-resources/router-forward.d/000-empty.nft':
            '# Empty placeholder so nft include globs always match.\n',
    }
    for row in compiled['app_resource_effective_files']:
        if not isinstance(row, dict):
            raise UpdateError('resource compiler has an invalid effective file')
        if row.get('node') != box or row.get('host_role') != 'router':
            continue
        name, content = row.get('filename'), row.get('content')
        if (row.get('kind') != 'router-forward' or not isinstance(name, str) or
                not re.fullmatch(r'[a-zA-Z0-9_-]+\.nft', name) or
                not isinstance(content, str) or not 0 < len(content.encode()) <= 128 * 1024):
            raise UpdateError('resource compiler has an unsupported router include')
        path = '/etc/klokast/app-resources/router-forward.d/' + name
        if path in files:
            raise UpdateError('resource compiler has duplicate router includes')
        files[path] = content
    if len(files) > 1024 or sum(len(content.encode()) for content in files.values()) > 512 * 1024:
        raise UpdateError('compiled router includes exceed the bounded personalization contract')
    return files


def expected_includes(compiled, box):
    """Use the same reconstructable file set for inspection and preparation."""
    files = rendered_includes(compiled, box)
    return {'registry_sha256': compiled['registry_sha256'],
            'files': {path: hashlib.sha256(content.encode()).hexdigest() for path, content in files.items()}}


def legacy_baseline_findings(guest, dom0, box, *, adopted=False):
    """Report missing legacy evidence without granting adoption authority."""
    findings = []
    for target, value in (('router', guest), ('dom0', dom0)):
        if (not isinstance(value, dict) or value.get('kind') != 'klokast.router-inspection.v1' or
                value.get('box') != box or value.get('target') != target):
            raise UpdateError('router baseline inspection belongs to another box or target')
    if not isinstance(guest.get('alpine_branch'), str) or not re.fullmatch(r'v[0-9]+\.[0-9]+', guest['alpine_branch']):
        findings.append('router Alpine stable branch evidence is missing')
    if (guest.get('tailscale_running') is not True or guest.get('tailscale_ssh') is not True or
            not guest.get('machine_id') or guest.get('tags') != ['tag:vm']):
        findings.append('router management identity is not fully active')
    unsupported = guest.get('unsupported_state')
    if (guest.get('overlay_ipv6_enabled') is not False or not isinstance(unsupported, dict) or
            set(unsupported) != {'/var/lib/tailscale/tka', '/var/lib/tailscale/tpm-sealed'} or
            any(value is not False for value in unsupported.values())):
        findings.append('router has state that the replacement recipe cannot reconstruct')
    paths = guest.get('state_paths')
    accounts = guest.get('service_accounts')
    account_fields = {'dnsmasq_uid', 'dnsmasq_gid', 'tailscale_gid'}
    if (not isinstance(accounts, dict) or set(accounts) != account_fields or
            any(type(value) is not int or not 1 <= value <= 65535 for value in accounts.values())):
        findings.append('router service-account evidence is incomplete')
        accounts = {}

    def owners(path):
        result = {(0, 0)}
        if accounts:
            if path == '/var/lib/misc/dnsmasq.leases':
                result.add((accounts['dnsmasq_uid'], accounts['dnsmasq_gid']))
            if path.startswith('/var/lib/tailscale/'):
                result.add((0, accounts['tailscale_gid']))
        return result

    required = {'/' + path: limit for path, limit in router_state.REQUIRED.items()}
    optional = {'/' + path: limit for path, limit in router_state.OPTIONAL.items()}
    if (not isinstance(paths, dict) or set(paths) != set(required) | set(optional) or
            any(not _copyable_metadata(paths.get(path), limit, private=path in (
                '/var/lib/tailscale/tailscaled.state', '/var/lib/dhcpcd/secret'),
                owners=owners(path))
                for path, limit in required.items()) or
            any(not _copyable_metadata(paths[path], limit, owners=owners(path))
                for path, limit in optional.items() if path in paths and
                (not isinstance(paths[path], dict) or paths[path].get('present') is not False))):
        findings.append('a required router identity or lease file is absent or unsafe')
    if guest.get('dnsmasq_lease_paths') != ['/var/lib/misc/dnsmasq.leases']:
        findings.append('dnsmasq does not declare the fixed retained lease file')
    keys = guest.get('ssh_keys')
    if (not isinstance(keys, dict) or set(keys) != set(router_state.KEY_TYPES) or any(
            not isinstance(item, dict) or item.get('path') not in (
                '/etc/ssh/ssh_host_' + kind + '_key',
                '/var/lib/tailscale/ssh/ssh_host_' + kind + '_key') or
            not isinstance(item.get('fingerprint'), str) or
            not re.fullmatch(r'SHA256:[A-Za-z0-9+/]{43}', item['fingerprint']) or
            not _copyable_metadata(item.get('metadata'), (16384, False), private=True, owners=owners(item['path']))
            for kind, item in keys.items())):
        findings.append('effective router SSH host-key evidence is incomplete')
    first_contact = guest.get('first_contact_key')
    if not isinstance(first_contact, dict) or first_contact.get('present') is not False:
        findings.append('first-contact root SSH access is still present or unknown')
    openssh = guest.get('openssh_paths')
    if (guest.get('sshd_running') is not False or not isinstance(openssh, dict) or
            set(openssh) != {'/usr/sbin/sshd', '/etc/init.d/sshd', '/etc/runlevels/default/sshd'} or
            any(not isinstance(item, dict) or item.get('present') is not False
                for item in openssh.values()) or
            isinstance(guest.get('packages'), dict) and
            any(name in guest['packages'] for name in ('openssh', 'openssh-server',
                                                       'openssh-server-common', 'openssh-server-common-openrc'))):
        findings.append('router OpenSSH server retirement is incomplete or unknown')
    if guest.get('root_password_locked') is not True:
        findings.append('router root password is not proved locked')
    expected, observed = guest.get('expected_configuration'), guest.get('configuration_files')
    core_files = {'/etc/network/interfaces', '/etc/dhcpcd.conf', '/etc/dnsmasq.conf', '/etc/nftables.nft'}
    # New adoption uses current intent. An adopted disk instead uses its
    # protected generation hashes in legacy_live; a newer engine may render
    # a different candidate without changing that running disk.
    if (not isinstance(observed, dict) or set(observed) != core_files or any(
            not isinstance(observed[path], dict) or
            not match(HASH, observed[path].get('sha256')) or
            not _copyable_metadata(observed[path].get('metadata'), (128 * 1024, False), owners={(0, 0)})
            for path in core_files) or not adopted and (
                not isinstance(expected, dict) or set(expected) != core_files or any(
                    not match(HASH, expected[path]) or observed[path]['sha256'] != expected[path]
                    for path in core_files))):
        findings.append('router core configuration differs from the current compiled inventory and templates')
    if (not isinstance(guest.get('packages'), dict) or not guest['packages'] or
            not isinstance(guest.get('kernel_release'), str) or not guest['kernel_release']):
        findings.append('installed router package or kernel evidence is missing')
    expected_includes_record, observed_includes = guest.get('expected_includes'), guest.get('include_files')
    expected_files = expected_includes_record.get('files') if isinstance(expected_includes_record, dict) else None
    if (not isinstance(expected_includes_record, dict) or
            not match(HASH, expected_includes_record.get('registry_sha256')) or
            not isinstance(expected_files, dict) or not expected_files or
            not isinstance(observed_includes, dict) or set(observed_includes) != set(expected_files) or any(
                not match(HASH, checksum) or not isinstance(observed_includes.get(path), dict) or
                observed_includes[path].get('sha256') != checksum or not _copyable_metadata(
                    observed_includes[path].get('metadata'), (128 * 1024, False), owners={(0, 0)})
                for path, checksum in expected_files.items())):
        findings.append('router generated firewall or DNS includes differ from the current resource compiler')
    if (dom0.get('accepted_record_present') is not adopted or
            dom0.get('pending_record_present') is not False):
        findings.append('router assignment or transaction state differs from the inspection mode')
    xen = dom0.get('xen')
    if (not match(HASH, dom0.get('expected_configuration_sha256')) or
            dom0.get('configuration_sha256') != dom0.get('expected_configuration_sha256')):
        findings.append('router Xen configuration differs from the compiled inventory and template')
    runtime = dom0.get('xen_runtime')
    if (dom0.get('xen_runtime_matches') is not True or not isinstance(runtime, dict) or
            not isinstance(runtime.get('uuid'), str) or not re.fullmatch(
                r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}', runtime['uuid'])):
        findings.append('live router Xen identity or attachments differ from the recorded configuration')
    if (not isinstance(xen, dict) or xen.get('name') != 'router' or
            not isinstance(xen.get('disk'), list) or len(xen['disk']) != 1 or
            not isinstance(dom0.get('configuration_sha256'), str) or
            not match(HASH, dom0['configuration_sha256'])):
        findings.append('dom0 router boot assignment evidence is incomplete')
    else:
        disk = xen['disk'][0]
        selected = re.fullmatch(r'phy:(/dev/[A-Za-z0-9_-]+/[A-Za-z0-9_-]+),xvda,w', disk) if isinstance(disk, str) else None
        volumes = dom0.get('logical_volumes')
        try:
            rows = volumes['report'][0]['lv']
        except (KeyError, IndexError, TypeError):
            rows = None
        if (selected is None or not isinstance(rows, list) or
                len([row for row in rows if isinstance(row, dict) and row.get('lv_path') == selected[1] and
                     isinstance(row.get('lv_uuid'), str) and row['lv_uuid']]) != 1):
            findings.append('router OS disk has no unique recorded LVM identity')
    boot = dom0.get('boot_artifacts')
    if (not isinstance(boot, dict) or set(boot) != {'kernel', 'ramdisk'} or any(
            not isinstance(item, dict) or not isinstance(item.get('path'), str) or
            not item['path'].startswith('/mnt/dom0_data/') or
            item['path'] != (xen.get(name) if isinstance(xen, dict) else None) or
            not match(HASH, item.get('sha256')) or
            type(item.get('bytes')) is not int or not 0 < item['bytes'] <=
            (32 * 1024 * 1024 if name == 'kernel' else 128 * 1024 * 1024)
            for name, item in boot.items())):
        findings.append('router kernel or initramfs identity is missing')
    return findings


def legacy_live(*, box, assignment, source, guest, dom0, now):
    """Normalize fresh adopted legacy evidence without reading retained bytes."""
    router_records.assignment(assignment, box)
    router_generations.generation(source, box)
    if (source['origin'] != 'legacy' or assignment['current_sha256'] != source['record_sha256'] or
            assignment['operation_id'] != source['generation_id'] or
            assignment['engine_commit'] != source['engine_commit'] or
            assignment['evidence_sha256'] != source['evidence_sha256'] or
            assignment['previous_sha256'] is not None or
            assignment['policy_sha256'] != router_records.BASELINE_AUTHORITY_SHA256 or
            not fresh(guest.get('observed_at'), now, dt.timedelta(minutes=15)) or
            not fresh(dom0.get('observed_at'), now, dt.timedelta(minutes=15))):
        raise UpdateError('adopted legacy evidence is stale or differs from its protected assignment')
    findings = legacy_baseline_findings(guest, dom0, box, adopted=True)
    if findings:
        raise UpdateError('adopted legacy router has drift or incomplete evidence: ' + ', '.join(findings))
    files = {path.lstrip('/'):item['sha256'] for rows in
             (guest['configuration_files'],guest['include_files']) for path,item in rows.items()}
    rows = [item for item in dom0['logical_volumes']['report'][0]['lv']
            if item.get('lv_path') == source['disk']['path']]
    if (guest['alpine_branch'] != source['alpine_branch'] or
            guest['packages'] != source['packages'] or guest['kernel_release'] != source['kernel_release'] or
            guest['service_accounts'] != source['accounts'] or files != source['configuration_files'] or
            len(rows) != 1 or rows[0]['lv_uuid'] != source['disk']['uuid'] or
            int(rows[0]['lv_size']) != source['disk']['bytes'] or rows[0]['origin'] or
            dom0['xen_runtime']['uuid'] != source['xen']['uuid'] or
            dom0['xen']['vif'] != source['xen']['vif'] or
            dom0['xen']['disk'] != ['phy:' + source['disk']['path'] + ',xvda,w'] or
            {name:dom0['boot_artifacts'][key] for name,key in
             (('kernel','kernel'),('initramfs','ramdisk'))} != source['boot']):
        raise UpdateError('adopted legacy router differs from its protected generation')
    return {'observed_at':guest['observed_at'], 'box':box, 'role':'router',
            'generation':source['record_sha256'], 'packages':source['packages'],
            'kernel_release':source['kernel_release'], 'alpine_branch':source['alpine_branch'],
            'boot_artifacts':{name:item['sha256'] for name,item in source['boot'].items()},
            'configuration_verified':True, 'overlay_ipv6_enabled':False}


def _copyable_metadata(value, limit, *, private=False, owners):
    """Use the fixed copy contract to reject state that the helper cannot read."""
    maximum, empty = limit
    if not isinstance(value, dict) or value.get('present') is not True or value.get('regular') is not True or value.get('links') != 1:
        return False
    try:
        mode = int(value['mode'], 8)
    except (KeyError, TypeError, ValueError):
        return False
    return (type(value.get('bytes')) is int and (0 if empty else 1) <= value['bytes'] <= maximum and
            type(value.get('uid')) is int and value['uid'] >= 0 and
            type(value.get('gid')) is int and value['gid'] >= 0 and
            0 <= mode <= 0o7777 and not mode & 0o7022 and
            (not private or not mode & 0o077) and (value['uid'], value['gid']) in owners)


def lifecycle(mode, *, box, role, existing_disk, installation, accepted, bootstrap_authorized,
              replacement_authorized):
    """A missing assignment never makes an existing disk a blank target."""
    if role != 'router' or not match(BOX, box):
        raise UpdateError('router lifecycle requires an exact box and router role')
    if mode == 'initial-install':
        if not bootstrap_authorized or replacement_authorized or accepted is not None:
            raise UpdateError('initial installation requires bootstrap authority and no accepted assignment')
        if existing_disk is not None:
            try:
                router_initial_installation.validate(installation, box)
            except (RuntimeError, TypeError, ValueError) as error:
                raise UpdateError('existing router disk has no valid interrupted-install record') from error
            if installation['disk'] != existing_disk:
                raise UpdateError('existing router disk has no matching interrupted-install record')
            return 'resume'
        if installation is not None:
            raise UpdateError('recorded installation disk is missing; reconstruction requires review')
        return 'allocate'
    if mode == 'replacement':
        if (not replacement_authorized or bootstrap_authorized or not isinstance(accepted, dict) or
                accepted.get('box') != box or accepted.get('role') != role or
                existing_disk is None or accepted.get('disk') != existing_disk):
            raise UpdateError('replacement requires exact accepted assignment and replacement authority')
        return 'prepare'
    raise UpdateError('router lifecycle mode must be initial-install or replacement')


def select_branch(mode, releases, now, policy, *, current=None):
    """Select inputs, not execution authority, for the common router builder."""
    if (not isinstance(policy, dict) or policy.get('branch-policy') != 'tested-stable' or
            type(policy.get('branch-delay-days')) is not int or
            not 0 <= policy['branch-delay-days'] <= 365):
        raise UpdateError('router input selection requires the configured stable branch policy and delay')
    delay = policy['branch-delay-days']
    if mode == 'initial-install' and current is None:
        return newest_stable_branch(releases, now, delay)
    if mode == 'replacement' and current is not None:
        branch_number(current)
        return adjacent_stable_branch(current, releases, now, delay) or current
    raise UpdateError('router branch selection requires an exact lifecycle and predecessor')


def available_branches(releases, current, now, delay):
    """Retain availability separately from the adjacent-branch decision."""
    if not isinstance(releases, dict) or not isinstance(releases.get('release_branches'), list):
        raise UpdateError('Alpine release metadata is unavailable')
    current_number = branch_number(current)
    entries, seen = [], set()
    for row in releases['release_branches']:
        if not isinstance(row, dict):
            raise UpdateError('Alpine branch metadata is invalid')
        branch = row.get('rel_branch')
        if not isinstance(branch, str) or not re.fullmatch(r'v[0-9]+\.[0-9]+', branch):
            continue
        if branch in seen:
            raise UpdateError('Alpine branch metadata is duplicated')
        seen.add(branch)
        if branch_number(branch) < current_number:
            continue
        try:
            patches = [v['version'] for v in row['releases']
                       if re.fullmatch(re.escape(branch[1:]) + r'\.[0-9]+', v['version']) and
                       dt.date.fromisoformat(v['date']) <= now.date()]
            support = {'main': row['eol_date'], **{r['name']: r['eol_date'] for r in row['repos'] if r.get('eol_date')}}
            support = {r: {'ends': support[r], 'supported': now.date() < dt.date.fromisoformat(support[r])}
                       for r in ('main', 'community')}
        except (KeyError, TypeError, ValueError) as error:
            raise UpdateError('Alpine release or repository support evidence is incomplete') from error
        if patches:
            entries.append({'branch': branch, 'latest_patch': max(patches, key=lambda p: tuple(map(int, p.split('.')))),
                            'support': support})
    if current not in {r['branch'] for r in entries}:
        raise UpdateError('accepted router branch is absent from current Alpine metadata')
    selected = select_branch('replacement', releases, now,
        {'branch-policy': 'tested-stable', 'branch-delay-days': delay}, current=current)
    eligible = selected if selected != current else None
    for entry in entries:
        entry['eligible'] = entry['branch'] in (current, eligible)
    return sorted(entries, key=lambda r: branch_number(r['branch'])), eligible


def package_difference(old, new, compare):
    old, new = ({p['name']: p for p in rows} for rows in (old, new))
    result = []
    for name in sorted(old.keys() | new.keys()):
        before, after = old.get(name), new.get(name)
        if before == after:
            continue
        if before and after:
            order = compare(before['version'], after['version'])
            if order not in ('<', '=', '>'):
                raise UpdateError('APK returned an invalid package version comparison')
            if order == '>':
                raise UpdateError('unexplained package downgrade: ' + name)
            if order == '=' and before['sha256'] != after['sha256']:
                raise UpdateError('package bytes changed without a version change: ' + name)
        result.append({'name': name, 'old': before['version'] if before else None,
                       'new': after['version'] if after else None,
                       'change': 'added' if before is None else 'removed' if after is None else 'updated'})
    return result


def legacy_package_difference(old, new, compare):
    """Compare legacy versions without claiming unrecorded package byte hashes."""
    current = {p['name']:p['version'] for p in new}
    result = []
    for name in sorted(old.keys() | current.keys()):
        before, after = old.get(name), current.get(name)
        if before == after:
            continue
        if before is not None and after is not None:
            order = compare(before, after)
            if order not in ('<', '=', '>') or order == '>':
                raise UpdateError('unexplained legacy package downgrade: ' + name)
        result.append({'name':name, 'old':before, 'new':after,
                       'change':'added' if before is None else 'removed' if after is None else 'updated'})
    return result


def check(*, box, role, accepted, live, metadata, candidates, policy, policy_sha256, profile, engine,
          now, compare):
    """Classify complete evidence. Any missing evidence prevents `unchanged`."""
    report = {'kind': DECISION, 'box': box, 'role': role, 'checked_at': timestamp(now),
              'policy_sha256': policy_sha256, 'accepted_sha256': None, 'status': 'failed',
              'reason': '', 'availability': [], 'selected_branch': None,
              'candidate_inputs_sha256': None, 'effective_inputs_sha256': None,
              'package_difference': [], 'package_difference_scope': None,
              'explicit_request_difference': None, 'release_transition': None, 'source_sha256': None}
    try:
        if role != 'router' or not match(BOX, box) or not match(HASH, policy_sha256):
            raise UpdateError('router check requires an exact router target and policy receipt')
        if (not isinstance(policy, dict) or policy.get('branch-policy') != 'tested-stable' or
                type(policy.get('branch-delay-days')) is not int or not 0 <= policy['branch-delay-days'] <= 365 or
                type(policy.get('report-max-age-hours')) is not int or not 24 <= policy['report-max-age-hours'] <= 168 or
                role not in policy.get('targets', {}).get(box, [])):
            raise UpdateError('router target or branch policy is outside declared update intent')
        if accepted is None:
            report.update(status='deferred', reason='router baseline requires supervised adoption')
            return seal(report, 'report_sha256')
        if not isinstance(accepted, dict) or set(accepted) not in (
                {'box','role','generation','release'}, {'box','role','generation','legacy'}):
            raise UpdateError('accepted router evidence has missing or unknown fields')
        if accepted['box'] != box or accepted['role'] != role or not match(HASH, accepted['generation']):
            raise UpdateError('accepted generation belongs to another box or role')
        legacy = 'legacy' in accepted
        if legacy:
            source = router_generations.generation(accepted['legacy'], box)
            if source['origin'] != 'legacy' or source['record_sha256'] != accepted['generation']:
                raise UpdateError('legacy check source differs from its accepted generation')
            branch = source['alpine_branch']
            expected_packages, expected_kernel = source['packages'], source['kernel_release']
            expected_boot = {key:item['sha256'] for key,item in source['boot'].items()}
        else:
            release = accepted['release']
            # An accepted recipe may use an earlier engine. Validate it against
            # its recorded engine; compare the candidate with the new engine.
            validate_release(release, profile, release['engine_commit'])
            branch = release['inputs']['branch']
            expected_packages, expected_kernel = release['runtime_packages'], release['kernel_release']
            expected_boot = {k: release['artifacts'][k] for k in ('kernel', 'initramfs')}
        report['accepted_sha256'] = digest(accepted)
        if (not isinstance(live, dict) or not fresh(live.get('observed_at'), now, dt.timedelta(minutes=15)) or
                live.get('box') != box or live.get('role') != role or live.get('generation') != accepted['generation'] or
                live.get('packages') != expected_packages or live.get('kernel_release') != expected_kernel or
                live.get('boot_artifacts') != expected_boot or
                legacy and live.get('alpine_branch') != branch or
                live.get('configuration_verified') is not True):
            raise UpdateError('live router evidence is missing, stale, or differs from its accepted release')
        if live.get('overlay_ipv6_enabled') is not False:
            raise UpdateError('enabled or unknown overlay IPv6 state cannot be reconstructed')
        if (not isinstance(metadata, dict) or
                not fresh(metadata.get('observed_at'), now, dt.timedelta(hours=policy['report-max-age-hours'])) or
                not match(HASH, metadata.get('sha256'))):
            report.update(status='deferred', reason='fresh Alpine release metadata is unavailable')
            return seal(report, 'report_sha256')
        report['source_sha256'] = metadata['sha256']
        availability, eligible = available_branches(metadata.get('releases'), branch, now,
                                                   policy['branch-delay-days'])
        report['availability'] = availability
        branch = eligible or branch
        report['selected_branch'] = branch
        report['release_transition'] = {'from': source['alpine_branch'] if legacy else release['inputs']['branch'],
                                        'to': branch}
        evidence = candidates.get(branch)
        if not isinstance(evidence, dict) or evidence.get('status') == 'unavailable':
            report.update(status='deferred', reason='fresh authenticated package closure is unavailable')
            return seal(report, 'report_sha256')
        if evidence.get('status') != 'verified':
            raise UpdateError('APK signature verification or dependency resolution failed')
        if not fresh(evidence.get('observed_at'), now, dt.timedelta(hours=policy['report-max-age-hours'])):
            report.update(status='deferred', reason='authenticated package closure is stale')
            return seal(report, 'report_sha256')
        inputs = evidence['inputs']
        validate_inputs(inputs, profile, engine)
        if inputs['branch'] != branch:
            raise UpdateError('candidate package closure belongs to a different branch')
        upstream_version = tuple(map(int, inputs['tailscale']['version'].split('.')))
        if legacy:
            old_version = source['packages'].get('tailscale', '').split('-r', 1)[0]
            if re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+', old_version):
                if upstream_version < tuple(map(int, old_version.split('.'))):
                    raise UpdateError('unexplained upstream Tailscale downgrade from legacy router')
        else:
            current_tailscale = release['inputs']['tailscale']
            previous_version = tuple(map(int, current_tailscale['version'].split('.')))
            if upstream_version < previous_version:
                raise UpdateError('unexplained upstream Tailscale version downgrade')
            if (upstream_version == previous_version and
                    inputs['tailscale']['sha256'] != current_tailscale['sha256']):
                raise UpdateError('upstream Tailscale archive bytes changed without a version change')
        report['candidate_inputs_sha256'] = inputs['inputs_sha256']
        report['effective_inputs_sha256'] = effective_inputs(inputs)
        report['package_difference'] = (legacy_package_difference(source['packages'], inputs['packages'], compare)
                                        if legacy else package_difference(release['inputs']['packages'], inputs['packages'], compare))
        report['package_difference_scope'] = 'legacy-runtime-to-build-inputs' if legacy else 'build-inputs-to-build-inputs'
        # Legacy inspection does not establish an approved package request list.
        # Do not mislabel dependency differences as Klokast additions.
        if not legacy:
            before, after = set(release['inputs']['world']), set(inputs['world'])
            report['explicit_request_difference'] = {'added': sorted(after - before), 'removed': sorted(before - after)}
        changed = legacy or effective_inputs(release['inputs']) != effective_inputs(inputs)
        excluded = any(r.get('box') == box and r.get('role') == role for r in policy.get('exclusions', []))
        if policy.get('enabled') is not True or excluded:
            report.update(status='deferred', reason='router update policy is disabled or target is excluded')
        elif changed:
            report.update(status='update-required', reason=('legacy router requires its first approved template generation'
                          if legacy else 'eligible router build inputs changed'))
        elif any(not r['eligible'] for r in availability):
            report.update(status='deferred', reason='current inputs are unchanged; a future branch is held')
        else:
            report.update(status='unchanged', reason='all effective router build inputs match')
    except (UpdateError, router_generations.GenerationError, ValueError, TypeError, KeyError) as error:
        report.update(status='failed', reason=str(error))
    return seal(report, 'report_sha256')


def require_preparation(report, *, box, policy_sha256, accepted_sha256, inputs, now, max_age_hours):
    verify_seal(report, 'report_sha256')
    if (report.get('kind') != DECISION or report.get('role') != 'router' or report.get('box') != box or
            report.get('status') != 'update-required' or report.get('policy_sha256') != policy_sha256 or
            report.get('accepted_sha256') != accepted_sha256 or
            report.get('candidate_inputs_sha256') != inputs.get('inputs_sha256') or
            report.get('effective_inputs_sha256') != effective_inputs(inputs) or
            not fresh(report.get('checked_at'), now, dt.timedelta(hours=max_age_hours))):
        raise UpdateError('router preparation decision is stale or no longer matches policy, target, or inputs')
