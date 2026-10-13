"""VM discovery, assignment inspection and verification."""
import fcntl
import functools
import json
from pathlib import Path
import re
import socket
import subprocess
import sys
import tempfile
from platform_updates import REPORT_KIND, UpdateError, assess_host, canonical, digest, no_application_release, findings, fresh, parse_apk_database, timestamp, unique_object, VERIFY_AGE
from platform_update_metadata import collect_branch, compare_versions
import vm_storage_inventory
import vm_retention
import vm_no_application
import vm_accepted_audit
import vm_config_audit
import vm_supervised_probe
import platform_source
from platform_update_runtime import REPO, APPROVED_REPO, STATE, CACHE, MAP, PROFILE, RETENTION_CATALOGS, HOST, now, load, optional, write, command, require_controller

def selected_targets():
    return tuple((box, role) for box in sorted(platform_source.snapshot()['instance']['boxes'])
                 for role in ('bak', 'dmz', 'iot'))


def targets_from_map(mapping):
    """Inventory every configured or observed Xen VM; never start a stopped VM."""
    targets = {}
    for box, data in sorted(mapping["boxes"].items()):
        if not HOST.fullmatch(box):
            raise UpdateError("mapper contains an invalid box name")
        for role in ("router", "bak", "dmz", "iot", "ops"):
            machine = data.get("machines", {}).get(role)
            if machine is not None or role != "ops":
                targets[box + "-" + role] = {"box": box, "role": role}
        for host, entry in data.get("app_vms", {}).items():
            targets[host] = {"box": box, "role": "dedicated", "app": entry.get("app")}
        xen = data.get("dom0", {}).get("xen", {})
        for entry in xen.get("domains", []):
            guest = entry.get("name", "")
            if guest == "Domain-0":
                continue
            host = box + "-" + guest
            targets.setdefault(host, {"box": box, "role": "unknown"})
        for name in xen.get("config_files", []):
            guest = Path(name).stem
            targets.setdefault(box + "-" + guest, {"box": box, "role": "unknown"})
        for host, target in targets.items():
            if target["box"] != box:
                continue
            guest = host[len(box) + 1:]
            target["runtime"] = "unknown"
            if xen.get("available") is True:
                target["runtime"] = "running" if any(v.get("name") == guest for v in xen.get("domains", [])) else "stopped"
    if not targets or any(not HOST.fullmatch(host) for host in targets):
        raise UpdateError("mapper VM inventory is empty or contains invalid names")
    return targets


def collect_facts(targets, directory, log, suffix):
    if not isinstance(suffix, str) or not re.fullmatch(r"[a-z0-9.-]+\.ts\.net", suffix):
        raise UpdateError("the verified Platform map has no valid Tailnet DNS suffix")
    hosts = {}
    for host, target in targets.items():
        if target["runtime"] != "running":
            continue
        hosts[host] = {"ansible_host": host + "." + suffix, "ansible_user": "smith" if target["role"] == "ops" else "neo",
                       "vm_update_shared_storage": target['role'] in ('bak', 'dmz', 'iot'),
                       "ansible_become_method": "doas", "ansible_become_flags": "-n",
                       "ansible_python_interpreter": "/usr/bin/python3",
                       "ansible_ssh_common_args": "-o StrictHostKeyChecking=accept-new -o ConnectTimeout=8 -o ConnectionAttempts=1 -o ServerAliveInterval=5 -o ServerAliveCountMax=2",
                       "ansible_timeout": 15}
        if host == socket.gethostname().split(".")[0]:
            hosts[host]["ansible_connection"] = "local"
    if not hosts:
        return
    inventory = directory / "inventory.json"
    write(inventory, {"all": {"hosts": hosts}})
    arguments = directory / "arguments.json"
    write(arguments, {"vm_update_fact_dir": str(directory)})
    command(["ansible-playbook", "-vv", "-i", inventory,
             REPO / "ansible/playbooks/74-platform-update-scan.yml", "-e", "@" + str(arguments)], timeout=600, log=log)


def scan(refresh=True):
    require_controller()
    if not STATE.is_dir() or not CACHE.is_dir():
        raise UpdateError("install the platform-update-discovery Ansible role before scanning")
    with (STATE / "scan.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise UpdateError("another discovery operation holds the installation lock") from error
        started = now()
        report = {"kind": REPORT_KIND, "generated_at": timestamp(started), "hosts": [], "findings": [],
                  "source_controller": socket.gethostname(), "profile_sha256": digest(load(PROFILE)),
                  "implementation_commit": command(["git", "-C", REPO, "rev-parse", "HEAD"]).strip(),
                  "complete": False}
        # Invalidate the prior report before any remote or metadata work starts.
        write(STATE / "current.json", {**report, "findings": [findings("discovery.incomplete", "Discovery has started but has not completed.", "critical")]})
        with (STATE / "scan.log").open("w") as log:
            try:
                # The mapper can emit a partial report after an Ansible source
                # failure. Check the authoritative reader independently first.
                command(["/usr/local/sbin/platform-inventory", "--list"])
                if refresh:
                    command([APPROVED_REPO / "ansible/bin/platform-map", "refresh", "--remote-scope", "dom0"], timeout=600, log=log)
                mapping = load(MAP)
                if not fresh(mapping.get("generated_at"), now(), VERIFY_AGE):
                    raise UpdateError("the Platform map is stale; refresh it on the active controller")
                targets = targets_from_map(mapping)
                if any(v["runtime"] == "unknown" for v in targets.values()):
                    report["findings"].append(findings("inventory.runtime-unknown", "Xen inventory is incomplete; configured or running VMs may be missing.", "critical"))
                profile = load(PROFILE)
                retention_catalogs = [load(path) for path in RETENTION_CATALOGS]
                facts = {}
                with tempfile.TemporaryDirectory(prefix="facts-", dir=STATE) as work:
                    directory = Path(work)
                    try:
                        collect_facts(targets, directory, log, mapping.get("tailnet", {}).get("magicdns_suffix"))
                    except UpdateError:
                        report["findings"].append(findings("inventory.partial", "VM collection failed for one or more targets.", "critical"))
                    for host, target in targets.items():
                        fact = optional(directory / (host + ".json"))
                        if fact:
                            fact.update(target)
                            # Controller time bounds the collection. Guest clock drift
                            # stays visible without granting freshness to old files.
                            fact["guest_observed_at"] = fact["observed_at"]
                            fact["observed_at"] = timestamp(now())
                            release = fact.get("os", {}).get("version_id", "").split(".")
                            fact["branch"] = "v" + ".".join(release[:2]) if len(release) >= 2 else ""
                            try:
                                fact["packages"] = parse_apk_database(fact.pop("apk_database", ""))
                            except UpdateError:
                                fact["packages"] = {}
                        facts[host] = fact
                metadata = {}
                branches = sorted({v["branch"] for v in facts.values() if v and v.get("os", {}).get("id") == "alpine" and re.fullmatch(r"v[0-9]+\.[0-9]+", v.get("branch", ""))})
                for branch in branches:
                    print("platform-update: verify official repository metadata for " + branch, file=sys.stderr)
                    metadata[branch] = collect_branch(branch, CACHE, profile, now())
                compare = functools.lru_cache(maxsize=16384)(compare_versions)
                for host, target in sorted(targets.items()):
                    result = assess_host(host, facts[host], metadata, profile["packages"], compare, now())
                    if result['profile'] == 'shared-alpine-v1':
                        result['storage_assessment'] = vm_storage_inventory.assess(facts[host], retention_catalogs, host)
                        result['findings'].extend(result['storage_assessment']['findings'])
                        result['host_assessment'] = vm_storage_inventory.assess_host_data(facts[host], host)
                        result['findings'].extend(result['host_assessment']['findings'])
                    result["target"] = target
                    if target["runtime"] == "stopped":
                        result["findings"] = [v for v in result["findings"] if v["code"] != "inventory.unknown"]
                        result["findings"].append(findings("replacement.stopped", "VM is stopped; replacement is deferred until an approved start.", scope=host))
                    report["hosts"].append(result)
                # Keep raw official evidence separate from the concise report.
                write(STATE / "metadata.json", metadata)
                report["metadata_sha256"] = digest(metadata)
                report["complete"] = True
            except (UpdateError, KeyError, ValueError, TypeError) as error:
                report["findings"].append(findings("discovery.failed", str(error), "critical"))
            report["generated_at"] = timestamp(now())
            write(STATE / "current.json", report)
    return report


def retention_report():
    require_controller()
    commit = command(['git', '-C', REPO, 'rev-parse', 'HEAD']).strip()
    helper = ['/usr/local/sbin/platform-source', 'retention']

    def read_source():
        try:
            return json.loads(command(helper, timeout=900), object_pairs_hook=unique_object)
        except UpdateError as error:
            raise UpdateError('retention source verification failed; inspect platform-source retention on the active controller; the installed reader must validate Instance') from error

    source = read_source()
    report = vm_retention.report(source, optional(STATE / 'current.json'),
                                 [load(path) for path in RETENTION_CATALOGS], commit, now())
    if read_source() != source or command(['git', '-C', REPO, 'rev-parse', 'HEAD']).strip() != commit:
        raise UpdateError('retention authority or public source changed during reporting')
    return report


def unmanaged_source_status(box, role):
    """Use the installed root-only dom0 reader from the active controller."""
    if not HOST.fullmatch(box) or role not in vm_no_application.ROLES:
        raise UpdateError('unmanaged source inspection requires one selected shared VM')
    script = 'doas /usr/local/sbin/vm-update-transaction source-status --role ' + role + '\n'
    try:
        response = subprocess.run(['tailscale', 'ssh', 'neo@' + box + '-dom0', 'sh', '-s'],
                                  input=script, text=True, capture_output=True, timeout=210)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise UpdateError('unmanaged Xen source inspection failed') from error
    if response.returncode or not 0 < len(response.stdout) <= 16384:
        raise UpdateError('installed dom0 source reader is unavailable or returned unsafe output')
    try:
        return json.loads(response.stdout, object_pairs_hook=unique_object)
    except (ValueError, TypeError) as error:
        raise UpdateError('installed dom0 source reader returned invalid JSON') from error


def dom0_assignment_status(box, role):
    """Read the protected dom0 selection through its installed validator."""
    if (box, role) not in selected_targets():
        raise UpdateError('assignment inspection is limited to selected shared VMs')
    script = 'doas /usr/local/sbin/vm-update-transaction assignment-status --role ' + role + '\n'
    try:
        response = subprocess.run(['tailscale', 'ssh', 'neo@' + box + '-dom0', 'sh', '-s'],
                                  input=script, text=True, capture_output=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise UpdateError('protected dom0 assignment reader is unavailable') from error
    if response.returncode or not 0 < len(response.stdout) <= 65536:
        raise UpdateError('protected dom0 assignment reader is unavailable or returned unsafe output')
    try:
        result = json.loads(response.stdout, object_pairs_hook=unique_object)
    except (ValueError, TypeError) as error:
        raise UpdateError('protected dom0 assignment reader returned invalid JSON') from error
    basic = {'kind', 'role', 'managed'}
    common = {'operation_id', 'request_sha256', 'stage', 'selection',
              'release_sha256', 'engine_commit', 'profile'}
    selected = {'vm_uuid', 'boot_configuration', 'disks', 'artifacts', 'autostart',
                'runtime', 'configuration_sha256', 'configuration_drift',
                'autostart_drift'}
    if (not isinstance(result, dict) or result.get('kind') != 'klokast.vm-boot-assignment.v1' or
            result.get('role') != role or type(result.get('managed')) is not bool):
        raise UpdateError('protected dom0 assignment has an invalid contract')
    if result['managed'] is False:
        if set(result) != basic:
            raise UpdateError('unmanaged dom0 assignment contains unexpected fields')
        return result
    stage, choice = result.get('stage'), result.get('selection')
    if not isinstance(stage, str):
        raise UpdateError('protected dom0 assignment has an invalid stage')
    expected_choice = ('accepted' if stage in {'accepted', 'complete'} else
                       'recorded-previous' if stage in {'adopted', 'recovered'} else 'pending')
    if (stage not in {'adopted', 'armed', 'stopping', 'stopped', 'starting', 'booted', 'tested',
                      'recovering', 'accepted', 'complete', 'recovered', 'recovery-failed'} or
            choice != expected_choice or
            set(result) != basic | common | (selected if choice != 'pending' else set()) or
            not isinstance(result.get('operation_id'), str) or
            not re.fullmatch(r'[0-9a-f]{24}', result['operation_id']) or
            not isinstance(result.get('request_sha256'), str) or
            not re.fullmatch(r'[0-9a-f]{64}', result['request_sha256']) or
            not isinstance(result.get('engine_commit'), str) or
            not re.fullmatch(r'[0-9a-f]{40}', result['engine_commit']) or
            (result.get('release_sha256') is not None and
             (not isinstance(result['release_sha256'], str) or
              not re.fullmatch(r'[0-9a-f]{64}', result['release_sha256']))) or
            (result.get('profile') is None) != (result.get('release_sha256') is None) or
            (result.get('profile') is not None and result.get('profile') != 'shared-alpine-v1')):
        raise UpdateError('protected dom0 assignment has an invalid contract')
    if choice != 'pending':
        if (type(result['autostart']) is not bool or
                (not isinstance(result['runtime'], str) or
                 result['runtime'] not in {'running', 'stopped'}) or
                type(result['configuration_drift']) is not bool or
                type(result['autostart_drift']) is not bool or
                not isinstance(result['configuration_sha256'], str) or
                not re.fullmatch(r'[0-9a-f]{64}', result['configuration_sha256']) or
                not isinstance(result['disks'], dict) or
                not isinstance(result['artifacts'], dict) or
                not isinstance(result['boot_configuration'], dict)):
            raise UpdateError('protected dom0 assignment has incomplete selected disk evidence')
    return result


def assignment_summary():
    """Report only selected targets; absent and drifting assignments stay critical."""
    entries, problems = [], []
    for box, role in selected_targets():
        host = box + '-' + role
        try:
            entry = dom0_assignment_status(box, role)
        except UpdateError as error:
            entries.append({'host': host, 'status': 'reader-unavailable'})
            problems.append(findings('assignment.reader-unavailable', str(error), 'critical', host))
            continue
        entries.append({'host': host, 'status': 'managed' if entry['managed'] else 'unmanaged',
                        'assignment': entry})
        if not entry['managed']:
            problems.append(findings('assignment.unmanaged', 'Selected VM has no accepted disk assignment.',
                                     'critical', host))
        elif entry['stage'] != 'complete' or entry['release_sha256'] is None:
            problems.append(findings('assignment.incomplete', 'Selected VM has no completed accepted release.',
                                     'critical', host))
        elif (entry['configuration_drift'] or entry['autostart_drift'] or
              entry['runtime'] != 'running'):
            problems.append(findings('assignment.drift', 'Selected VM differs from its accepted boot assignment.',
                                     'critical', host))
    return entries, problems


def accepted_release(assignment):
    """A protected dom0 assignment supplies the digest, never discovery state."""
    wanted = assignment['release_sha256']
    if not isinstance(wanted, str) or not re.fullmatch(r'[0-9a-f]{64}', wanted):
        raise UpdateError('accepted assignment has no release identity')
    for path in sorted((STATE / 'builds').glob('*/release-evidence.json')):
        release = load(path)
        if release.get('release_sha256') != wanted:
            continue
        if digest({k: v for k, v in release.items() if k != 'release_sha256'}) != wanted:
            raise UpdateError('accepted release evidence checksum differs')
        inputs = load(path.with_name('inputs.json'))
        candidate = load(path.with_name('candidate.json'))
        boot = candidate.get('boot_test', {})
        if release != no_application_release(inputs, candidate, boot.get('openrc_test', {}),
                                               boot.get('personalized_test', {}),
                                               boot.get('maintenance_restore', {})):
            raise UpdateError('accepted release differs from its package and test evidence')
        return release
    raise UpdateError('accepted release evidence is missing from the controller')


def verify_accepted():
    """Read fresh boot assignments, packages, running kernel and required services."""
    require_controller()
    write(STATE / 'verification.json', {
        'kind': 'klokast.vm-update-verification.v1', 'generated_at': timestamp(now()),
        'verified': False, 'assignments': [],
        'findings': [findings('release.incomplete', 'Accepted release verification has not completed.', 'critical')]})
    entries, problems = assignment_summary()
    for item in entries:
        host = item['host']
        assignment = item.get('assignment', {})
        if (assignment.get('stage') != 'complete' or assignment.get('runtime') != 'running' or
                assignment.get('configuration_drift') or assignment.get('autostart_drift')):
            continue
        try:
            release = accepted_release(assignment)
            box, role = host.rsplit('-', 1)
            import vm_service_updates
            import vm_service_probe
            service = vm_service_updates.step(box,role,'status')['installed']
            if service:
                if service['requested_configuration']['image_evidence'] != {'kernel_release':release['kernel_release'],'packages_sha256':digest(release['packages'])}:
                    raise UpdateError('service image evidence differs from its accepted release')
                actual = vm_service_probe.probe(box,role)
                checks = vm_service_probe.verify(service['requested_configuration']['before'],actual,service,unfinished=False)
            else:
                actual = vm_supervised_probe.probe(host)
                checks = {
                    'packages': actual['packages_sha256'] == digest(release['packages']),
                    'kernel': actual['kernel'] == release['kernel_release'] and actual['modules_present'] is True,
                    'boot_assignment': actual['xen_uuid'] == assignment['vm_uuid'],
                    'management': actual['hostname'] == host and actual['tailscale_online'] is True,
                    'services': actual['rootless_podman'] is True and actual['firewall_valid'] is True,
                    'retained_data': actual['retained_mounted'] is True and actual['retained_state_private'] is True,
                    'no_application': all(actual[key] == [] for key in ('containers', 'images', 'volumes')),
                }
            if dom0_assignment_status(box, role) != assignment:
                raise UpdateError('boot assignment changed during verification')
            item['checks'] = checks
            for key, passed in checks.items():
                if not passed:
                    problems.append(findings('release.' + key, 'Accepted VM failed ' + key + ' verification.', 'critical', host))
        except (UpdateError, RuntimeError, OSError, ValueError, KeyError, TypeError, subprocess.TimeoutExpired) as error:
            problems.append(findings('release.unverified', str(error), 'critical', host))
    result = {'kind': 'klokast.vm-update-verification.v1', 'generated_at': timestamp(now()),
              'verified': not problems, 'assignments': entries, 'findings': problems}
    write(STATE / 'verification.json', result)
    return result


def inspect_target(box, role):
    """Account for one target without adopting it or changing its running generation."""
    controller_status = require_controller()
    commit = command(['git', '-C', REPO, 'rev-parse', 'HEAD']).strip()
    discovery = optional(STATE / 'current.json')
    sources, intent, source_error = None, None, None
    def read_sources():
        values = []
        for name in ('vm-retention-status', 'registry-source-status'):
            values.append(json.loads(command(['/usr/local/sbin/platform-source', {'vm-retention-status': 'retention', 'registry-source-status': 'registry'}[name]], timeout=900),
                                     object_pairs_hook=unique_object))
        return values
    try:
        sources = read_sources()
        intent = vm_no_application.source_intent(*sources, [load(p) for p in RETENTION_CATALOGS], box, role)
    except UpdateError:
        # Keep a reviewable refusal report when the installed source reader is
        # unavailable. Do not fall back to private files or observed workload.
        source_error = 'Installed source readers could not establish current no-application intent; check their controller diagnostics.'
    result = vm_no_application.report(discovery, box, role, commit, now(), intent=intent, source_error=source_error)
    observed_configuration = None
    observed_firewall = None
    legacy_comparison = None
    shadow_comparison = None
    source_status = None
    management_receipt = None
    if (intent and intent['eligible'] and result['items'] and
            discovery.get('implementation_commit') == commit):
        host = box + '-' + role
        inventory = json.loads(command(['ansible-inventory', '-i',
                                        REPO / 'ansible/execution-inventory/hosts',
                                        '--host', host]), object_pairs_hook=unique_object)
        expected = vm_config_audit.render(REPO, host, inventory)
        observed_configuration = vm_config_audit.guest_hashes(host)
        comparison = vm_config_audit.report(host, commit, result['report_sha256'],
                                            expected, observed_configuration,
                                            intent['engine_commit'] == commit)
        result = vm_no_application.with_config_comparison(result, comparison)
        firewall_row = next(row for row in comparison['rows'] if row['path'] == '/etc/nftables.nft')
        if not firewall_row['match']:
            observed_firewall = vm_config_audit.guest_firewall_bytes(host)
            if vm_config_audit.sha(observed_firewall) != firewall_row['observed_sha256']:
                raise UpdateError('selected guest firewall changed during the fixed-file comparison')
            legacy_comparison = vm_config_audit.legacy_firewall_report(
                REPO, host, inventory, observed_firewall, result['report_sha256'],
                commit, intent['engine_commit'] == commit)
            result = vm_no_application.with_legacy_firewall(result, comparison, legacy_comparison)
        shadow_row = next((row for row in result['items']
                           if row['area'] == 'package-difference' and row['key'] == '/etc/shadow'), None)
        if (shadow_row and shadow_row['rule'] ==
                'exact old package and locked service accounts; compare admin password hash with checked machine input'):
            fact = next(row['facts'] for row in discovery['hosts'] if row['host'] == host)
            shadow_comparison = vm_config_audit.shadow_input_report(
                REPO, host, inventory, fact['legacy_shadow_file']['neo_hash_sha256'],
                result['report_sha256'], commit, intent['engine_commit'] == commit)
            result = vm_no_application.with_shadow_input(result, shadow_comparison, discovery)
        if intent['engine_commit'] == commit and result['kind'] == 'klokast.vm-no-application-qualification.v4':
            try:
                source_status = unmanaged_source_status(box, role)
                result = vm_no_application.with_source_status(result, source_status, discovery, now())
            except UpdateError:
                source_status = None
                result = dict(result)
                result.pop('report_sha256')
                result['findings'] = [finding for finding in result['findings']
                                      if finding['code'] != 'qualification.unresolved']
                result['findings'].append(findings('qualification.source-unavailable',
                                                    'Installed dom0 source reader or matching guest mount evidence is unavailable.',
                                                    'critical', host))
                result = vm_no_application.finish(result)
        if result['kind'] == 'klokast.vm-no-application-qualification.v5' and result['source_match']:
            try:
                result = vm_no_application.with_identity_status(result, discovery)
            except UpdateError:
                result = dict(result)
                result.pop('report_sha256')
                result['findings'] = [finding for finding in result['findings']
                                      if finding['code'] != 'qualification.unresolved']
                result['findings'].append(findings('qualification.identity-unavailable',
                                                    'The four private machine files lack stable typed-retention evidence.',
                                                    'critical', host))
                result = vm_no_application.finish(result)
    if sources is not None and read_sources() != sources or optional(STATE / 'current.json') != discovery or command(['git', '-C', REPO, 'rev-parse', 'HEAD']).strip() != commit:
        raise UpdateError('qualification source, discovery, authority, or implementation changed during preparation')
    if observed_configuration is not None and vm_config_audit.guest_hashes(host) != observed_configuration:
        raise UpdateError('selected guest configuration changed during qualification')
    if observed_firewall is not None and vm_config_audit.guest_firewall_bytes(host) != observed_firewall:
        raise UpdateError('selected guest firewall changed during historical source comparison')
    if source_status is not None:
        current_source = unmanaged_source_status(box, role)
        if {k: v for k, v in current_source.items() if k != 'observed_at'} != {
                k: v for k, v in source_status.items() if k != 'observed_at'}:
            raise UpdateError('unmanaged Xen source changed during qualification')
    if result['kind'] == 'klokast.vm-no-application-qualification.v6' and result['summary']['unresolved'] == 0:
        management_receipt = {'kind': 'klokast.vm-independent-management.v1',
                              'host': host, 'dom0': box + '-dom0',
                              'controller': controller_status['hostname'],
                              'guest_transport': 'controller-tailnet-ssh',
                              'dom0_transport': 'controller-tailnet-ssh',
                              'configuration_evidence_sha256': comparison['report_sha256'],
                              'source_evidence_sha256': digest(source_status),
                              'observed_at': timestamp(now()), 'authority': 'comparison-only'}
        management_receipt['report_sha256'] = digest(management_receipt)
        result = vm_no_application.with_management_paths(
            result, management_receipt, comparison, source_status, now())
    if observed_configuration is not None:
        comparison_dir = STATE / 'config-audits'
        comparison_dir.mkdir(mode=0o700, exist_ok=True)
        comparison_record = comparison_dir / (comparison['report_sha256'] + '.json')
        if comparison_record.exists():
            if load(comparison_record) != comparison:
                raise UpdateError('configuration comparison conflicts with existing evidence')
        else:
            write(comparison_record, comparison)
        if legacy_comparison is not None:
            legacy_record = comparison_dir / (legacy_comparison['report_sha256'] + '.json')
            if legacy_record.exists():
                if load(legacy_record) != legacy_comparison:
                    raise UpdateError('historical firewall comparison conflicts with existing evidence')
            else:
                write(legacy_record, legacy_comparison)
        if shadow_comparison is not None:
            shadow_record = comparison_dir / (shadow_comparison['report_sha256'] + '.json')
            if shadow_record.exists():
                if load(shadow_record) != shadow_comparison:
                    raise UpdateError('shadow comparison conflicts with existing evidence')
            else:
                write(shadow_record, shadow_comparison)
    if source_status is not None:
        source_dir = STATE / 'source-audits'
        source_dir.mkdir(mode=0o700, exist_ok=True)
        source_record = source_dir / (digest(source_status) + '.json')
        if source_record.exists():
            if load(source_record) != source_status:
                raise UpdateError('unmanaged Xen source receipt conflicts with existing evidence')
        else:
            write(source_record, source_status)
    if management_receipt is not None:
        management_dir = STATE / 'management-audits'
        management_dir.mkdir(mode=0o700, exist_ok=True)
        management_record = management_dir / (management_receipt['report_sha256'] + '.json')
        if management_record.exists():
            if load(management_record) != management_receipt:
                raise UpdateError('independent management receipt conflicts with existing evidence')
        else:
            write(management_record, management_receipt)
    destination = STATE / 'qualifications'
    destination.mkdir(mode=0o700, exist_ok=True)
    record = destination / (result['report_sha256'] + '.json')
    if record.exists():
        if load(record) != result:
            raise UpdateError('qualification record conflicts with existing evidence')
    else:
        write(record, result)
    return result, record


def audit_accepted(box, role):
    """Record the remaining qualification gaps against a protected generation."""
    require_controller()
    if (box, role) not in selected_targets():
        raise UpdateError('accepted audit supports only the declared supported shared VMs')
    commit = command(['git', '-C', REPO, 'rev-parse', 'HEAD']).strip()
    discovery = load(STATE / 'current.json')
    if discovery.get('implementation_commit') != commit:
        raise UpdateError('run a fresh scan from this candidate before accepted audit')
    authority = []
    for name in ('vm-retention-status', 'registry-source-status'):
        authority.append(json.loads(command(['/usr/local/sbin/platform-source', {'vm-retention-status': 'retention', 'registry-source-status': 'registry'}[name]], timeout=900),
                                    object_pairs_hook=unique_object))
    intent = vm_no_application.source_intent(*authority, [load(p) for p in RETENTION_CATALOGS], box, role)
    base = vm_no_application.report(discovery, box, role, commit, now(), intent=intent)
    command(['ansible-playbook', '-i', REPO / 'ansible/execution-inventory/hosts',
             REPO / 'ansible/playbooks/74-platform-update-source-inspection.yml', '--limit', box + '-dom0',
             '-e', canonical({'update_inspect_box': box, 'update_inspect_role': role})], timeout=600)
    source = load(STATE / 'source-audits' / ('replacement-' + box + '-' + role + '.json'))
    release = accepted_release({'release_sha256': source['release']['release_sha256']})
    if release != source['release']:
        raise UpdateError('protected accepted source differs from tested build release')
    builds = [path.parent for path in sorted((STATE / 'builds').glob('*/release-evidence.json'))
              if load(path).get('release_sha256') == release['release_sha256']]
    if len(builds) != 1 or not re.fullmatch('[0-9a-f]{24}', builds[0].name):
        raise UpdateError('accepted build identity is missing or ambiguous')
    build = builds[0]
    recipe = vm_accepted_audit.template_recipe(
        load(build / 'inputs.json'), load(build / 'request.json'),
        CACHE / ('build-' + build.name) / 'capsule.tar', release)
    result = vm_accepted_audit.compare(base, source, discovery, now(), recipe)
    if load(STATE / 'current.json') != discovery:
        raise UpdateError('discovery changed during accepted audit; collect new evidence')
    for name, previous in zip(('vm-retention-status', 'registry-source-status'), authority):
        if json.loads(command(['/usr/local/sbin/platform-source', {'vm-retention-status': 'retention', 'registry-source-status': 'registry'}[name]], timeout=900),
                      object_pairs_hook=unique_object) != previous:
            raise UpdateError('checked application or retention intent changed during accepted audit')
    directory = STATE / 'accepted-audits'
    directory.mkdir(mode=0o700, exist_ok=True)
    path = directory / (result['report_sha256'] + '.json')
    if path.exists() and load(path) != result:
        raise UpdateError('accepted audit conflicts with its recorded evidence')
    if not path.exists(): write(path, result)
    return result

