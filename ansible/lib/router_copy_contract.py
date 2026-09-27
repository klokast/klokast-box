"""Fixed copy jobs shared by controller staging and the dom0 executor."""
import router_generations
import router_transaction


def job(request, old, candidate, inputs_sha256):
    router_transaction.validate(request)
    router_generations.pair(old, candidate, request)
    if not router_generations.matches('[0-9a-f]{64}', inputs_sha256):
        raise ValueError('router copy capsule needs its exact authenticated package input identity')
    common = {'kind': 'klokast.router-copy-request.v1', 'role': 'router', 'box': request['box'],
              'operation': request['operation_id'],
              'seconds': min(120, request['cutover_seconds'] - 30, request['recovery_seconds'] - 30)}
    jobs = {}
    for name, source, target in (('forward', old, candidate), ('reverse', candidate, old)):
        value = {**common, 'source_id': source['disk']['uuid'], 'destination_id': target['disk']['uuid'],
                 'source_accounts': source['accounts'], 'destination_accounts': target['accounts']}
        jobs[name] = {**value, 'request_sha256': router_generations.digest(value)}
    return {'kind': 'klokast.router-copy-job.v1', 'operation_id': request['operation_id'],
            'inputs_sha256': inputs_sha256, **jobs}


def capsule(value, request, old, candidate):
    fields = {'kind', 'operation_id', 'inputs_sha256', 'engine_commit', 'transaction_sha256',
              'job_sha256', 'bootstrap', 'domains'}
    if (not isinstance(value, dict) or set(value) != fields or value['kind'] != 'klokast.router-copy-capsule.v1' or
            value['operation_id'] != request['operation_id'] or value['engine_commit'] != request['engine_commit'] or
            value['transaction_sha256'] != router_generations.digest(request) or
            value['job_sha256'] != router_generations.digest(job(request, old, candidate, value['inputs_sha256']))):
        raise ValueError('router copy capsule differs from its exact transaction and generation pair')
    if (not isinstance(value['bootstrap'], dict) or set(value['bootstrap']) != {'kernel', 'initramfs'} or
            not isinstance(value['domains'], dict) or set(value['domains']) != {'forward', 'reverse'}):
        raise ValueError('router copy capsule lacks exact boot artifacts or disposable domain identities')
    for name, maximum in (('kernel', 32 * 1024 * 1024), ('initramfs', 1024 * 1024 * 1024)):
        item = value['bootstrap'][name]
        if (not isinstance(item, dict) or set(item) != {'bytes', 'sha256'} or
                type(item['bytes']) is not int or not 0 < item['bytes'] <= maximum or
                not router_generations.matches('[0-9a-f]{64}', item['sha256'])):
            raise ValueError('router copy capsule boot artifact is invalid')
    identities = list(value['domains'].values()) + [old['xen']['uuid'], candidate['xen']['uuid']]
    if (any(not router_generations.matches('[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}', v) for v in identities) or
            len(identities) != len(set(identities))):
        raise ValueError('router copy capsule domain identities overlap or are invalid')
    return value
