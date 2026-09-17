"""Read-only comparison of verified logical intent and untrusted discovery.

The root source reader supplies intent. This module never reads private files,
accesses disks, creates copy requests, or grants adoption authority.
"""
import re

from platform_updates import REPORT_KIND, VERIFY_AGE, UpdateError, digest, findings, fresh, timestamp
from vm_storage_inventory import NAME, catalog_index


def validate_source(source):
    fields = {'schema_version', 'kind', 'source', 'authority_state_sha256', 'engine_commit',
              'private_commit', 'inputs', 'projection', 'projection_sha256', 'adoption_authorized'}
    if (not isinstance(source, dict) or set(source) != fields or type(source['schema_version']) is not int
            or source['schema_version'] != 1 or source['kind'] != 'klokast.vm-retention-source.v1'
            or source['source'] != 'instance_specification_v1' or source['adoption_authorized'] is not False):
        raise UpdateError('retention source is not the closed read-only Instance result')
    for field, length in (('authority_state_sha256', 64), ('engine_commit', 40), ('private_commit', 40), ('projection_sha256', 64)):
        if not isinstance(source[field], str) or not re.fullmatch('[0-9a-f]{' + str(length) + '}', source[field]):
            raise UpdateError('retention source provenance is incomplete')
    inputs = source['inputs']
    if (not isinstance(inputs, list) or len(inputs) != 2
            or any(not isinstance(v, dict) or set(v) != {'path', 'sha256'}
                   or not isinstance(v['path'], str)
                   or not isinstance(v['sha256'], str) or not re.fullmatch('[0-9a-f]{64}', v['sha256']) for v in inputs)
            or sorted(v['path'] for v in inputs) != ['klokast-instance.json', 'klokast.lock.json']):
        raise UpdateError('retention source must bind both authoritative files')
    projection = source['projection']
    if (not isinstance(projection, dict) or set(projection) != {'boxes', 'datasets'}
            or digest(projection) != source['projection_sha256']):
        raise UpdateError('retention projection checksum or contract differs')
    boxes, datasets = projection['boxes'], projection['datasets']
    if (not isinstance(boxes, list) or not boxes
            or any(not isinstance(v, str) or not re.fullmatch('[a-z0-9][a-z0-9-]{0,62}', v) for v in boxes)
            or boxes != sorted(set(boxes)) or not isinstance(datasets, list)):
        raise UpdateError('retention projection has invalid boxes or datasets')
    keys = []
    for item in datasets:
        if (not isinstance(item, dict) or set(item) != {'app', 'dataset', 'box', 'retention', 'desired_state'}
                or any(not isinstance(item[k], str) or not NAME.fullmatch(item[k]) for k in ('app', 'dataset'))
                or item['box'] not in boxes or item['retention'] != 'preserve'
                or item['desired_state'] not in ('present', 'absent')):
            raise UpdateError('retention projection contains an invalid declaration')
        keys.append((item['app'], item['dataset']))
    if keys != sorted(set(keys)):
        raise UpdateError('retention projection contains duplicate or unordered datasets')
    return projection


def report(source, discovery, catalogs, implementation_commit, now):
    projection = validate_source(source)
    # Validate all catalog roles and detect ambiguous mappings before use.
    indexes = {role: catalog_index(catalogs, role) for role in ('bak', 'dmz', 'iot')}
    catalog_datasets = {(c['app'], dataset): (c['role'], entry['volumes'])
                        for c in catalogs for dataset, entry in c['datasets'].items()}
    result = {'kind': 'klokast.vm-retention-report.v1', 'generated_at': timestamp(now),
              'source': source, 'catalog_sha256': digest(catalogs), 'implementation_commit': implementation_commit,
              'discovery_sha256': digest(discovery), 'adoption_ready': False, 'datasets': [], 'findings': []}

    def add(code, message, scope='installation'):
        result['findings'].append(findings(code, message, 'critical', scope))

    add('retention.adoption-unverified', 'Backup, complete host-data accounting, maintenance adapters, writer fencing, and signed adoption remain required.')
    usable = True
    if implementation_commit != source['engine_commit']:
        add('retention.engine-mismatch', 'The report catalog source differs from the approved engine.')
        usable = False
    if (not isinstance(discovery, dict) or discovery.get('kind') != REPORT_KIND or discovery.get('complete') is not True
            or not fresh(discovery.get('generated_at'), now, VERIFY_AGE)
            or discovery.get('implementation_commit') != source['engine_commit']
            or not isinstance(discovery.get('hosts'), list)):
        add('retention.discovery-unknown', 'A complete scan from the approved engine no more than two hours old is required.')
        usable = False
    hosts = {}
    if usable:
        for host in discovery['hosts']:
            if not isinstance(host, dict) or not isinstance(host.get('target'), dict):
                raise UpdateError('discovery contains an invalid VM target')
            target = host.get('target', {})
            if any(not isinstance(target.get(k), str) for k in ('box', 'role', 'runtime')):
                raise UpdateError('discovery contains an incomplete VM target')
            key = (target.get('box'), target.get('role'))
            if key[1] not in indexes:
                continue
            if key in hosts or key[0] not in projection['boxes']:
                raise UpdateError('discovery contains duplicate or undeclared shared VM targets')
            hosts[key] = host
        for key, host in hosts.items():
            box, role = key
            scope = box + '-' + role
            assessment = host.get('storage_assessment')
            if (host['target'].get('runtime') != 'running' or not isinstance(assessment, dict)
                    or assessment.get('kind') != 'klokast.vm-storage-assessment.v1'
                    or assessment.get('catalog_sha256') != result['catalog_sha256']
                    or not isinstance(assessment.get('volumes'), list)
                    or not isinstance(assessment.get('findings'), list)):
                add('retention.storage-unknown', 'Fresh running-VM storage assessment with matching catalog evidence is required.', scope)
                hosts[key] = None
                continue
            # Keep existing refusals visible. A declaration cannot clear unsafe
            # paths, partial inventories, writable layers, or unknown storage.
            if any(not isinstance(v, dict) or v.get('severity') not in ('warning', 'critical')
                   or any(not isinstance(v.get(k), str) for k in ('code', 'message')) for v in assessment['findings']):
                raise UpdateError('storage assessment contains an invalid finding')
            result['findings'].extend(assessment['findings'])
            declared = {(d['app'], d['dataset']) for d in projection['datasets'] if d['box'] == box}
            seen = set()
            for volume in assessment['volumes']:
                name = volume.get('name') if isinstance(volume, dict) else None
                if not isinstance(name, str) or not NAME.fullmatch(name) or name in seen:
                    raise UpdateError('storage assessment contains an invalid or duplicate volume')
                seen.add(name)
                match = indexes[role].get(name)
                if match and (match['app'], match['dataset']) not in declared:
                    add('retention.volume-undeclared', 'Catalog volume ' + name + ' has no retention declaration on this box. Preserve it pending review.', scope)
    for declaration in projection['datasets']:
        row = {**declaration, 'status': 'unknown', 'role': None, 'expected_volumes': [], 'observed_volumes': []}
        result['datasets'].append(row)
        catalog = catalog_datasets.get((declaration['app'], declaration['dataset']))
        scope = declaration['box'] + '/' + declaration['app'] + '/' + declaration['dataset']
        if catalog is None:
            row['status'] = 'unsupported'
            add('retention.dataset-unsupported', 'Declared dataset has no reviewed storage mapping.', scope)
            continue
        role, volumes = catalog
        row.update(role=role, expected_volumes=sorted(volumes))
        host = hosts.get((declaration['box'], role))
        if host is None:
            add('retention.dataset-unknown', 'Declared dataset has no usable storage assessment; stopped VMs stay stopped.', scope)
            continue
        observed = {v['name']: v for v in host['storage_assessment']['volumes']}
        row['observed_volumes'] = sorted(set(volumes) & set(observed))
        if set(volumes) - set(observed):
            row['status'] = 'missing'
            add('retention.dataset-missing', 'One or more declared dataset volumes are absent from discovery.', scope)
        elif any(observed[name].get('path_supported') is not True for name in volumes):
            add('retention.dataset-unsafe', 'Declared dataset has an unsupported storage path.', scope)
        else:
            row['status'] = 'observed'
    return result
