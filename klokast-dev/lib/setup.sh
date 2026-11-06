#!/bin/bash
# Called by kk only after the public checkout has been updated.
set -euo pipefail
repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd -P)"
printf 'Checking and installing supported MacBook dependencies...\n'
"$repo_root/klokast-dev/bin/kk" doctor --install

python3 - "$repo_root" "$1" <<'PY'
import json
import os
from pathlib import Path
import re
import shlex
import stat
import subprocess
import sys
import tempfile


def run(*args):
    return subprocess.run(args, check=True, text=True)


def repository_id(url):
    match = re.fullmatch(
        r'(?:https://github\.com/|git@github\.com:|ssh://git@github\.com/)'
        r'([A-Za-z0-9][A-Za-z0-9-]{0,38}/klokast-instance)(?:\.git)?', url)
    if not match:
        raise ValueError('Instance origin must identify a GitHub OWNER/klokast-instance repository')
    return match[1]


def configure_profile(profile, tools, instance):
    # Follow dotfile symlinks instead of replacing the link.
    profile = profile.resolve()
    begin = '# >>> kk setup >>>'
    end = '# <<< kk setup <<<'
    old = profile.read_text(encoding='utf-8') if profile.exists() else ''
    lines = old.splitlines(keepends=True)
    starts = [i for i, line in enumerate(lines) if line.strip() == begin]
    ends = [i for i, line in enumerate(lines) if line.strip() == end]
    if starts or ends:
        if len(starts) != 1 or len(ends) != 1 or starts[0] >= ends[0]:
            raise ValueError(f'{profile}: malformed or duplicate kk setup markers; repair them and rerun setup')
        del lines[starts[0]:ends[0] + 1]
    content = ''.join(lines)
    if content and not content.endswith('\n'):
        content += '\n'
    content += (f'{begin}\n'
                f'export PATH={shlex.quote(str(tools))}:"$PATH"\n'
                f'export KLOKAST_INSTANCE={shlex.quote(str(instance))}\n'
                f'{end}\n')
    if content == old:
        print(f'Shell settings are current: {profile}', flush=True)
        return
    mode = stat.S_IMODE(profile.stat().st_mode) if profile.exists() else 0o600
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=profile.parent,
                                         prefix='.kk-zprofile-', delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(content)
            handle.flush()
            os.fchmod(handle.fileno(), mode)
            os.fsync(handle.fileno())
        os.replace(temporary, profile)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()
    print(f'Updated shell settings: {profile}', flush=True)


def main():
    repo = Path(sys.argv[1]).resolve()
    repository = repository_id(sys.argv[2])
    instance = Path.home() / 'src/private-klokast/klokast-instance'
    if instance.exists() or instance.is_symlink():
        if not (instance / '.git').is_dir():
            raise ValueError(f'{instance}: destination exists but is not a standalone Git checkout')
        root = subprocess.check_output(['git', '-C', str(instance), 'rev-parse', '--show-toplevel'], text=True).strip()
        if Path(root).resolve() != instance.resolve():
            raise ValueError(f'{instance}: destination must be the Instance Git repository root')
        origin = subprocess.check_output(['git', '-C', str(instance), 'config', '--get', 'remote.origin.url'], text=True).strip()
        if repository_id(origin).lower() != repository.lower():
            raise ValueError(f'{instance}: origin does not match {repository}; move the directory or select its repository')
        print(f'Reusing Instance checkout without changing its files: {instance}', flush=True)
    else:
        instance.parent.mkdir(parents=True, exist_ok=True)
        print(f'Cloning Instance through GitHub SSH: {repository}', flush=True)
        run(sys.executable, str(repo / 'klokast-dev/bin/prepare-private-instance-worktree'),
            '--repository', repository, '--worktree', str(instance))
    value = json.loads((instance / 'klokast-instance.json').read_text(encoding='utf-8'))
    if not isinstance(value, dict):
        raise ValueError(f'{instance}/klokast-instance.json must contain a JSON object')
    configure_profile(Path.home() / '.zprofile', repo / 'klokast-dev/bin', instance.resolve())
    print(f'kk setup complete.\nTools: {repo / "klokast-dev/bin"}\nInstance: {instance.resolve()}\n'
          'Start a new login shell: exec /bin/zsh -l', flush=True)


try:
    main()
except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as error:
    print(f'kk setup: {error}', file=sys.stderr)
    sys.exit(2)
PY
