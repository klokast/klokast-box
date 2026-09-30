"""One box configuration and package recipe for initial install and replacement.

Preparation runs inside networkless Xen on a newly cloned template. It never
selects a disk, enrolls a machine, starts network services, or grants cutover
authority. A job with first-contact access retains its frozen packages until
enrollment is verified and that access is retired offline.
"""
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import stat
import subprocess

import router_finalize
import router_personalize as personalize


def matches(pattern, value):
    return isinstance(value, str) and re.fullmatch(pattern, value) is not None


def upstream_binary(root, relative):
    personalize.directory(root, str(Path(relative).parent))
    path = Path(root) / relative
    info = path.lstat()
    if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or
            info.st_uid != os.geteuid() or info.st_dev != Path(root).stat().st_dev or
            stat.S_IMODE(info.st_mode) != 0o755 or not 0 < info.st_size <= 64 * 1024 * 1024):
        raise ValueError('router upstream Tailscale binary is unsafe: ' + relative)
    return path


def checksum(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def validate(request):
    if (not isinstance(request, dict) or request.get('kind') != 'klokast.router-candidate-job.v1' or
            request.get('mode') not in ('initial-install', 'replacement')):
        raise ValueError('router candidate requires an exact box, lifecycle mode, engine, and input identity')
    fields = {'kind', 'mode', 'box', 'role', 'operation_id', 'engine_commit', 'inputs_sha256',
              'kernel_release', 'personalization', 'runtime_packages'}
    if request['mode'] == 'initial-install':
        if 'first_contact' not in request:
            raise ValueError('initial router candidate requires one bounded first-contact key and backend address')
    if 'first_contact' in request:
        fields.add('first_contact')
    if (set(request) != fields or
            request['role'] != 'router' or not matches('[a-z0-9][a-z0-9-]{0,30}', request['box']) or
            not matches('[0-9a-f]{24}', request['operation_id']) or
            not matches('[0-9a-f]{40}', request['engine_commit']) or
            not matches('[0-9a-f]{64}', request['inputs_sha256']) or
            not matches('[A-Za-z0-9_.+-]{1,128}', request['kernel_release'])):
        raise ValueError('router candidate requires an exact box, lifecycle mode, engine, and input identity')
    if 'first_contact' in request:
        first = request['first_contact']
        try:
            address = str(ipaddress.IPv4Address(first.get('backend_address'))) if isinstance(first, dict) else ''
            source = str(ipaddress.IPv4Address(first.get('backend_source_address'))) if isinstance(first, dict) else ''
        except (ipaddress.AddressValueError, TypeError):
            address, source = '', ''
        if (not isinstance(first, dict) or set(first) != {
                'key', 'backend_address', 'backend_prefix', 'backend_source_address'} or
                not isinstance(first['key'], str) or not 1 <= len(first['key'].encode()) <= 4096 or
                '\n' in first['key'] or '\r' in first['key'] or '\0' in first['key'] or
                address != first['backend_address'] or type(first['backend_prefix']) is not int or
                not 1 <= first['backend_prefix'] <= 32 or source != first['backend_source_address']):
            raise ValueError('initial router candidate requires one bounded first-contact key and backend address')
    value = request['personalization']
    personalize.validate(value)
    if any(value[key] != request[key] for key in ('box', 'role', 'inputs_sha256')):
        raise ValueError('router candidate personalization differs from its lifecycle request')
    if not isinstance(request['runtime_packages'], dict):
        raise ValueError('router candidate lacks a qualified runtime package manifest')
    before = value['packages']
    after = request['runtime_packages']
    # Full manifest/world verification follows on the actual cloned root.
    if (not after or not after.keys() <= before.keys() or
            any(after[k] != before[k] for k in after) or
            not before.keys() - after.keys() <= router_finalize.BOOTSTRAP_PACKAGES or
            router_finalize.FORBIDDEN_PACKAGES & after.keys()):
        raise ValueError('router candidate runtime packages differ from the qualified first-contact retirement')
    return request


def manifest(root, request):
    path = personalize.regular(root, 'etc/klokast-router-inputs.json')
    value = json.loads(path.read_text())
    if (not isinstance(value, dict) or value.get('inputs_sha256') != request['inputs_sha256'] or
            personalize.digest({k:v for k,v in value.items() if k != 'inputs_sha256'}) != request['inputs_sha256'] or
            value.get('engine_commit') != request['engine_commit'] or value.get('profile') != 'router-alpine-v2' or
            {p['name']:p['version'] for p in value.get('packages',[])} != request['personalization']['packages']):
        raise ValueError('router candidate clone has another engine or frozen package input identity')
    router_finalize.validate_packages(request['personalization']['packages'], request['runtime_packages'], value['world'])
    return value


def identity_absent(root):
    for relative in ('root/.ssh/authorized_keys', 'etc/machine-id', 'var/lib/dbus/machine-id',
                     'var/lib/misc/dnsmasq.leases', 'etc/sysctl.d/91-klokast-ops-ipv6.conf'):
        path = root / relative
        if path.exists() or path.is_symlink():
            raise ValueError('router candidate already has service identity or first-contact access')
    for relative in ('var/lib/tailscale', 'var/lib/dhcpcd'):
        path = personalize.service_directory(root, relative)
        for entry in path.iterdir():
            if relative == 'var/lib/tailscale' and entry.name == 'ssh':
                info = entry.lstat()
                if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or
                        stat.S_IMODE(info.st_mode) != 0o700 or any(entry.iterdir())):
                    raise ValueError('router candidate fallback SSH directory has identity or unsafe metadata')
            else:
                raise ValueError('router candidate service identity directory is not empty')
    if list(personalize.directory(root, 'etc/ssh').glob('ssh_host_*')):
        raise ValueError('router candidate already has an effective SSH host identity')


def accounts(root):
    passwd = [line.split(':') for line in personalize.regular(root, 'etc/passwd').read_text().splitlines()]
    group = [line.split(':') for line in personalize.regular(root, 'etc/group').read_text().splitlines()]
    dnsmasq = [row for row in passwd if row[0] == 'dnsmasq']
    tailscale = [row for row in group if row[0] == 'tailscale']
    neo = [row for row in passwd if row[0] == 'neo']
    if (len(dnsmasq) != 1 or len(dnsmasq[0]) != 7 or len(tailscale) != 1 or len(tailscale[0]) != 4 or
            len(neo) != 1 or len(neo[0]) != 7 or neo[0][2:4] != ['1000','1000']):
        raise ValueError('router candidate has an invalid service or management account assignment')
    result = {'dnsmasq_uid':int(dnsmasq[0][2]), 'dnsmasq_gid':int(dnsmasq[0][3]), 'tailscale_gid':int(tailscale[0][2])}
    if any(not 1 <= v <= 65535 for v in result.values()):
        raise ValueError('router candidate service account IDs exceed their state-copy bounds')
    return result


def configuration_set(root, request):
    # An extra include could execute firewall rules or enable an unsupported
    # stateful DNS feature even when every expected file still matches.
    includes = personalize.directory(root, 'etc/klokast/app-resources/router-forward.d')
    expected = {Path(name).name for name in request['personalization']['files']
                if name.startswith('etc/klokast/app-resources/router-forward.d/')}
    if {p.name for p in includes.iterdir()} != expected:
        raise ValueError('router candidate has undeclared firewall includes')
    if any(personalize.directory(root, 'etc/dnsmasq.d').iterdir()):
        raise ValueError('router candidate has undeclared DNS configuration')


def verify(root, request):
    validate(request)
    root = Path(root)
    inputs = manifest(root, request)
    component = inputs.get('tailscale')
    if not isinstance(component, dict) or component.get('signature_verified') is not True:
        raise ValueError('router candidate lacks verified upstream Tailscale inputs')
    for name, relative in (('tailscale', 'usr/local/bin/tailscale'),
                           ('tailscaled', 'usr/local/sbin/tailscaled')):
        path = upstream_binary(root, relative)
        if checksum(path) != component[name + '_sha256']:
            raise ValueError('router candidate upstream Tailscale binary differs: ' + name)
    service = personalize.regular(root, 'etc/init.d/tailscale')
    if (stat.S_IMODE(service.stat().st_mode) != 0o755 or
            checksum(service) != component['openrc_sha256']):
        raise ValueError('router candidate upstream Tailscale service differs')
    temporary_access = 'first_contact' in request
    expected = request['personalization']['packages'] if temporary_access else request['runtime_packages']
    if personalize.packages(root) != expected:
        raise ValueError('router candidate installed packages differ from its exact lifecycle manifest')
    world = [name for name in inputs['world'] if temporary_access or name != 'openssh']
    if personalize.regular(root, 'etc/apk/world').read_text() != ''.join(name + '=' + expected[name] + '\n' for name in world):
        raise ValueError('router candidate package world differs from its frozen lifecycle manifest')
    modules = personalize.directory(root, 'lib/modules')
    if {p.name for p in modules.iterdir()} != {request['kernel_release']}:
        raise ValueError('router candidate modules differ from its selected kernel')
    personalize.directory(root, 'lib/modules/' + request['kernel_release'])
    for name in ('root', 'neo'):
        entries = [line.split(':') for line in personalize.regular(root, 'etc/shadow').read_text().splitlines()
                   if line.split(':')[0] == name]
        if len(entries) != 1 or len(entries[0]) != 9 or not entries[0][1].startswith(('!', '*')):
            raise ValueError('router candidate requires locked root and management passwords')
    if personalize.regular(root, 'etc/doas.d/20-klokast.conf').read_text() != 'permit nopass :wheel\n':
        raise ValueError('router candidate management privilege configuration differs')
    configuration_set(root, request)
    modes = personalize.file_modes(request['personalization']['files'])
    hashes = {}
    for name, content in request['personalization']['files'].items():
        path = personalize.regular(root, name)
        info = path.stat()
        if path.read_text() != content or stat.S_IMODE(info.st_mode) != modes[name] or info.st_gid != os.getegid():
            raise ValueError('router candidate configuration bytes or metadata differ: ' + name)
        hashes[name] = hashlib.sha256(content.encode()).hexdigest()
    level = personalize.directory(root, 'etc/runlevels/default')
    if {p.name:os.readlink(p) if p.is_symlink() else None for p in level.iterdir()} != {
            name:'/etc/init.d/'+name for name in personalize.SERVICES}:
        raise ValueError('router candidate default runlevel differs from the common recipe')
    for name in personalize.SERVICES:
        personalize.regular(root, 'etc/init.d/' + name)
    identity_absent(root)
    if not temporary_access:
        for name in router_finalize.SERVER_PATHS:
            path = root / name
            if path.exists() or path.is_symlink():
                raise ValueError('router replacement candidate still has a first-contact server')
    return {'kind':'klokast.router-candidate-files.v1', 'box':request['box'], 'role':'router', 'mode':request['mode'],
            'operation_id':request['operation_id'], 'inputs_sha256':request['inputs_sha256'],
            'engine_commit':request['engine_commit'], 'packages':expected, 'accounts':accounts(root),
            'tailscale':{key:component[key] for key in ('version', 'sha256', 'tailscale_sha256',
                                                       'tailscaled_sha256', 'openrc_sha256')},
            'configuration_files':hashes, 'identity_absent':True, 'replacement_authorized':False}


def prepare(root, request):
    personalize.environment()
    validate(request)
    root = Path(root)
    inputs = manifest(root, request)
    personalize.personalize(root, request['personalization'])
    if 'first_contact' not in request:
        result = router_finalize.finalize(root, inputs)
        if result['packages'] != request['runtime_packages']:
            raise ValueError('router candidate native retirement differs from the qualified release')
    evidence = verify(root, request)
    for argv in (['dnsmasq','--test','--conf-file=/etc/dnsmasq.conf'], ['nft','-c','-f','/etc/nftables.nft']):
        completed = subprocess.run(['chroot',str(root),*argv], stdin=subprocess.DEVNULL,
                                   capture_output=True,timeout=30)
        if completed.returncode:
            raise ValueError('router candidate native service syntax check failed: ' + argv[0])
    return {**evidence, 'service_syntax':True}
