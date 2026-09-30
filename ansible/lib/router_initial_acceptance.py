"""Publish one verified first-router generation under fresh bootstrap authority."""
import hashlib
import os
from pathlib import Path
import re
import time

import router_generations as generations
import router_initial_finalization as finalization
import router_initial_installation as installation
import router_native as native
import router_personalize as personalize
import router_records as records
from router_transaction import TransactionError


def expected_for(box,operation,engine,current,prepared,release,offline):
    """Bind runtime checks to one frozen release and stable stopped-disk state."""
    state = offline.get('state') if isinstance(offline,dict) else None
    if not isinstance(state,dict):
        raise TransactionError('first router acceptance lacks offline identity evidence')
    identity = {}
    for name,part in state.items():
        if (name in ('var/lib/dhcpcd/duid','var/lib/dhcpcd/secret') or
                re.fullmatch(r'(etc/ssh|var/lib/tailscale/ssh)/ssh_host_(rsa|ecdsa|ed25519)_key',name)):
            if not isinstance(part,dict) or not generations.matches('[0-9a-f]{64}',part.get('sha256')):
                raise TransactionError('first router acceptance has invalid identity hashes')
            identity['/' + name] = part['sha256']
    if (not {'/var/lib/dhcpcd/duid','/var/lib/dhcpcd/secret'} <= identity.keys() or
            len(identity) != 5):
        raise TransactionError('first router acceptance lacks all stable identity files')
    config = {'/' + name:checksum for name,checksum in
              prepared['prepared']['configuration_files'].items()}
    return generations.seal({'kind':'klokast.router-initial-runtime-expected.v1',
        'box':box,'operation_id':operation,'engine_commit':engine,
        'release_sha256':release['receipt_sha256'],'machine_id':current['machine_id'],
        'kernel_release':release['kernel_release'],'packages':release['runtime_packages'],
        'tailscale':prepared['prepared']['tailscale'],
        'configuration_files':config,'identity_files':identity})


def verification(value,expected,box,operation):
    target = {'kind':'klokast.router-initial-runtime-verification.v1',
        'box':box,'operation_id':operation,
        'expected_sha256':expected['record_sha256'],
        'release_sha256':expected['release_sha256'],
        'machine_id':expected['machine_id'],'status':'verified',
        'services':True,'packages':True,'identity':True,'management':True,'dom0':True}
    if value != target:
        raise TransactionError('first router service verification differs from its frozen release')
    return value


def grant(value,verified,enrolled,engine,box,operation,now):
    if (not isinstance(value,dict) or set(value) != {'kind','box','operation_id',
            'engine_commit','verification_sha256','installation_sha256',
            'granted_at','expires_at'} or
            value['kind'] != 'klokast.router-initial-accept-grant.v1' or
            value['box'] != box or value['operation_id'] != operation or
            value['engine_commit'] != engine or
            value['verification_sha256'] != generations.digest(verified) or
            value['installation_sha256'] != enrolled['record_sha256'] or
            any(type(value[key]) is not int for key in ('granted_at','expires_at')) or
            not value['granted_at'] <= now < value['expires_at'] <= value['granted_at']+300):
        raise TransactionError('first router acceptance grant is stale or selects different evidence')


def generation_for(storage,operation,engine,current,source,prepared,release,
                   boot_request,boot_intent,verified):
    value = generations.seal({'kind':'klokast.router-generation.v1',
        'box':storage.box,'role':'router','generation_id':operation,
        'origin':'template','engine_commit':engine,
        'template_operation':source['template_operation'],
        'release_sha256':release['receipt_sha256'],
        'alpine_branch':release['inputs']['branch'],'disk':current['disk'],
        'boot':boot_intent['boot'],'xen':boot_request['xen'],
        'packages':release['runtime_packages'],'kernel_release':release['kernel_release'],
        'accounts':prepared['prepared']['accounts'],
        'configuration_files':prepared['prepared']['configuration_files'],
        'tailscale':prepared['prepared']['tailscale'],
        'evidence_sha256':generations.digest(verified)})
    generations.generation(value,storage.box)
    return value


def install_autostart(storage,record,host,*,xen=Path('/etc/xen')):
    """Complete or reconcile exact Xen autostart after accepted pointer exists."""
    deadline = time.monotonic()+120
    host.guard(storage.box,deadline=deadline)
    accepted = storage.accepted()
    if (accepted['policy_sha256'] != records.INITIAL_AUTHORITY_SHA256 or
            accepted['current_sha256'] != record['record_sha256'] or
            accepted['previous_sha256'] is not None or storage.pending() is not None):
        raise TransactionError('first router autostart lacks its exact accepted assignment')
    if host.guest({'accepted':record},deadline=deadline) is None:
        raise TransactionError('first router autostart requires its exact running accepted guest')
    content = generations.configuration(record)
    path = xen / 'router.cfg'
    if path.exists() or path.is_symlink():
        if records.secure(path).read_text() != content:
            raise TransactionError('first router autostart Xen definition changed')
    else:
        records.atomic(path,content.encode())
    link = xen / 'auto/router.cfg'
    if link.is_symlink():
        if link.lstat().st_uid != records.ROOT_UID or os.readlink(link) != '../router.cfg':
            raise TransactionError('first router autostart link changed')
    elif link.exists():
        raise TransactionError('first router autostart path is not the managed link')
    else:
        link.symlink_to('../router.cfg')
        records.syncdir(link.parent)
    native.command(['/usr/sbin/lbu','commit','-d'],deadline,maximum_seconds=120)
    return accepted


def execute(storage,operation,engine):
    host = native.Native()
    host.guard(storage.box,deadline=time.monotonic()+30)
    with storage.lock():
        (work,current,source,preparation_job,prepared,release,
         boot_request,boot_intent,enrollment) = finalization.context(
             storage,operation,engine,allow_verified=True)
        final = work / 'finalization'
        records.secure(final,directory=True)
        enrolled_result = records.read(work / 'initial-enrollment-result.json')
        enrolled = installation.validate(enrolled_result['installation'],storage.box)
        if (enrolled['stage'] != 'enrolled' or enrolled['operation_id'] != operation or
                enrolled['machine_id'] != current['machine_id'] or
                enrolled['disk'] != current['disk'] or
                enrolled['enrollment_sha256'] != current['enrollment_sha256']):
            raise TransactionError('first router acceptance changed its enrolled identity')
        offline = records.read(final / 'result.json')
        complete = records.read(final / 'complete.json')
        if (complete.get('kind') != 'klokast.router-initial-finalization-complete.v1' or
                complete.get('box') != storage.box or complete.get('operation_id') != operation or
                complete.get('status') != 'offline-finalized' or
                complete.get('result_sha256') != generations.digest(offline) or
                complete.get('disk') != current['disk'] or
                offline.get('kind') != 'klokast.router-initial-finalization-result.v1' or
                offline.get('operation_id') != operation or
                offline.get('inputs_sha256') != source['inputs_sha256'] or
                offline.get('job_sha256') != generations.digest(
                    finalization.job_for(storage.box,operation,source,preparation_job,
                                         prepared,release,enrollment)) or
                offline.get('success') is not True or
                offline.get('machine_id') != current['machine_id'] or
                offline.get('finalized',{}).get('packages') != release['runtime_packages'] or
                offline.get('finalized',{}).get('tests') != release['runtime_tests'] or
                offline.get('finalized',{}).get('enrolled_state_preserved') is not True or
                offline.get('state_sha256') != personalize.digest(offline.get('state')) or
                offline.get('state',{}).get('var/lib/tailscale/tailscaled.state',{}).get('sha256') is None):
            raise TransactionError('first router acceptance lacks exact offline cleanup evidence')
        expected = expected_for(storage.box,operation,engine,current,prepared,release,offline)
        if records.read(final / 'runtime-expected.json') != expected:
            raise TransactionError('first router acceptance runtime expectation changed')
        verified = verification(records.read(final / 'runtime-verification.json'),
                                expected,storage.box,operation)
        authorization = records.read(final / 'accept-grant.json')
        grant(authorization,verified,enrolled,engine,storage.box,operation,time.time())
        record = generation_for(storage,operation,engine,current,source,prepared,release,
                                boot_request,boot_intent,verified)
        intent = {'kind':'klokast.router-initial-accept-intent.v1',
            'box':storage.box,'operation_id':operation,'engine_commit':engine,
            'enrollment_sha256':enrolled['record_sha256'],
            'verification_sha256':generations.digest(verified),
            'generation_sha256':record['record_sha256']}
        intent_path = final / 'accept-intent.json'
        if intent_path.exists() or intent_path.is_symlink():
            if records.read(intent_path) != intent:
                raise TransactionError('first router acceptance intent changed')
        else:
            if current['stage'] != 'enrolled':
                raise TransactionError('verified first router has no recorded acceptance intent')
            records.write(intent_path,intent)
        accepted_path = storage.base / 'accepted.json'
        if not accepted_path.exists() and not accepted_path.is_symlink():
            finalization.verify_final_live(storage,operation,engine,already_locked=True)
            grant(authorization,verified,enrolled,engine,storage.box,operation,time.time())
            if current['stage'] == 'enrolled':
                current = storage.record_installation(generations.seal({
                    **{key:value for key,value in current.items() if key != 'record_sha256'},
                    'stage':'verified','generation_sha256':record['record_sha256']}))
            elif current['generation_sha256'] != record['record_sha256']:
                raise TransactionError('verified first router generation changed on retry')
            grant(authorization,verified,enrolled,engine,storage.box,operation,time.time())
            storage.accept_initial(record)
        elif (current['stage'] != 'verified' or
                current['generation_sha256'] != record['record_sha256']):
            raise TransactionError('accepted first router has no exact verified installation')
        accepted = install_autostart(storage,record,host)
        result = {'kind':'klokast.router-initial-acceptance-result.v1',
            'box':storage.box,'operation_id':operation,'status':'accepted',
            'generation_sha256':record['record_sha256'],
            'assignment_sha256':accepted['record_sha256']}
        records.write(final / 'accept-result.json',result)
        return result
