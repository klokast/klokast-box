"""Bind post-cleanup B service proof to its stopped-disk and device evidence."""
import router_generations as generations
import router_replacement_finalization as finalization
import router_transaction as transaction


TESTS = ('services','management','dom0','packages','configuration','identity')


def expected(request,candidate,enrolled,finalized,release,job):
    transaction.validate(request)
    generations.generation(candidate,request['box'])
    finalization.result(finalized,job,release,enrolled)
    if (candidate['record_sha256'] != request['candidate_sha256'] or
            candidate['generation_id'] != request['operation_id'] or
            candidate['packages'] != release['runtime_packages'] or
            candidate['tailscale'] != {name:release['inputs']['tailscale'][name] for name in
                ('version','sha256','tailscale_sha256','tailscaled_sha256','openrc_sha256')} or
            enrolled['hostname'] != generations.tailnet_hostname(request['box'],request['operation_id']) or
            enrolled['machine_id'] != finalized['machine_id']):
        raise transaction.TransactionError('replacement service target differs from final B identity or release')
    state = finalized['state']
    identity = {name:item['sha256'] for name,item in state.items()
                if name in ('var/lib/dhcpcd/duid','var/lib/dhcpcd/secret') or
                name.startswith('etc/ssh/ssh_host_') and name.endswith('_key') or
                name.startswith('var/lib/tailscale/ssh/ssh_host_') and name.endswith('_key')}
    if (not {'var/lib/dhcpcd/duid','var/lib/dhcpcd/secret'} <= identity.keys() or
            any(not generations.matches('[0-9a-f]{64}',item) for item in identity.values())):
        raise transaction.TransactionError('replacement service proof lacks stable DHCP and SSH identities')
    return generations.seal({'kind':'klokast.router-replacement-service-expected.v1',
        'box':request['box'],'operation_id':request['operation_id'],
        'request_sha256':generations.digest(request),
        'candidate_sha256':request['candidate_sha256'],
        'enrollment_sha256':generations.digest(enrolled),
        'finalization_sha256':generations.digest(finalized),
        'machine_id':enrolled['machine_id'],'hostname':enrolled['hostname'],
        'addresses':enrolled['addresses'],
        'packages':candidate['packages'],'kernel_release':candidate['kernel_release'],
        'configuration_files':candidate['configuration_files'],
        'tailscale':candidate['tailscale'],'identity_files':identity})


def proof(value,anticipated):
    generations.check_seal(anticipated)
    if (not isinstance(value,dict) or set(value) != {
            'kind','box','operation_id','expected_sha256','candidate_sha256',
            'machine_id','hostname','tests'} or
            value['kind'] != 'klokast.router-replacement-service-proof.v1' or
            value['box'] != anticipated['box'] or
            value['operation_id'] != anticipated['operation_id'] or
            value['expected_sha256'] != anticipated['record_sha256'] or
            value['candidate_sha256'] != anticipated['candidate_sha256'] or
            value['machine_id'] != anticipated['machine_id'] or
            value['hostname'] != anticipated['hostname'] or
            not isinstance(value['tests'],dict) or
            set(value['tests']) != set(TESTS) or
            any(item is not True for item in value['tests'].values())):
        raise transaction.TransactionError('replacement full-service proof is incomplete or selects another B')
    return value
