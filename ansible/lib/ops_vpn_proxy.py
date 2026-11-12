"""Run public downloads through a same-box VPN over existing Tailscale SSH.

Controller entry commands opt in with ENABLED. Each download then tries the
declared gateway first and direct access second. DNS, Tailscale, SSH and
recovery paths never use this transport.
"""
import contextlib
import json
import os
from pathlib import Path
import pwd
import signal
import socket
import subprocess
import sys
import time
import urllib.request

ENABLED = False
PROXY_KEYS = ('http_proxy', 'https_proxy', 'HTTP_PROXY', 'HTTPS_PROXY', 'no_proxy', 'NO_PROXY')
ACTIVE_PORT = None
UNDECLARED = set()  # Boxes whose controller is not a declared client, per process.


class NotDeclared(RuntimeError):
    pass


def authorize(box, *, instance=None):
    import platform_source as source
    if pwd.getpwuid(os.geteuid()).pw_name != 'smith' or socket.gethostname().split('.')[0] != box + '-ops':
        raise RuntimeError('with-proxy must run as smith on the selected box controller')
    source.require_local_image(box)
    instance = Path(instance or source.INSTANCE)
    if subprocess.check_output(['git', '-C', str(instance), 'status', '--porcelain'], text=True).strip():
        raise RuntimeError('commit the local Instance before selecting optional VPN download permission')
    projection = json.loads(subprocess.check_output([str(source.BINARY), 'registry', '--instance', str(instance), '--json'], text=True))
    if projection.get('valid') is not True: raise RuntimeError('Instance validation failed')
    config = projection['projection']['registry']['boxes'][box]
    if box + '-ops' not in config.get('vpn_egress', {}).get('clients', []):
        raise NotDeclared('this controller is not declared as an optional VPN download client')
    return box + '-vpn-egress'


def proxy_environment(port, original=None):
    result = {k: v for k, v in (os.environ if original is None else original).items()
              if k.lower() not in ('http_proxy', 'https_proxy', 'all_proxy', 'no_proxy')}
    for key in ('http_proxy', 'https_proxy', 'HTTP_PROXY', 'HTTPS_PROXY'):
        result[key] = 'http://127.0.0.1:' + str(port)
    result.update(no_proxy='localhost,127.0.0.1,::1', NO_PROXY='localhost,127.0.0.1,::1')
    return result


def stop(process):
    if process is None: return
    try: os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError: pass
    try: process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        try: os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError: pass
        process.wait()


@contextlib.contextmanager
def tunnel(box):
    """Open a loopback-only forward to the declared same-box gateway."""
    target = authorize(box)
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0)); port = listener.getsockname()[1]
    process = None
    try:
        # tailscale ssh verifies the SSH host key from the authenticated node
        # record and invokes OpenSSH. Forward only this loopback listener.
        process = subprocess.Popen(['tailscale', 'ssh', 'neo@' + target, '-N', '-T',
            '-o', 'ExitOnForwardFailure=yes', '-o', 'ServerAliveInterval=15',
            '-o', 'ServerAliveCountMax=2', '-L', '127.0.0.1:' + str(port) + ':192.168.200.41:7890'],
            stdin=subprocess.DEVNULL, start_new_session=True)
        deadline = time.monotonic() + 30
        while True:
            if process.poll() is not None: raise RuntimeError('VPN SSH tunnel failed; no command was started')
            try:
                with socket.create_connection(('127.0.0.1', port), timeout=0.5): break
            except OSError:
                if time.monotonic() >= deadline: raise RuntimeError('VPN SSH tunnel was not ready within 30 seconds')
                time.sleep(0.1)
        yield port, process
    finally:
        stop(process)


def execute(box, command):
    if not command: raise ValueError('with-proxy requires -- COMMAND [ARG ...]')
    child = None
    def interrupted(signum, frame): raise KeyboardInterrupt
    previous = {sig: signal.signal(sig, interrupted) for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP)}
    try:
        with tunnel(box) as (port, process):
            try:
                child = subprocess.Popen(command, env=proxy_environment(port), start_new_session=True)
                while child.poll() is None:
                    if process.poll() is not None: raise RuntimeError('VPN SSH tunnel stopped; the download command was stopped')
                    time.sleep(0.1)
                return child.returncode
            finally:
                stop(child)
    finally:
        for sig, handler in previous.items(): signal.signal(sig, handler)


def tunnel_environment():
    """Proxy variables for child downloads, only while a public download tunnel is open."""
    if ACTIVE_PORT is None: return {}
    return {k: v for k, v in proxy_environment(ACTIVE_PORT, {}).items() if k in PROXY_KEYS}


def tunnel_proxies():
    """urllib proxy mapping for the open tunnel; empty means direct access."""
    if ACTIVE_PORT is None: return {}
    return {'http': 'http://127.0.0.1:' + str(ACTIVE_PORT), 'https': 'http://127.0.0.1:' + str(ACTIVE_PORT)}


def urlopen(request, timeout):
    """Open a URL with the proxy state of this moment.

    urllib.request.urlopen keeps its first opener for the process, so a direct
    retry after a failed tunnel would still dial the closed tunnel port.
    """
    return urllib.request.build_opener(urllib.request.ProxyHandler(tunnel_proxies() or None)).open(request, timeout=timeout)


def public_download(action, description):
    """Run one repeatable public download through the declared VPN, then directly.

    A controller that is not declared in Instance vpn-egress.clients, a nested
    call, or an explicitly proxied command uses the current path only.
    """
    global ACTIVE_PORT
    host = socket.gethostname().split('.')[0]
    box = host[:-4]
    if (not ENABLED or ACTIVE_PORT is not None or not host.endswith('-ops') or box in UNDECLARED or
            os.environ.get('https_proxy') or os.environ.get('HTTPS_PROXY')):
        return action()
    try:
        with tunnel(box) as (port, _process):
            saved = {k: os.environ.get(k) for k in PROXY_KEYS}
            ACTIVE_PORT = port
            os.environ.update(tunnel_environment())
            try:
                return action()
            finally:
                ACTIVE_PORT = None
                for key, value in saved.items():
                    if value is None: os.environ.pop(key, None)
                    else: os.environ[key] = value
    except NotDeclared:
        UNDECLARED.add(box)
    except Exception as error:
        print('platform: ' + description + ' through ' + box + '-vpn-egress failed (' +
              (str(error) or type(error).__name__)[-300:] + '); retry with direct access', file=sys.stderr, flush=True)
    return action()
