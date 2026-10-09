"""Read the root-owned Platform client setting for non-login download jobs."""
import json
from pathlib import Path
import stat

PATH = Path('/etc/klokast/vpn-egress.json')
KEYS = {'http_proxy', 'https_proxy', 'HTTP_PROXY', 'HTTPS_PROXY', 'no_proxy', 'NO_PROXY'}


def environment(path=PATH):
    try:
        info = path.lstat()
    except FileNotFoundError:
        return {}
    if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
        raise RuntimeError('Platform proxy setting must be a root-owned regular file without group or other write access')
    value = json.loads(path.read_text())
    if not isinstance(value, dict) or set(value) != KEYS or any(not isinstance(v, str) for v in value.values()):
        raise RuntimeError('Platform proxy environment has invalid fields')
    if any(value[key] != 'http://192.168.200.41:7890' for key in KEYS - {'no_proxy', 'NO_PROXY'}):
        raise RuntimeError('Platform proxy address differs from the reserved gateway')
    return value
