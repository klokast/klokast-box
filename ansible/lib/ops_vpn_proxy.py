"""Run one explicit download through a same-box VPN over existing Tailscale SSH."""
import json
import os
from pathlib import Path
import pwd
import signal
import socket
import subprocess
import time


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
        raise RuntimeError('this controller is not declared as an optional VPN download client')
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


def execute(box, command):
    if not command: raise ValueError('with-proxy requires -- COMMAND [ARG ...]')
    target = authorize(box)
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0)); port = listener.getsockname()[1]
    tunnel = child = None
    def interrupted(signum, frame): raise KeyboardInterrupt
    previous = {sig: signal.signal(sig, interrupted) for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP)}
    try:
        # tailscale ssh verifies the SSH host key from the authenticated node
        # record and invokes OpenSSH. Forward only this loopback listener.
        tunnel = subprocess.Popen(['tailscale', 'ssh', 'neo@' + target, '-N', '-T',
            '-o', 'ExitOnForwardFailure=yes', '-o', 'ServerAliveInterval=15',
            '-o', 'ServerAliveCountMax=2', '-L', '127.0.0.1:' + str(port) + ':192.168.200.41:7890'],
            stdin=subprocess.DEVNULL, start_new_session=True)
        deadline = time.monotonic() + 30
        while True:
            if tunnel.poll() is not None: raise RuntimeError('VPN SSH tunnel failed; no command was started')
            try:
                with socket.create_connection(('127.0.0.1', port), timeout=0.5): break
            except OSError:
                if time.monotonic() >= deadline: raise RuntimeError('VPN SSH tunnel was not ready within 30 seconds')
                time.sleep(0.1)
        child = subprocess.Popen(command, env=proxy_environment(port), start_new_session=True)
        while child.poll() is None:
            if tunnel.poll() is not None: raise RuntimeError('VPN SSH tunnel stopped; the download command was stopped')
            time.sleep(0.1)
        return child.returncode
    finally:
        stop(child); stop(tunnel)
        for sig, handler in previous.items(): signal.signal(sig, handler)
