"""Select public Alpine transports once; never change package trust or versions."""
import concurrent.futures
import datetime as dt
import fcntl
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import tempfile
import time

MIRRORS = {
    'https://dl-cdn.alpinelinux.org/alpine': (),
    'https://mirrors.tuna.tsinghua.edu.cn/alpine': ('CN',),
    'https://mirrors.ustc.edu.cn/alpine': ('CN',),
}
POLICY = 1
ROOT = Path('/var/lib/klokast/download-sources')
RECORD = ROOT / 'alpine.json'
KIND = 'klokast.alpine-mirrors.v1'


def direct_environment():
    return {key: value for key, value in os.environ.items()
            if key.lower() not in ('http_proxy', 'https_proxy', 'all_proxy', 'ftp_proxy', 'no_proxy')}


def fetch_packages(argv, cwd, timeout, environment):
    """Bound one native fetch, including a continuously slow connection."""
    started = checked = time.monotonic()
    written = 0
    with subprocess.Popen([str(v) for v in argv], cwd=cwd, env=environment,
                          stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, text=True) as process:
        try:
            while True:
                try:
                    stdout, stderr = process.communicate(timeout=10)
                    return subprocess.CompletedProcess(argv, process.returncode, stdout, stderr)
                except subprocess.TimeoutExpired:
                    now = time.monotonic()
                    if now - started >= timeout:
                        raise subprocess.TimeoutExpired(argv, timeout)
                    if now - checked < 30: continue
                    # Native APK writes verified downloads. This counts output
                    # bytes without a second network request or packet capture.
                    counters = dict(line.split(':', 1) for line in Path(f'/proc/{process.pid}/io').read_text().splitlines())
                    current = int(counters['wchar'])
                    if (current - written) / (now - checked) < 32768:
                        raise subprocess.TimeoutExpired(argv, now - started)
                    written, checked = current, now
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate()


def repositories(mirror, branch):
    if mirror not in MIRRORS or not re.fullmatch(r'v[0-9]+\.[0-9]+', branch):
        raise RuntimeError('Alpine repository requires an approved mirror and a fixed release branch')
    return [mirror + '/' + branch + '/' + name for name in ('main', 'community')]


def valid_repositories(values, branch):
    return any(values == repositories(mirror, branch) for mirror in MIRRORS)


def validate(value, box=None):
    if (not isinstance(value, dict) or value.get('kind') != KIND or value.get('policy') != POLICY or
            not re.fullmatch('[a-z0-9][a-z0-9-]{0,30}', value.get('box', '')) or
            (box is not None and value['box'] != box) or value.get('primary') not in MIRRORS or
            value.get('fallback') not in MIRRORS or value['primary'] == value['fallback'] or
            not re.fullmatch('[A-Z]{2}|', value.get('country', '')) or
            not isinstance(value.get('measurements'), list)):
        raise RuntimeError('Alpine mirror record is invalid; refresh it on this box controller')
    try: dt.datetime.strptime(value.get('selected_at', ''), '%Y-%m-%dT%H:%M:%SZ')
    except (TypeError, ValueError) as error:
        raise RuntimeError('Alpine mirror selection time is invalid; refresh the record') from error
    return value


def require_storage(root=ROOT):
    # Refuse the root-disk fallback if the retained volume or bind is absent.
    target = Path('/srv/retained/download-sources')
    if not os.path.ismount('/srv/retained') or not os.path.ismount(root) or not target.samefile(root):
        raise RuntimeError('controller retained download-source volume is not mounted; run 66-ops-data.yml')


def read(box=None, path=RECORD):
    require_storage(path.parent)
    try:
        info = path.lstat()
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or
                info.st_mode & 0o077 or not 0 < info.st_size <= 65536):
            raise RuntimeError('Alpine mirror record has unsafe ownership, permissions, or size')
        return validate(json.loads(path.read_text()), box)
    except FileNotFoundError as error:
        raise RuntimeError('Alpine mirrors are not selected; run platform-update mirrors --box BOX --refresh') from error


def publish(path, value):
    validate(value)
    with tempfile.NamedTemporaryFile(mode='w', prefix='.alpine-', dir=path.parent, delete=False) as stream:
        temporary = Path(stream.name)
        json.dump(value, stream, sort_keys=True)
        stream.write('\n'); stream.flush(); os.fsync(stream.fileno())
    try:
        os.replace(temporary, path)
        descriptor = os.open(path.parent, os.O_DIRECTORY)
        try: os.fsync(descriptor)
        finally: os.close(descriptor)
    finally:
        temporary.unlink(missing_ok=True)


def probe(mirror, branch):
    """Bounded HTTPS samples, not ICMP or an installed package transaction."""
    started = time.monotonic()
    total = 0
    with tempfile.TemporaryDirectory(prefix='alpine-probe-') as directory:
        for index, repository in enumerate(repositories(mirror, branch)):
            output = Path(directory) / str(index)
            # Fetch indexes in full, with strict time/size bounds. A sample
            # cannot authorize a build: native APK verifies indexes again.
            result = subprocess.run(['curl', '--fail', '--silent', '--show-error', '--noproxy', '*',
                '--proto', '=https', '--connect-timeout', '4', '--max-time', '12',
                '--speed-time', '5', '--speed-limit', '32768', '--max-filesize', '4194304',
                '--output', str(output), repository + '/x86_64/APKINDEX.tar.gz'],
                env=direct_environment(), capture_output=True, timeout=15)
            if result.returncode or not output.is_file() or output.stat().st_size < 1024:
                raise RuntimeError('mirror did not provide the supported Alpine index within download limits')
            with output.open('rb') as stream:
                if stream.read(2) != b'\x1f\x8b':
                    raise RuntimeError('mirror returned a non-APK index response')
            total += output.stat().st_size
    elapsed = time.monotonic() - started
    return {'mirror': mirror, 'bytes': total, 'seconds': round(elapsed, 4),
            'bytes_per_second': round(total / elapsed)}


def measure(box, country='', branch='v3.23'):
    if not re.fullmatch('[a-z0-9][a-z0-9-]{0,30}', box) or not re.fullmatch('[A-Z]{2}|', country):
        raise RuntimeError('mirror selection requires a box name and an optional two-letter country')
    ordered = sorted(MIRRORS, key=lambda mirror: country not in MIRRORS[mirror])
    results = []
    # Three independent bounded probes complete within 30 seconds. No timer
    # or background process survives this explicit invocation.
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        attempts = [(mirror, pool.submit(probe, mirror, branch)) for mirror in ordered]
        for mirror, attempt in attempts:
            try: results.append(attempt.result())
            except (OSError, RuntimeError, subprocess.TimeoutExpired):
                results.append({'mirror': mirror, 'unavailable': True})
    ranked = sorted((row for row in results if not row.get('unavailable')),
                    key=lambda row: -row['bytes_per_second'])
    if len(ranked) < 2:
        raise RuntimeError('fewer than two approved Alpine mirrors passed; preserve the current selection and inspect connectivity')
    return validate({'kind': KIND, 'policy': POLICY, 'box': box, 'country': country,
        'selected_at': dt.datetime.now(dt.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
        'primary': ranked[0]['mirror'], 'fallback': ranked[1]['mirror'], 'measurements': results})


def select(box, refresh=False, country='', path=RECORD):
    import socket
    if socket.gethostname().split('.')[0] != box + '-ops':
        raise RuntimeError('select Alpine mirrors on the target box controller')
    require_storage(path.parent)
    with (path.parent / 'selection.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if not refresh: return read(box, path)
        branch = 'v' + '.'.join(Path('/etc/alpine-release').read_text().strip().split('.')[:2])
        value = measure(box, country, branch)
        publish(path, value)
        return value


def promote_fallback(value, path=RECORD):
    """Change only the record we actually used; a concurrent refresh wins."""
    with (path.parent / 'selection.lock').open('a') as lock:
        try: fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError: return
        if read(value['box'], path) != value: return
        publish(path, dict(value, primary=value['fallback'], fallback=value['primary']))


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--box', required=True)
    parser.add_argument('--country', default='')
    parser.add_argument('--refresh', action='store_true')
    parser.add_argument('--initialize', action='store_true')
    parser.add_argument('--measure-only', action='store_true')
    args = parser.parse_args()
    if args.measure_only:
        value = measure(args.box, args.country)
    else:
        value = select(args.box, args.refresh or (args.initialize and not RECORD.exists()), args.country)
    print(json.dumps(value, sort_keys=True))
