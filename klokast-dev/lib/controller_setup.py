"""Guide the operator through the independent credentials of one controller.

The controller reports each credential without printing a secret. For each
missing item, this client shows the provider page and the exact settings,
reads secrets at hidden prompts, and sends them through the existing
installers. Nothing is copied from another controller.
"""
import argparse
import getpass
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import webbrowser

import air

REPO = Path(__file__).resolve().parents[2]
REMOTE_REPO = '/home/smith/src/klokast/klokast-box'
HOST = re.compile(r'[a-z][a-z0-9-]{0,30}-ops')
VALUE = re.compile(r'[^\s"\'\\$`]{1,512}')
PUBLIC_KEY = re.compile(r'(?:ssh-ed25519|ssh-rsa|ecdsa-sha2-nistp(?:256|384|521)) [A-Za-z0-9+/]+={0,3}(?: [ -~]{1,120})?')
TAILSCALE_PAGE = 'https://console.tailscale.com/admin/settings/trust-credentials'
TOKEN_PAGE = 'https://github.com/settings/personal-access-tokens/new'
ACCOUNT_KEY_PAGE = 'https://github.com/settings/ssh/new'
LABELS = {'tailscale-policy': 'Tailscale policy and enrollment client',
          'tailscale-devices': 'Tailscale devices client',
          'instance-read': 'Instance read key (GitHub deploy key)',
          'instance-write': 'Instance write key (GitHub account key)',
          'github-metadata': 'GitHub public metadata token',
          'freebox': 'Freebox app token (optional)'}


class SetupError(Exception):
    pass


def say(text=''):
    print(text, flush=True)


def ssh(host, command, *, data=None, show=False, timeout=600):
    try:
        return subprocess.run([*air.SSH, 'smith@' + host, command], input=data, text=True,
                              stdout=None if show else subprocess.PIPE,
                              stderr=None if show else subprocess.PIPE, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise SetupError(host + ' did not answer within ' + str(timeout) + ' s') from None


def report(host):
    result = ssh(host, shlex.join([REMOTE_REPO + '/ansible/bin/controller-credentials', 'status']), timeout=300)
    if result.returncode not in (0, 3):
        raise SetupError(host + ': ' + (result.stderr.strip()[-600:] or 'credential report failed') +
                         '. Update the public checkout of ' + host + ' if it has no controller-credentials command.')
    try:
        value = json.loads(result.stdout)
    except ValueError:
        raise SetupError(host + ' returned an invalid credential report') from None
    if value.get('kind') != 'klokast.controller-credentials.v1' or value.get('hostname') != host:
        raise SetupError(host + ' returned a report for another controller')
    return value


def show(value):
    say('Credentials of %s (role: %s)' % (value['hostname'], value['role']))
    for entry in value['items']:
        mark = 'ok' if entry['state'] == 'ok' else ('MISSING' if entry['required'] else 'info')
        say('  %-8s %-42s %s' % (mark, LABELS.get(entry['id'], entry['id']), entry['detail']))
    say('Ready for a planned switch.' if value['ready'] else 'Not ready for a planned switch.')


def ask(prompt, *, secret=False, default=None):
    while True:
        try:
            value = (getpass.getpass(prompt) if secret else input(prompt)).strip()
        except EOFError:
            raise SetupError('input ended; nothing was installed') from None
        if not value and default is not None:
            return default
        if VALUE.fullmatch(value):
            return value
        say('Enter one value without spaces, quotes, backslashes, "$" or "`".')


def confirm(prompt):
    return ask(prompt + ' [y/N] ', default='n').lower() in ('y', 'yes')


def offer(url):
    say('  Page: ' + url)
    if confirm('  Open this page in the browser?'):
        webbrowser.open(url)


def wait(text):
    ask(text + ' Press Enter to continue. ', default='')


def tailscale(host):
    box = host[:-4]
    say('\nCreate two NEW OAuth clients for %s. Never reuse the clients of another controller.' % host)
    say('In the Tailscale admin console, open Trust credentials, select Credential, then OAuth.')
    say('  1. Name "%s policy/enrollment": Policy file Write, Auth Keys Write, tag tag:ops.' % host)
    say('  2. Name "%s devices": Devices Core Write, tag tag:ops.' % host)
    say('Keep the dependent scopes that Tailscale adds. The secret is shown once:')
    say('save each client ID and secret in the password manager before you close the page.')
    offer(TAILSCALE_PAGE)
    tailnet = ask('Tailnet ID (press Enter for "-", the tailnet of the client): ', default='-')
    clients = {}
    for name, label in (('policy', 'policy/enrollment'), ('devices', 'devices')):
        client = ask('%s %s client ID: ' % (host, label))
        clients[name] = (client, ask('%s %s client secret (hidden): ' % (host, label), secret=True))
    if clients['policy'][0] == clients['devices'][0]:
        raise SetupError('the two clients must be distinct; nothing was installed')
    with tempfile.TemporaryDirectory(prefix='klokast-oauth-' + box + '-') as directory:
        paths = {}
        for name, (client, secret) in clients.items():
            paths[name] = Path(directory) / ('tailscale-' + name + '.env')
            descriptor = os.open(paths[name], os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, 'w') as stream:
                stream.write('TAILNET_ID="%s"\nTS_OAUTH_CLIENT_ID="%s"\nTS_OAUTH_CLIENT_SECRET="%s"\n'
                             % (tailnet, client, secret))
        result = subprocess.run([str(REPO / 'klokast-dev/bin/install-tailscale-oauth'), '--controller', host,
                                 '--policy-env', str(paths['policy']), '--devices-env', str(paths['devices'])])
    if result.returncode:
        raise SetupError('the Tailscale installer failed; correct the client scopes or tags and run again')


def metadata(host):
    say('\nCreate a fine-grained GitHub token for %s in your human GitHub account.' % host)
    say('  Name "Klokast %s public metadata", choose an expiration,' % host[:-4])
    say('  select "Public repositories (read-only)" and grant no other permission.')
    say('  Save it in the password manager.')
    offer(TOKEN_PAGE)
    if subprocess.run([str(REPO / 'klokast-dev/bin/install-github-metadata-token'), '--controller', host]).returncode:
        raise SetupError('the GitHub metadata token installer failed')


def gh_login():
    if not shutil.which('gh'):
        return ''
    result = subprocess.run(['gh', 'api', 'user', '--jq', '.login'], capture_output=True, text=True, timeout=30)
    return result.stdout.strip() if result.returncode == 0 else ''


def register(entry, host, repository):
    key, write = entry.get('public_key', ''), entry['id'] == 'instance-write'
    if not PUBLIC_KEY.fullmatch(key):
        raise SetupError(host + ' did not report a valid public key for ' + entry['id'])
    title = '%s Instance %s' % (host, 'development' if write else 'read')
    say('\nRegister the public key of %s:' % host)
    say('  ' + key)
    if write:
        say('This is an account key: add it to the development agent GitHub account,')
        say('which needs Write access to %s.' % repository)
        question, page = '  Add it to the GitHub CLI account "%s"?', ACCOUNT_KEY_PAGE
        command = lambda path: ['gh', 'ssh-key', 'add', path, '--title', title]
    else:
        say('This is a read-only deploy key of %s. Do not select "Allow write access".' % repository)
        question, page = '  Add it with the GitHub CLI account "%s"?', 'https://github.com/%s/settings/keys/new' % repository
        command = lambda path: ['gh', 'repo', 'deploy-key', 'add', path, '--repo', repository, '--title', title]
    login = gh_login()
    if not login or not confirm(question % login):
        say('  Add it on the web page with title "%s".' % title)
        offer(page)
        return wait('After you add the key,')
    with tempfile.TemporaryDirectory(prefix='klokast-key-') as directory:
        path = Path(directory) / 'key.pub'
        path.write_text(key + '\n')
        if subprocess.run(command(str(path))).returncode:
            raise SetupError('GitHub refused the key; add it on the web page and run again')


def instance_git(host, repository, tag):
    script = ('set -eu\ncd "$1"\nANSIBLE_CONFIG=ansible/ansible.cfg ansible-playbook -i localhost, '
              'ansible/playbooks/68-ops-instance-git.yml --tags "$2" -e "instance_git_repository=$3"\n')
    say('\nRunning the Instance Git %s step on %s.' % (tag, host))
    if ssh(host, shlex.join(['sh', '-s', '--', REMOTE_REPO, tag, repository]), data=script, show=True).returncode:
        raise SetupError('the Instance Git %s step failed on %s; see the output above' % (tag, host))


def repair(host, entry, value):
    repository, state = value['instance_repository'], entry['state']
    if entry['id'] in ('tailscale-policy', 'tailscale-devices'):
        tailscale(host)
    elif entry['id'] == 'github-metadata':
        metadata(host)
    elif entry['id'] == 'instance-write' and state == 'absent':
        instance_git(host, repository, 'prepare')
        refreshed = next(i for i in report(host)['items'] if i['id'] == 'instance-write')
        register(refreshed, host, repository)
        instance_git(host, repository, 'configure')
    elif entry['id'] == 'instance-write':
        if state == 'failed':
            register(entry, host, repository)
        instance_git(host, repository, 'configure')
    elif entry['id'] == 'instance-read' and state == 'failed':
        register(entry, host, repository)
    else:
        say('\n%s has no Instance read key. Controller convergence creates it. On the active' % host)
        say('controller, run: ansible/bin/converge-ops-controller --box ' + host[:-4])
        say('Then run this command again.')


def main(argv=None):
    parser = argparse.ArgumentParser(prog='kk setup --controller', description=__doc__.splitlines()[0])
    parser.add_argument('host', help='controller, for example k001-ops')
    parser.add_argument('--check', action='store_true', help='show the credential report only')
    args = parser.parse_args(argv)
    if not HOST.fullmatch(args.host):
        parser.error('the controller must have the BOX-ops form, for example k001-ops')
    try:
        value = report(args.host)
        show(value)
        if args.check or value['ready']:
            return 0 if value['ready'] else 3
        if not sys.stdin.isatty():
            raise SetupError('credential setup needs a terminal; use --check for the report only')
        done = set()
        for entry in value['items']:
            # One Tailscale step installs both client files.
            key = 'tailscale' if entry['id'].startswith('tailscale-') else entry['id']
            if entry['required'] and entry['state'] != 'ok' and key not in done:
                done.add(key)
                repair(args.host, entry, value)
        if any(e['id'] == 'freebox' and e['state'] != 'ok' for e in value['items']):
            say('\nThe optional Freebox app token needs a physical approval at the Freebox of %s.'
                % args.host[:-4])
            say('See install-freebox-application in doc/cli-tools.md.')
        say()
        value = report(args.host)
        show(value)
        return 0 if value['ready'] else 3
    except SetupError as error:
        print('kk setup --controller: ' + str(error), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print('\nkk setup --controller: stopped; run it again to continue', file=sys.stderr)
        return 130


if __name__ == '__main__':
    raise SystemExit(main())
