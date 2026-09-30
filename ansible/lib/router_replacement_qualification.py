"""Bind isolated old/new/old DHCP evidence to one retained A/B router pair."""
import router_dom0
import router_generations as generations
import router_transaction as transaction


PHASES = ('prepare','seed','forward','new','reverse','old')


def records(request, old, candidate, preparation, accepted_source, release,
            preflight, host, result, phases, lifecycle, retired_disk, gateway):
    transaction.validate(request)
    generations.pair(old,candidate,request)
    common = {'box':request['box'],'operation_id':request['operation_id'],
        'engine_commit':request['engine_commit'],
        'old_sha256':request['old_sha256'],
        'candidate_sha256':request['candidate_sha256']}
    if (not isinstance(preparation,dict) or
            preparation.get('kind') != 'klokast.router-replacement-preparation.v1' or
            any(preparation.get(key) != request[key] for key in
                ('box','operation_id','engine_commit','policy_sha256','old_sha256')) or
            preparation.get('accepted_assignment_sha256') != request['accepted_sha256'] or
            preparation.get('template_operation') != candidate['template_operation'] or
            preparation.get('release_sha256') != release.get('receipt_sha256') or
            preparation.get('inputs_sha256') != release.get('inputs',{}).get('inputs_sha256') or
            candidate.get('release_sha256') != release.get('receipt_sha256') or
            candidate.get('packages') != release.get('runtime_packages') or
            not isinstance(accepted_source,dict) or
            accepted_source.get('assignment',{}).get('record_sha256') != request['accepted_sha256'] or
            accepted_source.get('generation') != old):
        raise transaction.TransactionError('retained B differs from the accepted A or frozen release')
    if (not isinstance(host,dict) or
            host.get('kind') != 'klokast.router-compatibility-host.v2' or
            host.get('box') != request['box'] or
            host.get('engine_commit') != request['engine_commit'] or
            host.get('inputs_sha256') != preparation['inputs_sha256'] or
            host.get('template',{}).get('operation') != candidate['template_operation'] or
            host['template'].get('sha256') != preparation.get('template_sha256') or
            host.get('source',{}).get('accepted') != accepted_source or
            host['source'].get('disk') != old['disk'] or
            host.get('guest',{}).get('source_packages') != old['packages'] or
            host['guest'].get('source_kernel_release') != old['kernel_release'] or
            not isinstance(host['guest'].get('source_files'),dict) or
            not {'etc/network/interfaces','etc/dhcpcd.conf','etc/dnsmasq.conf',
                 'etc/nftables.nft'} <= host['guest']['source_files'].keys() or
            any(old['configuration_files'].get(path) != checksum for path,checksum in
                host['guest']['source_files'].items()) or
            host['guest'].get('runtime_packages') != candidate['packages'] or
            host['guest'].get('kernel_release') != candidate['kernel_release'] or
            not isinstance(result,dict) or
            result.get('kind') != 'klokast.router-compatibility-result.v2' or
            result.get('box') != request['box'] or
            result.get('engine_commit') != request['engine_commit'] or
            result.get('inputs_sha256') != preparation['inputs_sha256'] or
            result.get('operation_id') != host.get('operation_id') or
            result.get('source') != host['source'] or
            result.get('template_operation') != candidate['template_operation'] or
            result.get('source_packages') != old['packages'] or
            result.get('runtime_packages') != candidate['packages'] or
            result.get('candidate_modes') != ['initial-install','replacement'] or
            result.get('phases') != list(PHASES) or
            result.get('success') is not True or
            result.get('production_identity') is not False or
            result.get('enrollment_tested') is not False or
            result.get('replacement_authorized') is not False):
        raise transaction.TransactionError('isolated DHCP diagnostic selects another A/B software pair')
    if (not isinstance(phases,dict) or set(phases) != set(PHASES) or
            any(not isinstance(phase,dict) or
                phase.get('kind') != 'klokast.router-compatibility-phase.v2' or
                phase.get('phase') != name or
                phase.get('operation_id') != host['operation_id'] or
                phase.get('inputs_sha256') != preparation['inputs_sha256'] or
                phase.get('success') is not True or
                phase.get('production_identity') is not False
                for name,phase in phases.items()) or
            any(phases[name].get('tests') != dict.fromkeys(
                {'wan_dhcp_identity','wan_lease','lan_lease','lan_dns'} |
                ({'expiry_lease_created'} if name == 'seed' else
                 {'dhcp_identity_continuity','copied_timestamps',
                  'generation_identity_preserved','fresh_wan_negotiation'} |
                 ({'expired_lease_removed','old_client_renewed'} if name == 'new' else set())),True)
                or phases[name].get('enrollment_tested') is not False
                or phases[name].get('packages') != (
                    candidate['packages'] if name == 'new' else old['packages'])
                or not isinstance(phases[name].get('state'),dict)
                or set(phases[name]['state']) != {'files','generation_identity','lan_leases'}
                for name in ('seed','new','old')) or
            any(phases[name].get('copy_complete') is not True or
                not generations.matches('[0-9a-f]{64}',phases[name].get('copy_receipt_sha256'))
                for name in ('forward','reverse'))):
        raise transaction.TransactionError('old/new/old DHCP phases or copy receipts are incomplete')
    if (not isinstance(lifecycle,dict) or lifecycle.get('operation_id') != host['operation_id'] or
            lifecycle.get('stage') != 'cleaned' or
            not isinstance(retired_disk,dict) or
            retired_disk.get('operation_id') != host['operation_id'] or
            retired_disk.get('stage') != 'retired' or
            result.get('candidate_disk') != {'path':retired_disk.get('path'),
                'uuid':retired_disk.get('uuid'),'bytes':2147483648}):
        raise transaction.TransactionError('isolated DHCP guests or disposable disks lack complete retirement')
    compatibility = {'kind':'klokast.router-retained-compatibility.v2',**common,
        'success':True,'production_identity':False,
        'phases':{name:generations.digest(phases[name]) for name in
            ('forward','new','reverse','old')}}
    copy = {'kind':'klokast.router-retained-copy-qualification.v2',**common,
        'forward_receipt_sha256':phases['forward']['copy_receipt_sha256'],
        'reverse_receipt_sha256':phases['reverse']['copy_receipt_sha256'],
        'copy_guest_detached':True}
    readiness = {'kind':'klokast.router-readiness.v4',
        'request_sha256':generations.digest(request),
        'release_sha256':candidate['release_sha256'],
        'candidate_preflight_sha256':generations.digest(preflight),
        'compatibility_sha256':generations.digest(compatibility),
        'copy_qualification_sha256':generations.digest(copy),
        'gateway':gateway}
    router_dom0.readiness(readiness,request)
    return compatibility,copy,readiness
