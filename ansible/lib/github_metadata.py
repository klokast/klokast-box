"""Request public release metadata from the installed controller credential broker."""
import json
import os
import re
import subprocess

BROKER = '/usr/local/sbin/ksa-github-metadata'
MAX_BYTES = 4 * 1024 * 1024


def fetch(name):
    if name != 'codex-stable':
        raise RuntimeError('unsupported GitHub metadata source')
    argv = ['/usr/bin/doas', '-n', BROKER, 'fetch', '--source', name]
    proxy = os.environ.get('https_proxy') or os.environ.get('HTTPS_PROXY')
    if proxy:
        match = re.fullmatch(r'http://127\.0\.0\.1:([0-9]{1,5})', proxy)
        if not match or not 1 <= int(match[1]) <= 65535:
            raise RuntimeError('controller metadata downloads require direct access or the existing with-proxy loopback transport')
        argv += ['--proxy-port', match[1]]
    result = subprocess.run(argv, capture_output=True, text=True, timeout=120, check=False)
    if result.returncode:
        raise RuntimeError(result.stderr.strip()[-800:] or 'controller GitHub metadata broker failed')
    if len(result.stdout.encode()) > MAX_BYTES:
        raise RuntimeError('controller GitHub metadata response exceeds its size limit')
    value = json.loads(result.stdout)
    if not isinstance(value, dict):
        raise RuntimeError('controller GitHub metadata response is invalid')
    return value
