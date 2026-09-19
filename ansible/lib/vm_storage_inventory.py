"""Read-only storage assessment. Catalog matches are not retention authority.

Inputs are untrusted guest observations and reviewed public catalog mappings.
This module has no disk, filesystem, subprocess, or private-source access.
No result is a copy request, a deletion list, or an accepted adoption record.
"""
import re
import stat

from platform_updates import UpdateError, digest, findings

NAME = re.compile(r'[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}')
HASH = re.compile(r'(?:sha256:)?[0-9a-f]{64}')
UNVERIFIED = [
    'instance-retention', 'recoverable-backup', 'host-data-completeness',
    'other-container-accounts', 'filesystem-identities', 'application-configuration',
    'writer-fencing', 'accepted-release',
]


def path(value):
    return (isinstance(value, str) and value.startswith('/') and len(value) <= 4096 and
            all(part not in ('', '.', '..') for part in value[1:].split('/')) and
            all(ord(c) >= 32 and ord(c) != 127 for c in value))


def catalog_index(catalogs, role):
    """Require closed public mappings; never load paths named by observations."""
    result = {}
    apps = set()
    for catalog in catalogs:
        if (not isinstance(catalog, dict) or set(catalog) != {'kind', 'app', 'role', 'datasets'} or
                catalog['kind'] != 'klokast.vm-retention-catalog.v1' or
                not isinstance(catalog['app'], str) or not NAME.fullmatch(catalog['app']) or
                catalog['app'] in apps or catalog['role'] not in ('bak', 'dmz', 'iot') or
                not isinstance(catalog['datasets'], dict) or not catalog['datasets']):
            raise UpdateError('retention catalog has an invalid or duplicate app contract')
        apps.add(catalog['app'])
        for dataset, entry in catalog['datasets'].items():
            if (not isinstance(dataset, str) or not NAME.fullmatch(dataset) or
                    not isinstance(entry, dict) or set(entry) != {'volumes', 'legacy_directories'} or
                    not isinstance(entry['volumes'], list) or not entry['volumes'] or
                    any(not isinstance(v, str) or not NAME.fullmatch(v) for v in entry['volumes']) or
                    len(set(entry['volumes'])) != len(entry['volumes']) or
                    not isinstance(entry['legacy_directories'], list) or
                    any(not path(p) or len(p.split('/')) < 4 for p in entry['legacy_directories']) or
                    len(set(entry['legacy_directories'])) != len(entry['legacy_directories'])):
                raise UpdateError('retention catalog dataset has invalid physical mappings')
            if catalog['role'] != role:
                continue
            for volume in entry['volumes']:
                if volume in result:
                    raise UpdateError('retention catalog volume has more than one owner')
                result[volume] = {'app': catalog['app'], 'dataset': dataset}
    return result


def subids(content, uid, allocation_id):
    """Preserve numeric ranges and reject ambiguous or overlapping allocations."""
    if not isinstance(content, str) or not content or len(content) > 65536:
        raise UpdateError('subordinate identity inventory is missing or too large')
    ranges, selected = [], []
    for line in content.splitlines():
        if not line or line.startswith('#'):
            continue
        parts = line.split(':')
        if len(parts) != 3 or not parts[0] or any(not re.fullmatch('[0-9]{1,10}', p) for p in parts[1:]):
            raise UpdateError('subordinate identity inventory has an invalid record')
        start, count = map(int, parts[1:])
        if not 0 < start < start + count < 2**32 or any(start < end and old < start + count for old, end in ranges):
            raise UpdateError('subordinate identity allocations overlap or are invalid')
        ranges.append((start, start + count))
        if parts[0] in ('neo', str(uid)):
            if start <= allocation_id < start + count:
                raise UpdateError('subordinate allocation includes the runtime identity')
            selected.append([start, count])
    if not 1 <= len(selected) <= 16:
        raise UpdateError('neo has no supported subordinate identity allocation')
    return sorted(selected)


def assess(fact, catalogs, host):
    result = {'kind': 'klokast.vm-storage-assessment.v1', 'adoption_ready': False,
              'catalog_sha256': digest(catalogs), 'volumes': [], 'bind_mounts': [], 'containers': [],
              'runtime_identity': None, 'unverified_gates': list(UNVERIFIED), 'findings': []}

    def add(code, message, critical=True):
        result['findings'].append(findings(code, message, 'critical' if critical else 'warning', host))

    add('storage.adoption-unverified', 'Storage observations require approved retention intent, complete host-data accounting, a recoverable backup, and an authorized adoption.', False)
    index = catalog_index(catalogs, fact.get('role'))
    owner = fact.get('runtime_owner')
    try:
        if (not isinstance(owner, dict) or set(owner) != {'uid', 'gid'} or
                any(type(v) is not int or not 0 < v < 2**32 - 1 for v in owner.values())):
            raise UpdateError('numeric runtime UID/GID inventory is missing')
        result['runtime_identity'] = {**owner, 'subuid': subids(fact.get('subuid'), owner['uid'], owner['uid']),
                                      'subgid': subids(fact.get('subgid'), owner['uid'], owner['gid'])}
    except UpdateError:
        add('storage.identity-unknown', 'Runtime UID, GID, or non-overlapping subordinate ranges are not established.')

    runtime = fact.get('podman_storage')
    expected_root = '/home/neo/.local/share/containers/storage'
    store_ok = (isinstance(runtime, dict) and runtime.get('rootless') is True and
                runtime.get('graph_root') == expected_root and
                runtime.get('volume_path') == expected_root + '/volumes' and
                runtime.get('graph_root_directory') is True and runtime.get('transient') is False)
    if not store_ok:
        add('storage.runtime-unknown', 'The rootless persistent Podman store has no supported verified path.')
    if fact.get('podman_inventory_stable') is not True:
        add('storage.inventory-unstable', 'Container or volume inventory failed or changed during collection.')

    volumes = fact.get('volumes')
    by_name = {}
    if not isinstance(volumes, list) or len(volumes) > 4096:
        add('storage.volumes-unknown', 'Complete named-volume inspection is unavailable.')
        volumes = []
    for item in volumes:
        if (not isinstance(item, dict) or not isinstance(item.get('Name'), str) or
                not NAME.fullmatch(item['Name']) or item['Name'] in by_name):
            add('storage.volume-invalid', 'Named-volume inventory has an invalid or duplicate identity.')
            continue
        name = item['Name']
        by_name[name] = item
        source = item.get('Mountpoint')
        safe = (store_ok and source == expected_root + '/volumes/' + name + '/_data' and
                item.get('Driver') == 'local' and item.get('options_empty') is True and
                item.get('directory_verified') is True)
        resource = {'name': name, 'source': source if path(source) else None,
                    'catalog_match': index.get(name), 'path_supported': safe,
                    'retention_approved': False}
        result['volumes'].append(resource)
        if not safe:
            add('storage.volume-unsafe', 'A volume uses an unverified path, driver, mount option, or filesystem boundary.')
        if name not in index:
            add('storage.volume-unclassified', 'A named volume has no reviewed retained-dataset mapping. Preserve it pending review.')
    matched_datasets = {(index[name]['app'], index[name]['dataset']) for name in by_name if name in index}
    for app, dataset in sorted(matched_datasets):
        if any(name not in by_name for name, entry in index.items() if (entry['app'], entry['dataset']) == (app, dataset)):
            add('storage.dataset-incomplete', 'An observed catalog dataset is missing one or more required volumes.')

    containers = fact.get('containers')
    if not isinstance(containers, list) or len(containers) > 4096:
        add('storage.containers-unknown', 'Complete container inspection is unavailable.')
        containers = []
    seen = set()
    for container in containers:
        if (not isinstance(container, dict) or not isinstance(container.get('id'), str) or
                not re.fullmatch('[0-9a-f]{64}', container['id']) or container['id'] in seen or
                not isinstance(container.get('name'), str) or not NAME.fullmatch(container['name']) or
                not isinstance(container.get('mounts'), list)):
            add('storage.container-invalid', 'Container inspection has an incomplete or duplicate identity.')
            continue
        seen.add(container['id'])
        result['containers'].append({k: container.get(k) for k in
                                     ('id', 'name', 'image_id', 'infra', 'pod', 'runtime_state', 'read_only_root')})
        image_id = container.get('image_id')
        if not isinstance(image_id, str) or not HASH.fullmatch(image_id):
            if (container.get('infra') is True and image_id == '' and
                    isinstance(container.get('pod'), str) and re.fullmatch('[0-9a-f]{64}', container['pod'])):
                add('storage.infra-unqualified', 'An image-less Podman infrastructure container needs a fixed pod reconstruction adapter.', False)
            else:
                add('storage.image-unknown', 'A container has no exact image identity; its mounts still require accounting.')
        # A catalog name or image ID is not an approved deployment receipt.
        add('storage.container-unqualified', 'A deployed container needs an approved image, configuration, and maintenance adapter.', False)
        if container.get('read_only_root') is not True:
            add('storage.writable-layer', 'A container has a writable or unknown root layer; its changes are not accounted for.')
        destinations = set()
        for mount in container['mounts']:
            if (not isinstance(mount, dict) or not path(mount.get('Destination')) or
                    mount['Destination'] in destinations or type(mount.get('RW')) is not bool):
                add('storage.mount-invalid', 'Container mounts contain an ambiguous path or access mode.')
                continue
            destinations.add(mount['Destination'])
            if mount.get('Type') == 'volume':
                volume = by_name.get(mount.get('Name')) if isinstance(mount.get('Name'), str) else None
                if volume is None or mount.get('Source') != volume.get('Mountpoint'):
                    add('storage.mount-conflict', 'A container volume mount differs from the named-volume inventory.')
            elif mount.get('Type') == 'bind':
                source = mount.get('Source')
                result['bind_mounts'].append({'container': container['name'], 'source': source if path(source) else None,
                                             'destination': mount['Destination'], 'writable': mount['RW']})
                add('storage.bind-unclassified', 'A host bind mount needs a reviewed configuration or retained-data mapping, including read-only mounts.')
            elif mount.get('Type') != 'tmpfs':
                add('storage.mount-unsupported', 'A container mount uses an unsupported storage type.')
    result['volumes'].sort(key=lambda v: v['name'])
    result['containers'].sort(key=lambda v: v['name'])
    result['bind_mounts'].sort(key=lambda v: (v['container'], v['destination']))
    # Report each refusal once even if several resources have the same issue.
    result['findings'] = list({entry['code']: entry for entry in result['findings']}.values())
    return result


def checked_native_services(value, maintenance):
    """Validate guest observations and cross-check complete script coverage."""
    if (not isinstance(value, dict) or set(value) != {'kind', 'health_verified', 'services', 'markers_sha256'} or
            value['kind'] != 'klokast.vm-native-services.v1' or value['health_verified'] is not False or
            not isinstance(value['markers_sha256'], str) or not HASH.fullmatch(value['markers_sha256']) or
            not isinstance(value['services'], list) or not 1 <= len(value['services']) <= 1024):
        raise UpdateError('native service inventory is unavailable')
    names, scripts, levels = [], {}, {}
    for item in maintenance:
        parts = item['path'].split('/')
        if len(parts) == 4 and parts[1:3] == ['etc', 'init.d'] and not parts[3].endswith('.sh'):
            scripts[parts[3]] = {k: v for k, v in item.items() if k != 'path'}
        elif parts[1:3] == ['etc', 'runlevels']:
            if len(parts) != 5 or 'link_sha256' not in item:
                raise UpdateError('native service runlevel is unsupported')
            levels.setdefault(parts[4], []).append(parts[3])
    for item in value['services']:
        if (not isinstance(item, dict) or set(item) != {'name', 'script', 'runlevels', 'markers'} or
                not isinstance(item['name'], str) or not re.fullmatch('[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}', item['name'])):
            raise UpdateError('native service identity is invalid')
        name = item['name']
        for key in ('runlevels', 'markers'):
            entries = item[key]
            if (not isinstance(entries, list) or len(entries) > 4096 or
                    any(not isinstance(v, str) or not re.fullmatch('[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}', v) for v in entries) or
                    entries != sorted(set(entries))):
                raise UpdateError('native service state is invalid')
        if (set(item['markers']) - {'started', 'starting', 'stopping', 'inactive', 'wasinactive', 'hotplugged', 'failed', 'scheduled'} or
                item['script'] != scripts.get(name) or item['runlevels'] != sorted(levels.get(name, []))):
            raise UpdateError('native service evidence conflicts with maintenance inventory')
        names.append(name)
    if names != sorted(set(names)) or (set(scripts) | set(levels)) - set(names):
        raise UpdateError('native service coverage is incomplete or duplicated')
    return value


def checked_processes(value, services):
    v2 = isinstance(value, dict) and value.get('kind') == 'klokast.vm-process-inventory.v2'
    if (not isinstance(value, dict) or set(value) != {'kind', 'health_verified', 'boot_id', 'processes'} | ({'collector_pid'} if v2 else set()) or
            value['kind'] not in ('klokast.vm-process-inventory.v1', 'klokast.vm-process-inventory.v2') or value['health_verified'] is not False or
            not isinstance(value['boot_id'], str) or
            not re.fullmatch(r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}', value['boot_id']) or
            not isinstance(value['processes'], list) or not 1 <= len(value['processes']) <= 4096):
        raise UpdateError('process inventory is unavailable')
    known = {v['name'] for v in services['services']}
    pids = []
    extra = {'no_application_role'} if value['kind'] == 'klokast.vm-process-inventory.v2' else set()
    for item in value['processes']:
        if (not isinstance(item, dict) or set(item) != {'pid', 'parent_pid', 'start_ticks', 'uids', 'gids',
                'executable', 'executable_deleted', 'kernel_thread', 'service'} | extra or
                ('no_application_role' in extra and item['no_application_role'] not in
                 ('unknown', 'kernel-thread', 'os-init', 'console-getty', 'podman-pause',
                  'tailscale-supervisor', 'tailscale-daemon', 'inspection-process')) or
                any(type(item[k]) is not int or not 0 <= item[k] < 2**63 for k in ('pid', 'parent_pid', 'start_ticks')) or
                item['pid'] == 0 or item['pid'] == item['parent_pid'] or
                any(not isinstance(item[k], list) or len(item[k]) != 4 or
                    any(type(v) is not int or not 0 <= v < 2**32 for v in item[k]) for k in ('uids', 'gids')) or
                (item['executable'] is not None and not path(item['executable'])) or
                any(type(item[k]) is not bool for k in ('executable_deleted', 'kernel_thread')) or
                (item['kernel_thread'] and item['executable'] is not None) or
                (item['service'] is not None and (not isinstance(item['service'], str) or item['service'] not in known or
                                                not (item['executable'] or '').endswith('/supervise-daemon')))):
            raise UpdateError('process identity or ownership is invalid')
        pids.append(item['pid'])
    if (pids != sorted(set(pids)) or 1 not in pids or
            any(v['parent_pid'] not in {0, *pids} for v in value['processes'])):
        raise UpdateError('process coverage is incomplete or duplicated')
    if v2:
        collector = value['collector_pid']
        if collector is not None and (type(collector) is not int or collector not in pids):
            raise UpdateError('inspection process has no observed identity')
        by_pid = {row['pid']: row for row in value['processes']}
        ancestry = set()
        while collector in by_pid and collector not in ancestry:
            ancestry.add(collector)
            collector = by_pid[collector]['parent_pid']
        for row in value['processes']:
            role = row['no_application_role']
            if role == 'unknown':
                continue
            root = row['uids'] == [0] * 4
            executable = row['executable']
            valid = {
                'kernel-thread': row['kernel_thread'] and root,
                'os-init': row['pid'] == 1 and root and executable == '/bin/busybox',
                'console-getty': row['parent_pid'] == 1 and root and executable == '/bin/busybox',
                'podman-pause': row['parent_pid'] == 1 and len(set(row['uids'])) == 1 and row['uids'][0] >= 1000 and executable == '/usr/bin/catatonit',
                'tailscale-supervisor': root and executable == '/sbin/supervise-daemon' and row['service'] == 'tailscale',
                'tailscale-daemon': root and executable == '/usr/sbin/tailscaled',
                'inspection-process': row['pid'] in ancestry and executable in ('/bin/busybox', '/usr/sbin/tailscaled', '/usr/bin/python3.12', '/usr/bin/python3.13', '/usr/bin/python3.14'),
            }[role]
            if not valid or row['executable_deleted']:
                raise UpdateError('fixed process role conflicts with process identity or collector ancestry')
    return value


def checked_unowned_tree(value, delegated):
    fields = {'kind', 'complete', 'stable', 'data_accounted', 'roots', 'entries', 'excluded', 'metadata_sha256'}
    if (not isinstance(value, dict) or set(value) != fields or value['kind'] != 'klokast.vm-unowned-tree.v1' or
            value['complete'] is not True or value['stable'] is not True or value['data_accounted'] is not False):
        raise UpdateError('unowned directory metadata is unavailable or unstable')
    roots = sorted(v['path'] for v in delegated if v['reason'] == 'unclassified-directory')
    if value['roots'] != roots or not isinstance(value['entries'], list) or len(value['entries']) > 8192:
        raise UpdateError('unowned directory root coverage differs')
    by_path = {}
    for row in value['entries']:
        required = {'path', 'mode', 'uid', 'gid', 'bytes', 'inode', 'links', 'mtime_ns'}
        if (not isinstance(row, dict) or set(row) not in (required, required | {'link_sha256'}) or
                not path(row['path']) or row['path'] in by_path or
                any(type(row[k]) is not int for k in required - {'path'}) or
                not 0 <= row['mode'] <= 0o177777 or
                any(not 0 <= row[k] < 2**32 for k in ('uid', 'gid')) or
                not 0 <= row['bytes'] < 2**64 or not 0 < row['inode'] < 2**64 or
                not 0 < row['links'] < 2**32 or not -2**63 <= row['mtime_ns'] < 2**63 or
                stat.S_ISLNK(row['mode']) != ('link_sha256' in row) or
                ('link_sha256' in row and (not isinstance(row['link_sha256'], str) or not HASH.fullmatch(row['link_sha256'])))):
            raise UpdateError('unowned directory metadata has an invalid entry')
        by_path[row['path']] = row
    if list(by_path) != sorted(by_path) or any(r not in by_path or not stat.S_ISDIR(by_path[r]['mode']) for r in roots):
        raise UpdateError('unowned directory coverage omits a root or is unordered')
    boundaries = {v['path']: v['reason'] for v in delegated if v['reason'] in ('mount', 'podman-store')}
    if not isinstance(value['excluded'], list) or len(value['excluded']) > 8192:
        raise UpdateError('unowned directory exclusions are invalid')
    excluded = []
    for row in value['excluded']:
        if (not isinstance(row, dict) or set(row) != {'path', 'reason'} or not path(row['path']) or
                boundaries.get(row['path']) != row['reason'] or row['path'] in by_path):
            raise UpdateError('unowned directory exclusion has no recorded boundary')
        excluded.append(row['path'])
    if excluded != sorted(set(excluded)) or len(by_path) + len(excluded) > 8192:
        raise UpdateError('unowned directory coverage is duplicated or excessive')
    root_set = set(roots)
    for name in [*by_path, *excluded]:
        if name in root_set:
            continue
        parent = name.rpartition('/')[0]
        if parent not in by_path or not stat.S_ISDIR(by_path[parent]['mode']):
            raise UpdateError('unowned directory entry has no inventoried parent')
    for boundary in boundaries:
        parent = boundary.rpartition('/')[0]
        if parent in by_path and boundary not in excluded:
            raise UpdateError('unowned directory inventory omitted a filesystem boundary')
    if value['metadata_sha256'] != digest({k: value[k] for k in ('roots', 'entries', 'excluded')}):
        raise UpdateError('unowned directory metadata checksum differs')
    return value


def checked_package_audit(value, database_sha256):
    fields = {'kind', 'complete', 'stable', 'database_sha256', 'protected_paths',
              'check_permissions', 'differences', 'adoption_authorized'}
    if (not isinstance(value, dict) or set(value) != fields or value['kind'] != 'klokast.vm-package-audit.v1' or
            value['complete'] is not True or value['stable'] is not True or
            not isinstance(database_sha256, str) or not HASH.fullmatch(database_sha256) or
            value['database_sha256'] != database_sha256 or value['protected_paths'] != 'none' or
            value['check_permissions'] is not True or value['adoption_authorized'] is not False):
        raise UpdateError('native package audit is incomplete, unstable, or has different database coverage')
    rows = value['differences']
    if (not isinstance(rows, list) or len(rows) > 8192 or
            any(not isinstance(v, dict) or set(v) != {'code', 'path'} or
                not isinstance(v['code'], str) or v['code'] not in ('A', 'D', 'd', 'M', 'm', 'x', 'U', 'X') or
                not path(v['path']) for v in rows) or
            [v['path'] for v in rows] != sorted({v['path'] for v in rows})):
        raise UpdateError('native package audit contains invalid, duplicate, or error rows')
    return value


def checked_rootful_store(value):
    fields = {'kind', 'graph_root', 'complete', 'stable', 'present', 'entries', 'metadata_sha256',
              'database', 'adoption_authorized', 'custom_store_coverage', 'error'}
    if (not isinstance(value, dict) or set(value) != fields or value['kind'] != 'klokast.vm-rootful-store.v1' or
            value['graph_root'] != '/var/lib/containers/storage' or value['complete'] is not True or
            value['stable'] is not True or value['error'] is not None or type(value['present']) is not bool or
            value['adoption_authorized'] is not False or value['custom_store_coverage'] is not False or
            type(value['entries']) is not int or not 0 <= value['entries'] <= 8192 or
            value['present'] != (value['entries'] > 0) or not isinstance(value['metadata_sha256'], str) or
            not re.fullmatch('[0-9a-f]{64}', value['metadata_sha256'])):
        raise UpdateError('complete stable rootful store metadata is unavailable')
    database = value['database']
    if database is not None:
        tables = {'DBConfig', 'IDNamespace', 'ContainerConfig', 'ContainerState', 'ContainerExecSession',
                  'ContainerDependency', 'ContainerVolume', 'ContainerExitCode', 'PodConfig', 'PodState',
                  'VolumeConfig', 'VolumeState'}
        if (not value['present'] or not isinstance(database, dict) or set(database) != {'path', 'sha256', 'table_rows'} or
                not isinstance(database['path'], str) or database['path'] not in {'db.sql', 'libpod/db.sql'} or not isinstance(database['sha256'], str) or
                not re.fullmatch('[0-9a-f]{64}', database['sha256']) or not isinstance(database['table_rows'], dict) or
                set(database['table_rows']) != tables or database['table_rows']['DBConfig'] != 1 or
                any(type(v) is not int or not 0 <= v <= 1000000 for v in database['table_rows'].values())):
            raise UpdateError('rootful store database evidence is invalid')
    return value


def assess_host_data(fact, host):
    """Host-wide metadata is evidence, never an allowlist of disposable files."""
    result = {'kind': 'klokast.vm-host-assessment.v1', 'adoption_ready': False,
              'inventory': None, 'findings': []}
    def add(code, message, severity='critical'):
        result['findings'].append(findings(code, message, severity, host))
    inventory = fact.get('host_inventory')
    if (not isinstance(inventory, dict) or inventory.get('kind') != 'klokast.vm-host-inventory.v1' or
            inventory.get('complete') is not True or inventory.get('stable') is not True or
            not isinstance(inventory.get('accounts'), list) or not inventory['accounts'] or
            any(not isinstance(inventory.get(k), list) for k in ('maintenance_files', 'unowned_paths', 'delegated_roots')) or
            not isinstance(inventory.get('topology_sha256'), str) or not HASH.fullmatch(inventory['topology_sha256']) or
            type(inventory.get('entries')) is not int or not 1 <= inventory['entries'] <= 100000):
        add('host.inventory-unknown', 'Complete stable host metadata is unavailable; all host data must be accounted for before adoption.')
        return result
    accounts = inventory['accounts']
    if (len(accounts) > 4096 or any(not isinstance(v, dict) or set(v) != {'name', 'uid', 'gid', 'home', 'shell'} or
            not isinstance(v['name'], str) or not re.fullmatch('[a-zA-Z_][a-zA-Z0-9_.-]{0,63}', v['name']) or
            any(type(v[k]) is not int or not 0 <= v[k] < 2**32 for k in ('uid', 'gid')) or
            (v['home'] != '/' and not path(v['home'])) or not path(v['shell']) for v in accounts) or
            len({v['name'] for v in accounts}) != len(accounts)):
        add('host.accounts-unknown', 'Host account inventory is malformed or ambiguous.')
        return result
    unowned, maintenance, delegated = (inventory[k] for k in ('unowned_paths', 'maintenance_files', 'delegated_roots'))
    if (len(unowned) > 8192 or len(maintenance) > 4096 or len(delegated) > 100000 or
            any(not isinstance(v, dict) or not {'path', 'mode', 'uid', 'gid'} <= set(v) or
                set(v) - {'path', 'mode', 'uid', 'gid', 'link_sha256'} or not path(v['path']) or
                any(type(v[k]) is not int or v[k] < 0 for k in ('mode', 'uid', 'gid')) or
                ('link_sha256' in v and (not isinstance(v['link_sha256'], str) or not HASH.fullmatch(v['link_sha256']))) for v in unowned) or
            any(not isinstance(v, dict) or set(v) not in ({'path', 'sha256'}, {'path', 'link_sha256'}) or
                not path(v['path']) or not isinstance(v.get('sha256', v.get('link_sha256')), str) or
                not HASH.fullmatch(v.get('sha256', v.get('link_sha256'))) for v in maintenance) or
            any(not isinstance(v, dict) or set(v) != {'path', 'reason'} or not path(v['path']) or
                v['reason'] not in ('mount', 'podman-store', 'unclassified-directory') for v in delegated) or
            any(len({v['path'] for v in rows}) != len(rows) for rows in (unowned, maintenance, delegated))):
        add('host.paths-unknown', 'Host metadata contains an invalid, duplicate, or excessive path inventory.')
        return result
    result['inventory'] = {k: inventory[k] for k in ('accounts', 'maintenance_files', 'unowned_paths',
                                                   'delegated_roots', 'entries', 'topology_sha256')}
    try:
        result['inventory']['unowned_tree'] = checked_unowned_tree(inventory.get('unowned_tree'), delegated)
        add('host.unowned-tree-unclassified', 'Unowned directory metadata is available; each file still needs an approved retention or reconstruction rule.', 'warning')
    except UpdateError:
        add('host.unowned-tree-unknown', 'Complete stable metadata for unresolved host directories is unavailable; preserve these directories pending inspection.')
    try:
        services = checked_native_services(inventory.get('native_services'), maintenance)
        result['inventory']['native_services'] = services
        add('host.services-unqualified', 'Native service markers do not verify daemon health, approved configuration, or safe maintenance.', 'warning')
        if any(v['script'] is None for v in services['services']):
            add('host.service-script-missing', 'An enabled or marked native service has no inventoried init script.')
        if any('failed' in v['markers'] for v in services['services']):
            add('host.service-failed', 'OpenRC records a failed native service; service verification is required.')
        if any(set(v['markers']) & {'starting', 'stopping', 'scheduled'} for v in services['services']):
            add('host.service-transition', 'OpenRC records a native service transition or scheduled start; adoption must wait for stable verified services.')
    except UpdateError:
        add('host.services-unknown', 'Complete stable native service evidence is unavailable or conflicts with script and runlevel inventory.')
    try:
        processes = checked_processes(inventory.get('processes'), result['inventory'].get('native_services', {'services': []}))
        result['inventory']['processes'] = processes
        markers = {v['name']: v['markers'] for v in result['inventory'].get('native_services', {}).get('services', [])}
        if any(v['service'] and 'started' not in markers.get(v['service'], []) for v in processes['processes']):
            add('host.unmarked-supervisor', 'A native service supervisor is running without an OpenRC started marker; verify and quiesce the exact service.')
        if any(v['executable_deleted'] or (v['executable'] is None and not v['kernel_thread']) for v in processes['processes']):
            add('host.process-executable-unknown', 'A user process has a deleted or unavailable executable; its deployed code and maintenance behavior are unknown.')
        add('host.processes-unqualified', 'Live process identities and accounts require approved service coverage; process metadata is not maintenance approval.', 'warning')
    except UpdateError:
        add('host.processes-unknown', 'Complete stable process evidence is unavailable or conflicts with native service inventory.')
    add('host.accounting-unverified', 'Host files, accounts, services, timers, identities, and mounted filesystems require approved mappings; metadata is not adoption approval.')
    add('host.package-integrity-unverified', 'Package path ownership does not verify installed package contents or generated configuration.', 'warning')
    try:
        audit = checked_package_audit(inventory.get('package_audit'), inventory.get('package_database_sha256'))
        result['inventory']['package_audit'] = audit
        if audit['differences']:
            add('host.package-differences', 'Native APK audit found changes relative to its local database; classify each path against approved configuration before adoption.')
        else:
            add('host.package-local-match', 'Native APK audit matches the local database; signed source and generated-configuration checks are still required.', 'warning')
    except UpdateError:
        add('host.package-audit-unknown', 'Complete stable native APK audit, including configuration and permissions, is unavailable.')
    try:
        rootful = checked_rootful_store(inventory.get('rootful_store'))
        result['inventory']['rootful_store'] = rootful
        database = rootful['database']
        if database and any(v for k, v in database['table_rows'].items() if k != 'DBConfig'):
            add('host.rootful-registrations', 'The standard rootful Podman database contains registered state; qualify it before adoption.')
        else:
            add('host.rootful-store-unqualified', 'Standard rootful store metadata is available. Zero database counts do not qualify other stores, unregistered layers, or host data.', 'warning')
    except UpdateError:
        add('host.rootful-store-unknown', 'Complete stable standard rootful Podman store evidence is unavailable; preserve its state.')
    if unowned:
        add('host.unowned-paths', 'Files outside package ownership require explicit retention, generated-configuration, or reconstructable-state classification.')
    if maintenance:
        add('host.maintenance-unqualified', 'Enabled services, cron jobs, and local scripts require fixed maintenance adapters or an approved OS baseline.', 'warning')
    root_aliases = [v['name'] for v in accounts if v['uid'] == 0]
    if root_aliases != ['root']:
        add('host.root-identity-ambiguous', 'Host accounts do not identify exactly one root UID owner.')
    return result
