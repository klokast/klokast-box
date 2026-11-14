"""Make the MacBook Instance worktree equal to the published GitHub main, or stop.

kk answers from the Instance. A worktree that a person must pull by hand can
be stale without a sign, so each Instance command first fetches main. A clean
worktree that is behind is fast-forwarded. Local changes, unpublished commits
and an unreachable GitHub stop the command: kk never answers from a copy that
can differ from the published desired state.
"""
import os
from pathlib import Path
import subprocess
import sys

FILE = 'klokast-instance.json'


class StaleInstance(Exception):
    pass


def git(root, *args, timeout=30):
    # No prompt can stop kk: a missing SSH agent key fails at once.
    env = dict(os.environ, GIT_TERMINAL_PROMPT='0')
    env.setdefault('GIT_SSH_COMMAND', 'ssh -o BatchMode=yes -o ConnectTimeout=20')
    try:
        result = subprocess.run(['git', '-C', str(root), *args], stdin=subprocess.DEVNULL, capture_output=True,
                                text=True, timeout=timeout, env=env)
    except subprocess.TimeoutExpired:
        raise StaleInstance('git ' + args[0] + ' did not finish in ' + str(timeout) + ' s') from None
    if result.returncode:
        raise StaleInstance('git ' + ' '.join(args)[:80] + ' failed: ' + (result.stderr.strip() or 'no error text')[-300:])
    return result.stdout.strip()


def refresh(path):
    """Return (commit, previous commit or None) of the published Instance in this worktree."""
    try:
        root = Path(path).expanduser().resolve(strict=True)
        present = (root / FILE).is_file()
    except (OSError, RuntimeError) as error:
        raise StaleInstance('cannot read Instance: ' + str(error)) from None
    if not present:
        raise StaleInstance('cannot read Instance: ' + str(root) + ' has no ' + FILE +
                            '; select the Instance worktree with --instance PATH')
    if git(root, 'rev-parse', '--show-toplevel') != str(root):
        raise StaleInstance(str(root) + ' is not the root of its Git repository')
    if git(root, 'symbolic-ref', '--quiet', '--short', 'HEAD') != 'main':
        raise StaleInstance(str(root) + ' is not on branch main; switch to main and rerun')
    if git(root, 'status', '--porcelain', '--untracked-files=no'):
        raise StaleInstance(str(root) + ' has uncommitted changes. kk uses only the published Instance: '
                            'commit and push them, or discard them, then rerun')
    try:
        git(root, 'fetch', '--quiet', '--no-tags', 'origin', '+refs/heads/main:refs/remotes/origin/main', timeout=60)
    except StaleInstance as error:
        raise StaleInstance('cannot read the published Instance from GitHub, so kk cannot show current '
                            'information. Check the network and the GitHub SSH key. Cause: ' + str(error)) from None
    head, published = git(root, 'rev-parse', 'HEAD'), git(root, 'rev-parse', 'refs/remotes/origin/main')
    if head == published:
        return head, None
    if subprocess.run(['git', '-C', str(root), 'merge-base', '--is-ancestor', head, published],
                      stdin=subprocess.DEVNULL, capture_output=True).returncode:
        raise StaleInstance(str(root) + ' has commits that are not on GitHub main. Push them, or reset the '
                            'worktree to origin/main, then rerun')
    git(root, 'merge', '--ff-only', '--quiet', published)
    return published, head


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 1 or not argv[0]:
        print('kk: select a private Instance worktree with --instance PATH or KLOKAST_INSTANCE', file=sys.stderr)
        return 2
    try:
        commit, previous = refresh(argv[0])
    except StaleInstance as error:
        print('kk: ' + str(error), file=sys.stderr)
        return 2
    if previous:
        print('kk: Instance worktree updated to GitHub main: %s -> %s' % (previous[:12], commit[:12]), file=sys.stderr)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
