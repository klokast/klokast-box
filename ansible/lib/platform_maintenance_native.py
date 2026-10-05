"""Native artifact and recovery checks retained from the former Apply executor."""
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import platform_source as source
ApplyError = source.SourceError
unique_object = source.unique
inventory_digest = source.digest
REPO_ROOT = source.REPO
VM_UPDATE_DISCOVERY = Path('/var/lib/klokast/updates/discovery')
DOAS = Path('/usr/bin/doas')
CONTROLLER_USER = 'smith'

def now_utc(): return dt.datetime.now(dt.timezone.utc)
def parse_utc(value, label):
    result = dt.datetime.fromisoformat(value.replace('Z', '+00:00'))
    if result.utcoffset() != dt.timedelta(0): raise ApplyError(label + ' must be UTC')
    return result

def load_json(path, **kwargs): return source.read_json(path)
def canonical(value): return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'))

def vm_update_release_dom0_files(box, operation_id):
    """Hash fixed published template files through the selected dom0 root."""
    if box not in source.snapshot()['instance']['boxes'] or not isinstance(operation_id, str) or not re.fullmatch(r"[0-9a-f]{24}", operation_id):
        raise ApplyError("VM release artifact check has an invalid box or build identity")
    script = '''set -eu
mountpoint -q /mnt/dom0_data
python3 - "$1" <<'PY'
import hashlib, json, os, pathlib, stat, sys
operation = sys.argv[1]
base = pathlib.Path('/mnt/dom0_data/klokast-vm-templates/candidates') / operation
for directory in (pathlib.Path('/mnt/dom0_data'), base.parent.parent, base.parent, base):
    info = directory.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
        raise SystemExit('published candidate directory is unsafe')
limits = {'candidate.json': 1048576, 'root': 4 * 1024**3,
          'kernel': 32 * 1024**2, 'initramfs': 128 * 1024**2}
files = {}
for name, limit in limits.items():
    path = base / name
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        info = os.fstat(descriptor)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or
                info.st_mode & 0o022 or info.st_nlink != 1 or
                not 0 < info.st_size <= limit):
            raise SystemExit('published candidate file is unsafe: ' + name)
        checksum = hashlib.sha256()
        with os.fdopen(descriptor, 'rb', closefd=False) as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b''):
                checksum.update(block)
        after = os.fstat(descriptor)
        fields = ('st_dev', 'st_ino', 'st_mode', 'st_uid', 'st_gid',
                  'st_nlink', 'st_size', 'st_mtime_ns', 'st_ctime_ns')
        if any(getattr(after, field) != getattr(info, field) for field in fields):
            raise SystemExit('published candidate file changed while reading: ' + name)
        files[name] = {'sha256': checksum.hexdigest(), 'bytes': info.st_size}
    finally:
        os.close(descriptor)
print(json.dumps({'kind': 'klokast.vm-dom0-release-files.v1',
                  'operation_id': operation, 'files': files}, sort_keys=True, separators=(',', ':')))
PY
'''
    try:
        result = subprocess.run(["/usr/bin/tailscale", "ssh", "neo@" + box + "-dom0", "doas",
                                 "sh", "-s", "--", operation_id], input=script,
                                capture_output=True, text=True, timeout=900,
                                env={"PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "LANG": "C", "LC_ALL": "C",
                                     "HOME": "/root" if os.geteuid() == 0 else os.environ.get("HOME", "/home/smith")})
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ApplyError("bounded dom0 VM release artifact check did not finish") from error
    if result.returncode or len(result.stdout) > 4096:
        raise ApplyError("dom0 VM release artifact check failed: " + result.stderr[-1024:].strip())
    try:
        value = json.loads(result.stdout, object_pairs_hook=unique_object)
    except (ValueError, TypeError) as error:
        raise ApplyError("dom0 VM release artifact check returned invalid JSON") from error
    if (not isinstance(value, dict) or set(value) != {"kind", "operation_id", "files"} or
            value["kind"] != "klokast.vm-dom0-release-files.v1" or value["operation_id"] != operation_id):
        raise ApplyError("dom0 VM release artifact check returned a different build")
    return value["files"]

def vm_adoption_run_controller(*arguments, timeout=1500):
    started = now_utc()
    try:
        result = subprocess.run([str(DOAS), "-u", CONTROLLER_USER,
                                 str(REPO_ROOT / "ansible/bin/platform-update"), *arguments],
                                stdin=subprocess.DEVNULL, capture_output=True, text=True,
                                timeout=timeout, env={"PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
                                                      "LANG": "C", "LC_ALL": "C"})
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ApplyError("bounded VM adoption requalification did not finish") from error
    if arguments[0] == "scan":
        if result.returncode not in {0, 1}:
            raise ApplyError("fresh VM discovery failed before adoption")
        discovery = load_json(VM_UPDATE_DISCOVERY / "current.json", canonical_stored=True)
        if (discovery.get("complete") is not True or
                parse_utc(discovery.get("generated_at"), "discovery time") < started - dt.timedelta(seconds=1) or
                any(finding.get("code") == "discovery.failed" for finding in discovery.get("findings", []))):
            raise ApplyError("fresh VM discovery is incomplete before adoption")
        return discovery
    if arguments[:2] == ("adopt", "prepare"):
        try:
            report = json.loads(result.stdout, object_pairs_hook=unique_object)
        except (ValueError, TypeError) as error:
            raise ApplyError("fresh VM qualification did not return a report") from error
        if not isinstance(report, dict) or report.get("kind") != "klokast.vm-no-application-qualification.v7":
            raise ApplyError("fresh VM qualification is incomplete or blocked")
        return report

def vm_adoption_remote(box, arguments, *, input_text=None, timeout=180):
    if box not in source.snapshot()['instance']['boxes']:
        raise ApplyError("adoption remote target is outside the selected boxes")
    try:
        result = subprocess.run(["/usr/bin/tailscale", "ssh", "neo@" + box + "-dom0", "doas",
                                 "/usr/local/sbin/vm-update-transaction", *arguments],
                                input=input_text, capture_output=True, text=True, timeout=timeout,
                                env={"PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "LANG": "C", "LC_ALL": "C",
                                     "HOME": "/root" if os.geteuid() == 0 else os.environ.get("HOME", "/home/smith")})
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ApplyError("bounded dom0 adoption command did not finish") from error
    if result.returncode or len(result.stdout) > 65536:
        raise ApplyError("dom0 adoption command failed: " + result.stderr[-1024:].strip())
    try:
        value = json.loads(result.stdout, object_pairs_hook=unique_object)
    except (ValueError, TypeError) as error:
        raise ApplyError("dom0 adoption command returned invalid JSON") from error
    if not isinstance(value, dict):
        raise ApplyError("dom0 adoption command returned no record")
    return value

def vm_adoption_request(intent):
    return {"kind": "klokast.vm-adoption.v1", "operation_id": intent["operation_id"],
            "box": intent["box"], "role": intent["role"],
            "engine_commit": intent["engine_commit"], "policy_sha256": intent["policy_sha256"],
            "qualification_sha256": intent["qualification_sha256"],
            "source_evidence_sha256": intent["source_evidence_sha256"],
            "old_uuid": intent["old_uuid"], "old_config_sha256": intent["old_config_sha256"],
            "old_disks": intent["old_disks"], "old_artifacts": intent["old_artifacts"],
            "autostart": True}

def vm_adoption_assignment_verified(intent):
    request = vm_adoption_request(intent)
    assignment = vm_adoption_remote(intent["box"], ["assignment-status", "--role", intent["role"]])
    if (assignment.get("managed") is not True or assignment.get("operation_id") != intent["operation_id"] or
            assignment.get("request_sha256") != inventory_digest(request) or
            assignment.get("stage") != "adopted" or assignment.get("vm_uuid") != intent["old_uuid"] or
            assignment.get("disks") != intent["old_disks"] or
            assignment.get("artifacts") != intent["old_artifacts"] or
            assignment.get("runtime") != "running" or
            assignment.get("configuration_drift") is not False or
            assignment.get("autostart_drift") is not False):
        raise ApplyError("dom0 adopted assignment differs from recorded old generation")
    return assignment

def vm_update_recovery_evidence(box, operation_id):
    """Read one root-owned native test and the installed boot recovery chain."""
    if box not in source.snapshot()['instance']['boxes'] or not isinstance(operation_id, str) or not re.fullmatch(r'[0-9a-f]{24}', operation_id):
        raise ApplyError('VM recovery readiness requires one fixed box and test operation')
    script = '''set -eu
doas /usr/bin/python3 - "$1" <<'PY'
import hashlib, json, os, pathlib, stat, sys, tarfile
operation = sys.argv[1]
base = pathlib.Path('/mnt/dom0_data/klokast-vm-recovery-tests') / operation
result = base / 'result.json'
for path in (base.parent, base, result):
    info = path.lstat()
    if info.st_uid != 0 or info.st_mode & 0o022 or (
            not stat.S_ISREG(info.st_mode) if path == result else not stat.S_ISDIR(info.st_mode)):
        raise SystemExit('native recovery evidence has unsafe metadata')
if result.stat().st_size > 1048576:
    raise SystemExit('native recovery evidence is too large')
def checked(path, maximum, mode):
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or stat.S_IMODE(info.st_mode) != mode or info.st_size > maximum:
        raise SystemExit('installed recovery code has unsafe metadata')
    return hashlib.sha256(path.read_bytes()).hexdigest()
helper = checked(pathlib.Path('/usr/local/sbin/vm-update-transaction'), 1048576, 0o700)
service = checked(pathlib.Path('/etc/init.d/klokast-vm-update-recovery'), 1048576, 0o755)
link = pathlib.Path('/etc/runlevels/default/klokast-vm-update-recovery')
if not link.is_symlink() or os.readlink(link) != '/etc/init.d/klokast-vm-update-recovery':
    raise SystemExit('dom0 boot recovery is not enabled')
archives = [*pathlib.Path('/media').glob('*.apkovl.tar.gz'),
            *pathlib.Path('/media').glob('*/*.apkovl.tar.gz')]
if len(archives) != 1:
    raise SystemExit('dom0 has no unique persisted recovery archive')
archive = archives[0]
info = archive.lstat()
if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022 or info.st_size > 128 * 1024 * 1024:
    raise SystemExit('dom0 persisted recovery archive has unsafe metadata')
required = {'usr/local/sbin/vm-update-transaction',
            'etc/init.d/klokast-vm-update-recovery',
            'etc/runlevels/default/klokast-vm-update-recovery',
            'etc/conf.d/xendomains'}
entries = {}
with tarfile.open(archive, 'r:gz') as saved:
    for index, member in enumerate(saved):
        if index >= 10000:
            raise SystemExit('dom0 persisted recovery archive has too many entries')
        name = member.name.removeprefix('./')
        if name.startswith('mnt/dom0_data/') or name == 'mnt/dom0_data':
            raise SystemExit('dom0 persisted recovery archive includes live data')
        if name in required:
            if name in entries:
                raise SystemExit('dom0 persisted recovery archive duplicates recovery code')
            if name.endswith('klokast-vm-update-recovery') and name.startswith('etc/runlevels/'):
                if not member.issym() or member.linkname != '/etc/init.d/klokast-vm-update-recovery':
                    raise SystemExit('dom0 persisted recovery runlevel differs')
            else:
                if not member.isfile() or member.size > 1048576:
                    raise SystemExit('dom0 persisted recovery code is unsafe')
                data = saved.extractfile(member).read()
                if name.endswith('vm-update-transaction') and hashlib.sha256(data).hexdigest() != helper:
                    raise SystemExit('dom0 persisted recovery helper differs')
                if name.endswith('klokast-vm-update-recovery') and hashlib.sha256(data).hexdigest() != service:
                    raise SystemExit('dom0 persisted recovery service differs')
                if name == 'etc/conf.d/xendomains' and b'rc_need="${rc_need:-} localmount klokast-vm-update-recovery"' not in data:
                    raise SystemExit('dom0 guest autostart has no recovery dependency')
            entries[name] = True
if set(entries) != required:
    raise SystemExit('dom0 persisted recovery boot chain is incomplete')
with result.open() as stream:
    value = json.load(stream)
print(json.dumps({'result': value, 'helper_sha256': helper, 'service_sha256': service}, sort_keys=True, separators=(',', ':')))
PY
'''
    try:
        process = subprocess.run(['/usr/bin/tailscale', 'ssh', 'neo@' + box + '-dom0',
                                  'sh', '-s', '--', operation_id], input=script,
                                 capture_output=True, text=True, timeout=180,
                                 env={'PATH': '/usr/bin:/bin:/usr/sbin:/sbin', 'LANG': 'C',
                                      'LC_ALL': 'C', 'HOME': '/root'})
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ApplyError('dom0 native recovery evidence did not finish') from error
    if process.returncode or len(process.stdout) > 2 * 1024 * 1024:
        raise ApplyError('dom0 native recovery evidence is unavailable')
    try:
        value = json.loads(process.stdout, object_pairs_hook=unique_object)
    except (ValueError, TypeError) as error:
        raise ApplyError('dom0 native recovery evidence is invalid JSON') from error
    if not isinstance(value, dict) or set(value) != {'result', 'helper_sha256', 'service_sha256'}:
        raise ApplyError('dom0 native recovery evidence has an unknown contract')
    return value
