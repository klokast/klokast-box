"""Root-owned, durable router assignments and transaction records on dom0.

Checksums bind records; they do not grant permission. Only the approved
controller workflow may stage a grant. Recovery needs no remote authority.
"""
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import secrets
import stat

import router_generations as generations
import router_initial_installation as initial_installation
import router_transaction as transaction

BASE = Path('/mnt/dom0_data/klokast-router-updates')
ROOT_UID = 0
BASELINE_AUTHORITY_SHA256 = generations.digest({'kind':'klokast.router-supervised-baseline.v1'})
INITIAL_AUTHORITY_SHA256 = generations.digest({'kind':'klokast.router-approved-bootstrap.v1'})


def secure(path, *, directory=False, maximum=1024 * 1024):
    path = Path(path)
    info = path.lstat()
    expected = stat.S_ISDIR if directory else stat.S_ISREG
    if (not expected(info.st_mode) or info.st_uid != ROOT_UID or info.st_mode & 0o022 or
            not directory and (info.st_nlink != 1 or not 0 < info.st_size <= maximum)):
        raise transaction.TransactionError('router record path has unsafe ownership, type, mode, or size: ' + path.name)
    return path


def parents(path):
    for parent in Path(path).parents:
        secure(parent, directory=True)


def unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise transaction.TransactionError('router record contains duplicate JSON fields')
        result[key] = value
    return result


def read(path):
    parents(path)
    return json.loads(secure(path).read_text(), object_pairs_hook=unique)


def syncdir(path):
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def atomic(path, content):
    """A stale temporary file from a power loss cannot block the next write."""
    path = Path(path)
    parents(path)
    if path.exists() or path.is_symlink():
        secure(path)
    temporary = path.with_name('.' + path.name + '-' + secrets.token_hex(12))
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        syncdir(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def write(path, value):
    atomic(path, (json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':')) + '\n').encode())


def assignment(value, box):
    generations.check_seal(value)
    if (set(value) != {'kind', 'box', 'role', 'current_sha256', 'previous_sha256', 'operation_id',
                       'engine_commit', 'policy_sha256', 'evidence_sha256', 'record_sha256'} or
            value['kind'] != 'klokast.router-assignment.v1' or value['box'] != box or value['role'] != 'router' or
            not generations.matches('[a-z0-9][a-z0-9-]{0,30}', box) or
            not generations.matches('[0-9a-f]{24}', value['operation_id']) or
            not generations.matches('[0-9a-f]{40}', value['engine_commit']) or
            any(not generations.matches('[0-9a-f]{64}', value[k]) for k in
                ('current_sha256', 'policy_sha256', 'evidence_sha256')) or
            value['previous_sha256'] is not None and
            (not generations.matches('[0-9a-f]{64}', value['previous_sha256']) or
             value['previous_sha256'] == value['current_sha256'])):
        raise transaction.TransactionError('router accepted assignment is incomplete or has an invalid target')
    return value


def accepted_candidate(request, evidence_sha256):
    transaction.validate(request)
    value = generations.seal({'kind': 'klokast.router-assignment.v1', 'role': 'router', 'box': request['box'],
        'current_sha256': request['candidate_sha256'], 'previous_sha256': request['old_sha256'],
        'operation_id': request['operation_id'], 'engine_commit': request['engine_commit'],
        'policy_sha256': request['policy_sha256'], 'evidence_sha256': evidence_sha256})
    return assignment(value, request['box'])


class Records:
    def __init__(self, box, base=BASE):
        if not generations.matches('[a-z0-9][a-z0-9-]{0,30}', box):
            raise transaction.TransactionError('router records require an exact box selector')
        self.box, self.base = box, Path(base)
        parents(self.base)
        secure(self.base, directory=True)
        for name in ('records', 'generations', 'operations'):
            secure(self.base / name, directory=True)

    @contextmanager
    def lock(self):
        path = self.base / 'transaction.lock'
        descriptor = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        try:
            info = os.fstat(descriptor)
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != ROOT_UID or
                    info.st_nlink != 1 or stat.S_IMODE(info.st_mode) != 0o600):
                raise transaction.TransactionError('router transaction lock is unsafe')
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise transaction.TransactionError('another local router command holds the transaction lock') from error
            yield
        finally:
            os.close(descriptor)

    def operation(self, identity):
        if not generations.matches('[0-9a-f]{24}', identity):
            raise transaction.TransactionError('router operation selector is invalid')
        return secure(self.base / 'operations' / identity, directory=True)

    def generation(self, checksum):
        if not generations.matches('[0-9a-f]{64}', checksum):
            raise transaction.TransactionError('router generation selector is invalid')
        value = generations.generation(read(self.base / 'records' / (checksum + '.json')), self.box)
        if value['record_sha256'] != checksum:
            raise transaction.TransactionError('router generation differs from its protected filename')
        return value

    def accepted(self):
        value = assignment(read(self.base / 'accepted.json'), self.box)
        for checksum in (value['current_sha256'], value['previous_sha256']):
            if checksum is not None:
                self.generation(checksum)
        if value['policy_sha256'] == INITIAL_AUTHORITY_SHA256:
            if value['previous_sha256'] is not None:
                raise transaction.TransactionError('first accepted router cannot have a previous generation')
            installation = self.installation()
            initial_installation.matches_generation(
                installation, self.generation(value['current_sha256']), self.box)
            if value['evidence_sha256'] != installation['record_sha256']:
                raise transaction.TransactionError('first accepted router differs from its verified installation record')
        return value

    def installation(self):
        path = self.base / 'installation.json'
        if not path.exists() and not path.is_symlink():
            return None
        return initial_installation.validate(read(path), self.box)

    def record_installation(self, value):
        """Store one exact first-install stage while the caller holds the lock."""
        initial_installation.validate(value, self.box)
        if self.pending() is not None or (self.base / 'accepted.json').exists() or (
                self.base / 'accepted.json').is_symlink():
            raise transaction.TransactionError('first installation cannot replace an accepted or pending router')
        current = self.installation()
        if current is None:
            if value['stage'] != 'allocated':
                raise transaction.TransactionError('first installation must record its allocation before preparation')
        else:
            initial_installation.advance(current, value, self.box)
        write(self.base / 'installation.json', value)
        return value

    def adopt(self, record):
        """Publish one supervised legacy source after the caller's native checks.

        The caller holds the local transaction lock and validates the short
        controller grant. This method keeps the generation durable before the
        accepted pointer. It never formats or changes the running router.
        """
        generations.generation(record, self.box)
        if record['origin'] != 'legacy' or self.pending() is not None:
            raise transaction.TransactionError('baseline adoption requires one legacy router and no pending operation')
        accepted_path = self.base / 'accepted.json'
        if accepted_path.exists() or accepted_path.is_symlink():
            raise transaction.TransactionError('router already has an accepted generation')
        path = self.base / 'records' / (record['record_sha256'] + '.json')
        if path.exists() or path.is_symlink():
            if read(path) != record:
                raise transaction.TransactionError('legacy router record path contains different evidence')
        else:
            write(path, record)
        accepted = assignment(generations.seal({
            'kind':'klokast.router-assignment.v1', 'box':self.box, 'role':'router',
            'current_sha256':record['record_sha256'], 'previous_sha256':None,
            'operation_id':record['generation_id'], 'engine_commit':record['engine_commit'],
            'policy_sha256':BASELINE_AUTHORITY_SHA256,
            'evidence_sha256':record['evidence_sha256']}), self.box)
        write(accepted_path, accepted)
        return accepted

    def accept_initial(self, record):
        """Publish a fully verified first template generation under bootstrap authority.

        The caller holds the local lock and proves the installation grant,
        disk, enrollment, runtime state, and service checks before this write.
        """
        generations.generation(record, self.box)
        if record['origin'] != 'template' or self.pending() is not None:
            raise transaction.TransactionError('initial acceptance requires one template and no pending replacement')
        installation = self.installation()
        initial_installation.matches_generation(installation, record, self.box)
        target = assignment(generations.seal({
            'kind':'klokast.router-assignment.v1', 'box':self.box, 'role':'router',
            'current_sha256':record['record_sha256'], 'previous_sha256':None,
            'operation_id':record['generation_id'], 'engine_commit':record['engine_commit'],
            'policy_sha256':INITIAL_AUTHORITY_SHA256,
            'evidence_sha256':installation['record_sha256']}), self.box)
        accepted_path = self.base / 'accepted.json'
        if accepted_path.exists() or accepted_path.is_symlink():
            if self.accepted() == target:
                return target
            raise transaction.TransactionError('router already has a different accepted generation')
        path = self.base / 'records' / (record['record_sha256'] + '.json')
        if path.exists() or path.is_symlink():
            if self.generation(record['record_sha256']) != record:
                raise transaction.TransactionError('initial generation record path contains different evidence')
        else:
            write(path, record)
        write(accepted_path, target)
        return target

    def pending(self):
        path = self.base / 'pending.json'
        if not path.exists() and not path.is_symlink():
            return None
        value = read(path)
        if not isinstance(value, dict) or not isinstance(value.get('request'), dict):
            raise transaction.TransactionError('router pending record has no exact request')
        transaction.validate_pending(value, value['request'])
        if value['request']['box'] != self.box:
            raise transaction.TransactionError('router pending record targets a different box')
        return value

    def persist(self, pending):
        transaction.validate_pending(pending, pending['request'])
        if pending['request']['box'] != self.box:
            raise transaction.TransactionError('cannot persist another box router operation')
        current = self.pending()
        if current is not None and current['request'] != pending['request']:
            raise transaction.TransactionError('another router operation is still pending')
        # Keep history durable before publishing the pointer used at boot.
        work = self.operation(pending['request']['operation_id'])
        write(work / 'latest.json', pending)
        write(self.base / 'pending.json', pending)

    def committed(self, request):
        current = self.accepted()
        if current['record_sha256'] == request['accepted_sha256'] and current['current_sha256'] == request['old_sha256']:
            return False
        if (current['current_sha256'] == request['candidate_sha256'] and
                current['previous_sha256'] == request['old_sha256'] and
                all(current[k] == request[k] for k in ('operation_id', 'engine_commit', 'policy_sha256'))):
            return True
        raise transaction.TransactionError('accepted router assignment differs from both recorded recovery choices')

    def commit(self, request, evidence_sha256):
        target = accepted_candidate(request, evidence_sha256)
        if self.committed(request):
            if self.accepted() != target:
                raise transaction.TransactionError('candidate acceptance evidence changed after commitment')
            return
        write(self.base / 'accepted.json', target)

    def finish(self, request, outcome):
        pending = self.pending()
        if (pending is None or pending['request'] != request or outcome not in ('accepted', 'rolled-back') or
                pending['phase'] != outcome or self.committed(request) != (outcome == 'accepted')):
            raise transaction.TransactionError('router completion contradicts its durable assignment or pending phase')
        write(self.operation(request['operation_id']) / 'complete.json', pending)
        (self.base / 'pending.json').unlink()
        syncdir(self.base)
