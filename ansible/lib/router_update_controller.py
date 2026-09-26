"""Bounded controller transport for router evidence, with no shared-VM executor."""
import json
import os
from pathlib import Path
import pwd
import re
import signal
import socket
import subprocess
import tempfile
import contextlib
import fcntl
import stat

from platform_updates import UpdateError, canonical, unique_object


@contextlib.contextmanager
def installation_lock():
    directory = Path('/var/lib/klokast/updates')
    path = directory / 'operation.lock'
    info = directory.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or stat.S_IMODE(info.st_mode) != 0o755:
        raise UpdateError('Platform installation lock directory is absent or unsafe')
    descriptor = os.open(path, os.O_RDWR | os.O_NOFOLLOW)
    try:
        info = os.fstat(descriptor)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_gid != os.getegid() or
                stat.S_IMODE(info.st_mode) != 0o660 or info.st_nlink != 1):
            raise UpdateError('Platform installation lock file is unsafe')
        inherited = os.environ.get('KLOKAST_INSTALLATION_LOCK_FD')
        if inherited is not None:
            if inherited != '9' or (os.fstat(9).st_dev, os.fstat(9).st_ino) != (info.st_dev, info.st_ino):
                raise UpdateError('inherited installation lock descriptor differs')
            # Reuse the same open file description. Never unlock a parent lock.
            reused = os.dup(9)
            os.close(descriptor)
            descriptor = reused
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise UpdateError('another update or provisioning operation holds the installation lock') from error
        yield
    finally:
        os.close(descriptor)


def load(path):
    path = Path(path)
    if path.is_symlink() or not path.is_file() or not 0 < path.stat().st_size <= 16 * 1024 * 1024:
        raise UpdateError('router evidence file is absent, unsafe, or too large')
    return json.loads(path.read_text(), object_pairs_hook=unique_object)


def write(path, value):
    path = Path(path)
    with tempfile.NamedTemporaryFile(mode='w', dir=path.parent, prefix='.router-', delete=False) as stream:
        temporary = Path(stream.name)
        stream.write(canonical(value) + '\n')
        stream.flush()
        os.fsync(stream.fileno())
    try:
        temporary.replace(path)
        descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    finally:
        temporary.unlink(missing_ok=True)


def command(argv, *, timeout=120, log=None):
    with subprocess.Popen([str(v) for v in argv], stdin=subprocess.DEVNULL,
                          stdout=log or subprocess.PIPE, stderr=log or subprocess.PIPE,
                          text=True, start_new_session=True) as process:
        try:
            output, _ = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired as error:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
            raise UpdateError('router inspection timed out; inspect the controller operation log') from error
        if process.returncode:
            raise UpdateError('router inspection command failed: ' + str(argv[0]) + '; inspect the controller operation log')
        return output or ''


def require_controller():
    if pwd.getpwuid(os.geteuid()).pw_name != 'smith':
        raise UpdateError('run router update commands on the active controller as smith')
    status = json.loads(command(['/usr/local/sbin/klokast-controller-guard', '--status', '--json', '--require-active']),
                        object_pairs_hook=unique_object)
    local = socket.gethostname().split('.')[0]
    if (status.get('active') is not True or status.get('configured') is not True or
            status.get('role') != 'active' or status.get('hostname') != local or
            not local.endswith('-ops') or local.startswith(('vultr-', 'hetzner-'))):
        raise UpdateError('router inspection requires the active box controller')
    return status


def approved_engine():
    """Return only the signed policy reader's approved engine, if available."""
    try:
        value = json.loads(command(['/usr/bin/doas', '/usr/local/sbin/ksa-apply',
                                    'vm-update-policy', 'source-status'], timeout=30),
                           object_pairs_hook=unique_object)
    except (UpdateError, OSError, ValueError, TypeError):
        return None
    if (not isinstance(value, dict) or value.get('kind') != 'klokast.vm-update-policy-source.v1' or
            not isinstance(value.get('engine_commit'), str) or
            not re.fullmatch('[0-9a-f]{40}', value['engine_commit'])):
        return None
    return value['engine_commit']
