"""Fixed no-application assessment for shared VM adoption.

This is inspection code, not execution authority. It consumes checked source
receipts and untrusted discovery, emits no copy request, and never removes
files. A proposed classification is not an approved machine input. Incomplete
coverage, unknown data, and pending cleanup must remain visible.
"""
import re
import stat
from collections import Counter

from platform_updates import REPORT_KIND, VERIFY_AGE, UpdateError, digest, findings, fresh, timestamp
import vm_retention
import vm_storage_inventory as storage

PROFILE = 'shared-alpine-no-application-v1'
ROLES = ('dmz', 'iot')
IDENTITIES = {'/var/lib/tailscale/tailscaled.state': 'platform-tailscale-state',
              **{'/etc/ssh/ssh_host_' + k + '_key': 'platform-ssh-' + k
                 for k in ('rsa', 'ecdsa', 'ed25519')}}
CONFIGURATION = frozenset('''
/etc/apk/arch /etc/apk/repositories /etc/apk/world /etc/containers/registries.conf
/etc/containers/containers.conf.d/10-klokast-network.conf /etc/doas.d/doas.conf
/etc/fstab /etc/group /etc/hostname /etc/hosts /etc/mdev.conf /etc/motd
/etc/network/interfaces /etc/nftables.nft /etc/passwd /etc/shadow /etc/subgid /etc/subuid
/etc/resolv.conf /etc/init.d/klokast-podman-runroot-cleanup
/etc/klokast/app-resources/router-forward.d/000-empty.nft
/etc/klokast/app-resources/router-forward.nft
/etc/klokast/app-resources/vm-input.d/000-empty.nft
/etc/klokast/app-resources/vm-input.nft
/etc/klokast/overlay-ipv6-input.nft
/usr/local/libexec/klokast-app-resources-reconcile
'''.split())
OS_FILES = frozenset('''
/etc/group- /etc/passwd- /etc/shadow- /etc/resolv.pre-tailscale-backup.conf
/etc/klokast-podman-template-built /lib/apk/db/installed /lib/apk/db/lock
/lib/apk/db/scripts.tar.gz /lib/apk/db/triggers /root/.wget-hsts
/home/neo/.wget-hsts /var/log/apk.log /var/log/tailscaled.log
/var/lib/tailscale/derpmap.cached.json /var/lib/tailscale/tailscaled.log.conf
/home/neo/.local/share/containers/cache/blob-info-cache-v1.sqlite
/home/neo/.cache/containers/short-name-aliases.conf.lock
/etc/klokast/platform-resources/desired.json /etc/klokast/platform-resources/last-applied.json
'''.split())
BOOT_SERVICES = frozenset('''
bootmisc cgroups devfs dmesg fsck hostname hwclock hwdrivers killprocs
klokast-podman-runroot-cleanup localmount loopback mdev modules mount-ro
mtab networking nftables procfs root savecache seedrng swap sysctl sysfs
tailscale
'''.split())
# These paths are application state even if no process uses them. A report
# lists exact observed descendants; it does not turn this list into rm -rf.
CLEANUP_ROOTS = (
    '/var/tmp/klokast-static-site-backup', '/var/lib/klokast/immich-private-ingress',
    '/var/log/klokast/immich-private-ingress',
)


def below(path, root):
    return path == root or path.startswith(root + '/')


def checked_empty_store(value):
    fields = {'kind', 'graph_root', 'complete', 'stable', 'empty', 'metadata', 'database_sha256',
              'unresolved_paths', 'adoption_authorized', 'error', 'evidence_sha256'}
    graph = '/home/neo/.local/share/containers/storage'
    if (not isinstance(value, dict) or set(value) != fields or value['kind'] != 'klokast.vm-empty-store.v1' or
            value['graph_root'] != graph or value['complete'] is not True or value['stable'] is not True or
            type(value['empty']) is not bool or value['adoption_authorized'] is not False or value['error'] is not None or
            not isinstance(value['database_sha256'], str) or not re.fullmatch('[0-9a-f]{64}', value['database_sha256']) or
            value['evidence_sha256'] != digest({k: v for k, v in value.items() if k != 'evidence_sha256'})):
        raise UpdateError('complete stable empty-store evidence is unavailable')
    metadata = value['metadata']
    if not isinstance(metadata, dict) or set(metadata) != {'roots', 'entries', 'excluded'} or metadata['excluded'] != []:
        raise UpdateError('empty-store file coverage is unavailable')
    storage.checked_unowned_tree({**metadata, 'kind': 'klokast.vm-unowned-tree.v1', 'complete': True,
                                  'stable': True, 'data_accounted': False, 'metadata_sha256': digest(metadata)},
                                 [{'path': graph, 'reason': 'unclassified-directory'}])
    unresolved = value['unresolved_paths']
    paths = {row['path'] for row in metadata['entries']}
    if (not isinstance(unresolved, list) or any(not isinstance(p, str) or p not in paths for p in unresolved) or
            unresolved != sorted(set(unresolved)) or value['empty'] != (not unresolved)):
        raise UpdateError('empty-store result conflicts with unresolved files')
    return value


def source_intent(retention, registry, catalogs, box, role):
    """Join two installed root readers; the registry alone omits retained data."""
    projection = vm_retention.validate_source(retention)
    if role not in ROLES or box not in projection['boxes']:
        raise UpdateError('no-application adoption supports only declared DMZ and IoT guests; backend VMs remain excluded')
    if (not isinstance(registry, dict) or
            set(registry) != {'schema_version', 'kind', 'source', 'authority_state_sha256', 'engine_commit', 'rendered'} or
            type(registry['schema_version']) is not int or registry['schema_version'] != 1 or
            registry['kind'] != 'klokast.registry-source-status.v1' or registry['source'] != retention['source'] or
            any(registry[k] != retention[k] for k in ('authority_state_sha256', 'engine_commit'))):
        raise UpdateError('registry and retained-data readers do not bind the same active authority and engine')
    rendered = registry['rendered']
    if (not isinstance(rendered, dict) or rendered.get('kind') != 'klokast.registry.v1' or
            rendered.get('schema_version') != 1 or rendered.get('valid') is not True or rendered.get('diagnostics') != [] or
            rendered.get('engine', {}).get('commit') != retention['engine_commit'] or
            rendered.get('inputs') != retention['inputs'] or
            rendered.get('repository', {}).get('head_commit') != retention['private_commit'] or
            rendered.get('repository', {}).get('clean') is not True or
            rendered.get('repository', {}).get('branch') != 'main'):
        raise UpdateError('registry and retained-data readers do not bind the same checked private files')
    resolved = rendered.get('projection')
    if (not isinstance(resolved, dict) or set(resolved) != {'registry', 'registry_sha256', 'scopes'} or
            digest(resolved['registry']) != resolved['registry_sha256']):
        raise UpdateError('checked registry projection is incomplete or has a different checksum')
    view = resolved['registry']
    if (not isinstance(view, dict) or view.get('schema_version') != 1 or
            not isinstance(view.get('boxes'), dict) or sorted(view['boxes']) != projection['boxes'] or
            not isinstance(view.get('apps'), dict)):
        raise UpdateError('checked registry has an incomplete box or application set')
    # This first profile relies on the sealed registry's disabled-app contract.
    # Do not guess compute placement when that contract gains present apps.
    workloads = []
    for app, binding in sorted(view['apps'].items()):
        if not isinstance(binding, dict) or type(binding.get('enabled')) is not bool:
            raise UpdateError('checked application intent is incomplete')
        if binding['enabled']:
            workloads.append(app)
    known = {}
    for candidate_role in ('bak', 'dmz', 'iot'):
        storage.catalog_index(catalogs, candidate_role)
    for catalog in catalogs:
        for dataset in catalog['datasets']:
            known[(catalog['app'], dataset)] = catalog['role']
    datasets = [d for d in projection['datasets'] if d['box'] == box and
                known.get((d['app'], d['dataset'])) in (None, role)]
    guest = view['boxes'][box].get('shared_guests', {}).get(role, {})
    runtime = guest.get('runtime_state', 'running')
    if runtime not in ('running', 'stopped'):
        raise UpdateError('checked shared VM runtime intent is invalid')
    return {'box': box, 'role': role, 'runtime_state': runtime, 'workloads': workloads,
            'datasets': datasets, 'eligible': runtime == 'running' and not workloads and not datasets,
            'retention_source_sha256': digest(retention), 'registry_source_sha256': digest(registry),
            'authority_state_sha256': retention['authority_state_sha256'],
            'engine_commit': retention['engine_commit'], 'private_commit': retention['private_commit'],
            'inputs': retention['inputs']}


def path_classification(entry):
    """Fixed reconstruction rules. Unknown paths never inherit a parent rule."""
    name, mode = entry['path'], entry['mode']
    category, rule, resolved = 'unknown', 'no fixed rule', False
    if name in IDENTITIES:
        category, rule = 'retained-machine-identity', IDENTITIES[name]
        # Discovery alone does not validate private keys or state usability.
    elif name in CONFIGURATION:
        category, rule = 'generated-configuration', 'compare with rendered machine inputs'
    elif any(below(name, root) for root in CLEANUP_ROOTS):
        category, rule = 'exact-cleanup-item', 'application residue; verify independent copy and approve exact removal'
    elif re.fullmatch(r'/home/neo/next-[a-zA-Z0-9.-]+\.(?:crt|key)', name):
        category, rule = 'exact-cleanup-item', 'legacy application certificate; approve exact removal'
    elif name in ('/root/.ssh/authorized_keys', '/home/neo/.ssh/authorized_keys',
                  '/etc/ssh/sshd_config.d/10-klokast-bootstrap.conf'):
        category, rule = 'exact-cleanup-item', 'retire bootstrap access after independent management qualification'
    elif stat.S_ISDIR(mode):
        category, rule = 'reconstructable-os-state', 'directory; every descendant needs its own rule'
        resolved = True  # Resolved children and complete coverage are checked separately.
    elif name in OS_FILES:
        category, rule, resolved = 'reconstructable-os-state', 'fixed OS cache, log, or generated record', True
    elif re.fullmatch(r'/etc/ssh/ssh_host_(?:rsa|ecdsa|ed25519)_key.pub', name):
        category, rule, resolved = 'reconstructable-os-state', 'derive public key from retained private key', True
    elif re.fullmatch(r'/lib/modules/[^/]+/(?:kernel/.+\.ko(?:\.(?:gz|xz|zst))?|modules\.[a-z_.]+)', name):
        category, rule = 'reconstructable-os-state', 'compare legacy modules with their signed source before discarding'
    elif re.fullmatch(r'/lib/firmware/qat_(?:402xx|4xxx)(?:_mmp)?\.bin.zst', name):
        category, rule = 'reconstructable-os-state', 'compare legacy firmware with its signed source before discarding'
    elif re.fullmatch(r'/var/cache/apk/APKINDEX\.[0-9a-f]+\.tar.gz', name):
        category, rule, resolved = 'reconstructable-os-state', 'rebuild package index cache from signed inputs', True
    elif re.fullmatch(r'/(?:usr/)?s?bin/[^/]+', name) and stat.S_ISLNK(mode):
        category, rule = 'approved-package-content', 'verify generated applet link against signed package recipe'
    elif below(name, '/etc/ssl/certs') and stat.S_ISLNK(mode):
        category, rule = 'approved-package-content', 'verify generated certificate link against signed package recipe'
    elif re.fullmatch(r'/etc/runlevels/[^/]+/[^/]+', name):
        category, rule = 'generated-configuration', 'compare enabled service with fixed boot recipe'
    # A known path with the wrong type or unsafe ownership is not disposable.
    if category != 'unknown' and not stat.S_ISDIR(mode):
        expected_link = rule.startswith('verify generated') or name.startswith('/etc/runlevels/')
        if (not (stat.S_ISLNK(mode) if expected_link else stat.S_ISREG(mode)) or
                entry['uid'] not in (0, 1000) or (not expected_link and mode & 0o002)):
            category, rule, resolved = 'unknown', 'unexpected type, ownership, or writable metadata', False
    return category, rule, resolved


def report(discovery, box, role, implementation_commit, now, *, intent=None, source_error=None):
    if role not in ROLES or not isinstance(box, str) or not re.fullmatch('[a-z0-9][a-z0-9-]{0,30}', box):
        raise UpdateError('no-application qualification requires one DMZ or IoT target')
    result = {'kind': 'klokast.vm-no-application-qualification.v1', 'profile': PROFILE,
              'generated_at': timestamp(now), 'box': box, 'role': role,
              'implementation_commit': implementation_commit, 'discovery_sha256': digest(discovery),
              'intent': intent, 'items': [], 'cleanup_items': [], 'findings': [],
              'classification_complete': False, 'qualified': False,
              'application_tests': {'status': 'not-run', 'executed': False},
              'adoption_intent': None, 'adoption_authorized': False}
    host = box + '-' + role
    def add(code, message):
        result['findings'].append(findings(code, message, 'critical', host))
    def item(area, key, category, rule, resolved, evidence):
        row = {'area': area, 'key': key, 'classification': category, 'rule': rule,
               'resolved': resolved, 'evidence_sha256': digest(evidence)}
        result['items'].append(row)
        if category == 'exact-cleanup-item':
            result['cleanup_items'].append({**row, 'metadata': evidence, 'removal_approved': False})

    if intent is None:
        add('qualification.intent-unknown', source_error or 'Current checked application, retention, and runtime intent is unavailable.')
    elif (intent['box'], intent['role']) != (box, role):
        raise UpdateError('qualification intent names another target')
    elif not intent['eligible']:
        add('qualification.intent-blocked', 'Checked intent declares a stopped VM, an application, or retained data on this target.')
    if intent and intent['engine_commit'] != implementation_commit:
        add('qualification.engine-unapproved', 'Qualification rules differ from the approved engine; promote tested source before adoption.')
    if (not isinstance(discovery, dict) or discovery.get('kind') != REPORT_KIND or
            discovery.get('complete') is not True or not fresh(discovery.get('generated_at'), now, VERIFY_AGE) or
            not isinstance(discovery.get('hosts'), list)):
        add('qualification.discovery-unknown', 'A complete scan no more than two hours old is required.')
        return finish(result)
    if discovery.get('implementation_commit') != implementation_commit:
        add('qualification.discovery-source', 'Discovery and qualification must use the same implementation commit.')
    hosts = [h for h in discovery['hosts'] if isinstance(h, dict) and h.get('host') == host]
    if len(hosts) != 1 or hosts[0].get('target') != {'box': box, 'role': role, 'runtime': 'running'}:
        add('qualification.target-unavailable', 'Exactly one running target is required; stopped guests stay stopped.')
        return finish(result)
    fact = hosts[0].get('facts')
    if not isinstance(fact, dict):
        add('qualification.facts-unknown', 'Target facts are missing.')
        return finish(result)
    # Old packages and an expired *source* branch are update reasons, not
    # workload or data-safety refusals. Recompute safety from the underlying facts.
    if (fact.get('hostname') != host or fact.get('role') != role or fact.get('box') != box or
            fact.get('architecture') != 'x86_64' or fact.get('os', {}).get('id') != 'alpine' or
            not fresh(fact.get('observed_at'), now, VERIFY_AGE)):
        add('qualification.fact-identity', 'Target identity, Alpine profile, or fact freshness differs.')
    host_data = storage.assess_host_data(fact, host)
    inventory = host_data['inventory']
    if inventory is None:
        add('qualification.host-unknown', 'Complete stable host accounting is unavailable.')
        return finish(result)
    required = {'unowned_tree', 'native_services', 'processes', 'package_audit', 'rootful_store'}
    if not required <= set(inventory):
        add('qualification.coverage-unknown', 'Host accounting lacks complete files, services, processes, audit, or rootful store evidence.')
    for finding in host_data['findings']:
        if finding['code'] in {'host.root-identity-ambiguous', 'host.service-failed', 'host.service-script-missing',
                               'host.service-transition', 'host.unmarked-supervisor', 'host.process-executable-unknown'}:
            result['findings'].append(finding)
    entries = {v['path']: v for v in inventory['unowned_paths']}
    for value in inventory.get('unowned_tree', {}).get('entries', []):
        previous = entries.get(value['path'])
        if previous and any(previous[k] != value.get(k) for k in previous):
            add('qualification.path-conflict', 'Shallow and deep file metadata disagree.')
        entries[value['path']] = value
    for name, entry in sorted(entries.items()):
        item('file', name, *path_classification(entry), entry)
    for difference in inventory.get('package_audit', {}).get('differences', []):
        name = difference['path']
        category = 'generated-configuration' if name in CONFIGURATION else 'unknown'
        rule = 'compare changed package file with approved machine recipe'
        resolved = False
        if difference['code'] == 'm' and name in {'/dev/shm', '/proc', '/run/lock', '/sys', '/var/lib/tailscale'}:
            category, rule = 'reconstructable-os-state', 'verify runtime directory ownership and mode'
        item('package-difference', name, category, rule, resolved, difference)
    for account in inventory['accounts']:
        # Numeric identities alone cannot establish an account's purpose.
        category = 'retained-machine-identity' if account['name'] == 'neo' else 'approved-package-content'
        item('account', account['name'], category, 'compare account with signed package or machine recipe', False, account)
    for service in inventory.get('native_services', {}).get('services', []):
        active = bool(service['runlevels'] or service['markers'])
        category = 'generated-configuration' if service['name'] in BOOT_SERVICES else ('unknown' if active else 'approved-package-content')
        item('service', service['name'], category, 'verify script and permitted OpenRC state', False, service)
    for maintenance in inventory['maintenance_files']:
        if not maintenance['path'].startswith(('/etc/init.d/', '/etc/runlevels/')):
            item('timer', maintenance['path'], 'unknown', 'compare exact timer contents with OS baseline', False, maintenance)
    for process in inventory.get('processes', {}).get('processes', []):
        kernel = process['kernel_thread'] and process['uids'] == [0] * 4
        profile_role = process.get('no_application_role', 'kernel-thread' if kernel else 'unknown')
        resolved = profile_role in ('kernel-thread', 'os-init', 'console-getty', 'inspection-process')
        item('process', str(process['pid']), 'reconstructable-os-state' if profile_role != 'unknown' else 'unknown',
             profile_role if profile_role != 'unknown' else 'bind live process to fixed service or inspection ancestry', resolved, process)
    mounts = fact.get('storage', {}).get('mounts')
    expected = {'/': 'ext4', '/boot': 'ext4', '/dev': 'devtmpfs', '/dev/pts': 'devpts', '/dev/shm': 'tmpfs',
                '/proc': 'proc', '/proc/xen': 'xenfs', '/run': 'tmpfs', '/sys': 'sysfs', '/sys/fs/cgroup': 'cgroup2'}
    if not isinstance(mounts, list) or not mounts:
        add('qualification.mounts-unknown', 'Complete mount inventory is required.')
    else:
        for mount in mounts:
            supported = (mount.get('path') in expected and mount.get('type') == expected[mount['path']] and mount.get('root') == '/')
            item('mount', mount.get('path', '?'), 'reconstructable-os-state' if supported else 'unknown',
                 'bind filesystem to the recorded source disk' if supported else 'unsupported filesystem boundary', False, mount)
    for name in ('containers', 'volumes'):
        if fact.get(name) != []:
            add('qualification.' + name, 'The no-application profile requires a complete empty ' + name + ' inventory.')
    assessed = storage.assess(fact, [], host)
    for finding in assessed['findings']:
        if finding['severity'] == 'critical':
            result['findings'].append(finding)
    rootful = inventory.get('rootful_store')
    if rootful:
        item('container-store', rootful['graph_root'], 'unknown' if rootful['present'] else 'reconstructable-os-state',
             'classify every store file and registration' if rootful['present'] else 'standard rootful store absent', not rootful['present'], rootful)
    try:
        empty_store = checked_empty_store(fact.get('host_inventory', {}).get('no_application_store'))
        item('container-store', empty_store['graph_root'],
             'reconstructable-os-state' if empty_store['empty'] else 'unknown',
             'fixed empty rootless store layout' if empty_store['empty'] else 'unregistered rootless store files require classification',
             empty_store['empty'], empty_store)
        for entry in empty_store['metadata']['entries']:
            resolved = entry['path'] not in empty_store['unresolved_paths']
            item('store-file', entry['path'], 'reconstructable-os-state' if resolved else 'unknown',
                 'fixed empty store metadata' if resolved else 'not part of the fixed empty store layout', resolved, entry)
    except UpdateError:
        item('container-store', '/home/neo/.local/share/containers/storage', 'unknown',
             'empty registrations do not account for cached images or unregistered layers', False, fact.get('podman_storage'))
    add('qualification.machine-inputs', 'Approved machine configuration, complete store accounting, source disks, and independent management checks remain required.')
    return finish(result)


def finish(result):
    result['items'].sort(key=lambda row: (row['area'], row['key']))
    result['summary'] = {'items': len(result['items']),
                         'unresolved': sum(not row['resolved'] for row in result['items']),
                         'classes': dict(sorted(Counter(row['classification'] for row in result['items']).items())),
                         'cleanup_items': len(result['cleanup_items'])}
    if result['summary']['unresolved']:
        result['findings'].append(findings('qualification.unresolved', 'Each unresolved item needs its stated evidence or exact cleanup before adoption.', 'critical', result['box'] + '-' + result['role']))
    # This report supplies review evidence. It cannot mint a signed intent or
    # mark missing production checks successful, even for an empty fixture.
    result['report_sha256'] = digest(result)
    return result
