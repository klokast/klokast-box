"""Resolve the existing public controller tools before freezing replacement inputs."""
import json
import re
import subprocess

GALAXY='https://galaxy.ansible.com/api/v3/plugin/ansible/content/published/collections/index/community/general/'


def metadata(url):
    result=subprocess.run(['curl','--fail','--silent','--show-error','--location',
        '--proto','=https','--proto-redir','=https','--connect-timeout','10','--max-time','60',url],
        capture_output=True,text=True,timeout=75)
    if result.returncode or len(result.stdout)>4*1024**2:
        raise RuntimeError('cannot freeze public controller tool metadata; no replacement was started')
    return json.loads(result.stdout)


def validate(value):
    go=value['go'];artifact=value['go_artifact'];collection=value['collection']
    if (not re.fullmatch(r'go[0-9]+\.[0-9]+\.[0-9]+',go['version']) or
            artifact['filename']!=go['version']+'.linux-amd64.tar.gz' or
            not re.fullmatch('[0-9a-f]{64}',artifact['sha256']) or
            not re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+',collection['version']) or
            collection['artifact']['filename']!='community-general-'+collection['version']+'.tar.gz' or
            not re.fullmatch('[0-9a-f]{64}',collection['artifact']['sha256'])):
        raise RuntimeError('public controller tool artifact identity is invalid')
    return value


def resolve():
    releases=metadata('https://go.dev/dl/?mode=json')
    go=next(v for v in releases if v.get('stable') is True)
    artifact=next(v for v in go['files'] if (v['os'],v['arch'],v['kind'])==('linux','amd64','archive'))
    version=metadata(GALAXY)['highest_version']['version']
    if not re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+',version):
        raise RuntimeError('controller collection release is not a stable version')
    collection=metadata(GALAXY+'versions/'+version+'/')
    return validate({'go':{'version':go['version']},
        'go_artifact':{k:artifact[k] for k in ('filename','sha256')},
        'collection':{'version':version,'artifact':{k:collection['artifact'][k] for k in ('filename','sha256')}}})
