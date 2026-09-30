"""Dom0 adapter for one protected router transaction.

The approved controller stages exact records and an expiring authorization.
This adapter cannot discover releases, enroll identities, or extend deadlines.
Its native copy backend must leave both production disks detached on return.
"""
import ipaddress
import logging
import os
from pathlib import Path
import secrets
import time

import router_generations as generations
import router_generation_device as devices
import router_native as native
import router_records as records
import router_replacement_enrollment as enrollment
import router_replacement_finalization as finalization
import router_replacement_service as service
import router_transaction as transaction


def readiness(value, request):
    if (not isinstance(value, dict) or set(value) != {'kind', 'request_sha256', 'release_sha256',
            'candidate_preflight_sha256', 'compatibility_sha256', 'copy_qualification_sha256', 'gateway'} or
            value['kind'] != 'klokast.router-readiness.v4' or value['request_sha256'] != generations.digest(request) or
            any(not generations.matches('[0-9a-f]{64}', value[k]) for k in
                ('release_sha256', 'candidate_preflight_sha256', 'compatibility_sha256', 'copy_qualification_sha256'))):
        raise transaction.TransactionError('router preparation lacks exact release, candidate, compatibility, or copy evidence')
    try:
        gateway = ipaddress.IPv4Address(value['gateway'])
        if str(gateway) != value['gateway'] or not gateway.is_private or gateway.is_loopback or gateway.is_unspecified:
            raise ValueError('unsupported gateway')
    except (ValueError, TypeError) as error:
        raise transaction.TransactionError('router local probe requires its approved private backend IPv4 gateway') from error
    return value


def acceptance(value, request, expected, proof):
    identities = {'request_sha256':generations.digest(request),
        'candidate_sha256':request['candidate_sha256'],
        'enrollment_sha256':expected['enrollment_sha256'],
        'finalization_sha256':expected['finalization_sha256'],
        'service_sha256':generations.digest(proof)}
    if (not isinstance(value,dict) or set(value) != {
            'kind',*identities,'evidence_sha256'} or
            value['kind'] != 'klokast.router-controller-acceptance.v2' or
            any(value[key] != item for key,item in identities.items()) or
            value['evidence_sha256'] != generations.digest(identities)):
        raise transaction.TransactionError('controller acceptance does not bind final B enrollment, cleanup, and service proof')
    return value


class Adapter:
    def __init__(self, storage, operation, copy_backend, *, host=None, xen=Path('/etc/xen')):
        self.storage, self.work = storage, storage.operation(operation)
        self.request = records.read(self.work / 'transaction-request.json')
        transaction.validate(self.request)
        if self.request['box'] != storage.box or self.request['operation_id'] != operation:
            raise transaction.TransactionError('router operation directory differs from its recorded target')
        self.pair = {side: storage.generation(self.request[key]) for side, key in
                     (('old', 'old_sha256'), ('candidate', 'candidate_sha256'))}
        generations.pair(self.pair['old'], self.pair['candidate'], self.request)
        self.host, self.copy_backend, self.xen = host or native.Native(), copy_backend, Path(xen)
        self.ready = readiness(records.read(self.work / 'readiness.json'), self.request)

    def verify_qualifications(self):
        """Require offline candidate checks and exact-pair rollback compatibility.

        The candidate's first router boot occurs only after the old router
        stops and state copying completes. Live service proof comes afterward.
        """
        old, candidate = self.pair['old'], self.pair['candidate']
        if self.ready['release_sha256'] != candidate['release_sha256']:
            raise transaction.TransactionError('router readiness selects a different candidate release')
        common = {'box':self.request['box'], 'operation_id':self.request['operation_id'],
                  'engine_commit':self.request['engine_commit'],
                  'old_sha256':self.request['old_sha256'],
                  'candidate_sha256':self.request['candidate_sha256']}
        candidate_record = records.read(self.work / 'candidate-preflight.json')
        if (not isinstance(candidate_record, dict) or set(candidate_record) != {
                'kind','box','operation_id','engine_commit','candidate_sha256',
                'disk','boot','status','candidate_booted','production_identity','temporary_access','tests'} or
                candidate_record['kind'] != 'klokast.router-candidate-preflight.v2' or
                any(candidate_record[key] != common[key] for key in (
                    'box','operation_id','engine_commit','candidate_sha256')) or
                candidate_record['disk'] != candidate['disk'] or
                candidate_record['boot'] != candidate['boot'] or
                candidate_record['status'] != 'first-contact-ready-and-detached' or
                candidate_record['candidate_booted'] is not False or
                candidate_record['production_identity'] is not False or
                candidate_record['temporary_access'] is not True or
                candidate_record['tests'] != dict.fromkeys((
                    'boot_artifacts','packages','openrc','configuration_syntax',
                    'rendered_files','identity_absent','first_contact_pinned'), True) or
                generations.digest(candidate_record) != self.ready['candidate_preflight_sha256']):
            raise transaction.TransactionError('router offline candidate preflight differs from its exact generation')
        compatibility = records.read(self.work / 'compatibility.json')
        if (not isinstance(compatibility, dict) or set(compatibility) != {
                'kind',*common,'success','production_identity','phases'} or
                compatibility['kind'] != 'klokast.router-retained-compatibility.v2' or
                any(compatibility[key] != value for key,value in common.items()) or
                compatibility['success'] is not True or
                compatibility['production_identity'] is not False or
                not isinstance(compatibility['phases'], dict) or
                set(compatibility['phases']) != {'forward','new','reverse','old'} or
                any(not generations.matches('[0-9a-f]{64}', value)
                    for value in compatibility['phases'].values()) or
                generations.digest(compatibility) != self.ready['compatibility_sha256']):
            raise transaction.TransactionError('router state compatibility differs from its retained generation pair')
        copy = records.read(self.work / 'copy-qualification.json')
        if (not isinstance(copy, dict) or set(copy) != {
                'kind',*common,'forward_receipt_sha256','reverse_receipt_sha256',
                'copy_guest_detached'} or
                copy['kind'] != 'klokast.router-retained-copy-qualification.v2' or
                any(copy[key] != value for key,value in common.items()) or
                any(not generations.matches('[0-9a-f]{64}', copy[key]) for key in (
                    'forward_receipt_sha256','reverse_receipt_sha256')) or
                copy['copy_guest_detached'] is not True or
                generations.digest(copy) != self.ready['copy_qualification_sha256']):
            raise transaction.TransactionError('router copy qualification differs from its retained generation pair')

    def monotonic(self):
        return self.host.monotonic()

    def persist(self, value):
        self.storage.persist(value)
        logging.getLogger('klokast.router-update').info('box=%s operation=%s phase=%s reason=%s',
            self.storage.box, self.request['operation_id'], value['phase'], value['reason'])

    def resources(self, *, deadline):
        self.host.guard(self.storage.box, deadline=deadline)
        for side, value in self.pair.items():
            self.host.disk(value['disk'], deadline=deadline)
            for item in value['boot'].values():
                self.host.artifact(item, deadline=deadline)
            config = self.work / (side + '.cfg')
            if records.secure(config).read_text() != generations.configuration(value):
                raise transaction.TransactionError('router staged configuration differs from its generation record')
        self.copy_backend.verify(self, deadline=deadline)

    def verify_prepared(self, request):
        if request != self.request or self.storage.pending() is not None or (self.work / 'complete.json').exists():
            raise transaction.TransactionError('router operation is already pending or completed; refusing replay')
        self.verify_qualifications()
        self.enrollment_source()
        grant = records.read(self.work / 'authorization.json')
        now = time.time()
        if (not isinstance(grant, dict) or set(grant) != {'kind', 'request_sha256', 'granted_at', 'expires_at', 'readiness_sha256'} or
                grant['kind'] != 'klokast.router-operation-authorization.v1' or
                grant['request_sha256'] != generations.digest(request) or grant['readiness_sha256'] != generations.digest(self.ready) or
                any(type(grant[k]) is not int for k in ('granted_at', 'expires_at')) or
                not grant['granted_at'] <= now < grant['expires_at'] <= grant['granted_at'] + 900):
            raise transaction.TransactionError('router controller authorization is stale or differs from the qualified operation')
        deadline = self.monotonic() + min(120, grant['expires_at'] - now)
        self.resources(deadline=deadline)
        if self.storage.committed(request):
            raise transaction.TransactionError('router candidate is already accepted')
        current = self.host.guest(self.pair, deadline=deadline)
        if current is None or current[0] != 'old':
            raise transaction.TransactionError('router cutover requires the exact running accepted old generation')
        self.host.detached([self.pair['candidate']['disk']['path']], deadline=deadline)
        config = native.literal_configuration(records.secure(self.xen / 'router.cfg').read_text())
        expected = native.literal_configuration(generations.configuration(self.pair['old']))
        # Adoption can retain the original legacy file without an explicit UUID.
        if self.pair['old']['origin'] == 'legacy' and 'uuid' not in config:
            config['uuid'] = expected['uuid']
        if config != expected:
            raise transaction.TransactionError('installed router configuration drifted from its accepted generation')
        self.autostart_link(required=True)
        if time.time() >= grant['expires_at'] or self.monotonic() >= deadline:
            raise transaction.TransactionError('router authorization expired during preflight')

    def autostart_link(self, *, required=False):
        directory = self.xen / 'auto'
        records.parents(directory)
        records.secure(directory, directory=True)
        link = directory / 'router.cfg'
        if link.is_symlink():
            if link.lstat().st_uid != 0 or os.readlink(link) not in ('../router.cfg', str(self.xen / 'router.cfg')):
                raise transaction.TransactionError('router autostart link has an unexpected owner or target')
        elif link.exists() or required:
            raise transaction.TransactionError('router autostart is not its recorded managed link')
        return link

    def disable_autostart(self, *, deadline):
        link = self.autostart_link()
        if link.is_symlink():
            link.unlink()
            records.syncdir(link.parent)
        # Persist before stopping a router. Pending also survives on dom0_data.
        native.command(['/usr/sbin/lbu', 'commit', '-d'], deadline, maximum_seconds=120)

    def enrollment_source(self):
        source = records.read(self.work / 'enrollment-source.json')
        prepared = records.read(self.work / 'preparation-result.json')
        if (not isinstance(source,dict) or set(source) != {'kind','request_sha256',
                'old_sha256','old_machine_id','preparation_sha256'} or
                source['kind'] != 'klokast.router-replacement-enrollment-source.v1' or
                source['request_sha256'] != generations.digest(self.request) or
                source['old_sha256'] != self.request['old_sha256'] or
                source['preparation_sha256'] != generations.digest(prepared) or
                prepared.get('kind') != 'klokast.router-candidate-preparation-result.v1' or
                prepared.get('operation_id') != self.request['operation_id'] or
                prepared.get('success') is not True or
                not isinstance(prepared.get('first_contact'),dict)):
            raise transaction.TransactionError('router enrollment source differs from the prepared candidate')
        keys = prepared['first_contact'].get('host_key_public_sha256')
        enrollment.attempt(self.request,self.pair['candidate'],nonce='0'*24,
            old_machine_id=source['old_machine_id'],host_keys=keys)
        return source,keys

    def arm(self, *, deadline):
        source,keys = self.enrollment_source()
        self.disable_autostart(deadline=deadline)
        path = self.work / 'enrollment-attempt.json'
        if path.exists() or path.is_symlink():
            intent = enrollment.validate_attempt(records.read(path),self.request)
            if (intent['old_machine_id'] != source['old_machine_id'] or
                    intent['host_key_public_sha256'] != keys):
                raise transaction.TransactionError('router enrollment attempt changed after preparation')
        else:
            intent = enrollment.attempt(self.request,self.pair['candidate'],
                nonce=secrets.token_hex(12),old_machine_id=source['old_machine_id'],host_keys=keys)
            records.write(path,intent)

    def stop(self, side, *, deadline):
        self.host.stop(self.pair, side, deadline=deadline)

    def copy(self, source, target, *, deadline):
        self.copy_backend.copy(self, source, target, deadline=deadline)

    def verify_copy(self, source, target, *, deadline):
        self.copy_backend.verify_copy(self, source, target, deadline=deadline)

    def start(self, side, *, deadline):
        self.copy_backend.fence(self, deadline=deadline)
        self.host.start(self.pair, side, self.work / (side + '.cfg'), deadline=deadline)

    def check_local(self, side, *, deadline):
        gateway = self.ready['gateway']
        while self.monotonic() < deadline:
            current = self.host.guest(self.pair, deadline=deadline)
            if current is None or current[0] != side:
                raise transaction.TransactionError('router local check reached the wrong running generation')
            try:
                native.command(['/bin/ping', '-n', '-c', '1', '-W', '1', gateway], deadline, maximum_seconds=3)
                # The fixed DNS question exercises the router resolver and WAN.
                native.command(['/usr/bin/nslookup', 'example.com', gateway], deadline, maximum_seconds=5)
                return
            except transaction.TransactionError:
                time.sleep(min(1, max(0, deadline - self.monotonic())))
        raise transaction.TransactionError('router backend gateway or WAN DNS did not recover within its fixed budget')

    def wait_enrollment(self, *, deadline):
        intent = enrollment.validate_attempt(records.read(self.work / 'enrollment-attempt.json'),
                                             self.request)
        path = self.work / 'enrollment-result.json'
        while self.monotonic() < deadline:
            if path.exists() or path.is_symlink():
                enrollment.result(records.read(path),self.request,intent)
                return True
            current = self.host.guest(self.pair,deadline=deadline)
            if current is None or current[0] != 'candidate':
                raise transaction.TransactionError('candidate stopped before its exact enrollment result')
            time.sleep(min(1,max(0,deadline-self.monotonic())))
        return False

    def finalize_candidate(self, *, deadline):
        finalization.run_candidate(self,deadline=deadline)

    def wait_acceptance(self, *, deadline):
        path = self.work / 'acceptance.json'
        while self.monotonic() < deadline:
            if path.exists() or path.is_symlink():
                self.acceptance_proof(records.read(path))
                return True
            current = self.host.guest(self.pair, deadline=deadline)
            if current is None or current[0] != 'candidate':
                raise transaction.TransactionError('candidate stopped before controller acceptance')
            time.sleep(min(1, max(0, deadline - self.monotonic())))
        return False

    def acceptance_proof(self, value):
        preparation_request = records.read(self.work / 'request.json')
        preparation_job = records.read(self.work / 'candidate-job.json')
        prepared = records.read(self.work / 'preparation-result.json')
        release = records.read(self.work / 'release.json')
        attempt = records.read(self.work / 'enrollment-attempt.json')
        enrolled = records.read(self.work / 'enrollment-result.json')
        finalized = records.read(self.work / 'finalization/result.json')
        job = finalization.job_for(self.request,self.pair['candidate'],
            preparation_request,preparation_job,prepared,release,attempt,enrolled)
        expected = service.expected(self.request,self.pair['candidate'],enrolled,
                                    finalized,release,job)
        source,_ = self.enrollment_source()
        old_device = devices.read(self.storage,self.request['old_sha256'])
        new_device = devices.read(self.storage,self.request['candidate_sha256'])
        if (old_device is None or new_device is None or
                old_device['machine_id'] != source['old_machine_id'] or
                new_device['machine_id'] != expected['machine_id'] or
                new_device['hostname'] != expected['hostname'] or
                old_device['machine_id'] == new_device['machine_id']):
            raise transaction.TransactionError('controller acceptance lacks distinct protected A/B devices')
        if records.read(self.work / 'controller-service-expected.json') != expected:
            raise transaction.TransactionError('controller service target differs from finalized B')
        proof = service.proof(records.read(self.work / 'controller-service-proof.json'),expected)
        return acceptance(value,self.request,expected,proof)

    def commit(self, side, *, deadline):
        if side != 'candidate':
            raise transaction.TransactionError('router commitment may select only the exact candidate')
        proof = self.acceptance_proof(records.read(self.work / 'acceptance.json'))
        current = self.host.guest(self.pair, deadline=deadline)
        if current is None or current[0] != side:
            raise transaction.TransactionError('candidate disappeared before its atomic acceptance')
        self.storage.commit(self.request, proof['evidence_sha256'])

    def committed(self, *, deadline):
        return self.storage.committed(self.request)

    def verify_recovery(self, request, *, deadline):
        if request != self.request:
            raise transaction.TransactionError('router recovery request changed')
        # Never require a working controller, current policy, or an unexpired
        # grant to recover an operation that was already durably armed.
        self.disable_autostart(deadline=deadline)
        finalization.fence(self,deadline=deadline)
        self.copy_backend.fence(self, deadline=deadline)
        self.resources(deadline=deadline)
        self.storage.committed(request)

    def disarm(self, side, *, deadline):
        if self.storage.committed(self.request) != (side == 'candidate'):
            raise transaction.TransactionError('router autostart selection contradicts the durable accepted assignment')
        current = self.host.guest(self.pair, deadline=deadline)
        if current is None or current[0] != side:
            raise transaction.TransactionError('router cannot enable autostart for an unverified running generation')
        records.atomic(self.xen / 'router.cfg', generations.configuration(self.pair[side]).encode())
        link = self.autostart_link()
        if not link.is_symlink():
            link.symlink_to('../router.cfg')
            records.syncdir(link.parent)
        native.command(['/usr/sbin/lbu', 'commit', '-d'], deadline, maximum_seconds=120)

    def finish(self, outcome):
        self.storage.finish(self.request, outcome)

    def fence_all(self, *, deadline):
        # A failed persistence command must not skip attempts to stop writers.
        errors = []
        for action in (lambda: self.disable_autostart(deadline=min(deadline, self.monotonic() + 5)),
                       lambda: finalization.fence(self,deadline=min(deadline,self.monotonic() + 8)),
                       lambda: self.copy_backend.fence(self, deadline=min(deadline, self.monotonic() + 5)),
                       lambda: self.host.stop(self.pair, 'candidate', deadline=min(deadline, self.monotonic() + 8), graceful=False),
                       lambda: self.host.stop(self.pair, 'old', deadline=min(deadline, self.monotonic() + 8), graceful=False)):
            try:
                action()
            except Exception as error:
                errors.append(type(error).__name__)
        if errors:
            raise transaction.TransactionError('router fencing could not confirm every writer stopped; use console recovery')
