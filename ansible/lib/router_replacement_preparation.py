"""Retain one replacement router clone while the accepted router stays online.

The active controller supplies a fresh, operation-bound grant after verifying
signed policy and live router health. This dom0 issuer holds the router record
lock through allocation and networkless preparation. It never cuts over.
"""
import os
import datetime as dt
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path
import time

import router_candidate
import router_candidate_disk as disks
import router_candidate_preparation as preparation
import router_copy_native
import router_generations as generations
import router_native as native
from router_preparation_assets import template, bootstrap
import router_records as records
import router_updates
from router_transaction import TransactionError

COPY_SPACE_MARGIN = 256 * 1024 * 1024


def capacity(storage):
    """Reserve a new LV and the fixed offline copy scratch before allocation."""
    output = native.command(['/sbin/vgs','--reportformat','json','--units','b',
        '--nosuffix','-o','vg_name,vg_free','vg0'],time.monotonic()+30)
    try:
        rows = json.loads(output,object_pairs_hook=records.unique)['report'][0]['vg']
        if len(rows) != 1 or set(rows[0]) != {'vg_name','vg_free'} or rows[0]['vg_name'] != 'vg0':
            raise ValueError('ambiguous volume group')
        free = Decimal(rows[0]['vg_free'])
        if not free.is_finite():
            raise ValueError('invalid free space')
    except (KeyError,IndexError,TypeError,ValueError,InvalidOperation) as error:
        raise TransactionError('replacement preparation cannot verify exact free LVM space') from error
    if free < disks.BYTES:
        raise TransactionError('replacement preparation lacks space for the new router LV')
    available = os.statvfs(storage.base)
    scratch = sum(router_copy_native.SLOTS.values()) + COPY_SPACE_MARGIN
    if available.f_bavail * available.f_frsize < scratch:
        raise TransactionError('replacement preparation lacks space for offline copy and recovery scratch')


def selection(value, job, release, profile, binding, report, policy, accepted,
              box, operation, engine, now):
    fields = {'kind','box','mode','operation_id','engine_commit','inputs_sha256',
              'source_operation','check_operation','template_operation',
              'template_sha256','job_sha256','bootstrap','release_sha256',
              'registry_sha256','old_sha256','accepted_assignment_sha256',
              'policy_sha256','report_sha256','source_binding_sha256'}
    if (not isinstance(value,dict) or set(value) != fields or
            value['kind'] != 'klokast.router-replacement-preparation.v1' or
            (value['box'],value['mode'],value['operation_id'],value['engine_commit']) !=
            (box,'replacement',operation,engine) or
            not generations.matches('[a-z0-9][a-z0-9-]{0,30}',box) or
            not generations.matches('[0-9a-f]{24}',operation) or
            not generations.matches('[0-9a-f]{40}',engine) or
            any(not generations.matches('[0-9a-f]{24}',value[name]) for name in
                ('source_operation','check_operation','template_operation')) or
            any(not generations.matches('[0-9a-f]{64}',value[name]) for name in
                ('inputs_sha256','template_sha256','job_sha256','release_sha256',
                 'registry_sha256','old_sha256','accepted_assignment_sha256',
                 'policy_sha256','report_sha256','source_binding_sha256'))):
        raise TransactionError('replacement preparation has an invalid target or input identity')
    router_candidate.validate(job)
    router_updates.validate_release(release,profile,engine)
    if (any(job[name] != value[name] for name in
            ('box','mode','operation_id','engine_commit','inputs_sha256')) or
            value['job_sha256'] != generations.digest(job) or
            value['release_sha256'] != release['receipt_sha256'] or
            value['inputs_sha256'] != release['inputs']['inputs_sha256'] or
            job['kernel_release'] != release['kernel_release'] or
            job['runtime_packages'] != release['runtime_packages'] or
            job['personalization']['packages'] != {
                item['name']:item['version'] for item in release['inputs']['packages']} or
            not isinstance(report,dict) or not isinstance(accepted,dict) or accepted.get('box') != box or
            accepted.get('role') != 'router' or accepted.get('generation') != value['old_sha256'] or
            value['policy_sha256'] != report.get('policy_sha256') or
            value['report_sha256'] != report.get('report_sha256') or
            value['source_binding_sha256'] != generations.digest(binding)):
        raise TransactionError('replacement preparation differs from its selected release or check')
    if router_updates.require_checked_source(binding,report,release['inputs'],profile,
            box=box,engine=engine,policy=policy,
            policy_sha256=value['policy_sha256'],accepted=accepted,
            now=dt.datetime.fromtimestamp(now,dt.timezone.utc)) != value['source_operation']:
        raise TransactionError('replacement source operation differs from its checked input')
    return value


def authority(value, job, release, profile, binding, report, policy, accepted,
              grant, box, operation, engine, now):
    selection(value,job,release,profile,binding,report,policy,accepted,
              box,operation,engine,now)
    if (not isinstance(grant,dict) or set(grant) != {
            'kind','engine_commit','request_sha256','accepted_assignment_sha256',
            'policy_sha256','granted_at','expires_at'} or
            grant['kind'] != 'klokast.router-replacement-preparation-grant.v1' or
            grant['engine_commit'] != engine or
            grant['request_sha256'] != generations.digest(value) or
            grant['accepted_assignment_sha256'] != value['accepted_assignment_sha256'] or
            grant['policy_sha256'] != value['policy_sha256'] or
            any(type(grant[name]) is not int for name in ('granted_at','expires_at')) or
            not grant['granted_at'] <= now < grant['expires_at'] <= grant['granted_at'] + 900):
        raise TransactionError('replacement preparation grant is stale or selects different inputs')
    return value


def accepted_runtime(storage, value, accepted, accepted_profile, *, xen=Path('/etc/xen')):
    if storage.pending() is not None:
        raise TransactionError('replacement preparation cannot overlap a pending cutover')
    assignment = storage.accepted()
    if (assignment['record_sha256'] != value['accepted_assignment_sha256'] or
            assignment['current_sha256'] != value['old_sha256']):
        raise TransactionError('accepted router assignment changed before replacement preparation')
    old = storage.generation(value['old_sha256'])
    if old['origin'] == 'legacy':
        if set(accepted) != {'box','role','generation','legacy'} or accepted['legacy'] != old:
            raise TransactionError('legacy replacement source differs from the protected generation')
    else:
        if set(accepted) != {'box','role','generation','release'}:
            raise TransactionError('template replacement source lacks its exact release')
        router_updates.accepted_template_release(box=storage.box,generation=old,
            release=accepted['release'],template_operation=old['template_operation'],
            profile=accepted_profile)
    host = native.Native()
    deadline = host.monotonic() + 120
    host.guard(storage.box,deadline=deadline)
    host.disk(old['disk'],deadline=deadline)
    for artifact in old['boot'].values():
        host.artifact(artifact,deadline=deadline)
    current = host.guest({'old':old},deadline=deadline)
    if current is None or current[0] != 'old':
        raise TransactionError('replacement preparation requires the exact running accepted router')
    records.parents(xen / 'router.cfg')
    config = native.literal_configuration(records.secure(xen / 'router.cfg').read_text())
    expected = native.literal_configuration(generations.configuration(old))
    if old['origin'] == 'legacy' and 'uuid' not in config:
        config['uuid'] = expected['uuid']
    if config != expected:
        raise TransactionError('installed router Xen definition differs from its accepted generation')
    link = xen / 'auto/router.cfg'
    records.parents(link)
    if (not link.is_symlink() or link.lstat().st_uid != records.ROOT_UID or
            os.readlink(link) not in ('../router.cfg',str(xen / 'router.cfg'))):
        raise TransactionError('accepted router has no exact managed autostart link')
    return assignment,old


def execute(storage, operation, engine, *, xen=Path('/etc/xen')):
    work = storage.operation(operation)
    value,job,release,profile,binding,report,policy,accepted,accepted_profile,grant = (
        records.read(work / (name + '.json')) for name in (
            'request','candidate-job','release','profile','candidate-source','check',
            'policy','accepted','accepted-profile','authorization'))
    authority(value,job,release,profile,binding,report,policy,accepted,grant,
              storage.box,operation,engine,time.time())
    native.Native().guard(storage.box,deadline=time.monotonic()+30)
    source,expected = template(value,release)
    bootstrap(work,value)
    with storage.lock():
        assignment,old = accepted_runtime(storage,value,accepted,accepted_profile,xen=xen)
        authority(value,job,release,profile,binding,report,policy,accepted,grant,
                  storage.box,operation,engine,time.time())
        disk_record_path = work / 'candidate-disk.json'
        if (not disk_record_path.exists() and not disk_record_path.is_symlink() or
                disks.record(work,operation)['stage'] == 'planned'):
            capacity(storage)
        disk = disks.replacement_clone(work,operation,source,expected,storage,old['record_sha256'])
        result = preparation.prepare(work,value,job,disk)
        component = {name:release['inputs']['tailscale'][name] for name in (
            'version','sha256','tailscale_sha256','tailscaled_sha256','openrc_sha256')}
        if result['prepared']['tailscale'] != component:
            raise TransactionError('replacement candidate Tailscale binaries differ from the selected release')
        if storage.accepted() != assignment or storage.pending() is not None:
            raise TransactionError('accepted router assignment changed during networkless preparation')
        return {'kind':'klokast.router-replacement-preparation-result.v1',
                'box':storage.box,'operation_id':operation,'status':'replacement-prepared',
                'router_started':False,'old_sha256':old['record_sha256'],
                'candidate_disk':disk,'preparation_sha256':generations.digest(result),
                'release_sha256':release['receipt_sha256']}
