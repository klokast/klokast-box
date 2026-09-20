"""Compare accepted-generation evidence without granting replacement authority.

The caller obtains `source` from the protected dom0 reader. Guest observations
cannot supply the expected hashes. Unknown files, services, and data remain
unresolved in the full no-application report.
"""
import copy
import gzip
import hashlib
import json
import re
import stat
import tarfile
import uuid
from pathlib import Path

from platform_updates import UpdateError, digest, fresh, VERIFY_AGE
import vm_no_application as noapp
import vm_storage_inventory as storage

PACKAGE_BOOT_SERVICES = frozenset('''
bootmisc cgroups devfs dmesg hostname killprocs localmount mdev modules
mount-ro procfs sysctl sysfs
'''.split())
TEMPLATE_RUNLEVELS = {
    'sysinit': frozenset(('devfs', 'dmesg', 'mdev')),
    'boot': frozenset(('hostname', 'modules', 'sysctl', 'bootmisc', 'cgroups', 'localmount')),
    'shutdown': frozenset(('killprocs', 'mount-ro')),
    'default': frozenset(('klokast-podman-runroot-cleanup',)),
}
KERNEL_MOUNTS = {'/dev/mqueue': 'mqueue', '/proc/sys/fs/binfmt_misc': 'binfmt_misc',
                 '/sys/fs/bpf': 'bpf', '/sys/fs/pstore': 'pstore',
                 '/sys/kernel/debug': 'debugfs', '/sys/kernel/security': 'securityfs',
                 '/sys/kernel/tracing': 'tracefs'}


def template_recipe(inputs, request, capsule_path, release):
    """Reconstruct bounded template files from the accepted, frozen build input."""
    if (inputs.get('kind') != 'klokast.vm-template-inputs.v1' or
            inputs.get('inputs_sha256') != digest({k: v for k, v in inputs.items() if k != 'inputs_sha256'}) or
            inputs.get('inputs_sha256') != release.get('inputs_sha256') or
            inputs.get('engine_commit') != release.get('engine_commit') or
            inputs.get('profile') != 'shared-alpine-v1' or
            request.get('inputs_sha256') != inputs['inputs_sha256']):
        raise UpdateError('accepted build inputs differ from the protected release')
    capsule = request.get('capsule', {})
    path = Path(capsule_path)
    if (path.is_symlink() or not path.is_file() or
            path.stat().st_size != capsule.get('bytes') or
            not 0 < path.stat().st_size <= 2 * 1024 * 1024 * 1024):
        raise UpdateError('accepted build capsule is absent or changed')
    checksum = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            checksum.update(chunk)
    if checksum.hexdigest() != capsule.get('sha256'):
        raise UpdateError('accepted build capsule checksum differs')
    names = {'smoke.py': ('/usr/local/libexec/klokast-template-test', 0o700),
             'retained_data.py': ('/usr/local/libexec/retained_data.py', 0o700)}
    for name in ('retained_data_test.py', 'vm_app_compatibility.py', 'static_site_test.py',
                 'vm_personalize.py', 'vm_personalize_test.py', 'personalization-config.json'):
        names[name] = ('/usr/local/libexec/' + name, 0o600)
    result = {}
    with tarfile.open(path, 'r:') as archive:
        members = archive.getmembers()
        if len(members) > 600 or len({m.name for m in members}) != len(members):
            raise UpdateError('accepted build capsule has unsafe member coverage')
        for name, (target, mode) in names.items():
            member = archive.getmember(name)
            if not member.isfile() or not 0 < member.size <= 1024 * 1024:
                raise UpdateError('accepted build helper is unsafe: ' + name)
            result[target] = (hashlib.sha256(archive.extractfile(member).read()).hexdigest(), mode)
        manifest = archive.getmember('inputs.json')
        if not manifest.isfile() or manifest.size > 1024 * 1024 or json.loads(archive.extractfile(manifest).read()) != inputs:
            raise UpdateError('accepted capsule and build manifest differ')
    def fixed(path, content, mode=0o644):
        result[path] = (hashlib.sha256(content.encode()).hexdigest(), mode)
    versions = {p['name']: p['version'] for p in inputs['packages']}
    fixed('/etc/apk/arch', inputs['architecture'] + '\n')
    fixed('/etc/apk/repositories', '\n'.join(inputs['repositories']) + '\n')
    fixed('/etc/apk/world', '\n'.join(name + '=' + versions[name] for name in inputs['world']) + '\n')
    fixed('/etc/conf.d/clock', 'clock="UTC"\ntimezone="UTC"\n')
    fixed('/etc/mkinitfs/mkinitfs.conf', 'features="base ext4 virtio xen"\n')
    fixed('/etc/klokast-template.json', json.dumps({
        'kind': 'klokast.vm-template-marker.v1', 'engine_commit': inputs['engine_commit'],
        'profile': inputs['profile'], 'inputs_sha256': inputs['inputs_sha256']}, sort_keys=True) + '\n')
    kernel = release.get('kernel_release')
    if not isinstance(kernel, str) or not re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+-[0-9]+-virt', kernel):
        raise UpdateError('accepted kernel has an unsupported package artifact name')
    fixed('/lib/modules/' + kernel + '/initramfs-suffix', '-virt\n')
    package_names = {'openrc', 'busybox-mdev-openrc', 'linux-virt'}
    packages = {p['name']: p for p in inputs['packages'] if p['name'] in package_names}
    if set(packages) != package_names:
        raise UpdateError('accepted template lacks fixed boot packages')
    wanted = {'etc/init.d/' + name for name in PACKAGE_BOOT_SERVICES}
    wanted.update({'boot/System.map-' + kernel, 'boot/config-' + kernel})
    package_files = {}
    with tarfile.open(path, 'r:') as archive:
        for name in sorted(packages):
            member = archive.getmember(packages[name]['file'])
            if not member.isfile() or member.size != packages[name]['bytes']:
                raise UpdateError('accepted boot package differs from frozen input')
            with gzip.GzipFile(fileobj=archive.extractfile(member)) as compressed, \
                    tarfile.open(fileobj=compressed, mode='r|', ignore_zeros=True) as package:
                for index, entry in enumerate(package):
                    if index > 100000:
                        raise UpdateError('accepted boot package has too many entries')
                    if entry.name not in wanted:
                        continue
                    if entry.name in package_files or not entry.isfile() or not 0 < entry.size <= 128 * 1024 * 1024:
                        raise UpdateError('accepted boot package artifact is unsafe')
                    package_files['/' + entry.name] = hashlib.sha256(package.extractfile(entry).read()).hexdigest()
    if set(package_files) != {'/' + name for name in wanted}:
        raise UpdateError('accepted boot package artifacts are incomplete')
    return {'files': result, 'package_files': package_files}


def compare(base, source, discovery, now, recipe=None):
    if (base.get('kind') != 'klokast.vm-no-application-qualification.v1' or
            base.get('report_sha256') != digest({k: v for k, v in base.items() if k != 'report_sha256'}) or
            base.get('discovery_sha256') != digest(discovery)):
        raise UpdateError('accepted audit requires the complete bound no-application report')
    host = base['box'] + '-' + base['role']
    rows = [row['facts'] for row in discovery.get('hosts', []) if row.get('host') == host]
    if len(rows) != 1 or not fresh(discovery.get('generated_at'), now, VERIFY_AGE):
        raise UpdateError('accepted audit requires one fresh target observation')
    fact = rows[0]
    expected = source.get('files_sha256')
    release = source.get('release', {})
    operation = source.get('operation_id')
    if (source.get('kind') != 'klokast.vm-replacement-source.v1' or
            source.get('dom0') != base['box'] + '-dom0' or source.get('role') != base['role'] or
            source.get('runtime') != 'running' or source.get('autostart') is not True or
            type(source.get('observed_at')) is not int or
            not 0 <= now.timestamp() - source['observed_at'] <= 300 or
            not isinstance(operation, str) or not re.fullmatch('[0-9a-f]{24}', operation) or
            release.get('release_sha256') != digest({k: v for k, v in release.items() if k != 'release_sha256'}) or
            not isinstance(expected, dict) or not expected or
            any(not isinstance(k, str) or not re.fullmatch(r'etc/[a-zA-Z0-9_./-]+', k) or '..' in k.split('/') or
                not isinstance(v, str) or not re.fullmatch('[0-9a-f]{64}', v) for k, v in expected.items())):
        raise UpdateError('accepted source is incomplete, stale, or names another guest')
    disks = {'/dev/vg0/vmupd_' + operation + suffix: device
             for suffix, device in (('_root', 'xvda'), ('_data', 'xvdb'))}
    if source.get('disk_mappings') != disks or set(source.get('disks', {})) != set(disks):
        raise UpdateError('accepted source does not bind distinct OS and retained disks')
    try:
        if str(uuid.UUID(source['vm_uuid'])) != source['vm_uuid']:
            raise ValueError()
    except (KeyError, ValueError, TypeError, AttributeError) as error:
        raise UpdateError('accepted Xen identity is invalid') from error
    observed = fact.get('accepted_file_hashes')
    if (not isinstance(observed, dict) or set(observed) != {'kind', 'complete', 'stable', 'files', 'error', 'evidence_sha256'} or
            observed['kind'] != 'klokast.vm-accepted-file-hashes.v1' or
            observed['complete'] is not True or observed['stable'] is not True or observed['error'] is not None or
            observed['evidence_sha256'] != digest({k: v for k, v in observed.items() if k != 'evidence_sha256'}) or
            not isinstance(observed['files'], dict)):
        raise UpdateError('accepted recipe file coverage is missing or unstable')
    matching = set()
    private = {'etc/shadow', 'etc/doas.d/doas.conf', 'etc/klokast-personalization.json',
               *('etc/ssh/ssh_host_' + key + '_key' for key in ('rsa', 'ecdsa', 'ed25519'))}
    for path, checksum in expected.items():
        mode = 0o600 if path in private else 0o755 if path == 'etc/init.d/klokast-podman-runroot-cleanup' else 0o644
        record = observed['files'].get('/' + path)
        if (isinstance(record, dict) and set(record) == {'sha256', 'mode', 'uid', 'gid'} and
                record['sha256'] == checksum and record['uid'] == 0 and
                record['gid'] in ({0, 42} if path == 'etc/shadow' else {0}) and
                type(record['mode']) is int and record['mode'] == mode):
            matching.add('/' + path)
    recipe_matching = set()
    package_files = {}
    if recipe is not None:
        if (not isinstance(recipe, dict) or set(recipe) != {'files', 'package_files'} or
                not isinstance(recipe['files'], dict) or not isinstance(recipe['package_files'], dict) or any(
                not isinstance(path, str) or not path.startswith('/') or
                not isinstance(value, tuple) or len(value) != 2 or
                not isinstance(value[0], str) or not re.fullmatch('[0-9a-f]{64}', value[0]) or
                value[1] not in (0o600, 0o644, 0o700)
                for path, value in recipe['files'].items())):
            raise UpdateError('accepted template recipe evidence is invalid')
        package_files = recipe['package_files']
        if (set(package_files) != {'/etc/init.d/' + name for name in PACKAGE_BOOT_SERVICES} |
                {'/boot/System.map-' + release['kernel_release'], '/boot/config-' + release['kernel_release']} or
                any(not isinstance(v, str) or not re.fullmatch('[0-9a-f]{64}', v)
                    for v in package_files.values())):
            raise UpdateError('accepted boot package recipe is incomplete')
        for path, (checksum, mode) in recipe['files'].items():
            record = observed['files'].get(path)
            if (isinstance(record, dict) and set(record) == {'sha256', 'mode', 'uid', 'gid'} and
                    record == {'sha256': checksum, 'mode': mode, 'uid': 0, 'gid': 0}):
                recipe_matching.add(path)
    mounts = fact.get('accepted_mount_sources', {})
    devices = mounts.get('devices')
    observed_mounts = fact.get('storage', {}).get('mounts', [])
    wanted = {'/': '/dev/xvda', '/srv/retained': '/dev/xvdb'}
    mount_match = (set(mounts) == {'kind', 'complete', 'stable', 'devices', 'error', 'evidence_sha256'} and
                   mounts.get('kind') == 'klokast.vm-accepted-mount-sources.v1' and
                   mounts.get('complete') is True and mounts.get('stable') is True and mounts.get('error') is None and
                   mounts.get('evidence_sha256') == digest({k: v for k, v in mounts.items() if k != 'evidence_sha256'}) and
                   isinstance(devices, dict) and set(devices) == set(wanted))
    if mount_match:
        mount_match = all(isinstance(devices[path], dict) and set(devices[path]) == {'source', 'device'} and
                          devices[path]['source'] == device and
                          len([m for m in observed_mounts if m.get('path') == path and m.get('root') == '/' and
                               m.get('type') == 'ext4' and m.get('device') == devices[path]['device']]) == 1
                          for path, device in wanted.items())
        mount_match &= devices['/']['device'] != devices['/srv/retained']['device']
    boot = noapp.checked_boot_files(fact.get('host_inventory', {}).get('boot_files'), observed_mounts)
    artifacts = source.get('artifacts', {})
    boot_match = (mount_match and boot['kind'] == 'klokast.vm-boot-files.v2' and
                  len(artifacts) == 2 and
                  all(sum(row.get('sha256') == boot['artifacts'].get('/boot/' + guest)
                          for row in artifacts.values()) == 1
                      for guest in ('vmlinuz-virt', 'initramfs-virt')))
    packages_match = ({name: value.get('version') for name, value in fact.get('packages', {}).items()} == release.get('packages') and
                      fact.get('kernel') == release.get('kernel_release'))
    inventory = fact.get('host_inventory', {})
    entries = {entry['path']: entry for entry in inventory.get('unowned_paths', [])}
    for entry in inventory.get('unowned_tree', {}).get('entries', []):
        entries[entry['path']] = entry
    try:
        audit = storage.checked_package_audit(inventory.get('package_audit'),
                                              inventory.get('package_database_sha256'))
        native = storage.checked_native_services(inventory.get('native_services'),
                                                 inventory.get('maintenance_files', []))
        audited = True
    except UpdateError:
        audit, native, audited = {}, {}, False
    changed = {item['path'] for item in audit.get('differences', [])}
    services = {item['name']: item for item in native.get('services', [])}
    service_match = set()
    for name in PACKAGE_BOOT_SERVICES | {'klokast-podman-runroot-cleanup'}:
        service = services.get(name, {})
        levels = sorted(level for level, names in TEMPLATE_RUNLEVELS.items() if name in names)
        markers = [] if name in {'killprocs', 'mount-ro'} else ['started']
        script = '/etc/init.d/' + name
        expected_script = (expected.get(script.removeprefix('/')) if
                           name == 'klokast-podman-runroot-cleanup' else package_files.get(script))
        if (audited and packages_match and expected_script and script not in changed and
                service.get('script') == {'sha256': expected_script} and
                service.get('runlevels') == levels and service.get('markers') == markers and
                (name != 'klokast-podman-runroot-cleanup' or script in matching)):
            service_match.add(name)
    links_match = set()
    for level, names in TEMPLATE_RUNLEVELS.items():
        for name in names & service_match:
            path = '/etc/runlevels/' + level + '/' + name
            entry = entries.get(path, {})
            if (stat.S_ISLNK(entry.get('mode', 0)) and entry.get('uid') == 0 and entry.get('gid') == 0 and
                    entry.get('link_sha256') == hashlib.sha256(('/etc/init.d/' + name).encode()).hexdigest()):
                links_match.add(path)
    extra_boot = set()
    if boot_match and packages_match and audited:
        for path in ('/boot/System.map-' + release['kernel_release'],
                     '/boot/config-' + release['kernel_release']):
            if path not in changed and boot['artifacts'].get(path) == package_files.get(path):
                extra_boot.add(path)
    boot_link = entries.get('/boot/boot', {})
    boot_link_match = (boot_match and stat.S_ISLNK(boot_link.get('mode', 0)) and
                       boot_link.get('uid') == 0 and boot_link.get('gid') == 0 and
                       boot_link.get('link_sha256') == hashlib.sha256(b'.').hexdigest())
    logs = {'/var/log/dmesg': (0o640, 0), '/var/log/wtmp': (0o664, 406)}
    log_match = {path for path, (mode, gid) in logs.items()
                 if (boot_match and packages_match and
                     stat.S_ISREG(entries.get(path, {}).get('mode', 0)) and
                     stat.S_IMODE(entries[path]['mode']) == mode and
                     entries[path].get('uid') == 0 and entries[path].get('gid') == gid)}
    virtual_mounts = {m['path']: m for m in observed_mounts if m.get('path') in KERNEL_MOUNTS}
    virtual_match = {path for path, kind in KERNEL_MOUNTS.items()
                     if (mount_match and path in virtual_mounts and
                         virtual_mounts[path].get('type') == kind and
                         virtual_mounts[path].get('root') == '/' and
                         re.fullmatch(r'0:[0-9]+', virtual_mounts[path].get('device', '')))}
    runtime = inventory.get('runtime_directories', {})
    lock_match = (audited and packages_match and runtime.get('kind') == 'klokast.vm-runtime-directories.v1' and
                  runtime.get('complete') is True and runtime.get('stable') is True and
                  runtime.get('evidence_sha256') == digest({k: v for k, v in runtime.items() if k != 'evidence_sha256'}) and
                  any(item == {'path': '/run/lock', 'mode': stat.S_IFDIR | 0o775, 'uid': 0, 'gid': 14}
                      for item in runtime.get('entries', [])) and
                  {'code': 'm', 'path': '/run/lock'} in audit.get('differences', []) and
                  'alpine-baselayout' in fact.get('packages', {}))
    result = copy.deepcopy(base)
    result.pop('report_sha256')
    result.update(kind='klokast.vm-accepted-audit.v1', prior_report_sha256=base['report_sha256'],
                  source_sha256=digest(source), generated_files_match=len(matching) == len(expected),
                  template_files_match=recipe is not None and len(recipe_matching) == len(recipe['files']),
                  mount_match=bool(mount_match), boot_match=bool(boot_match), packages_match=packages_match)
    for row in result['items']:
        proven = ((row['area'] in {'file', 'package-difference'} and row['key'] in matching | recipe_matching) or
                  (row['area'] == 'account' and '/etc/passwd' in matching and '/etc/group' in matching) or
                  (row['area'] == 'mount' and row['key'] in wanted and mount_match) or
                  (row['area'] in {'file', 'boot-file'} and row['key'] in {'/boot/vmlinuz-virt', '/boot/initramfs-virt'} and boot_match) or
                  (row['area'] in {'file', 'boot-file'} and row['key'] in extra_boot) or
                  (row['area'] in {'file', 'boot-file'} and row['key'] == '/boot/boot' and boot_link_match) or
                  (row['area'] == 'file' and row['key'] in links_match) or
                  (row['area'] == 'file' and row['key'] in log_match) or
                  (row['area'] == 'mount' and row['key'] in virtual_match) or
                  (row['area'] == 'package-difference' and row['key'] == '/run/lock' and lock_match) or
                  (row['area'] == 'service' and row['key'] in service_match))
        if proven and row['area'] in {'file', 'service'} and row['key'] in entries | services:
            evidence = entries.get(row['key'], services.get(row['key']))
            proven = row['evidence_sha256'] == digest(evidence)
        if proven:
            row.update(resolved=True, classification='accepted-generation-content',
                       rule='matches the protected accepted generation and current guest observation',
                       evidence_sha256=digest({'old': row['evidence_sha256'], 'source': digest(source),
                                               'discovery': digest(discovery)}))
    result['findings'] = [row for row in result['findings'] if row['code'] != 'qualification.unresolved']
    # No source observation enrolls a guest or grants standing mutation authority.
    return noapp.finish(result)
