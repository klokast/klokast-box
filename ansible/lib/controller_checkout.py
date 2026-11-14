"""Fast-forward this controller's public checkout from GitHub.

The fetch is a public download: a declared VPN client tries its same-box
gateway first and direct access second. The checkout holds no credential.
A dirty, diverged or off-main checkout stops the update unchanged.
"""
import os
from pathlib import Path
import pwd
import socket
import subprocess
import sys

import ops_vpn_proxy

REPO = Path(__file__).resolve().parents[2]
ORIGIN = 'https://github.com/klokast/klokast-box.git'
# A stalled HTTPS path ends this attempt, so the direct retry can start.
FETCH = ['-c', 'http.lowSpeedLimit=1024', '-c', 'http.lowSpeedTime=20', 'fetch', '--no-tags',
         'origin', '+refs/heads/main:refs/remotes/origin/main']


def git(*args, repo=REPO, timeout=30):
    result = subprocess.run(['git', '-C', str(repo), *args], stdin=subprocess.DEVNULL,
                            capture_output=True, text=True, timeout=timeout)
    if result.returncode:
        raise RuntimeError('git ' + ' '.join(args)[-120:] + ' failed: ' + (result.stderr.strip() or 'no error text')[-300:])
    return result.stdout.strip()


def log(message):
    print('controller-checkout-update: ' + message, file=sys.stderr, flush=True)


def require_checkout(repo=REPO):
    host = socket.gethostname().split('.')[0]
    if pwd.getpwuid(os.geteuid()).pw_name != 'smith' or not host.endswith('-ops'):
        raise RuntimeError('run as smith on a <box>-ops controller')
    if git('config', '--get', 'remote.origin.url', repo=repo) != ORIGIN:
        raise RuntimeError('origin is not ' + ORIGIN + '; inspect the checkout')
    if git('symbolic-ref', '--quiet', '--short', 'HEAD', repo=repo) != 'main':
        raise RuntimeError('checkout is not on main; inspect the checkout')
    if git('status', '--porcelain', repo=repo):
        raise RuntimeError('checkout has local changes; nothing was changed')
    return host


def update(repo=REPO):
    host = require_checkout(repo)
    previous = git('rev-parse', 'HEAD', repo=repo)
    path = []
    def fetch():
        path.append('vpn-egress' if ops_vpn_proxy.ACTIVE_PORT is not None else 'direct')
        log('fetch main from GitHub on ' + host + ' (' + path[-1] + ')')
        git(*FETCH, repo=repo, timeout=120)
    ops_vpn_proxy.ENABLED = True
    ops_vpn_proxy.public_download(fetch, 'public checkout fetch')
    # The fetch changed only the remote-tracking ref. This step is local.
    try: git('merge', '--ff-only', 'refs/remotes/origin/main', repo=repo)
    except RuntimeError as error:
        raise RuntimeError('checkout cannot fast-forward to origin/main; nothing was changed') from error
    commit = git('rev-parse', 'HEAD', repo=repo)
    if git('status', '--porcelain', repo=repo):
        raise RuntimeError('checkout is not clean after the update')
    log(host + ' checkout ' + ('unchanged at ' + commit[:8] if commit == previous else
        previous[:8] + ' -> ' + commit[:8]))
    return {'controller': host, 'previous': previous, 'commit': commit, 'path': path[-1]}
