"""Bound one K001 cold first-install window and return its original router.

This local operation is not an outage grant. A controller caller must provide
separate supervised authority before it invokes the eventual installed action.
"""
import contextlib
import fcntl
import os
import stat
import time

import router_cold_disk as cold_disk
import router_cold_filesystem as cold_filesystem
import router_cold_return as cold_return
import router_cold_supervisor as cold_supervisor
import router_cold_window as cold_window
import router_generations as generations
import router_records as records
from router_transaction import TransactionError

WINDOW_SECONDS = 3600


class Cycle:
    def __init__(self, bundle):
        self.bundle, self.storage, self.host = bundle, bundle.storage, bundle.host
        self.window = cold_window.Window(bundle)
        self.request = cold_supervisor.Request(bundle)
        self.ready = bundle.directory / 'supervisor-ready.json'
        self.return_request = bundle.directory / 'supervisor-return-request.json'
        self.result = bundle.directory / 'supervisor-result.json'

    @contextlib.contextmanager
    def exclusive(self):
        """One local cycle at a time, independent of short record locks."""
        path = self.bundle.directory / 'supervisor.lock'
        records.parents(path)
        descriptor = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
        try:
            info = os.fstat(descriptor)
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != records.ROOT_UID or
                    info.st_mode & 0o077 or
                    info.st_nlink != 1):
                raise TransactionError('cold supervisor lock has unsafe ownership or mode')
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise TransactionError('another cold supervisor owns this operation') from error
            yield
        finally:
            os.close(descriptor)

    def open(self, request):
        """Arm, stop, verify backup, and expose one exact first-install target."""
        self.request.validate(request)
        metadata, original = self.bundle.verify()
        if original['xen']['uuid'] != request['original_xen_uuid']:
            raise TransactionError('cold supervisor original Xen identity changed')
        marker = self.window.arm(request['initial_operation'], int(time.time()) + WINDOW_SECONDS)
        self.host.stop({'accepted': original}, 'accepted', deadline=time.monotonic() + 90)
        cold_disk.DiskBackup(self.bundle).copy()
        cold_filesystem.Inspector(self.bundle).run()
        self.window.hold()
        marker = self.window.open_target()
        value = generations.seal({'kind': 'klokast.router-cold-supervisor-ready.v1',
            'box': self.storage.box, 'operation_id': self.bundle.operation,
            'engine_commit': self.bundle.engine,
            'request_sha256': request['record_sha256'],
            'marker_sha256': marker['record_sha256'],
            'initial_operation': request['initial_operation'],
            'opened_at': int(time.time()), 'expires_at': marker['expires_at']})
        if self.ready.exists() or self.ready.is_symlink():
            if records.read(self.ready) != value:
                raise TransactionError('cold supervisor ready record changed after target opening')
        else:
            records.write(self.ready, value)
        return value

    def return_signaled(self, ready):
        if not self.return_request.exists() and not self.return_request.is_symlink():
            return False
        value = records.read(self.return_request)
        generations.check_seal(value)
        if (set(value) != {'kind', 'box', 'operation_id', 'engine_commit',
                'ready_sha256', 'initial_operation', 'requested_at', 'record_sha256'} or
                value['kind'] != 'klokast.router-cold-supervisor-return-request.v1' or
                value['box'] != self.storage.box or value['operation_id'] != self.bundle.operation or
                value['engine_commit'] != self.bundle.engine or
                value['ready_sha256'] != ready['record_sha256'] or
                value['initial_operation'] != ready['initial_operation'] or
                type(value['requested_at']) is not int or
                not ready['opened_at'] <= value['requested_at'] <= int(time.time())):
            raise TransactionError('cold supervisor return signal differs from its open target')
        return True

    def run(self):
        """A lost controller signal causes a bounded return, never a second boot."""
        with self.exclusive():
            marker = self.storage.cold_test()
            if self.result.exists() or self.result.is_symlink():
                result = records.read(self.result)
                generations.check_seal(result)
                if (set(result) != {'kind', 'box', 'operation_id', 'engine_commit',
                        'reason', 'status', 'finished_at', 'record_sha256'} or
                        result['kind'] != 'klokast.router-cold-supervisor-result.v1' or
                        result['box'] != self.storage.box or
                        result['operation_id'] != self.bundle.operation or
                        result['engine_commit'] != self.bundle.engine or
                        result['reason'] not in ('interrupted', 'timeout', 'controller-return') or
                        result['status'] != 'original-running-fenced' or
                        type(result['finished_at']) is not int):
                    raise TransactionError('cold supervisor completion differs from this operation')
                if marker is not None:
                    cold_return.Return(self.bundle).restore()
                return result
            if marker is not None:
                if marker['operation_id'] != self.bundle.operation or marker['engine_commit'] != self.bundle.engine:
                    raise TransactionError('cold supervisor restart found another fenced operation')
                # After an interrupted process, recover instead of reopening
                # the target or trusting an expired request.
                reason = 'interrupted'
            else:
                request = self.request.verify()
                reason = 'timeout'
                try:
                    ready = self.open(request)
                    deadline = time.monotonic() + min(
                        WINDOW_SECONDS, max(0, ready['expires_at'] - time.time()))
                    while time.monotonic() < deadline and time.time() < ready['expires_at']:
                        if self.return_signaled(ready):
                            reason = 'controller-return'
                            break
                        time.sleep(min(2, max(0, deadline - time.monotonic())))
                finally:
                    if self.storage.cold_test() is not None:
                        cold_return.Return(self.bundle).restore()
            if reason == 'interrupted':
                cold_return.Return(self.bundle).restore()
            result = generations.seal({'kind': 'klokast.router-cold-supervisor-result.v1',
                'box': self.storage.box, 'operation_id': self.bundle.operation,
                'engine_commit': self.bundle.engine, 'reason': reason,
                'status': 'original-running-fenced', 'finished_at': int(time.time())})
            records.write(self.result, result)
            return result
