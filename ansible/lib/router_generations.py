"""Closed router generation records. Validation supplies no execution authority."""
import hashlib
import json
import re

import router_personalize
import router_finalize


class GenerationError(RuntimeError):
    pass


def matches(pattern, value):
    return isinstance(value, str) and re.fullmatch(pattern, value) is not None


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()


def seal(value):
    if 'record_sha256' in value:
        raise GenerationError('router record already has a checksum')
    return {**value, 'record_sha256': digest(value)}


def check_seal(value):
    if (not isinstance(value, dict) or not matches('[0-9a-f]{64}', value.get('record_sha256')) or
            digest({k: v for k, v in value.items() if k != 'record_sha256'}) != value['record_sha256']):
        raise GenerationError('router record checksum differs')


def generation(value, box):
    check_seal(value)
    fields = {'kind', 'box', 'role', 'generation_id', 'origin', 'engine_commit', 'disk', 'boot',
              'xen', 'packages', 'kernel_release', 'accounts', 'configuration_files', 'evidence_sha256', 'record_sha256'}
    if (set(value) != fields or value['kind'] != 'klokast.router-generation.v1' or value['role'] != 'router' or
            value['box'] != box or not matches('[a-z0-9][a-z0-9-]{0,30}', box) or
            not matches('[0-9a-f]{24}', value['generation_id']) or value['origin'] not in ('legacy', 'template') or
            not matches('[0-9a-f]{40}', value['engine_commit']) or not matches('[0-9a-f]{64}', value['evidence_sha256']) or
            not matches('[A-Za-z0-9_.+-]{1,128}', value['kernel_release'])):
        raise GenerationError('router generation has an invalid target or provenance')
    disk = value['disk']
    permitted = ['/dev/vg0/routergen_' + value['generation_id']]
    if value['origin'] == 'legacy':
        permitted = ['/dev/vg0/lv_router']
    if (not isinstance(disk, dict) or set(disk) != {'path', 'uuid', 'bytes'} or disk['path'] not in permitted or
            not matches('[A-Za-z0-9-]{1,64}', disk['uuid']) or type(disk['bytes']) is not int or disk['bytes'] != 2147483648):
        raise GenerationError('router generation disk is outside its fixed identity and size')
    directory = '/mnt/dom0_data/klokast-router-updates/generations/' + value['generation_id']
    boot = value['boot']
    if not isinstance(boot, dict) or set(boot) != {'kernel', 'initramfs'}:
        raise GenerationError('router generation lacks its fixed boot artifacts')
    for name, maximum in (('kernel', 32 * 1024 * 1024), ('initramfs', 128 * 1024 * 1024)):
        item = boot[name]
        paths = [directory + '/' + name]
        if value['origin'] == 'legacy':
            paths.append('/mnt/dom0_data/xen_images/router-' + name)
        if (not isinstance(item, dict) or set(item) != {'path', 'sha256', 'bytes'} or item['path'] not in paths or
                not matches('[0-9a-f]{64}', item['sha256']) or type(item['bytes']) is not int or not 0 < item['bytes'] <= maximum):
            raise GenerationError('router generation boot artifact is outside its recorded path or size')
    xen = value['xen']
    if (not isinstance(xen, dict) or set(xen) != {'uuid', 'memory', 'vcpus', 'vif'} or
            not matches('[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}', xen['uuid']) or
            type(xen['memory']) is not int or not 256 <= xen['memory'] <= 2048 or
            type(xen['vcpus']) is not int or not 1 <= xen['vcpus'] <= 4 or
            not isinstance(xen['vif'], list) or not 1 <= len(xen['vif']) <= 16 or
            any(not matches(r'bridge=[A-Za-z0-9_-]+,mac=(?:[0-9a-f]{2}:){5}[0-9a-f]{2}', v) for v in xen['vif']) or
            len(xen['vif']) != len(set(xen['vif']))):
        raise GenerationError('router generation has an invalid Xen identity or topology')
    packages = value['packages']
    if (not isinstance(packages, dict) or not 1 <= len(packages) <= 512 or
            not {'tailscale', 'dhcpcd', 'dnsmasq', 'nftables', 'openssh-keygen'} <= packages.keys() or
            any(not matches('[A-Za-z0-9][A-Za-z0-9+_.-]*', k) or not matches('[A-Za-z0-9][A-Za-z0-9+_.~-]*', v)
                for k, v in packages.items()) or router_finalize.FORBIDDEN_PACKAGES & packages.keys()):
        raise GenerationError('router generation has an incomplete or unsupported runtime package set')
    if value['origin'] == 'template' and 'linux-virt' not in packages:
        raise GenerationError('template router generation lacks a pinned native kernel package')
    accounts = value['accounts']
    if (not isinstance(accounts, dict) or set(accounts) != {'dnsmasq_uid', 'dnsmasq_gid', 'tailscale_gid'} or
            any(type(v) is not int or not 1 <= v <= 65535 for v in accounts.values())):
        raise GenerationError('router generation lacks exact state-copy service accounts')
    files = value['configuration_files']
    if value['origin'] == 'legacy':
        # The legacy OS predates the complete template recipe. Its baseline
        # records only files independently checked against current Ansible and
        # compiler output by the read-only inspector.
        required = {'etc/network/interfaces', 'etc/dhcpcd.conf', 'etc/dnsmasq.conf',
                    'etc/nftables.nft', 'etc/klokast/app-resources/router-forward.nft',
                    'etc/klokast/app-resources/router-forward.d/000-empty.nft'}
        if (not isinstance(files, dict) or not required <= files.keys() or
                len(files) > 1030 or any(name not in required and not matches(
                    r'etc/klokast/app-resources/router-forward.d/[A-Za-z0-9_-]+\.nft', name)
                    for name in files)):
            raise GenerationError('legacy router record has unsupported configuration selectors')
    else:
        try:
            router_personalize.file_modes(files)
        except ValueError as error:
            raise GenerationError('router generation configuration selectors are invalid') from error
    if any(not matches('[0-9a-f]{64}', v) for v in files.values()):
        raise GenerationError('router generation configuration hashes are invalid')
    return value


def pair(old, candidate, request):
    generation(old, request['box'])
    generation(candidate, request['box'])
    if (old['record_sha256'] != request['old_sha256'] or candidate['record_sha256'] != request['candidate_sha256'] or
            candidate['origin'] != 'template' or candidate['engine_commit'] != request['engine_commit'] or
            old['generation_id'] == candidate['generation_id'] or old['disk']['path'] == candidate['disk']['path'] or
            old['disk']['uuid'] == candidate['disk']['uuid'] or old['xen']['uuid'] == candidate['xen']['uuid'] or
            old['xen']['vif'] != candidate['xen']['vif']):
        raise GenerationError('router pair differs from its operation, engine, disjoint disks, or production topology')


def configuration(value):
    """Render only the fixed PVH router profile, including its durable Xen UUID."""
    generation(value, value['box'])
    items = {'name': 'router', 'uuid': value['xen']['uuid'], 'type': 'pvh',
             'memory': value['xen']['memory'], 'vcpus': value['xen']['vcpus'],
             'kernel': value['boot']['kernel']['path'], 'ramdisk': value['boot']['initramfs']['path'],
             'extra': 'console=hvc0 root=/dev/xvda3 rw modules=ext4',
             'disk': ['phy:' + value['disk']['path'] + ',xvda,w'], 'vif': value['xen']['vif'],
             'on_crash': 'destroy', 'on_reboot': 'restart'}
    # Alpine xendomains reads the domain name from double quotes with sed.
    return '\n'.join(k + ' = ' + (json.dumps(v) if k == 'name' else repr(v)) for k, v in items.items()) + '\n'
