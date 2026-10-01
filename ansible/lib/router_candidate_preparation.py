"""Run the shared networkless preparer on one recorded, detached router clone.

The caller owns authorization, the installation and dom0 locks, allocation,
retention, and boot authority. This runner never enrolls or retires a disk.
A stopped guest's complete result can be recovered without rerunning the
preparer. Missing or incomplete results require exact reconciliation.
"""
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import time
import uuid

import router_candidate
import router_candidate_disk
import router_initial_contact
import router_native
import router_personalize
import router_records
from xen_build_runtime import (attach_loop, boot_guest, checksum, detach_loop,
    domain, duplicate_fields, loop_devices, read_slot, safe_directory, safe_file)

MIB = 1024 * 1024


def load(path):
    safe_file(path, MIB)
    return json.loads(path.read_text(), object_pairs_hook=duplicate_fields)


def configuration(work, value, disk, result_loop, identity):
    operation = value['operation_id']
    name = 'router-candidate-prepare-' + operation
    extra = ('console=hvc0 panic=1 klokast_operation=' + operation +
             ' klokast_inputs=' + value['inputs_sha256'] + ' klokast_job=' + value['job_sha256'])
    content = (f'name = {name!r}\nuuid = {identity!r}\ntype = "pvh"\nmemory = 2048\nmaxmem = 2048\n'
               f'vcpus = 2\nkernel = {str(work / "bootstrap-kernel")!r}\n'
               f'ramdisk = {str(work / "bootstrap-initramfs")!r}\nextra = {extra!r}\n'
               f'disk = {["phy:" + disk["path"] + ",xvda,w", "phy:" + result_loop + ",xvdb,w"]!r}\n'
               'vif = []\non_poweroff = "destroy"\non_reboot = "destroy"\non_crash = "destroy"\n')
    path = work / 'prepare.cfg'
    with path.open('x') as stream:
        stream.write(content)
        stream.flush(); os.fsync(stream.fileno())
    return name, path


def validate_result(result, value, job):
    expected_result_fields = {'kind','operation_id','inputs_sha256','job_sha256','success','prepared'}
    temporary_access = 'first_contact' in job
    if temporary_access:
        expected_result_fields.add('first_contact')
    first = result.get('first_contact') if isinstance(result, dict) else None
    first_contact_valid = not temporary_access
    if temporary_access and isinstance(first, dict):
        router_initial_contact.host_public_keys(first)
        host_keys = first.get('host_key_public_sha256')
        first_contact_valid = (
            set(first) == {'kind','authorized_key_sha256','interfaces_sha256','firewall_sha256',
                'sshd_config_sha256','host_key_public_sha256','host_key_public'}
            and first.get('kind') == 'klokast.router-first-contact.v2'
            and first.get('authorized_key_sha256') == hashlib.sha256(
                (job['first_contact']['key'] + '\n').encode()).hexdigest()
            and first.get('interfaces_sha256') == hashlib.sha256(
                router_initial_contact.first_contact_interfaces(job['first_contact']['backend_address'],
                    job['first_contact']['backend_prefix']).encode()).hexdigest()
            and first.get('firewall_sha256') == hashlib.sha256(
                router_initial_contact.first_contact_firewall(
                    job['personalization']['files']['etc/nftables.nft'],
                    job['first_contact']['backend_source_address'],
                    job['first_contact']['backend_address'],
                    job['first_contact']['backend_prefix']).encode()).hexdigest()
            and first.get('sshd_config_sha256') == hashlib.sha256(
                router_initial_contact.first_contact_sshd_config(
                    job['first_contact']['backend_address']).encode()).hexdigest()
            and isinstance(host_keys, dict) and bool(host_keys)
            and set(host_keys) <= {'rsa','ecdsa','ed25519'}
            and all(isinstance(digest, str) and re.fullmatch('[0-9a-f]{64}', digest)
                    for digest in host_keys.values())
        )
    if (not isinstance(result, dict) or set(result) != expected_result_fields or
            result['kind'] != 'klokast.router-candidate-preparation-result.v1' or
            result['operation_id'] != value['operation_id'] or
            result['inputs_sha256'] != value['inputs_sha256'] or
            result['job_sha256'] != value['job_sha256'] or result['success'] is not True or
            not first_contact_valid or not isinstance(result['prepared'], dict) or
            result['prepared'].get('mode') != value['mode'] or
            result['prepared'].get('identity_absent') is not True or
            result['prepared'].get('service_syntax') is not True or
            result['prepared'].get('packages') != (
                job['personalization']['packages'] if temporary_access
                else job['runtime_packages'])):
        raise RuntimeError('candidate preparation guest returned incomplete or different evidence')
    prepared = result['prepared']
    fields = {'kind', 'box', 'role', 'mode', 'operation_id', 'inputs_sha256',
              'engine_commit', 'packages', 'accounts', 'configuration_files',
              'tailscale', 'identity_absent', 'replacement_authorized', 'service_syntax'}
    selected_overlay = job['personalization'].get('overlay_ipv6')
    if selected_overlay is not None:
        fields.add('overlay_source_sha256')
    component = prepared.get('tailscale')
    if (set(prepared) != fields or prepared['kind'] != 'klokast.router-candidate-files.v1' or
            selected_overlay is not None and prepared['overlay_source_sha256'] !=
                selected_overlay['source']['source_sha256'] or
            any(prepared[key] != job[key] for key in (
                'box', 'role', 'mode', 'operation_id', 'inputs_sha256', 'engine_commit')) or
            prepared['replacement_authorized'] is not False or
            prepared['configuration_files'] != {name:hashlib.sha256(content.encode()).hexdigest()
                for name, content in job['personalization']['files'].items()} or
            not isinstance(component, dict) or set(component) != {
                'version', 'sha256', 'tailscale_sha256', 'tailscaled_sha256', 'openrc_sha256'} or
            not router_candidate.matches(r'[0-9]+\.[0-9]+\.[0-9]+', component['version']) or
            any(not router_candidate.matches('[0-9a-f]{64}', component[key]) for key in (
                'sha256', 'tailscale_sha256', 'tailscaled_sha256', 'openrc_sha256')) or
            not isinstance(prepared['accounts'], dict) or
            set(prepared['accounts']) != {'dnsmasq_uid', 'dnsmasq_gid', 'tailscale_gid'} or
            any(type(number) is not int or not 1 <= number <= 65535
                for number in prepared['accounts'].values())):
        raise RuntimeError('candidate preparation evidence differs from its complete job')
    return result


def run_record(work, binding):
    """Validate the exact helper ledger, configuration and owned result slot."""
    ledger = load(work / 'preparation.json')
    if (not isinstance(ledger, dict) or set(ledger) != set(binding) | {
            'stage', 'uuid', 'config_sha256', 'result_loop', 'result_sha256'} or
            any(ledger[key] != expected for key, expected in binding.items()) or
            ledger['stage'] not in ('booting', 'prepared') or
            not router_candidate.matches('[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}', ledger['uuid']) or
            not router_candidate.matches('[0-9a-f]{64}', ledger['config_sha256']) or
            not router_candidate.matches(r'/dev/loop[0-9]+', ledger['result_loop']) or
            (ledger['stage'] == 'booting' and ledger['result_sha256'] is not None) or
            (ledger['stage'] == 'prepared' and not router_candidate.matches('[0-9a-f]{64}', ledger['result_sha256']))):
        raise RuntimeError('candidate preparation retry differs from its recorded run')
    safe_file(work / 'prepare.cfg', MIB)
    safe_file(work / 'result.slot', MIB)
    if checksum(work / 'prepare.cfg') != ledger['config_sha256'] or (work / 'result.slot').stat().st_size != MIB:
        raise RuntimeError('candidate preparation configuration or result slot changed')
    return ledger


def fence(work, value, job, disk, *, deadline, authorize, host=None):
    """Fence only the recorded networkless preparer; never retire its disk.

    The cleanup caller owns the installation and record locks, verifies the
    accepted router and absence of launch/enrollment, and supplies fresh
    authority. Unrecorded helper resources require explicit reconciliation.
    """
    work = Path(work)
    router_records.secure(work, directory=True)
    router_candidate.validate(job)
    operation = job['operation_id']
    if (any(value[key] != job[key] for key in ('box','mode','operation_id','engine_commit','inputs_sha256')) or
            value['job_sha256'] != router_personalize.digest(job) or
            disk['path'] != router_candidate_disk.selection(operation)[0]):
        raise RuntimeError('candidate fencing source differs from its recorded job or disk')
    host = host or router_native.Native()
    name = 'router-candidate-prepare-' + operation
    binding = {'kind':'klokast.router-preparation-run.v1', 'operation_id':operation,
               'request_sha256':router_personalize.digest(value), 'disk':disk, 'domain':name}
    ledger_path = work / 'preparation.json'
    ledger = run_record(work, binding) if ledger_path.exists() or ledger_path.is_symlink() else None
    def guests():
        return [guest for guest in host.inventory(deadline=deadline) if guest['domid'] > 0 and
            (guest['config']['c_info']['name'] == name or
             ledger is not None and guest['config']['c_info']['uuid'] == ledger['uuid'])]
    found = guests()
    if ledger is None:
        if found or any((work / name).exists() or (work / name).is_symlink() for name in (
                'prepare.cfg','result.slot','preparation-result.json')):
            raise RuntimeError('candidate preparation has unrecorded helper resources; preserve them')
        return {'kind':'klokast.router-preparation-fenced.v1', 'operation_id':operation,
                'request_sha256':binding['request_sha256'], 'disk':disk, 'helper_uuid':None}
    slot = router_records.secure(work / 'result.slot', maximum=MIB)
    attached = loop_devices(slot)
    if attached and attached != [ledger['result_loop']]:
        raise RuntimeError('candidate preparation result loop differs from the exact ledger')
    if len(found) > 1:
        raise RuntimeError('candidate preparation helper identity is ambiguous')
    if found:
        guest = found[0]
        config = guest['config']
        info, boot = config['c_info'], config['b_info']
        nics = config.get('nics', [])
        expected = [(host.device(disk['path']), 'xvda', 1),
                    (host.device(ledger['result_loop']), 'xvdb', 1)]
        actual = [(host.device(item['pdev_path']), item['vdev'], item.get('readwrite', 0))
                  for item in config['disks']]
        extra = ('console=hvc0 panic=1 klokast_operation=' + operation +
                 ' klokast_inputs=' + value['inputs_sha256'] + ' klokast_job=' + value['job_sha256'])
        if (attached != [ledger['result_loop']] or info['name'] != name or info['uuid'] != ledger['uuid'] or
                info['type'] != 'pvh' or not isinstance(nics, list) or nics or actual != expected or
                any(item['format'] != 'raw' for item in config['disks']) or
                boot['kernel'] != str(work / 'bootstrap-kernel') or
                boot['ramdisk'] != str(work / 'bootstrap-initramfs') or boot['cmdline'] != extra):
            raise RuntimeError('candidate preparation guest differs from its exact networkless run')
        if not isinstance(value.get('bootstrap'), dict) or set(value['bootstrap']) != {'kernel','initramfs'}:
            raise RuntimeError('candidate preparation fencing lacks its exact boot artifacts')
        for artifact, expected in value['bootstrap'].items():
            maximum = (32 if artifact == 'kernel' else 1024) * MIB
            if (not isinstance(expected, dict) or set(expected) != {'bytes','sha256'} or
                    type(expected['bytes']) is not int or not 0 < expected['bytes'] <= maximum or
                    not router_candidate.matches('[0-9a-f]{64}', expected['sha256'])):
                raise RuntimeError('candidate preparation fencing has invalid boot artifact metadata')
            host.artifact({'path':str(work / ('bootstrap-' + artifact)), **expected}, deadline=deadline)
        authorize()
        logging.info('Fencing router preparation operation=%s helper_uuid=%s', operation, ledger['uuid'])
        router_native.command(['/usr/sbin/xl','destroy',ledger['uuid']], deadline, maximum_seconds=30)
        if guests():
            raise RuntimeError('candidate preparation guest remains after exact fencing')
    host.wait_detached([disk['path'], *attached], deadline=deadline)
    if attached:
        authorize()
        logging.info('Detaching router preparation result operation=%s loop=%s', operation, ledger['result_loop'])
        # The shared detacher rechecks this exact backing file and loop ID.
        detach_loop(slot, ledger['result_loop'])
    if guests() or loop_devices(slot):
        raise RuntimeError('candidate preparation helper resources remain after fencing')
    return {'kind':'klokast.router-preparation-fenced.v1', 'operation_id':operation,
            'request_sha256':binding['request_sha256'], 'disk':disk, 'helper_uuid':ledger['uuid']}


def prepare(work, value, job, disk):
    """Prepare once, or recover a complete stopped-guest result without a boot."""
    work = Path(work)
    safe_directory(work)
    router_candidate.validate(job)
    operation = job['operation_id']
    if (any(value[key] != job[key] for key in ('box', 'mode', 'operation_id',
                                              'engine_commit', 'inputs_sha256')) or
            value['job_sha256'] != router_personalize.digest(job)):
        raise RuntimeError('candidate preparation request differs from its complete job')
    name = 'router-candidate-prepare-' + operation
    if domain(name) is not None:
        raise RuntimeError('candidate preparation guest remains; reconcile its exact domain')
    if (router_candidate_disk.record(work, operation)['stage'] != 'cloned' or
            router_candidate_disk.verify(work, operation) != disk):
        raise RuntimeError('candidate preparation disk differs from its recorded clone')
    binding = {'kind':'klokast.router-preparation-run.v1', 'operation_id':operation,
               'request_sha256':router_personalize.digest(value), 'disk':disk, 'domain':name}
    ledger_path, result_path = work / 'preparation.json', work / 'preparation-result.json'
    slot, config = work / 'result.slot', work / 'prepare.cfg'
    ledger = None
    result_loop = None
    if ledger_path.exists() or ledger_path.is_symlink():
        ledger = run_record(work, binding)
        attached = loop_devices(slot)
        if attached:
            if attached != [ledger['result_loop']]:
                raise RuntimeError('candidate preparation result loop changed during interruption')
            router_native.Native().wait_detached(attached, deadline=time.monotonic() + 30)
            result_loop = attached[0]
    elif any(path.exists() or path.is_symlink() for path in (slot, config, result_path)):
        raise RuntimeError('candidate preparation has unrecorded files; reconcile before retry')
    try:
        if ledger is None:
            with slot.open('xb') as stream:
                os.posix_fallocate(stream.fileno(), 0, MIB)
                os.fsync(stream.fileno())
            result_loop = attach_loop(slot)
            identity = str(uuid.uuid4())
            name, config = configuration(work, value, disk, result_loop, identity)
            ledger = {**binding, 'stage':'booting', 'uuid':identity,
                      'config_sha256':checksum(config), 'result_loop':result_loop,
                      'result_sha256':None}
            router_records.write(ledger_path, ledger)
            boot_guest(work, name, identity, config, value,
                kind='klokast.router-candidate-preparation-result.v1', timeout=420)
        if domain(name) is not None or router_candidate_disk.verify(work, operation) != disk:
            raise RuntimeError('candidate preparation did not release its exact disk')
        result = validate_result(read_slot(slot), value, job)
        digest = router_personalize.digest(result)
        if ledger['stage'] == 'prepared' and ledger['result_sha256'] != digest:
            raise RuntimeError('candidate preparation result changed after completion')
        if result_path.exists() or result_path.is_symlink():
            if load(result_path) != result:
                raise RuntimeError('candidate preparation has conflicting completion evidence')
        else:
            router_records.write(result_path, result)
        if ledger['stage'] != 'prepared':
            router_records.write(ledger_path, {**ledger, 'stage':'prepared', 'result_sha256':digest})
        return result
    finally:
        if domain(name) is not None:
            raise RuntimeError('candidate preparation guest remains; retain its disk and result')
        if result_loop is not None:
            router_native.Native().wait_detached([result_loop], deadline=time.monotonic() + 30)
            detach_loop(slot, result_loop)
