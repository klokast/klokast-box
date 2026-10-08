"""Resolve and verify the current stable native Codex package, without identity."""
import hashlib
import json
from pathlib import Path
import re
import urllib.request

from platform_updates import UpdateError

METADATA = 'https://api.github.com/repos/openai/codex/releases/latest'
TARGET = 'x86_64-unknown-linux-musl'
ASSET = 'codex-package-' + TARGET + '.tar.gz'
MAX_BYTES = 512 * 1024 * 1024


def selection(release):
    tag = release.get('tag_name', '')
    if (release.get('draft') is not False or release.get('prerelease') is not False or
            not re.fullmatch(r'rust-v[0-9]+\.[0-9]+\.[0-9]+', tag)):
        raise UpdateError('Codex metadata does not select a stable release')
    assets = [v for v in release.get('assets', []) if v.get('name') == ASSET]
    url = 'https://github.com/openai/codex/releases/download/' + tag + '/' + ASSET
    if len(assets) != 1:
        raise UpdateError('Codex stable release has no unique native musl package')
    asset = assets[0]
    if (asset.get('browser_download_url') != url or
            not re.fullmatch(r'sha256:[0-9a-f]{64}', asset.get('digest', '')) or
            type(asset.get('size')) is not int or not 0 < asset['size'] <= MAX_BYTES):
        raise UpdateError('Codex native artifact lacks a bounded verified identity')
    return {'version': tag[6:], 'target': TARGET, 'url': url,
            'file': 'codex-package.tar.gz', 'sha256': asset['digest'][7:], 'bytes': asset['size']}


def verify(directory, record):
    if (not isinstance(record, dict) or set(record) != {'version', 'target', 'url', 'file', 'sha256', 'bytes'} or
            not re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+', record.get('version', '')) or
            record.get('target') != TARGET or record.get('file') != 'codex-package.tar.gz' or
            record.get('url') != 'https://github.com/openai/codex/releases/download/rust-v' + record['version'] + '/' + ASSET or
            not re.fullmatch(r'[0-9a-f]{64}', record.get('sha256', '')) or
            type(record.get('bytes')) is not int or not 0 < record['bytes'] <= MAX_BYTES):
        raise UpdateError('Codex artifact record is invalid')
    path = Path(directory) / record['file']
    if path.is_symlink() or not path.is_file() or path.stat().st_size != record['bytes']:
        raise UpdateError('Codex artifact is absent or changed size')
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    if digest.hexdigest() != record['sha256']:
        raise UpdateError('Codex artifact checksum failed')


def resolve(directory):
    request = urllib.request.Request(METADATA, headers={'User-Agent': 'Klokast-image-builder'})
    with urllib.request.urlopen(request, timeout=30) as stream:
        content = stream.read(4 * 1024 * 1024 + 1)
    if len(content) > 4 * 1024 * 1024:
        raise UpdateError('Codex release metadata exceeds its limit')
    record = selection(json.loads(content))
    path = Path(directory) / record['file']
    try:
        with urllib.request.urlopen(record['url'], timeout=30) as source, path.open('xb') as output:
            total = 0
            while chunk := source.read(1024 * 1024):
                total += len(chunk)
                if total > record['bytes']:
                    raise UpdateError('Codex download exceeds its declared size')
                output.write(chunk)
        verify(directory, record)
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    return record
