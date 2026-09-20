"""Compare accepted-generation evidence without granting replacement authority.

The caller obtains `source` from the protected dom0 reader. Guest observations
cannot supply the expected hashes. Unknown files, services, and data remain
unresolved in the full no-application report.
"""
import copy
import re
import uuid

from platform_updates import UpdateError, digest, fresh, VERIFY_AGE
import vm_no_application as noapp


def compare(base, source, discovery, now):
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
    result = copy.deepcopy(base)
    result.pop('report_sha256')
    result.update(kind='klokast.vm-accepted-audit.v1', prior_report_sha256=base['report_sha256'],
                  source_sha256=digest(source), generated_files_match=len(matching) == len(expected),
                  mount_match=bool(mount_match), boot_match=bool(boot_match), packages_match=packages_match)
    for row in result['items']:
        proven = ((row['area'] in {'file', 'package-difference'} and row['key'] in matching) or
                  (row['area'] == 'account' and '/etc/passwd' in matching and '/etc/group' in matching) or
                  (row['area'] == 'mount' and row['key'] in wanted and mount_match) or
                  (row['area'] in {'file', 'boot-file'} and row['key'] in {'/boot/vmlinuz-virt', '/boot/initramfs-virt'} and boot_match))
        if proven:
            row.update(resolved=True, classification='accepted-generation-content',
                       rule='matches the protected accepted generation and current guest observation',
                       evidence_sha256=digest({'old': row['evidence_sha256'], 'source': digest(source),
                                               'discovery': digest(discovery)}))
    result['findings'] = [row for row in result['findings'] if row['code'] != 'qualification.unresolved']
    # No source observation enrolls a guest or grants standing mutation authority.
    return noapp.finish(result)
