"""Shared locking and durable records for controller network operations."""
import contextlib
import fcntl
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import time
import platform_source as source

LOCK = Path('/var/lib/klokast/updates/operation.lock')
# The bound of the recent stages in a progress file.
PROGRESS_STAGES = 100


def write(path, value):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode='w', prefix='.record-', dir=path.parent, delete=False) as stream:
        temporary = Path(stream.name)
        stream.write(json.dumps(value, sort_keys=True, separators=(',', ':')) + '\n')
        stream.flush()
        os.fsync(stream.fileno())
    try:
        temporary.replace(path)
        fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try: os.fsync(fd)
        finally: os.close(fd)
    finally:
        temporary.unlink(missing_ok=True)


def progress(text):
    """Publish the current stage when the calling job names a file in KLOKAST_PROGRESS.

    The file also keeps the recent stages, so a client that polls sees the short ones too.
    """
    path = os.environ.get('KLOKAST_PROGRESS')
    if not path: return
    print('progress: ' + text, file=sys.stderr, flush=True)
    now = time.time()
    try:
        try: stages = json.loads(Path(path).read_text()).get('stages', [])
        except (FileNotFoundError, ValueError, AttributeError): stages = []
        write(Path(path), {'progress': text, 'updated_at': now, 'stages': (stages + [[now, text]])[-PROGRESS_STAGES:]})
    except OSError as error:
        # Progress is only a display aid; it must never stop the operation.
        print('progress: cannot publish the stage to ' + path + ': ' + str(error), file=sys.stderr, flush=True)


@contextlib.contextmanager
def operation_lock():
    info = LOCK.parent.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
        raise source.SourceError('installation lock directory is unsafe')
    fd = os.open(LOCK, os.O_RDWR | os.O_NOFOLLOW)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_nlink != 1:
            raise source.SourceError('installation lock is unsafe')
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        os.close(fd)
