"""Fixed no-application assessment for shared VM adoption.

This is inspection code, not execution authority. It consumes checked source
receipts and untrusted discovery, emits no copy request, and never removes
files. A proposed classification is not an approved machine input. Incomplete
coverage, unknown data, and pending cleanup must remain visible.
"""
import hashlib
import re
import stat
from collections import Counter
from pathlib import Path

from platform_updates import REPORT_KIND, VERIFY_AGE, UpdateError, digest, findings, fresh, timestamp
import vm_retention
import vm_storage_inventory as storage
import vm_config_audit

PROFILE = 'shared-alpine-no-application-v1'
ROLES = ('dmz', 'iot')
ROOTFUL_GRAPH = '/var/lib/containers/storage'
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
PODMAN_RUNROOT_FILES = frozenset('''
/tmp/storage-run-1000/containers/overlay-layers/mountpoints.lock
/tmp/storage-run-1000/containers/overlay/idmapped-lower-dir-false
/tmp/storage-run-1000/containers/overlay/metacopy()-false
/tmp/storage-run-1000/containers/overlay/native-diff()-true
/tmp/storage-run-1000/containers/overlay/overlay-true
/tmp/storage-run-1000/containers/overlay/volatile-true
/tmp/storage-run-1000/libpod/tmp/alive
/tmp/storage-run-1000/libpod/tmp/alive.lck
/tmp/storage-run-1000/libpod/tmp/events/events.log
/tmp/storage-run-1000/libpod/tmp/events/events.log.lock
/tmp/storage-run-1000/libpod/tmp/pause.pid
'''.split())
BOOT_SERVICES = frozenset('''
bootmisc cgroups devfs dmesg fsck hostname hwclock hwdrivers killprocs
klokast-podman-runroot-cleanup localmount loopback mdev modules mount-ro
mtab networking nftables procfs root savecache seedrng swap sysctl sysfs
tailscale
'''.split())
ACTIVE_SERVICE_STATE = {
    'cgroups': ['default'], 'fsck': [], 'hostname': [], 'localmount': [],
    'networking': ['default'], 'nftables': ['default'], 'root': [],
    'tailscale': ['default'],
}
SYSTEM_ACCOUNTS = {
    'bin': (1, 1, '/bin', '/sbin/nologin'),
    'cron': (16, 16, '/var/spool/cron', '/sbin/nologin'),
    'daemon': (2, 2, '/sbin', '/sbin/nologin'),
    'ftp': (21, 21, '/var/lib/ftp', '/sbin/nologin'),
    'games': (35, 35, '/usr/games', '/sbin/nologin'),
    'guest': (405, 100, '/dev/null', '/sbin/nologin'),
    'halt': (7, 0, '/sbin', '/sbin/halt'),
    'klogd': (100, 101, '/dev/null', '/sbin/nologin'),
    'lp': (4, 7, '/var/spool/lpd', '/sbin/nologin'),
    'mail': (8, 12, '/var/mail', '/sbin/nologin'),
    'news': (9, 13, '/usr/lib/news', '/sbin/nologin'),
    'nobody': (65534, 65534, '/', '/sbin/nologin'),
    'root': (0, 0, '/root', '/bin/sh'),
    'shutdown': (6, 0, '/sbin', '/sbin/shutdown'),
    'sync': (5, 0, '/sbin', '/bin/sync'),
    'tailscale': (101, 102, '/var/lib/tailscale', '/sbin/nologin'),
    'uucp': (10, 14, '/var/spool/uucppublic', '/sbin/nologin'),
}
LEGACY_SYSTEM_ACCOUNTS = {
    'ntp': (123, 123, '/var/empty', '/sbin/nologin'),
    'sshd': (22, 22, '/dev/null', '/sbin/nologin'),
}
DMZ_PACKAGE_ACCOUNTS = {
    'cloudflared': (102, 103, '/home/cloudflared', '/sbin/nologin'),
    'nginx': (103, 104, '/var/lib/nginx', '/sbin/nologin'),
}
# Old requests come from podman-template-rootfs, podman-host, and
# tailscale-client. The DMZ additions came from the old local-ingress and
# Nextcloud/Static Site roles. They are omitted from the candidate profile.
LEGACY_APK_WORLD_BASE = frozenset('''
alpine-base ca-certificates doas iproute2 iptables jq nftables podman python3
shadow-subids tailscale tailscale-openrc wget
'''.split())
LEGACY_APK_WORLD_DMZ = frozenset({'nginx', 'cloudflared=2026.3.0-r1'})
TAILSCALE_LOGS = frozenset('/home/neo/.local/share/tailscale/tailscaled.log' + suffix
                           for suffix in ('.conf', '1.txt', '2.txt'))
COLLECTOR_SOURCE = Path(__file__).resolve().parents[1] / 'roles/vm-update-inventory/files/collect-vm-update-facts'
# These paths are application state even if no process uses them. A report
# lists exact observed descendants; it does not turn this list into rm -rf.
CLEANUP_ROOTS = (
    '/var/tmp/klokast-static-site-backup', '/var/lib/klokast/immich-private-ingress',
    '/var/log/klokast/immich-private-ingress',
)
LEGACY_TEMPLATE_MARKER = 'hostname=klokast-podman-template\nlv=/dev/vg0/lv_podman_template\n'
# Rendered from the checked-in 17cfd0b podman-host boot helper with runner neo.
# That version cleared only Podman's containers and libpod/tmp at boot. The
# candidate recipe clears the whole ephemeral runroot; never copy this file.
LEGACY_RUNROOT_HELPER_SHA256 = '90363aa710e409fc5d3fbb978ed79e6a8896d523ff536a23c1517b57d31b718e'
RUNTIME_DIRECTORY_MODES = {
    '/run/lock': (stat.S_IFDIR | 0o775, 0, 14),
    '/var/lib/tailscale': (stat.S_IFDIR | 0o700, 0, 0),
}
BUSYBOX_LINK_SHA256 = hashlib.sha256(b'/bin/busybox').hexdigest()
BBSUID_LINK_SHA256 = hashlib.sha256(b'/bin/bbsuid').hexdigest()
PINENTRY_LINK_SHA256 = hashlib.sha256(b'pinentry-curses').hexdigest()


def below(path, root):
    return path == root or path.startswith(root + '/')


def checked_legacy_apk_world(fact, entries, role):
    """Recognize only the fixed old no-application package requests."""
    value = fact.get('apk_world')
    configuration = fact.get('configuration')
    requests = value.get('requests') if isinstance(value, dict) else None
    entry = entries.get('/etc/apk/world')
    packages = fact.get('packages')
    if (role not in ROLES or not isinstance(value, dict) or
            set(value) != {'kind', 'sha256', 'requests'} or
            value['kind'] != 'klokast.vm-apk-world.v1' or
            not isinstance(requests, list) or not 0 < len(requests) <= 64 or
            any(not isinstance(row, str) or not re.fullmatch(
                r'[a-z0-9][a-z0-9+_.-]*(?:=[0-9][A-Za-z0-9._-]*)?', row)
                for row in requests) or requests != sorted(set(requests)) or
            not isinstance(value['sha256'], str) or
            value['sha256'] != hashlib.sha256(('\n'.join(requests) + '\n').encode()).hexdigest() or
            not isinstance(configuration, dict) or
            not isinstance(configuration.get('sha256'), dict) or
            configuration['sha256'].get('/etc/apk/world') != value['sha256'] or
            not isinstance(entry, dict) or entry.get('mode') != (stat.S_IFREG | 0o644) or
            entry.get('uid') != 0 or entry.get('gid') != 0 or
            not isinstance(packages, dict)):
        return False
    expected = LEGACY_APK_WORLD_BASE | (LEGACY_APK_WORLD_DMZ if role == 'dmz' else frozenset())
    observed = set(requests)
    if role == 'dmz' and 'curl' in observed:
        expected = expected | {'curl'}
    if observed != expected:
        return False
    for request in requests:
        name, separator, version = request.partition('=')
        package = packages.get(name)
        if not isinstance(package, dict) or (separator and package.get('version') != version):
            return False
    return True


def checked_tailscale_resolver(fact, entries):
    """Recognize Tailscale's exact generated DNS file, without its contents."""
    value = fact.get('tailscale_resolver')
    entry = entries.get('/etc/resolv.conf')
    tailscale = fact.get('tailscale')
    packages = fact.get('packages')
    return (isinstance(value, dict) and
            set(value) == {'kind', 'suffix_sha256', 'expected_sha256', 'observed_sha256'} and
            value['kind'] == 'klokast.vm-tailscale-resolver.v1' and
            all(isinstance(value[key], str) and re.fullmatch('[0-9a-f]{64}', value[key])
                for key in ('suffix_sha256', 'expected_sha256', 'observed_sha256')) and
            value['expected_sha256'] == value['observed_sha256'] and
            isinstance(entry, dict) and entry.get('mode') == (stat.S_IFREG | 0o644) and
            entry.get('uid') == 0 and entry.get('gid') == 102 and
            isinstance(tailscale, dict) and tailscale.get('backend_state') == 'Running' and
            isinstance(packages, dict) and 'tailscale' in packages)


def checked_empty_store(value, graph='/home/neo/.local/share/containers/storage'):
    fields = {'kind', 'graph_root', 'complete', 'stable', 'empty', 'metadata', 'database_sha256',
              'unresolved_paths', 'adoption_authorized', 'error', 'evidence_sha256'}
    if (graph not in ('/home/neo/.local/share/containers/storage', ROOTFUL_GRAPH) or
            not isinstance(value, dict) or set(value) != fields or value['kind'] != 'klokast.vm-empty-store.v1' or
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
    if graph == ROOTFUL_GRAPH and any(row['uid'] != 0 or row['gid'] != 0 for row in metadata['entries']):
        raise UpdateError('rootful empty-store ownership differs')
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


def checked_boot_files(value, mounts):
    fields = {'kind', 'complete', 'stable', 'mount', 'metadata', 'artifacts',
              'adoption_authorized', 'error', 'evidence_sha256'}
    if (not isinstance(value, dict) or set(value) != fields or value['kind'] != 'klokast.vm-boot-files.v1' or
            value['complete'] is not True or value['stable'] is not True or
            value['adoption_authorized'] is not False or value['error'] is not None or
            not isinstance(mounts, list) or any(not isinstance(m, dict) for m in mounts) or
            [m for m in mounts if m.get('path') == '/boot'] != [value['mount']] or
            any(str(m.get('path', '')).startswith('/boot/') for m in mounts) or
            value['evidence_sha256'] != digest({k: v for k, v in value.items() if k != 'evidence_sha256'})):
        raise UpdateError('complete stable boot filesystem coverage is unavailable')
    mount = value['mount']
    if (not isinstance(mount, dict) or set(mount) != {'path', 'root', 'type', 'device'} or
            mount['path'] != '/boot' or mount['root'] != '/' or mount['type'] != 'ext4' or
            not isinstance(mount['device'], str) or not re.fullmatch(r'[0-9]+:[0-9]+', mount['device'])):
        raise UpdateError('boot filesystem identity is unsupported')
    metadata = value['metadata']
    if (not isinstance(metadata, dict) or set(metadata) != {'roots', 'entries', 'excluded'} or
            metadata['roots'] != ['/boot'] or metadata['excluded'] != []):
        raise UpdateError('boot file coverage is incomplete')
    storage.checked_unowned_tree({**metadata, 'kind': 'klokast.vm-unowned-tree.v1', 'complete': True,
                                  'stable': True, 'data_accounted': False, 'metadata_sha256': digest(metadata)},
                                 [{'path': '/boot', 'reason': 'unclassified-directory'}])
    names = {'/boot/' + name for name in ('vmlinuz-virt', 'initramfs-virt', 'config-virt', 'System.map-virt')}
    present = {row['path'] for row in metadata['entries']}
    hashes = value['artifacts']
    if (not isinstance(hashes, dict) or set(hashes) != names & present or
            not {'/boot/vmlinuz-virt', '/boot/initramfs-virt'} <= set(hashes) or
            any(not stat.S_ISREG(r['mode']) or r['links'] != 1 or r['mode'] & 0o022
                for r in metadata['entries'] if r['path'] in hashes) or
            any(not isinstance(v, str) or not re.fullmatch('[0-9a-f]{64}', v) for v in hashes.values())):
        raise UpdateError('boot artifact hashes are missing or outside the fixed scope')
    return value


def legacy_modloop_file(entry, kernel):
    """Recognize OS files copied by the checked-in legacy template recipe."""
    name = entry['path']
    if (not isinstance(kernel, str) or
            not re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+-[0-9]+-virt', kernel) or
            entry.get('uid') != 0 or entry.get('gid') != 0 or
            not stat.S_ISREG(entry['mode']) or entry.get('links') != 1 or
            type(entry.get('bytes')) is not int or not 0 < entry['bytes'] <= 16 * 1024 * 1024 or
            entry['mode'] & 0o022):
        return False
    prefix = '/lib/modules/' + kernel + '/'
    relative = name[len(prefix):] if name.startswith(prefix) else ''
    return bool((relative and (
        re.fullmatch(r'kernel/.+\.ko(?:\.(?:gz|xz|zst))?', relative) or
        re.fullmatch(r'modules\.[a-z_.]+', relative) or
        (relative == 'kernel-suffix' and entry['bytes'] <= 64))) or
        re.fullmatch(r'/lib/firmware/qat_(?:402xx|4xxx)(?:_mmp)?\.bin\.zst', name))


def certificate_link_resolutions(entries, packages, package_audit, database_sha256):
    """Validate the fixed two-link chain made by Alpine CA package hooks."""
    if not isinstance(packages, dict) or not {'ca-certificates', 'ca-certificates-bundle'} <= set(packages):
        return set()
    try:
        storage.checked_package_audit(package_audit, database_sha256)
    except UpdateError:
        return set()
    changed = {row.get('path') for row in package_audit['differences'] if isinstance(row, dict)}
    prefix = '/etc/ssl/certs/ca-cert-'
    pem_links = {}
    for name, entry in entries.items():
        if not name.startswith(prefix) or not name.endswith('.pem'):
            continue
        stem = name[len(prefix):-4]
        source = '/usr/share/ca-certificates/mozilla/' + stem + '.crt'
        if (not stem or '/' in stem or len(stem.encode()) > 180 or
                any(ord(character) < 32 for character in stem) or
                not stat.S_ISLNK(entry['mode']) or entry.get('uid') != 0 or entry.get('gid') != 0 or
                source in entries or source in changed or
                entry.get('link_sha256') != hashlib.sha256(source.encode()).hexdigest()):
            continue
        pem_links[hashlib.sha256(name.rsplit('/', 1)[1].encode()).hexdigest()] = name
    resolved = set(pem_links.values())
    for name, entry in entries.items():
        if (re.fullmatch(r'/etc/ssl/certs/[0-9a-f]{8}\.[0-9]+', name) and
                stat.S_ISLNK(entry['mode']) and entry.get('uid') == 0 and entry.get('gid') == 0 and
                entry.get('link_sha256') in pem_links):
            resolved.add(name)
    return resolved


def checked_nginx_default_copy(value, packages, audit, database_sha256, entries):
    source = '/usr/share/nginx/http-default_server.conf'
    target = '/etc/nginx/http.d/default.conf'
    fields = {'kind', 'source', 'target', 'complete', 'stable', 'present', 'source_owned',
              'source_sha256', 'target_sha256', 'matching', 'adoption_authorized',
              'error_log_empty', 'error', 'evidence_sha256'}
    if (not isinstance(value, dict) or set(value) != fields or
            value['kind'] != 'klokast.vm-nginx-default-copy.v1' or
            value['source'] != source or value['target'] != target or
            any(value[k] is not True for k in ('complete', 'stable', 'present', 'source_owned', 'matching')) or
            value['adoption_authorized'] is not False or value['error'] is not None or
            type(value['error_log_empty']) is not bool or
            value['source_sha256'] != value['target_sha256'] or
            not isinstance(value['source_sha256'], str) or
            not re.fullmatch('[0-9a-f]{64}', value['source_sha256']) or
            value['evidence_sha256'] != digest({k: v for k, v in value.items() if k != 'evidence_sha256'}) or
            not isinstance(packages, dict) or 'nginx' not in packages or
            source in entries or target not in entries):
        raise UpdateError('Nginx default copy lacks complete package-source evidence')
    storage.checked_package_audit(audit, database_sha256)
    if any(v['path'] in {source, target} for v in audit['differences']):
        raise UpdateError('Nginx default copy or its package source changed')
    item = entries[target]
    if (not stat.S_ISREG(item['mode']) or item['uid'] != 0 or item['gid'] != 0 or
            item['mode'] & 0o022):
        raise UpdateError('Nginx default copy metadata differs')
    return value['error_log_empty']


def checked_legacy_firmware(value, entries):
    fields = {'kind', 'complete', 'stable', 'files', 'adoption_authorized', 'error', 'evidence_sha256'}
    names = {'/lib/firmware/qat_' + stem + '.bin.zst' for stem in
             ('402xx', '402xx_mmp', '4xxx', '4xxx_mmp')}
    if (not isinstance(value, dict) or set(value) != fields or
            value['kind'] != 'klokast.vm-legacy-firmware.v1' or
            value['complete'] is not True or value['stable'] is not True or
            value['adoption_authorized'] is not False or value['error'] is not None or
            value['evidence_sha256'] != digest({k: v for k, v in value.items() if k != 'evidence_sha256'}) or
            not isinstance(value['files'], list) or len(value['files']) > 4 or
            any(not isinstance(v, dict) or not isinstance(v.get('path'), str) for v in value['files']) or
            [v['path'] for v in value['files']] != sorted({v['path'] for v in value['files']})):
        raise UpdateError('legacy firmware metadata receipt is incomplete')
    for row in value['files']:
        if (not isinstance(row, dict) or set(row) != {'path', 'mode', 'uid', 'gid', 'links', 'bytes', 'sha256'} or
                row['path'] not in names or row['path'] not in entries or
                any(type(row[key]) is not int for key in ('mode', 'uid', 'gid', 'links', 'bytes')) or
                not stat.S_ISREG(row['mode']) or row['uid'] != 0 or row['gid'] != 0 or
                row['links'] != 1 or type(row['bytes']) is not int or
                not 0 < row['bytes'] <= 16 * 1024 * 1024 or row['mode'] & 0o022 or
                not isinstance(row['sha256'], str) or not re.fullmatch('[0-9a-f]{64}', row['sha256']) or
                any(entries[row['path']][key] != row[key] for key in ('mode', 'uid', 'gid'))):
            raise UpdateError('legacy firmware metadata differs from host inventory')
    return value['files']


def checked_inspection_artifact(value, entries, collector_pid, runtime_owner):
    fields = {'kind', 'path', 'pid', 'complete', 'stable', 'mode', 'uid', 'gid',
              'links', 'bytes', 'sha256', 'adoption_authorized', 'error', 'evidence_sha256'}
    if (not isinstance(value, dict) or set(value) != fields or
            value['kind'] != 'klokast.vm-inspection-artifact.v1' or
            value['complete'] is not True or value['stable'] is not True or
            value['adoption_authorized'] is not False or value['error'] is not None or
            type(value['pid']) is not int or value['pid'] != collector_pid or
            not isinstance(value['path'], str) or not re.fullmatch(
                r'/tmp/ansible-tmp-[0-9]+(?:\.[0-9]+)?-[0-9]+-[0-9]+/collect-vm-update-facts', value['path']) or
            value['path'] not in entries or
            any(type(value[k]) is not int for k in ('mode', 'uid', 'gid', 'links', 'bytes')) or
            not stat.S_ISREG(value['mode']) or value['links'] != 1 or
            not 0 < value['bytes'] <= 1024 * 1024 or value['mode'] & 0o022 or
            not isinstance(value['sha256'], str) or not re.fullmatch('[0-9a-f]{64}', value['sha256']) or
            value['evidence_sha256'] != digest({k: v for k, v in value.items() if k != 'evidence_sha256'})):
        raise UpdateError('running collector staging evidence is incomplete')
    owners = {(0, 0)}
    if (isinstance(runtime_owner, dict) and set(runtime_owner) == {'uid', 'gid'} and
            all(type(runtime_owner[k]) is int and runtime_owner[k] >= 1000 for k in ('uid', 'gid'))):
        owners.add((runtime_owner['uid'], runtime_owner['gid']))
    if ((value['uid'], value['gid']) not in owners or
            any(entries[value['path']][k] != value[k] for k in ('mode', 'uid', 'gid'))):
        raise UpdateError('running collector staging metadata differs')
    try:
        source_sha256 = hashlib.sha256(COLLECTOR_SOURCE.read_bytes()).hexdigest()
    except OSError as error:
        raise UpdateError('checked-in collector source is unavailable') from error
    if value['sha256'] != source_sha256:
        raise UpdateError('running collector bytes differ from the checked-in source')
    return value['path']


def fixed_service_resolution(service, entries, audit_verified, changed_paths):
    """Resolve only package-owned, unchanged scripts in the fixed boot profile."""
    script = '/etc/init.d/' + service['name']
    package_source = (audit_verified and script not in entries and
                      script not in changed_paths and service['script'] is not None)
    active = bool(service['runlevels'] or service['markers'])
    expected = (service['name'] in ACTIVE_SERVICE_STATE and
                service['runlevels'] == ACTIVE_SERVICE_STATE.get(service['name']) and
                service['markers'] == ['started'])
    return package_source and (not active or expected)


def legacy_runroot_service_resolution(service, entries, empty_rootless_runtime):
    """Resolve only the old checked-in boot helper on an empty Podman guest."""
    script = entries.get('/etc/init.d/klokast-podman-runroot-cleanup')
    return (empty_rootless_runtime and service['name'] == 'klokast-podman-runroot-cleanup' and
            service['script'] == {'sha256': LEGACY_RUNROOT_HELPER_SHA256} and
            service['runlevels'] == ['boot'] and service['markers'] == ['started'] and
            isinstance(script, dict) and
            script.get('mode') == stat.S_IFREG | 0o755 and
            script.get('uid') == 0 and script.get('gid') == 0)


def checked_runtime_directories(value):
    """Accept only stable metadata for two known runtime-only APK changes."""
    if (not isinstance(value, dict) or
            set(value) != {'kind', 'complete', 'stable', 'entries', 'evidence_sha256'} or
            value['kind'] != 'klokast.vm-runtime-directories.v1' or
            value['complete'] is not True or value['stable'] is not True or
            value['evidence_sha256'] != digest({k: v for k, v in value.items()
                                                if k != 'evidence_sha256'})):
        raise UpdateError('stable runtime directory metadata is unavailable')
    entries = value['entries']
    if (not isinstance(entries, list) or len(entries) != len(RUNTIME_DIRECTORY_MODES) or
            any(not isinstance(v, dict) for v in entries) or
            [v.get('path') for v in entries] !=
            list(RUNTIME_DIRECTORY_MODES)):
        raise UpdateError('runtime directory metadata has incomplete path coverage')
    for row in entries:
        if (set(row) != {'path', 'mode', 'uid', 'gid'} or
                any(type(row[key]) is not int for key in ('mode', 'uid', 'gid')) or
                (row['mode'], row['uid'], row['gid']) !=
                RUNTIME_DIRECTORY_MODES[row['path']]):
            raise UpdateError('runtime directory ownership or mode differs from the fixed profile')
    return {row['path']: row for row in entries}


def fixed_account_classification(account, role, legacy_template, packages, *,
                                 runtime_owner=None, subuid=None, subgid=None):
    """Recognize exact template accounts; the neo identity needs machine inputs."""
    name = account['name']
    if name == 'neo':
        matched = (runtime_owner == {'uid': 1000, 'gid': 1000} and
                   subuid == 'neo:100000:65536\n' and subgid == 'neo:100000:65536\n' and
                   tuple(account[k] for k in ('uid', 'gid', 'home', 'shell')) ==
                   (1000, 1000, '/home/neo', '/bin/ash'))
        return ('retained-machine-identity',
                'fixed no-application runtime identity; preserve UID, GID, home, shell, and subordinate ranges',
                matched)
    expected = SYSTEM_ACCOUNTS.get(name)
    if name == 'tailscale' and 'tailscale' not in packages:
        expected = None
    if name in LEGACY_SYSTEM_ACCOUNTS and legacy_template:
        expected = LEGACY_SYSTEM_ACCOUNTS[name]
    if name in DMZ_PACKAGE_ACCOUNTS and role == 'dmz' and name in packages:
        expected = DMZ_PACKAGE_ACCOUNTS[name]
    observed = tuple(account[k] for k in ('uid', 'gid', 'home', 'shell'))
    matched = expected == observed
    return ('reconstructable-os-state' if matched else 'unknown',
            'exact shared-VM system account; regenerate or omit with candidate packages' if matched else
            'account is outside the fixed no-application template profile', matched)


def path_classification(entry, *, legacy_kernel=None, busybox_present=False,
                        busybox_suid_present=False, pinentry_present=False,
                        verified_ca_links=frozenset(), nginx_default_copy=False,
                        nginx_error_empty=False, inspection_artifact=None,
                        tailscale_log_owner=None, tailscale_present=False,
                        verified_rootful_paths=frozenset(), empty_rootless_runtime=False,
                        verified_runlevel_links=frozenset(), legacy_runroot_helper=False,
                        verified_apk_world=False, verified_tailscale_resolver=False):
    """Fixed reconstruction rules. Unknown paths never inherit a parent rule."""
    name, mode = entry['path'], entry['mode']
    category, rule, resolved = 'unknown', 'no fixed rule', False
    if name in IDENTITIES:
        category, rule = 'retained-machine-identity', IDENTITIES[name]
        # Discovery alone does not validate private keys or state usability.
    elif name == '/etc/apk/world' and verified_apk_world:
        category, rule, resolved = ('reconstructable-os-state',
                                    'exact legacy package requests; generate candidate world from signed manifest and omit old DMZ application packages', True)
    elif name == '/etc/resolv.conf' and verified_tailscale_resolver:
        category, rule, resolved = ('generated-configuration',
                                    'exact Tailscale-generated resolver; rebuild from approved DNS input and live Tailnet policy', True)
    elif name in CONFIGURATION:
        category, rule = 'generated-configuration', 'compare with rendered machine inputs'
        if name == '/etc/init.d/klokast-podman-runroot-cleanup' and legacy_runroot_helper:
            rule = 'exact former checked-in Podman boot helper; replace with current candidate recipe'
            resolved = True
    elif name == '/etc/nginx/http.d/default.conf':
        category = 'reconstructable-os-state' if nginx_default_copy else 'unknown'
        rule = 'exact copy of the installed Nginx package default; replace with candidate packages'
        resolved = nginx_default_copy
    elif name == '/var/log/nginx/error.log':
        resolved = (nginx_error_empty and stat.S_ISREG(mode) and entry.get('uid') == 0 and
                    entry.get('gid') == 0)
        category = 'reconstructable-os-state' if resolved else 'unknown'
        rule = 'empty Nginx runtime log; do not copy into the candidate'
    elif name == inspection_artifact:
        category, rule, resolved = ('reconstructable-os-state',
                                    'exact running discovery collector; Ansible removes its staged copy', True)
    elif name in TAILSCALE_LOGS:
        maximum = 4096 if name.endswith('.conf') else 16 * 1024 * 1024
        resolved = (tailscale_present and isinstance(tailscale_log_owner, dict) and
                    set(tailscale_log_owner) == {'uid', 'gid'} and
                    entry.get('uid') == tailscale_log_owner['uid'] and
                    entry.get('gid') == tailscale_log_owner['gid'] and
                    stat.S_ISREG(mode) and mode & 0o777 == 0o600 and
                    entry.get('links') == 1 and type(entry.get('bytes')) is int and
                    0 <= entry['bytes'] <= maximum)
        category = 'reconstructable-os-state' if resolved else 'unknown'
        rule = 'fixed Tailscale log policy files; keep machine state separately'
    elif name in verified_rootful_paths:
        category, rule, resolved = ('reconstructable-os-state',
                                    'fixed empty rootful Podman metadata; omit from the candidate', True)
    elif name in PODMAN_RUNROOT_FILES:
        resolved = (empty_rootless_runtime and stat.S_ISREG(mode) and entry.get('uid') == 1000 and
                    entry.get('gid') == 1000 and entry.get('links') == 1 and not mode & 0o022 and
                    isinstance(entry.get('bytes'), int) and 0 <= entry['bytes'] <= 2 * 1024 * 1024)
        category = 'reconstructable-os-state' if resolved else 'unknown'
        rule = 'fixed empty rootless Podman runroot file; omit from the candidate and clear at boot'
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
    elif re.fullmatch(r'/lib/modules/[^/]+/(?:kernel/.+\.ko(?:\.(?:gz|xz|zst))?|modules\.[a-z_.]+|kernel-suffix)', name):
        category, rule = 'reconstructable-os-state', 'legacy template modloop file; replace with the candidate kernel'
        resolved = legacy_modloop_file(entry, legacy_kernel)
    elif re.fullmatch(r'/lib/firmware/qat_(?:402xx|4xxx)(?:_mmp)?\.bin\.zst', name):
        category, rule = 'reconstructable-os-state', 'legacy template modloop firmware; replace with the candidate kernel'
        resolved = legacy_modloop_file(entry, legacy_kernel)
    elif re.fullmatch(r'/var/cache/apk/APKINDEX\.[0-9a-f]+\.tar.gz', name):
        category, rule, resolved = 'reconstructable-os-state', 'rebuild package index cache from signed inputs', True
    elif re.fullmatch(r'/(?:usr/)?s?bin/[^/]+', name) and stat.S_ISLNK(mode):
        target = entry.get('link_sha256')
        if entry.get('uid') == 0 and entry.get('gid') == 0 and (
                (busybox_present and target == BUSYBOX_LINK_SHA256) or
                (busybox_suid_present and target == BBSUID_LINK_SHA256) or
                (pinentry_present and name == '/usr/bin/pinentry' and target == PINENTRY_LINK_SHA256)):
            category, rule, resolved = ('reconstructable-os-state',
                                         'fixed package applet link; regenerate from candidate package', True)
        else:
            category, rule = 'approved-package-content', 'verify generated applet link against signed package recipe'
    elif below(name, '/etc/ssl/certs') and stat.S_ISLNK(mode):
        if name in verified_ca_links:
            category, rule, resolved = ('reconstructable-os-state',
                                         'fixed CA link chain; regenerate from candidate package', True)
        else:
            category, rule = 'approved-package-content', 'verify generated certificate link against signed package recipe'
    elif re.fullmatch(r'/etc/runlevels/[^/]+/[^/]+', name):
        service_name = name.rsplit('/', 1)[1]
        resolved = (name in verified_runlevel_links and entry.get('uid') == 0 and
                    entry.get('gid') == 0 and entry.get('link_sha256') ==
                    hashlib.sha256(('/etc/init.d/' + service_name).encode()).hexdigest())
        category, rule = ('generated-configuration',
                          'fixed enabled package-owned service link' if resolved else
                          'compare enabled service with fixed boot recipe')
    # A known path with the wrong type or unsafe ownership is not disposable.
    if category != 'unknown' and not stat.S_ISDIR(mode):
        expected_link = rule.startswith(('verify generated', 'fixed package applet', 'fixed CA link')) or name.startswith('/etc/runlevels/')
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
    legacy_kernel = fact.get('kernel') if (fact.get('legacy_template_marker') == LEGACY_TEMPLATE_MARKER and
                                              fact.get('module_releases') == [fact.get('kernel')]) else None
    if legacy_kernel:
        try:
            for row in checked_legacy_firmware(fact['host_inventory'].get('legacy_firmware'), entries):
                entries[row['path']] = {**entries[row['path']], **row}
        except UpdateError:
            pass
    installed_packages = fact.get('packages') if isinstance(fact.get('packages'), dict) else {}
    package_audit = inventory.get('package_audit')
    try:
        storage.checked_package_audit(package_audit, fact['host_inventory'].get('package_database_sha256'))
        audited_package_paths = True
        changed_package_paths = {row['path'] for row in package_audit['differences']}
    except UpdateError:
        audited_package_paths = False
        changed_package_paths = set()
    storage_facts = fact.get('storage')
    observed_mounts = storage_facts.get('mounts') if isinstance(storage_facts, dict) else None
    if not isinstance(observed_mounts, list):
        observed_mounts = []
    verified_ca_links = certificate_link_resolutions(entries, fact.get('packages'),
                                                     inventory.get('package_audit'),
                                                     fact['host_inventory'].get('package_database_sha256'))
    try:
        nginx_error_empty = checked_nginx_default_copy(
            fact['host_inventory'].get('nginx_default_copy'), fact.get('packages'),
            inventory.get('package_audit'), fact['host_inventory'].get('package_database_sha256'), entries)
        nginx_default_copy = True
    except UpdateError:
        nginx_default_copy = False
        nginx_error_empty = False
    try:
        inspection_artifact = checked_inspection_artifact(
            fact['host_inventory'].get('inspection_artifact'), entries,
            inventory.get('processes', {}).get('collector_pid'), fact.get('runtime_owner'))
    except UpdateError:
        inspection_artifact = None
    verified_rootful_paths = frozenset()
    rootful = inventory.get('rootful_store')
    try:
        empty_rootful = checked_empty_store(
            fact['host_inventory'].get('rootful_empty_store'), ROOTFUL_GRAPH)
        if (not empty_rootful['empty'] or not isinstance(rootful, dict) or
                rootful.get('present') is not True or
                rootful.get('entries') != len(empty_rootful['metadata']['entries']) or
                rootful.get('metadata_sha256') != digest(empty_rootful['metadata']) or
                not isinstance(rootful.get('database'), dict) or
                rootful['database'].get('sha256') != empty_rootful['database_sha256']):
            raise UpdateError('rootful empty-store and native registration evidence differ')
        paths = {row['path'] for row in empty_rootful['metadata']['entries']}
        if not paths <= set(entries):
            raise UpdateError('rootful store file coverage differs from host inventory')
        verified_rootful_paths = frozenset(paths)
    except UpdateError:
        pass
    try:
        empty_rootless_runtime = (checked_empty_store(
            fact['host_inventory'].get('no_application_store'))['empty'] and
            fact.get('containers') == [] and fact.get('volumes') == [] and
            fact.get('runtime_owner') == {'uid': 1000, 'gid': 1000})
    except UpdateError:
        empty_rootless_runtime = False
    try:
        verified_runtime_directories = checked_runtime_directories(
            fact['host_inventory'].get('runtime_directories'))
    except UpdateError:
        verified_runtime_directories = {}
    verified_apk_world = checked_legacy_apk_world(fact, entries, role)
    verified_tailscale_resolver = checked_tailscale_resolver(fact, entries)
    verified_runlevel_links = set()
    legacy_runroot_helper = False
    for service in inventory.get('native_services', {}).get('services', []):
        legacy_helper = legacy_runroot_service_resolution(service, entries,
                                                           empty_rootless_runtime)
        legacy_runroot_helper |= legacy_helper
        if not (legacy_helper or
                (service['name'] in ACTIVE_SERVICE_STATE and
                 fixed_service_resolution(service, entries, audited_package_paths,
                                          changed_package_paths))):
            continue
        for runlevel in service['runlevels']:
            path = '/etc/runlevels/' + runlevel + '/' + service['name']
            entry = entries.get(path)
            if (entry and stat.S_ISLNK(entry['mode']) and
                    entry['uid'] == 0 and entry['gid'] == 0 and
                    entry.get('link_sha256') ==
                    hashlib.sha256(('/etc/init.d/' + service['name']).encode()).hexdigest()):
                verified_runlevel_links.add(path)
    for name, entry in sorted(entries.items()):
        item('file', name, *path_classification(entry, legacy_kernel=legacy_kernel,
                                               busybox_present='busybox' in installed_packages,
                                               busybox_suid_present='busybox-suid' in installed_packages,
                                               pinentry_present='pinentry' in installed_packages,
                                               verified_ca_links=verified_ca_links,
                                               nginx_default_copy=nginx_default_copy,
                                               nginx_error_empty=nginx_error_empty,
                                               inspection_artifact=inspection_artifact,
                                               tailscale_log_owner=fact.get('runtime_owner'),
                                               tailscale_present='tailscale' in installed_packages,
                                               verified_rootful_paths=verified_rootful_paths,
                                               empty_rootless_runtime=empty_rootless_runtime,
                                               verified_runlevel_links=verified_runlevel_links,
                                               legacy_runroot_helper=legacy_runroot_helper,
                                               verified_apk_world=verified_apk_world,
                                               verified_tailscale_resolver=verified_tailscale_resolver), entry)
    for difference in inventory.get('package_audit', {}).get('differences', []):
        name = difference['path']
        category = 'generated-configuration' if name in CONFIGURATION else 'unknown'
        rule = 'compare changed package file with approved machine recipe'
        resolved = False
        if difference['code'] == 'm' and name in {'/dev/shm', '/proc', '/run/lock', '/sys', '/var/lib/tailscale'}:
            category, rule = 'reconstructable-os-state', 'verify runtime directory ownership and mode'
            mount_verified = any(m.get('path') == name for m in observed_mounts if isinstance(m, dict))
            metadata_verified = (name in verified_runtime_directories and
                                 ('tailscale' if name == '/var/lib/tailscale' else 'alpine-baselayout')
                                 in installed_packages and
                                 not any(m.get('path') == name or
                                         m.get('path', '').startswith(name + '/')
                                         for m in observed_mounts if isinstance(m, dict)))
            resolved = audited_package_paths and (mount_verified or metadata_verified)
            if metadata_verified:
                rule = 'exact fixed runtime directory metadata; rebuild from candidate package and boot policy'
        item('package-difference', name, category, rule, resolved, difference)
    for account in inventory['accounts']:
        category, rule, resolved = fixed_account_classification(
            account, role, fact.get('legacy_template_marker') == LEGACY_TEMPLATE_MARKER,
            installed_packages, runtime_owner=fact.get('runtime_owner'),
            subuid=fact.get('subuid'), subgid=fact.get('subgid'))
        item('account', account['name'], category, rule, resolved, account)
    for service in inventory.get('native_services', {}).get('services', []):
        active = bool(service['runlevels'] or service['markers'])
        resolved = fixed_service_resolution(service, entries, audited_package_paths,
                                            changed_package_paths)
        if legacy_runroot_service_resolution(service, entries, empty_rootless_runtime):
            resolved = True
        category = ('generated-configuration' if service['name'] in BOOT_SERVICES else
                    ('unknown' if active else 'approved-package-content'))
        rule = ('exact former checked-in helper and fixed OpenRC state' if
                resolved and service['name'] == 'klokast-podman-runroot-cleanup' else
                'verified package script and fixed OpenRC state' if resolved else
                'verify script and permitted OpenRC state')
        item('service', service['name'], category, rule, resolved, service)
    for maintenance in inventory['maintenance_files']:
        if not maintenance['path'].startswith(('/etc/init.d/', '/etc/runlevels/')):
            package_source = (audited_package_paths and maintenance['path'] not in entries and
                              maintenance['path'] not in changed_package_paths and
                              maintenance['path'] in {'/etc/crontabs/root', '/etc/local.d/README'})
            item('timer', maintenance['path'], 'approved-package-content' if package_source else 'unknown',
                 'verified package file; no active timer' if package_source else
                 'compare exact timer contents with OS baseline', package_source, maintenance)
    for process in inventory.get('processes', {}).get('processes', []):
        kernel = process['kernel_thread'] and process['uids'] == [0] * 4
        profile_role = process.get('no_application_role', 'kernel-thread' if kernel else 'unknown')
        resolved = profile_role in ('kernel-thread', 'os-init', 'console-getty', 'inspection-process',
                                    'podman-pause', 'tailscale-supervisor', 'tailscale-daemon')
        item('process', str(process['pid']), 'reconstructable-os-state' if profile_role != 'unknown' else 'unknown',
             profile_role if profile_role != 'unknown' else 'bind live process to fixed service or inspection ancestry', resolved, process)
    mounts = observed_mounts
    try:
        boot = checked_boot_files(fact.get('host_inventory', {}).get('boot_files'), mounts)
        for entry in boot['metadata']['entries']:
            known = entry['path'] in boot['artifacts']
            directory = stat.S_ISDIR(entry['mode'])
            item('boot-file', entry['path'], 'reconstructable-os-state' if known or directory else 'unknown',
                 'compare boot bytes with the recorded source artifact' if known else
                 ('directory; every descendant needs its own rule' if directory else 'unclassified boot filesystem file'),
                 directory, {'metadata': entry, 'sha256': boot['artifacts'].get(entry['path'])})
    except UpdateError:
        add('qualification.boot-unknown', 'The separate boot filesystem requires complete stable file and artifact coverage.')
    expected = {'/': 'ext4', '/boot': 'ext4', '/dev': 'devtmpfs', '/dev/pts': 'devpts', '/dev/shm': 'tmpfs',
                '/proc': 'proc', '/proc/xen': 'xenfs', '/run': 'tmpfs', '/sys': 'sysfs', '/sys/fs/cgroup': 'cgroup2'}
    if not isinstance(mounts, list) or not mounts:
        add('qualification.mounts-unknown', 'Complete mount inventory is required.')
    else:
        for mount in mounts:
            supported = (mount.get('path') in expected and mount.get('type') == expected[mount['path']] and mount.get('root') == '/')
            runtime_mount = supported and mount['path'] not in {'/', '/boot'}
            item('mount', mount.get('path', '?'), 'reconstructable-os-state' if supported else 'unknown',
                 ('fixed runtime filesystem' if runtime_mount else
                  'bind filesystem to the recorded source disk' if supported else
                  'unsupported filesystem boundary'), runtime_mount, mount)
    for name in ('containers', 'volumes'):
        if fact.get(name) != []:
            add('qualification.' + name, 'The no-application profile requires a complete empty ' + name + ' inventory.')
    assessed = storage.assess(fact, [], host)
    for finding in assessed['findings']:
        if finding['severity'] == 'critical':
            result['findings'].append(finding)
    if rootful:
        resolved = not rootful['present'] or bool(verified_rootful_paths)
        item('container-store', rootful['graph_root'], 'reconstructable-os-state' if resolved else 'unknown',
             'fixed empty rootful store, omitted from the candidate' if verified_rootful_paths else
             ('standard rootful store absent' if not rootful['present'] else 'classify every store file and registration'),
             resolved, rootful)
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


def with_config_comparison(base, comparison):
    """Bind live fixed-file comparison to a qualification observation.

    Matching bytes resolve only their own generated-configuration rows. The
    result remains non-authoritative and cannot clear machine-input checks.
    """
    if (not isinstance(base, dict) or
            base.get('kind') != 'klokast.vm-no-application-qualification.v1' or
            base.get('report_sha256') != digest({k: v for k, v in base.items()
                                                 if k != 'report_sha256'})):
        raise UpdateError('base qualification is not a complete observation')
    fields = {'kind', 'host', 'source_commit', 'approved_engine',
              'qualification_sha256', 'authority', 'rows', 'report_sha256'}
    host = base['box'] + '-' + base['role']
    if (not isinstance(comparison, dict) or set(comparison) != fields or
            comparison['kind'] != 'klokast.vm-config-comparison.v1' or
            comparison['host'] != host or
            comparison['source_commit'] != base['implementation_commit'] or
            comparison['qualification_sha256'] != base['report_sha256'] or
            comparison['authority'] != 'comparison-only' or
            type(comparison['approved_engine']) is not bool or
            comparison['approved_engine'] !=
            bool(base['intent'] and base['intent']['engine_commit'] == base['implementation_commit']) or
            comparison['report_sha256'] != digest({k: v for k, v in comparison.items()
                                                   if k != 'report_sha256'})):
        raise UpdateError('fixed configuration comparison does not bind this qualification')
    rows = comparison['rows']
    expected_fields = {'path', 'expected_sha256', 'observed_sha256', 'source',
                       'source_sha256', 'match'}
    if (not isinstance(rows, list) or len(rows) != len(vm_config_audit.PATHS) or
            [row.get('path') for row in rows if isinstance(row, dict)] !=
            list(vm_config_audit.PATHS)):
        raise UpdateError('fixed configuration comparison has incomplete path coverage')
    verified = {}
    for row in rows:
        if (set(row) != expected_fields or row['path'] not in CONFIGURATION or
                not isinstance(row['source'], str) or
                not re.fullmatch(r'ansible/[A-Za-z0-9_./-]+', row['source']) or
                '..' in Path(row['source']).parts or
                any(not isinstance(row[key], str) or not re.fullmatch(r'[0-9a-f]{64}', row[key])
                    for key in ('expected_sha256', 'observed_sha256', 'source_sha256')) or
                type(row['match']) is not bool or
                row['match'] != (row['expected_sha256'] == row['observed_sha256'])):
            raise UpdateError('fixed configuration comparison contains an unsafe row')
        verified[row['path']] = row
    result = {**base, 'kind': 'klokast.vm-no-application-qualification.v2',
              'base_report_sha256': base['report_sha256'],
              'configuration_evidence_sha256': comparison['report_sha256']}
    result.pop('report_sha256')
    result['items'] = []
    for item in base['items']:
        value = dict(item)
        row = verified.get(item['key'])
        if (item['area'] in {'file', 'package-difference'} and
                item['classification'] == 'generated-configuration' and
                row is not None and row['match'] and comparison['approved_engine']):
            value['resolved'] = True
            value['rule'] = 'exact checked recipe and approved inventory match'
            value['evidence_sha256'] = digest({'item': item['evidence_sha256'],
                                               'comparison': comparison['report_sha256'],
                                               'row': row})
        result['items'].append(value)
    result['findings'] = [finding for finding in base['findings']
                          if finding['code'] != 'qualification.unresolved']
    return finish(result)


def with_legacy_firewall(base, comparison, legacy):
    """Record a bounded old firewall comparison without granting adoption."""
    if (not isinstance(base, dict) or
            base.get('kind') != 'klokast.vm-no-application-qualification.v2' or
            base.get('report_sha256') != digest({k: v for k, v in base.items()
                                                 if k != 'report_sha256'}) or
            not isinstance(comparison, dict) or
            comparison.get('kind') != 'klokast.vm-config-comparison.v1' or
            comparison.get('host') != base.get('box', '') + '-' + base.get('role', '') or
            comparison.get('source_commit') != base.get('implementation_commit') or
            comparison.get('qualification_sha256') != base.get('base_report_sha256') or
            comparison.get('authority') != 'comparison-only' or
            comparison.get('report_sha256') != base.get('configuration_evidence_sha256') or
            comparison.get('report_sha256') != digest({k: v for k, v in comparison.items()
                                                       if k != 'report_sha256'})):
        raise UpdateError('legacy firewall comparison lacks bound qualification evidence')
    if not isinstance(comparison.get('rows'), list):
        raise UpdateError('historical firewall lacks a fixed configuration comparison')
    rows = [row for row in comparison['rows'] if isinstance(row, dict) and
            row.get('path') == '/etc/nftables.nft']
    fields = {'kind', 'host', 'source_commit', 'source_sha256', 'engine_commit',
              'approved_engine', 'qualification_sha256', 'authority', 'match',
              'missing_underlay_permit', 'observed_sha256', 'expected_sha256',
              'normalized_observed_sha256', 'normalized_expected_sha256', 'report_sha256'}
    if (len(rows) != 1 or not isinstance(legacy, dict) or set(legacy) != fields or
            legacy['kind'] != 'klokast.vm-legacy-firewall-comparison.v1' or
            legacy['host'] != base['box'] + '-' + base['role'] or
            legacy['source_commit'] != vm_config_audit.LEGACY_FIREWALL_COMMIT or
            legacy['source_sha256'] != vm_config_audit.LEGACY_FIREWALL_SHA256 or
            legacy['engine_commit'] != base['implementation_commit'] or
            legacy['qualification_sha256'] != base['report_sha256'] or
            legacy['authority'] != 'comparison-only' or
            type(legacy['approved_engine']) is not bool or
            legacy['approved_engine'] != bool(base['intent'] and
                                              base['intent']['engine_commit'] == base['implementation_commit']) or
            type(legacy['match']) is not bool or
            type(legacy['missing_underlay_permit']) is not bool or
            (legacy['missing_underlay_permit'] and not legacy['match']) or
            any(not isinstance(legacy[key], str) or not re.fullmatch('[0-9a-f]{64}', legacy[key])
                for key in ('observed_sha256', 'expected_sha256',
                            'normalized_observed_sha256', 'normalized_expected_sha256')) or
            legacy['observed_sha256'] != rows[0].get('observed_sha256') or
            legacy['report_sha256'] != digest({k: v for k, v in legacy.items()
                                               if k != 'report_sha256'})):
        raise UpdateError('historical firewall comparison conflicts with qualification')
    result = {**base, 'kind': 'klokast.vm-no-application-qualification.v3',
              'prior_report_sha256': base['report_sha256'],
              'legacy_firewall_evidence_sha256': legacy['report_sha256']}
    result.pop('report_sha256')
    result['items'] = []
    for item in base['items']:
        value = dict(item)
        if (item['area'] in {'file', 'package-difference'} and
                item['key'] == '/etc/nftables.nft' and
                item['classification'] == 'generated-configuration' and
                legacy['match'] and legacy['approved_engine']):
            value['resolved'] = True
            value['rule'] = 'exact old checked firewall recipe with no extra permit; replace with current candidate'
            value['evidence_sha256'] = digest({'item': item['evidence_sha256'],
                                               'legacy': legacy['report_sha256']})
        result['items'].append(value)
    result['findings'] = [finding for finding in base['findings']
                          if finding['code'] != 'qualification.unresolved']
    return finish(result)
