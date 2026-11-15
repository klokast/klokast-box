"""Select public Alpine transports once; never change package trust or versions."""
import concurrent.futures
import datetime as dt
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import tempfile
import time

ORIGIN = 'https://dl-cdn.alpinelinux.org/alpine'
CATALOG = 'https://mirrors.alpinelinux.org/mirrors.json'
# Official mirrors in the form that host and container repository rewrites use.
MIRROR = re.compile(r'https://[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+/alpine')
SAMPLES = 8
# The first gzip member of an index holds its Alpine signature (under 1 KiB),
# so the leading bytes identify the signed index version.
HEAD = 4096
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


def mirror_url(value):
    return isinstance(value, str) and MIRROR.fullmatch(value) is not None


def repositories(mirror, branch):
    if not mirror_url(mirror) or not isinstance(branch, str) or not re.fullmatch(r'v[0-9]+\.[0-9]+', branch):
        raise RuntimeError('Alpine repository requires an https://HOST/alpine mirror and a fixed release branch')
    return [mirror + '/' + branch + '/' + name for name in ('main', 'community')]


def valid_repositories(values, branch):
    """Accept one mirror for both repositories of the fixed branch."""
    if not isinstance(values, list) or not values or not isinstance(values[0], str):
        return False
    try: return values == repositories(values[0].removesuffix('/' + str(branch) + '/main'), branch)
    except RuntimeError: return False


def validate(value, box=None):
    if (not isinstance(value, dict) or value.get('kind') != KIND or value.get('policy') != POLICY or
            not re.fullmatch('[a-z0-9][a-z0-9-]{0,30}', value.get('box', '')) or
            (box is not None and value['box'] != box) or not mirror_url(value.get('primary')) or
            not mirror_url(value.get('fallback')) or value['primary'] == value['fallback'] or
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


def download(url, output, seconds, minimum, size, head=False):
    """One direct HTTPS download with time, speed, and size bounds."""
    result = subprocess.run(['curl', '--fail', '--silent', '--show-error', '--noproxy', '*',
        '--proto', '=https', '--connect-timeout', '4', '--max-time', str(seconds),
        '--speed-time', '5', '--speed-limit', str(minimum), '--max-filesize', str(size),
        *(['--range', f'0-{HEAD - 1}'] if head else []),
        '--output', str(output), url], env=direct_environment(), capture_output=True, timeout=seconds + 3)
    if result.returncode or not output.is_file():
        raise RuntimeError(f'{url} was not downloaded within limits: {result.stderr.decode(errors="replace").strip()}')


def catalog():
    """Return the official HTTPS mirrors; the index comparison decides which are current."""
    with tempfile.TemporaryDirectory(prefix='alpine-catalog-') as directory:
        output = Path(directory) / 'mirrors.json'
        download(CATALOG, output, 30, 1024, 1048576)
        try: entries = json.loads(output.read_text())
        except ValueError as error:
            raise RuntimeError('the official Alpine mirror list is not valid JSON') from error
    found = {ORIGIN}
    for entry in entries if isinstance(entries, list) else ():
        urls = entry.get('urls') if isinstance(entry, dict) else None
        for url in urls if isinstance(urls, list) else ():
            if isinstance(url, str) and mirror_url(url.rstrip('/')): found.add(url.rstrip('/'))
    if not 2 < len(found) <= 512:
        raise RuntimeError('the official Alpine mirror list has an unexpected number of HTTPS mirrors')
    return sorted(found)


def indexes(mirror, branch, directory, seconds, minimum, head=False):
    """Download both signed indexes and return their size and signature digests."""
    total, digests = 0, []
    for index, repository in enumerate(repositories(mirror, branch)):
        output = Path(directory) / str(index)
        download(repository + '/x86_64/APKINDEX.tar.gz', output, seconds, minimum, 4194304, head)
        data = output.read_bytes()
        if len(data) < HEAD or data[:2] != b'\x1f\x8b':
            raise RuntimeError('mirror returned a non-APK index response')
        total += len(data); digests.append(hashlib.sha256(data[:HEAD]).hexdigest())
    return total, digests


def reference(branch):
    """Identify the current indexes on the Alpine CDN with two small range requests.

    Full CDN transfers can stall from some countries; the first bytes still
    arrive. A mirror that copies these bytes onto old content fails APK
    signature verification instead of holding back updates without notice.
    """
    with tempfile.TemporaryDirectory(prefix='alpine-reference-') as directory:
        return indexes(ORIGIN, branch, directory, 30, 1, head=True)[1]


def first_byte(mirror, branch):
    """Rank the list with one small range request; None means no answer."""
    try:
        result = subprocess.run(['curl', '--fail', '--silent', '--noproxy', '*', '--proto', '=https',
            '--connect-timeout', '3', '--max-time', '5', '--range', '0-1023', '--output', '/dev/null',
            '--write-out', '%{time_starttransfer}', repositories(mirror, branch)[0] + '/x86_64/APKINDEX.tar.gz'],
            env=direct_environment(), capture_output=True, text=True, timeout=8)
        return float(result.stdout) if not result.returncode else None
    except (OSError, RuntimeError, ValueError, subprocess.TimeoutExpired):
        return None


def probe(mirror, branch):
    """Bounded HTTPS samples, not ICMP or an installed package transaction."""
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix='alpine-probe-') as directory:
        # Fetch indexes in full, with strict time/size bounds. A sample
        # cannot authorize a build: native APK verifies indexes again.
        total, digests = indexes(mirror, branch, directory, 12, 32768)
    elapsed = time.monotonic() - started
    return {'mirror': mirror, 'bytes': total, 'seconds': round(elapsed, 4),
            'bytes_per_second': round(total / elapsed), 'indexes': digests}


def measure(box, country='', branch='v3.23'):
    """Select from the official list by speed, among mirrors with current indexes.

    APK signatures reject changed packages from any mirror. A mirror can still
    serve an old signed index and so hold back security updates; such a mirror
    differs from the CDN index and is not selected. Country only marks the
    location that the measurement is for.
    """
    if not re.fullmatch('[a-z0-9][a-z0-9-]{0,30}', box) or not re.fullmatch('[A-Z]{2}|', country):
        raise RuntimeError('mirror selection requires a box name and an optional two-letter country')
    mirrors = catalog()
    # No timer or background process survives this explicit invocation.
    with concurrent.futures.ThreadPoolExecutor(max_workers=16) as pool:
        latency = dict(zip(mirrors, pool.map(lambda mirror: first_byte(mirror, branch), mirrors)))
    ordered = sorted((mirror for mirror in mirrors if latency[mirror] is not None), key=latency.get)[:SAMPLES]
    current = [reference(branch)]
    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        attempts = [(mirror, pool.submit(probe, mirror, branch)) for mirror in ordered]
        for mirror, attempt in attempts:
            try: results.append(attempt.result())
            except (OSError, RuntimeError, subprocess.TimeoutExpired):
                results.append({'mirror': mirror, 'unavailable': True})
            results[-1]['first_byte_seconds'] = round(latency[mirror], 4)
    # The CDN can publish a new index during the samples: accept both versions.
    current.append(reference(branch))
    for row in results:
        if not row.get('unavailable'): row['current'] = row.pop('indexes') in current
    ranked = sorted((row for row in results if row.get('current')), key=lambda row: -row['bytes_per_second'])
    if len(ranked) < 2:
        raise RuntimeError(f'fewer than two of the {len(ordered)} nearest official Alpine mirrors passed with current '
                           'indexes; the current selection stays. Refresh again later or inspect connectivity')
    return validate({'kind': KIND, 'policy': POLICY, 'box': box, 'country': country,
        'selected_at': dt.datetime.now(dt.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
        'primary': ranked[0]['mirror'], 'fallback': ranked[1]['mirror'], 'measurements': results})


def select(box, refresh=False, country=None, path=RECORD):
    """Return the saved choice, or measure again.

    None means that the caller has no Instance location. Any other country
    that differs from the recorded one makes the saved choice stale.
    """
    import socket
    import sys
    if socket.gethostname().split('.')[0] != box + '-ops':
        raise RuntimeError('select Alpine mirrors on the target box controller')
    require_storage(path.parent)
    with (path.parent / 'selection.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        current = read(box, path) if path.exists() else None
        if country is None: country = current['country'] if current else ''
        if not refresh:
            if current is None: return read(box, path)
            if current['country'] == country: return current
            print(f"alpine-mirrors: {box} location changed from {current['country'] or 'unknown'} to "
                  f"{country or 'unknown'} in Instance; measure the official mirrors again", file=sys.stderr, flush=True)
        branch = 'v' + '.'.join(Path('/etc/alpine-release').read_text().strip().split('.')[:2])
        value = measure(box, country, branch)
        publish(path, value)
        if current and current['primary'] != value['primary']:
            print(f"alpine-mirrors: {box} primary changed from {current['primary']} to {value['primary']}; "
                  f"apply it to the other {box} hosts with 66-alpine-mirrors.yml", file=sys.stderr, flush=True)
        return value


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--box', required=True)
    parser.add_argument('--country', help='Instance country; a change from the recorded country measures again')
    parser.add_argument('--refresh', action='store_true')
    parser.add_argument('--initialize', action='store_true')
    parser.add_argument('--measure-only', action='store_true')
    args = parser.parse_args()
    if args.measure_only:
        value = measure(args.box, args.country or '')
    else:
        value = select(args.box, args.refresh or (args.initialize and not RECORD.exists()), args.country)
    print(json.dumps(value, sort_keys=True))
