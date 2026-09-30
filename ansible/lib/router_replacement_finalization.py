"""Bind replacement cleanup inputs and its result to one enrolled generation.

These checks do not start a Xen guest or authorize a cutover. The dom0 runner
must verify the stopped candidate, staged boot inputs, and detached output.
"""
import router_candidate
import router_candidate_preparation as preparation
import router_generations as generations
import router_replacement_enrollment as enrollment
import router_transaction as transaction


def job_for(request, candidate, preparation_request, preparation_job, prepared,
            release, attempt, enrolled):
    transaction.validate(request)
    generations.generation(candidate, request['box'])
    enrollment.validate_attempt(attempt, request)
    enrollment.result(enrolled, request, attempt)
    router_candidate.validate(preparation_job)
    preparation.validate_result(prepared, preparation_request, preparation_job)
    if (candidate['record_sha256'] != request['candidate_sha256'] or
            preparation_request.get('box') != request['box'] or
            preparation_request.get('operation_id') != request['operation_id'] or
            preparation_request.get('engine_commit') != request['engine_commit'] or
            preparation_request.get('inputs_sha256') != preparation_job['inputs_sha256'] or
            preparation_request.get('job_sha256') != generations.digest(preparation_job) or
            preparation_job['operation_id'] != request['operation_id'] or
            preparation_job['engine_commit'] != request['engine_commit'] or
            prepared['prepared']['accounts'] != candidate['accounts'] or
            prepared['prepared']['tailscale'] != candidate['tailscale'] or
            release.get('receipt_sha256') != candidate['release_sha256'] or
            release.get('inputs', {}).get('inputs_sha256') != preparation_job['inputs_sha256'] or
            release.get('runtime_packages') != candidate['packages'] or
            release.get('runtime_packages') != preparation_job['runtime_packages'] or
            prepared['first_contact']['host_key_public_sha256'] !=
                enrolled['host_key_public_sha256']):
        raise transaction.TransactionError('replacement cleanup differs from its prepared or enrolled generation')
    return {'kind':'klokast.router-replacement-finalization-job.v1',
        'box':request['box'],'operation_id':request['operation_id'],
        'inputs_sha256':preparation_job['inputs_sha256'],
        'job_sha256':generations.digest(preparation_job),
        'preparation_job':preparation_job,'manifest':release['inputs'],
        'prepared':prepared['prepared'],'first_contact':prepared['first_contact'],
        'runtime_packages':release['runtime_packages'],'enrolled_guest':enrolled}


def result(value, job, release, enrolled):
    expected = {'kind','operation_id','inputs_sha256','job_sha256',
                'success','finalized','state','state_sha256','machine_id'}
    state = value.get('state') if isinstance(value, dict) else None
    finalized = value.get('finalized') if isinstance(value, dict) else None
    tailscale_state = state.get('var/lib/tailscale/tailscaled.state') if isinstance(state,dict) else None
    if (not isinstance(value,dict) or set(value) != expected or
            value['kind'] != 'klokast.router-replacement-finalization-result.v1' or
            value['operation_id'] != job['operation_id'] or
            value['inputs_sha256'] != job['inputs_sha256'] or
            value['job_sha256'] != generations.digest(job) or
            value['success'] is not True or
            value['machine_id'] != enrolled['machine_id'] or
            not isinstance(state,dict) or
            value['state_sha256'] != generations.digest(state) or
            not isinstance(tailscale_state,dict) or
            tailscale_state.get('sha256') !=
                enrolled['state_sha256'] or
            not isinstance(finalized,dict) or
            finalized.get('packages') != release['runtime_packages'] or
            finalized.get('tests') != release['runtime_tests'] or
            finalized.get('enrolled_state_preserved') is not True):
        raise transaction.TransactionError('replacement cleanup returned incomplete or different offline evidence')
    return value
