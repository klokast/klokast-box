"""Validate one controller-side B enrollment handoff and uncertain mint guard."""
import router_candidate_preparation as preparation
import router_generations as generations
import router_replacement_enrollment as enrollment
import router_transaction as transaction


def signal_source(request, pending, source, attempt, prepared, preparation_request,
                  preparation_job):
    transaction.validate_pending(pending,request)
    enrollment.validate_attempt(attempt,request)
    preparation.validate_result(prepared,preparation_request,preparation_job)
    if (pending['phase'] != 'awaiting-enrollment' or
            not pending['candidate_started'] or pending['old_started'] or
            not isinstance(source,dict) or set(source) != {
                'kind','request_sha256','old_sha256','old_machine_id','preparation_sha256'} or
            source['kind'] != 'klokast.router-replacement-enrollment-source.v1' or
            source['request_sha256'] != generations.digest(request) or
            source['old_sha256'] != request['old_sha256'] or
            source['old_machine_id'] != attempt['old_machine_id'] or
            source['preparation_sha256'] != generations.digest(prepared) or
            prepared['first_contact']['host_key_public_sha256'] !=
                attempt['host_key_public_sha256'] or
            preparation_request.get('operation_id') != request['operation_id'] or
            preparation_request.get('engine_commit') != request['engine_commit']):
        raise transaction.TransactionError('replacement controller enrollment differs from the waiting B attempt')
    return source


def mint_marker(request, attempt):
    transaction.validate(request)
    enrollment.validate_attempt(attempt,request)
    return {'kind':'klokast.router-replacement-mint-attempt.v1',
            'request_sha256':generations.digest(request),
            'attempt_sha256':attempt['record_sha256'],
            'nonce':attempt['nonce']}
