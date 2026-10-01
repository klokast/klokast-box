"""Return one interrupted K001 cold test to its saved original router.

This is a bounded recovery component for a future local supervisor. It keeps
the cold boot fence set. A separate controller health check must clear it.
"""
import hashlib
import time

import router_cold_recovery as cold_recovery
import router_cold_test_state as cold_test_state
import router_cold_window as cold_window
import router_generations as generations
import router_initial_installation as initial_installation
import router_native as native
import router_records as records
from router_transaction import TransactionError


class Return:
    def __init__(self, bundle):
        self.bundle, self.storage, self.host = bundle, bundle.storage, bundle.host
        self.window = cold_window.Window(bundle)
        self.test = cold_test_state.TestState(bundle)

    def stop_test(self, marker):
        """Stop only a first router with the recorded test disk and Xen UUID."""
        installation = self.storage.installation()
        if installation is None:
            return None
        initial_installation.validate(installation, self.storage.box)
        if (installation['operation_id'] != marker['initial_operation'] or
                installation['engine_commit'] != self.bundle.engine):
            raise TransactionError('cold return found a different first installation')
        work = self.storage.operation(marker['initial_operation'])
        boot_request_path = work / 'initial-boot-request.json'
        boot_intent_path = work / 'initial-boot-intent.json'
        if not boot_intent_path.exists() and not boot_intent_path.is_symlink():
            # No checked first boot was begun. The archive stage will still
            # reject any unrecorded router guest or attached original disk.
            self.window.located(self.bundle.verify()[1])
            return installation
        if not boot_request_path.exists() and not boot_request_path.is_symlink():
            raise TransactionError('cold return has a boot intent without its first-router request')
        request = records.read(boot_request_path)
        intent = records.read(boot_intent_path)
        disk = installation['disk']
        boot = intent.get('boot') if isinstance(intent, dict) else None
        if (installation['stage'] in ('planned', 'allocated') or
                not isinstance(request, dict) or
                request.get('kind') != 'klokast.router-initial-boot-request.v1' or
                request.get('box') != self.storage.box or
                request.get('operation_id') != marker['initial_operation'] or
                request.get('engine_commit') != self.bundle.engine or
                not isinstance(request.get('xen'), dict) or
                not isinstance(boot, dict) or set(boot) != {'kernel', 'initramfs'} or
                not isinstance(intent, dict) or
                intent.get('kind') != 'klokast.router-initial-boot-intent.v1' or
                intent.get('box') != self.storage.box or
                intent.get('operation_id') != marker['initial_operation'] or
                intent.get('request_sha256') != generations.digest(request) or
                intent.get('disk') != disk):
            raise TransactionError('cold return test boot differs from its exact installation')
        generations.xen_identity(request['xen'])
        if request['xen']['uuid'] == self.bundle.verify()[1]['xen']['uuid']:
            raise TransactionError('cold return test reused the original Xen identity')
        config = work / 'initial-router.cfg'
        content = generations.initial_configuration(request['xen'], disk, boot)
        if (records.secure(config).read_text() != content or
                intent.get('config_sha256') != hashlib.sha256(content.encode()).hexdigest()):
            raise TransactionError('cold return test Xen definition changed after its first boot')
        self.host.stop_initial(disk, native.literal_configuration(content),
                               deadline=time.monotonic() + 90)
        return installation

    def restore(self):
        """Restore the exact original and leave its persistent boot fence."""
        marker, metadata, original = self.window.context()
        if marker['phase'] not in ('armed', 'holding', 'held', 'opening', 'open', 'restoring'):
            raise TransactionError('cold return found an unsupported recovery phase')
        if marker['phase'] == 'open':
            self.stop_test(marker)
            archive = self.test.archive / 'manifest.json'
            if archive.exists() or archive.is_symlink():
                self.test.verify()
            elif self.storage.installation() is None:
                self.test.record_unstarted()
            else:
                self.test.capture()
            if archive.exists() or archive.is_symlink():
                self.test.remove_selectors()
                self.test.retire_disk()
        elif self.storage.installation() is not None:
            raise TransactionError('cold return found a first installation before the target opened')
        if marker['phase'] == 'armed':
            if (self.storage.accepted() != records.read(self.bundle.directory / 'accepted.json') or
                    self.host.guest({'accepted': original}, deadline=time.monotonic() + 30) is None):
                self.window.restore_disk()
            else:
                with self.storage.lock():
                    current, _, _ = self.window.context()
                    if current['phase'] != 'armed':
                        raise TransactionError('cold return phase changed before preserving the live original')
                    self.window.phase(current, 'restoring')
        else:
            live = self.host.guest({'accepted': original}, deadline=time.monotonic() + 30)
            if live is None:
                self.window.restore_disk()
            elif marker['phase'] != 'restoring':
                raise TransactionError('cold return found a running router before original restoration')
        if self.host.guest({'accepted': original}, deadline=time.monotonic() + 30) is None:
            self.bundle.restore()
            self.window.commit_xen()
            self.host.start({'accepted': original}, 'accepted',
                            self.bundle.local('/etc/xen/router.cfg'),
                            deadline=time.monotonic() + 90)
        cold_recovery.Baseline(self.bundle).verify_restored()
        return {'kind': 'klokast.router-cold-return.v1', 'box': self.storage.box,
                'operation_id': self.bundle.operation, 'engine_commit': self.bundle.engine,
                'metadata_sha256': metadata['record_sha256'],
                'generation_sha256': original['record_sha256'],
                'status': 'original-running-fenced'}
