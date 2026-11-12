"""Freshness checks for AIR image replacement."""
import json
import re
import subprocess


def version(value):
    if not re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+',value):raise RuntimeError('unsupported Codex version: '+value)
    return tuple(map(int,value.split('.')))


def require_current(box,selected,claude=None):
    import codex_artifact
    import github_metadata
    import ops_vpn_proxy
    release=ops_vpn_proxy.public_download(lambda:github_metadata.fetch('codex-stable'),'Codex release check')
    latest=codex_artifact.selection(release)['version']
    if version(selected)<version(latest):raise RuntimeError('a newer Codex release is available; prepare a fresh AIR image')
    if claude is not None:
        import claude_artifact
        if claude_artifact.version(claude)<claude_artifact.version(claude_artifact.current_version()):
            raise RuntimeError('a newer Claude Code release is available; prepare a fresh AIR image')
    script="""set -eu
python3 - <<'PYREMOTE'
import json,pathlib,subprocess
def versions(paths,field):
 found=[]
 for p in map(pathlib.Path,paths):
  if p.exists():found.append(subprocess.check_output([str(p),'--version'],text=True,timeout=15).strip().split()[field])
 return found
# Codex prints 'codex-cli VERSION'; Claude Code prints 'VERSION (Claude Code)'.
print(json.dumps({'codex':versions(['/usr/local/bin/codex','/home/agent/.local/bin/codex','/home/agent/.codex/packages/app-server-daemon/current/bin/codex'],-1),
                  'claude':versions(['/usr/local/bin/claude','/usr/bin/claude','/home/agent/.local/bin/claude'],0)}))
PYREMOTE
"""
    result=subprocess.run(['tailscale','ssh','agent@'+box+'-air','sh','-s'],input=script,capture_output=True,text=True,timeout=90,check=True)
    installed=json.loads(result.stdout)
    if any(version(v)>version(selected) for v in installed['codex']):
        raise RuntimeError('selected image would downgrade Codex; prepare a newer image')
    if claude is not None:
        import claude_artifact
        if any(claude_artifact.version(v)>claude_artifact.version(claude) for v in installed['claude']):
            raise RuntimeError('selected image would downgrade Claude Code; prepare a newer image')
