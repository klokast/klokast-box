"""Bind one replacement enrollment attempt and its controller result.

The dom0 transaction persists the intent before the controller may mint a key.
These record checks do not authorize a key, disk change, or router cutover.
"""
import ipaddress

import router_generations as generations
import router_transaction as transaction


def attempt(request, candidate, *, nonce, old_machine_id, host_keys):
    transaction.validate(request)
    generations.generation(candidate, request['box'])
    if (candidate['record_sha256'] != request['candidate_sha256'] or
            candidate['generation_id'] != request['operation_id'] or
            candidate['origin'] != 'template' or
            not generations.matches('[0-9a-f]{24}', nonce) or
            not generations.matches('[A-Za-z0-9_-]{1,128}', old_machine_id) or
            not isinstance(host_keys, dict) or not host_keys or
            not set(host_keys) <= {'rsa','ecdsa','ed25519'} or
            any(not generations.matches('[0-9a-f]{64}', item) for item in host_keys.values())):
        raise transaction.TransactionError('replacement enrollment attempt lacks its exact generation or old device')
    return generations.seal({'kind':'klokast.router-replacement-enrollment-attempt.v1',
        'box':request['box'],'operation_id':request['operation_id'],
        'request_sha256':generations.digest(request),
        'candidate_sha256':request['candidate_sha256'],
        'nonce':nonce,'old_machine_id':old_machine_id,
        'hostname':generations.tailnet_hostname(request['box'],candidate['generation_id']),
        'host_key_public_sha256':host_keys})


def validate_attempt(intent, request):
    transaction.validate(request)
    generations.check_seal(intent)
    if (set(intent) != {'kind','box','operation_id','request_sha256',
            'candidate_sha256','nonce','old_machine_id','hostname',
            'host_key_public_sha256','record_sha256'} or
            intent['kind'] != 'klokast.router-replacement-enrollment-attempt.v1' or
            intent['box'] != request['box'] or intent['operation_id'] != request['operation_id'] or
            intent['request_sha256'] != generations.digest(request) or
            intent['candidate_sha256'] != request['candidate_sha256'] or
            not generations.matches('[0-9a-f]{24}',intent['nonce']) or
            not generations.matches('[A-Za-z0-9_-]{1,128}',intent['old_machine_id']) or
            intent['hostname'] != generations.tailnet_hostname(request['box'],request['operation_id']) or
            not isinstance(intent['host_key_public_sha256'],dict) or
            not intent['host_key_public_sha256'] or
            not set(intent['host_key_public_sha256']) <= {'rsa','ecdsa','ed25519'} or
            any(not generations.matches('[0-9a-f]{64}',item)
                for item in intent['host_key_public_sha256'].values())):
        raise transaction.TransactionError('replacement enrollment attempt changed or selects another router')
    return intent


def result(value, request, intent):
    transaction.validate(request)
    validate_attempt(intent,request)
    if (not isinstance(value,dict) or set(value) != {
            'kind','box','operation_id','request_sha256','attempt_sha256',
            'candidate_sha256','nonce','machine_id','hostname','tags','ssh',
            'state_sha256','addresses','host_key_public_sha256'} or
            value['kind'] != 'klokast.router-replacement-enrollment-result.v1' or
            value['box'] != request['box'] or
            value['operation_id'] != request['operation_id'] or
            value['request_sha256'] != generations.digest(request) or
            value['candidate_sha256'] != request['candidate_sha256'] or
            value['attempt_sha256'] != intent['record_sha256'] or
            value['nonce'] != intent['nonce'] or
            value['hostname'] != intent['hostname'] or
            value['host_key_public_sha256'] != intent['host_key_public_sha256'] or
            value['tags'] != ['tag:vm'] or value['ssh'] is not True or
            not generations.matches('[A-Za-z0-9_-]{1,128}',value['machine_id']) or
            value['machine_id'] == intent['old_machine_id'] or
            not generations.matches('[0-9a-f]{64}',value['state_sha256'])):
        raise transaction.TransactionError('replacement enrollment result differs from the exact attempt')
    addresses = value['addresses']
    if (not isinstance(addresses,list) or not 1 <= len(addresses) <= 8 or
            any(not isinstance(item,str) for item in addresses) or
            len(set(addresses)) != len(addresses)):
        raise transaction.TransactionError('replacement enrollment lacks distinct Tailnet addresses')
    try:
        parsed = [ipaddress.ip_address(item) for item in addresses]
    except (ValueError,TypeError) as error:
        raise transaction.TransactionError('replacement enrollment has invalid Tailnet addresses') from error
    if (any(str(item) != original or item.is_unspecified or item.is_loopback or item.is_multicast
            for item,original in zip(parsed,addresses)) or
            not any(item.version == 4 for item in parsed)):
        raise transaction.TransactionError('replacement enrollment has no usable Tailnet IPv4 address')
    return value
