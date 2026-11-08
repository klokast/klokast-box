"""Resolve and verify current stable Claude Code on its controller.

Anthropic publishes Claude Code as an APK repository with a signed index. The
package itself is unsigned; native APK authenticates it against that index
with the pinned key below. The image extracts only the binary, so the package
never enters the Alpine package database or its update evidence.
"""
import hashlib
from pathlib import Path
import re
import subprocess
import tempfile

from platform_updates import UpdateError

REPOSITORY = 'https://downloads.claude.ai/claude-code/apk/stable'
KEY_NAME = 'claude-code.rsa.pub'
KEY = b'''-----BEGIN PUBLIC KEY-----
MIICIjANBgkqhkiG9w0BAQEFAAOCAg8AMIICCgKCAgEAp28rSV5I8HmK8CK9GixB
UZR/gtJxeOCsRXO4EJiej40jzBmQA3cWXGosVO82ZfFsRKVTtMC5iB/HH9sxjncr
mYNWGroJNbx29m/FgYQBgkCXT4AfFl6rnnXqRGLZOerj/4AqE4yQ1GZbhBgR55Z7
ro0ieKK8RHYUspBKAFHyWRhCCz6THW6YRbf0p/hG/08TOY6Sj3cJ7/AEoTRf9ZmV
NX1k0KvbUSiVGpGY9OIHWgxRJUF2pArU4o/hk+sqGAgEUh8Bjvjwvz6+quLXPg+y
0Y8Ugb1Fg6BUppam/zydYY/Q/+yNjnuF154gD1jEeeir8R5czs6zUHSbo2yXUpAs
IdWYo5End8vGsluVmFExnUWm/fTVMGoM5Wm3v1VRepMydEnJ+atz4oQdmPQcKNAi
p5GJO2uyk++xFr9CpKvlR5jral92toYV/m+mur3va8ydamWBo/qG7/wt0sdS81Iw
H6lcu0SQ39rgKD+bdoPLv05EqVMYTFRI2QZEsWGYTMs0DOrfCIJFH50qyD0x4sWw
1gEWeG3jDgY8cj2StZz+zjqzUd05CibcCzEAGm1EQg5y9D40tIsAU1OI7bpgQ9V0
lC8lrqE7zJY66UK9Z1daA8jrdDi6migNjHFrXfT3V4QvMthCIO05q05SS3x2G3Zp
IgmI+CePUPB1pDf+lhPkU1MCAwEAAQ==
-----END PUBLIC KEY-----
'''
KEY_SHA256 = '395759c1f7449ef4cdef305a42e820f3c766d6090d142634ebdb049f113168b6'
VERSION = re.compile(r'([0-9]+)\.([0-9]+)\.([0-9]+)-r[0-9]+')
MAX_BYTES = 512 * 1024 * 1024
FIELDS = {'version', 'repository', 'file', 'bytes', 'sha256', 'index_sha256'}


def version(value):
    """Upstream release order; the APK release suffix is packaging only."""
    match = VERSION.fullmatch(value) or re.fullmatch(r'([0-9]+)\.([0-9]+)\.([0-9]+)', value)
    if not match:
        raise UpdateError('unsupported Claude Code version: ' + value)
    return tuple(map(int, match.groups()))


def sha256(path):
    value = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(chunk)
    return value.hexdigest()


class Resolver:
    """A fresh APK root that trusts only the pinned Claude Code index key."""

    def __enter__(self):
        if hashlib.sha256(KEY).hexdigest() != KEY_SHA256:
            raise UpdateError('pinned Claude Code signing key differs')
        self.temporary = tempfile.TemporaryDirectory(prefix='claude-apk-')
        root = Path(self.temporary.name)
        for path in (root / 'root/etc/apk', root / 'root/lib/apk/db', root / 'cache', root / 'keys'):
            path.mkdir(parents=True, mode=0o700)
        (root / 'root/lib/apk/db/installed').touch()
        (root / 'root/etc/apk/world').touch()
        (root / 'keys' / KEY_NAME).write_bytes(KEY)
        (root / 'repositories').write_text(REPOSITORY + '\n')
        self.cache = root / 'cache'
        self.options = ['/sbin/apk', '--root', root / 'root', '--arch', 'x86_64', '--keys-dir', root / 'keys',
                        '--repositories-file', root / 'repositories', '--cache-dir', self.cache, '--no-progress']
        self.apk('update', timeout=180)
        return self

    def apk(self, *args, timeout):
        try:
            result = subprocess.run([str(v) for v in [*self.options, *args]], stdin=subprocess.DEVNULL,
                                    capture_output=True, text=True, timeout=timeout,
                                    env={'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'LANG': 'C', 'LC_ALL': 'C'})
        except (OSError, subprocess.TimeoutExpired) as error:
            raise UpdateError('Claude Code APK repository is unavailable or timed out') from error
        if result.returncode:
            # Repository text is untrusted. Report only the failed action.
            raise UpdateError('Claude Code APK ' + args[0] + ' failed (exit ' + str(result.returncode) + ')')
        return result.stdout

    def index(self):
        indexes = sorted(self.cache.glob('APKINDEX.*.tar.gz'))
        if len(indexes) != 1:
            raise UpdateError('Claude Code repository has no unique authenticated index')
        return indexes[0]

    def latest(self):
        lines = self.apk('search', '--exact', 'claude-code', timeout=60).split()
        if len(lines) != 1 or not lines[0].startswith('claude-code-'):
            raise UpdateError('Claude Code repository does not select one stable package')
        value = lines[0][len('claude-code-'):]
        version(value)
        if not VERSION.fullmatch(value):
            raise UpdateError('Claude Code stable package version is invalid')
        return value

    def __exit__(self, *args):
        self.temporary.cleanup()


def current_version():
    with Resolver() as resolver:
        return resolver.latest()


def resolve(directory):
    """Freeze the stable package and its signed index under directory/claude."""
    import vm_template_inputs
    output = Path(directory) / 'claude'
    output.mkdir(mode=0o700)
    with Resolver() as resolver:
        selected = resolver.latest()
        # Native fetch checks the download against the signed index checksum.
        resolver.apk('fetch', '--output', output, 'claude-code', timeout=1800)
        (output / 'APKINDEX.tar.gz').write_bytes(resolver.index().read_bytes())
    (output / KEY_NAME).write_bytes(KEY)
    # The same unchanged index selected both the version and the download.
    package = output / ('claude-code-' + selected + '.apk')
    identity = vm_template_inputs.read_package(package)
    if (identity['name'] != 'claude-code' or identity['version'] != selected or
            identity['architecture'] != 'x86_64' or not 0 < identity['bytes'] <= MAX_BYTES):
        raise UpdateError('Claude Code package identity differs from its signed index')
    record = {'version': selected, 'repository': REPOSITORY, 'file': 'claude/' + package.name,
              'bytes': identity['bytes'], 'sha256': identity['sha256'],
              'index_sha256': sha256(output / 'APKINDEX.tar.gz')}
    verify(directory, record)
    return record


def files(record):
    return [record['file'], 'claude/APKINDEX.tar.gz', 'claude/' + KEY_NAME]


def verify(directory, record):
    if (not isinstance(record, dict) or set(record) != FIELDS or
            not isinstance(record.get('version'), str) or not VERSION.fullmatch(record['version']) or
            record.get('repository') != REPOSITORY or
            record.get('file') != 'claude/claude-code-' + record['version'] + '.apk' or
            not all(isinstance(record.get(k), str) and re.fullmatch(r'[0-9a-f]{64}', record[k])
                    for k in ('sha256', 'index_sha256')) or
            type(record.get('bytes')) is not int or not 0 < record['bytes'] <= MAX_BYTES):
        raise UpdateError('Claude Code artifact record is invalid')
    directory = Path(directory)
    output = directory / 'claude'
    if output.is_symlink() or not output.is_dir() or {p.name for p in output.iterdir()} != {
            Path(v).name for v in files(record)}:
        raise UpdateError('Claude Code artifact set changed')
    if any((directory / v).is_symlink() or not (directory / v).is_file() for v in files(record)):
        raise UpdateError('Claude Code artifact is absent or unsafe')
    if (directory / record['file']).stat().st_size != record['bytes'] or sha256(directory / record['file']) != record['sha256']:
        raise UpdateError('Claude Code package checksum failed')
    if sha256(output / 'APKINDEX.tar.gz') != record['index_sha256']:
        raise UpdateError('Claude Code signed index changed')
    if (output / KEY_NAME).read_bytes() != KEY:
        raise UpdateError('Claude Code signing key differs from the pinned key')
