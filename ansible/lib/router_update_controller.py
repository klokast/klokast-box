"""Bounded controller transport for router evidence, with no shared-VM executor."""
import json
import os
from pathlib import Path
import pwd
import signal
import socket
import subprocess
import tempfile

from platform_updates import UpdateError, canonical, unique_object


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
