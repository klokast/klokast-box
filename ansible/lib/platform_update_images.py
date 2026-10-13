"""Qualified local image preparation, reuse and retirement."""
import datetime as dt
import fcntl
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import socket
import sys
import tempfile
from platform_updates import UpdateError, digest, no_application_release, timestamp, unique_object
from platform_update_metadata import fetch_json, newest_stable_branch
import vm_template_inputs
import vm_app_compatibility
import platform_source
import vm_local_images
import infrastructure_images
from platform_update_runtime import REPO, STATE, CACHE, PROFILE, HOST, now, load, write, command, require_controller

def cleanup_template(box, operation, result, dry_run=False, inventory=None, preserve_qualified=False):
    """Retire only checked unused images after controller qualification."""
    log_path = result / 'cleanup.log'
    try:
        plan_hash = None
        proposal = None
        for action in (('plan',) if dry_run else ('plan', 'apply')):
            arguments = result / ('cleanup-' + action + '-args.json')
            values = {'cleanup_box': box, 'cleanup_current': operation,
                      'cleanup_action': action, 'cleanup_result_dir': str(result)}
            if preserve_qualified:
                values['cleanup_preserve_qualified'] = True
            if plan_hash:
                values['cleanup_plan_sha256'] = plan_hash
            write(arguments, values)
            with log_path.open('a') as log:
                command(['ansible-playbook', '-vv', '-i', inventory or result / 'inventory/hosts.json',
                         REPO / 'ansible/playbooks/74-platform-update-template-cleanup.yml',
                         '--limit', box + '-dom0', '-e', '@' + str(arguments)], timeout=2100, log=log)
            record = load(result / ('cleanup-' + action + '.json'))
            if not isinstance(record, dict):
                raise UpdateError('template cleanup returned an invalid result object')
            if action == 'plan':
                if (record.get('box') != box or record.get('current') != operation or
                        record.get('plan_sha256') != digest({k: v for k, v in record.items() if k != 'plan_sha256'})):
                    raise UpdateError('template cleanup plan differs from the qualified build')
                plan_hash = record['plan_sha256']
                proposal = record
            elif (record.get('plan_sha256') != plan_hash or record.get('current') != operation or
                  type(record.get('complete')) is not bool or
                  record.get('retired_candidates') != [v['operation_id'] for v in proposal['removals'] if v['retire_candidate']]):
                raise UpdateError('template cleanup result differs from its plan')
        if dry_run: return {**record, 'status': 'planned'}
        return {**record, 'status': 'complete' if record['complete'] else 'deferred', 'log': str(log_path)}
    except (UpdateError, OSError, ValueError, TypeError, KeyError) as error:
        return {'status': 'deferred', 'complete': False, 'log': str(log_path),
                'error': str(error)}


def clean_template_cache(box, operations, *, retired=()):
    """Drop inputs of completed builds, keeping their manifests and test records."""
    for operation in operations:
        if not isinstance(operation, str) or not re.fullmatch('[0-9a-f]{24}', operation):
            raise UpdateError('template cleanup returned an invalid build identifier')
        work = CACHE / ('build-' + operation)
        if not work.exists() and not work.is_symlink():
            continue
        if work.is_symlink() or not work.is_dir() or work.stat().st_uid != os.geteuid():
            raise UpdateError('template input cache has unsafe ownership or type')
        request = load(work / 'request.json')
        origin = request.get('box')
        if (not isinstance(origin, str) or not HOST.fullmatch(origin) or
                (origin != box and operation not in retired) or request.get('operation_id') != operation or
                request != load(STATE / 'builds' / operation / 'request.json')):
            raise UpdateError('template input cache differs from its retained request')
        shutil.rmtree(work)


def cleanup_images(box, profile, keep, dry_run=False, preserve_qualified=False):
    platform_source.require_local_image(box)
    with (STATE / 'build.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        receipt = vm_local_images.export_receipt(STATE / 'builds', box, keep)
        if receipt['files']['inputs.json']['profile'] != profile:
            raise UpdateError('retained image profile differs from the cleanup request')
        result = STATE / 'builds' / keep
        with tempfile.TemporaryDirectory(prefix='cleanup-inventory-', dir=result) as temporary:
            inventory = vm_local_images.inventory(REPO, Path(temporary) / 'inventory', box,
                                      json.loads(command(['tailscale', 'status', '--json'])))
            outcome = cleanup_template(box, keep, result, dry_run, inventory, preserve_qualified)
        if not dry_run and 'retired_candidates' in outcome:
            # The dom0 plan is complete even if unknown resources remain reported.
            clean_template_cache(box, [keep, *outcome['retired_candidates']], retired=outcome['retired_candidates'])
        return outcome


def template_versions(inputs):
    """Compare installed upstream versions, without source or index identities."""
    recipe = inputs.get('profile_sha256')
    return (inputs['branch'], inputs.get('codex'), recipe, {row['name']: (row['architecture'], row['version'])
                               for row in inputs['packages']})


def reusable_template(box, inputs, result):
    """Reuse the latest qualified image only after fresh dom0 byte verification."""
    previous = []
    for directory in (STATE / 'builds').iterdir():
        if directory == result or directory.is_symlink() or not re.fullmatch('[0-9a-f]{24}', directory.name):
            continue
        try:
            outcome = load(directory / 'build-result.json')
            if outcome.get('state') != 'candidate-built':
                continue
            request = load(directory / 'request.json')
            frozen = load(directory / 'inputs.json')
            if (request.get('box') != box or
                    'app_test' in request or frozen.get('profile') != inputs['profile'] or
                    frozen.get('architecture') != inputs['architecture']):
                continue
            operation = directory.name
            candidate = load(directory / 'candidate.json')
            boot = candidate['boot_test']
            release = load(directory / 'release-evidence.json')
            if (request.get('kind') != 'klokast.vm-template-build-request.v1' or
                    request.get('operation_id') != operation or candidate.get('box') != box or
                    candidate.get('operation_id') != operation or
                    candidate.get('validation') != 'base-boot-tested' or
                    request.get('inputs_sha256') != frozen['inputs_sha256'] or
                    release != no_application_release(frozen, candidate, boot['openrc_test'],
                                                       boot['personalized_test'], boot['maintenance_restore']) or
                    outcome.get('operation_id') != operation or outcome.get('accepted') is not False or
                    outcome.get('inputs_sha256') != frozen['inputs_sha256'] or
                    outcome.get('artifacts') != candidate['artifacts'] or
                    outcome.get('release_evidence_sha256') != release['release_sha256']):
                raise UpdateError('saved image differs from its qualification evidence')
            for name, expected in (
                    ('lifecycle.json', {'stage': 'cleaned', 'domain': 'vm-build-' + operation}),
                    ('test-lifecycle.json', {'stage': 'cleaned', 'domain': 'vm-test-' + operation,
                        'openrc_domain': 'vm-openrc-' + operation, 'personalize_domain': 'vm-personalize-' + operation,
                        'profile_domain': 'vm-profile-' + operation, 'restore_domain': 'vm-restore-' + operation})):
                lifecycle = load(directory / name)
                if any(lifecycle.get(key) != value for key, value in expected.items()):
                    raise UpdateError('saved image has incomplete disposable guest cleanup')
            observed = load(directory / 'selection.json')['observed_at']
            observed = dt.datetime.fromisoformat(observed.replace('Z', '+00:00'))
            if observed.utcoffset() != dt.timedelta(0):
                raise UpdateError('saved image observation must use UTC')
            previous.append((observed, operation, directory, frozen, candidate, outcome))
        except (UpdateError, OSError, ValueError, KeyError, TypeError, AttributeError):
            print('platform-update: skip incomplete image evidence ' + directory.name, file=sys.stderr)
    if not previous:
        return None
    _observed, operation, directory, frozen, candidate, outcome = max(previous, key=lambda v: v[:2])
    if template_versions(frozen) != template_versions(inputs):
        print('platform-update: upstream versions changed; build a new image', file=sys.stderr)
        return None
    print('platform-update: upstream versions match; verify saved image ' + operation, file=sys.stderr)
    arguments = result / 'verify-args.json'
    write(arguments, {'cleanup_box': box, 'cleanup_current': operation, 'cleanup_action': 'verify',
                      'cleanup_observation_id': result.name, 'cleanup_result_dir': str(result)})
    with (result / 'verify.log').open('w') as log:
        try:
            command(['ansible-playbook', '-vv', '-i', result / 'inventory/hosts.json',
                     REPO / 'ansible/playbooks/74-platform-update-template-cleanup.yml',
                     '--limit', box + '-dom0', '-e', '@' + str(arguments)], timeout=2100, log=log)
        except UpdateError as error:
            raise UpdateError('saved image verification failed; inspect ' + str(result / 'verify.log')) from error
    verification = load(result / 'cleanup-verify.json')
    if (not isinstance(verification, dict) or verification.get('box') != box or
            verification.get('current') != operation or type(verification.get('verified')) is not bool):
        raise UpdateError('saved image verification returned an invalid result; inspect ' + str(result / 'verify.log'))
    if not verification['verified']:
        if verification.get('reason') not in ('missing', 'damaged'):
            raise UpdateError('saved image verification is uncertain; inspect ' + str(result / 'verify.log'))
        print('platform-update: saved image is ' + verification['reason'] + '; build a new image', file=sys.stderr)
        return None
    if (verification.get('candidate_sha256') != digest(candidate) or
            verification.get('artifacts') != candidate['artifacts']):
        raise UpdateError('saved image bytes or metadata differ from controller evidence')
    return {**outcome, 'state': 'candidate-reused', 'result_directory': str(result),
            'image_result_directory': str(directory), 'package_manifest': str(directory / 'inputs.json'),
            'artifact_directory': '/mnt/dom0_data/klokast-vm-templates/candidates/' + operation,
            'previous_cleanup': outcome.get('cleanup'), 'cleanup': {'status': 'not-run'}}


def image_preflight(box, operation, result):
    """Refuse missing tools, storage, or an unavailable local dom0 before downloads."""
    for name in ('/sbin/apk', 'cpio', 'ansible-playbook', 'tailscale'):
        if not shutil.which(name):
            raise UpdateError('local image preparation requires installed tool: ' + name)
    if shutil.disk_usage(CACHE).free < 6 * 1024**3:
        raise UpdateError('local image input cache requires at least 6 GiB free')
    status = json.loads(command(['tailscale', 'status', '--json']), object_pairs_hook=unique_object)
    vm_local_images.inventory(REPO, result / 'inventory', box, status)
    arguments = result / 'preflight-args.json'
    write(arguments, {'cleanup_box': box, 'cleanup_current': operation,
                      'cleanup_action': 'preflight', 'cleanup_result_dir': str(result)})
    with (result / 'preflight.log').open('w') as log:
        try:
            command(['ansible-playbook', '-vv', '-i', result / 'inventory/hosts.json',
                     REPO / 'ansible/playbooks/74-platform-update-template-cleanup.yml',
                     '--limit', box + '-dom0', '-e', '@' + str(arguments)], timeout=300, log=log)
        except UpdateError as error:
            raise UpdateError('local image preflight failed before downloads; inspect ' + str(result / 'preflight.log')) from error
    checked = load(result / 'cleanup-preflight.json')
    if checked.get('box') != box or checked.get('ready') is not True:
        raise UpdateError('local dom0 did not pass the image build preflight')


def image_receipt(box, operation=None, receive=False):
    # A local controller owns preparation. The active controller may also
    # exchange public receipts for deployment and the one-time history move.
    if socket.gethostname().split('.')[0] == box + '-ops':
        platform_source.require_local_image(box)
    else:
        require_controller()
    if not receive:
        # Export only validated public evidence. It does not select an image;
        # deployment checks the actual artifacts under the dom0 operation lock.
        # Missing or partial record sets fail validation. Artifact retirement
        # can occur after any export, so a receipt never authorizes installation.
        return vm_local_images.export_receipt(STATE / 'builds', box, operation)
    with (STATE / 'build.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        payload = sys.stdin.buffer.read(vm_local_images.MAX_RECEIPT + 1)
        if len(payload) > vm_local_images.MAX_RECEIPT:
            raise UpdateError('public image receipt exceeds the size limit')
        receipt = json.loads(payload, object_pairs_hook=unique_object)
        operation = vm_local_images.import_receipt(STATE / 'builds', box, receipt)
        return {'operation_id': operation, 'imported': True}


def prepare(box, inputs_only=False, test_app=None, profile_name='shared-alpine-v1'):
    """Build candidate evidence; this command cannot select a production release."""
    platform_source.require_local_image(box)
    if profile_name not in {'shared-alpine-v1', 'air-alpine-v1', 'ops-alpine-v1', 'vpn-egress-alpine-v1'}:
        raise UpdateError('unsupported infrastructure image profile')
    if test_app and profile_name != 'shared-alpine-v1':
        raise UpdateError('application image tests require the shared image profile')
    if not HOST.fullmatch(box) or len(box) > 31:
        raise UpdateError("template construction requires a valid box identifier")
    if not STATE.is_dir() or not CACHE.is_dir():
        raise UpdateError("install discovery directories before preparing templates")
    commit = command(["git", "-C", REPO, "rev-parse", "HEAD"]).strip()
    with (STATE / "build.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise UpdateError("another template build holds the controller build lock") from error
        profile = load(PROFILE if profile_name == 'shared-alpine-v1' else REPO / 'ansible/update-profiles' / (profile_name + '.json'))
        if profile_name in infrastructure_images.PROFILES:
            profile = infrastructure_images.qualify_profile(profile, repo=REPO)
        operation = secrets.token_hex(12)
        work = CACHE / ("build-" + operation)
        result = STATE / "builds" / operation
        result.mkdir(parents=True, mode=0o700)
        print('platform-update: check local image prerequisites on ' + box + '; operation ' + operation, file=sys.stderr)
        image_preflight(box, operation, result)
        work.mkdir(mode=0o700)
        releases, metadata_sha256 = fetch_json(profile['release_metadata'])
        observed = now()
        branch = newest_stable_branch(releases, observed, 0)
        print("platform-update: freeze template inputs for " + branch + "; operation " + operation, file=sys.stderr)
        write(result / 'selection.json', {'branch': branch, 'branch_delay_days': 0,
              'observed_at': timestamp(observed), 'metadata_sha256': metadata_sha256,
              'profile': profile['profile'], 'architecture': profile['architecture']})
        app_test = None
        if test_app:
            print('platform-update: stage the unchanged catalog image for ' + test_app, file=sys.stderr)
            app_test = vm_app_compatibility.prepare(REPO, work / 'app-test')
        inputs = vm_template_inputs.freeze(work / "inputs", profile, branch, commit,
                                          expected_profile=profile_name, apk_network_timeout=10, cache_root=CACHE)
        write(result / 'inputs.json', inputs)
        if not inputs_only and not test_app:
            reused = reusable_template(box, inputs, result)
            if reused:
                # This invocation created work under the held controller lock.
                # Only its newly resolved inputs are disposable here.
                shutil.rmtree(work)
                write(result / 'build-result.json', reused)
                print('platform-update: reuse qualified image ' + reused['operation_id'], file=sys.stderr)
                return reused
        job = REPO / "ansible/roles/vm-template-builder/files/vm-template-build-guest"
        retained = REPO / "ansible/roles/vm-retained-data/files"
        vm_template_inputs.personalization_fixture(REPO, work / 'personalization-config.json')
        capsule = vm_template_inputs.capsule(work / "inputs", work / "capsule.tar", job, job.with_name("vm-template-smoke-guest"),
                                            retained / "retained_data.py", retained / "retained_data_test.py",
                                            REPO / 'ansible/lib/vm_app_compatibility.py',
                                            REPO / 'apps/static-site/ansible/roles/static-site-runtime/files/vm_compatibility.py',
                                            REPO / 'ansible/roles/vm-personalize/files/vm_personalize.py',
                                            REPO / 'ansible/roles/vm-personalize/files/vm_personalize_test.py',
                                            work / 'personalization-config.json', expected_profile=profile_name)
        print("platform-update: assemble the scriptless builder boot environment", file=sys.stderr)
        boot = vm_template_inputs.bootstrap(work / "inputs", work / "boot", job, expected_profile=profile_name)
        request = {"kind": "klokast.vm-template-build-request.v1", "box": box,
                   "operation_id": operation, "inputs_sha256": inputs["inputs_sha256"],
                   "capsule": capsule, "bootstrap": boot}
        if app_test:
            request['app_test'] = app_test
        write(work / "request.json", request)
        write(result / "request.json", request)
        if inputs_only:
            return {"operation_id": operation, "inputs_sha256": inputs["inputs_sha256"],
                    "state": "inputs-prepared", "accepted": False, "result_directory": str(result),
                    'branch': branch, 'profile': inputs['profile'], 'architecture': inputs['architecture'],
                    'package_manifest': str(result / 'inputs.json'), 'cleanup': {'status': 'not-run'}}
        print("platform-update: build the candidate in a networkless Xen guest on " + box, file=sys.stderr)
        arguments = work / "arguments.json"
        write(arguments, {"template_box": box, "template_operation_id": operation,
                          "template_input_dir": str(work), "template_result_dir": str(result),
                          "template_app_test": bool(app_test)})
        with (result / "build.log").open("w") as log:
            try:
                command(["ansible-playbook", "-vv", "-i", result / 'inventory/hosts.json',
                         REPO / "ansible/playbooks/74-platform-update-template.yml", "--limit", box + "-dom0",
                         "-e", "@" + str(arguments)], timeout=3600, log=log)
            except UpdateError as error:
                raise UpdateError("candidate construction failed; inspect " + str(result / "build.log")) from error
        candidate = load(result / "candidate.json")
        lifecycle = load(result / "lifecycle.json")
        test_lifecycle = load(result / "test-lifecycle.json")
        base_tests = {k: True for k in ("boot", "kernel_modules", "tailscale_offline", "rootless_podman", "nftables_kernel", "retained_data_copy", "retained_data_stage", "retained_identity", "retained_partition", "personalization", "backup_restore")}
        boot_test = candidate.get("boot_test", {})
        normal = boot_test.get('openrc_test', {})
        personalized = boot_test.get('personalized_test', {})
        personalization = boot_test.get('personalization_stage', {})
        maintenance = boot_test.get('maintenance_restore', {})
        restored = maintenance.get('restore', {})
        normal_tests = dict.fromkeys(('openrc_boot', 'cgroup_v2', 'kernel_modules',
                                      'tailscale_offline', 'default_rootless_podman'), True)
        personalized_tests = dict.fromkeys(('personalized_boot', 'configuration', 'retained_mount',
            'runtime_identity', 'tailscale_retained_state', 'firewall', 'default_rootless_podman', 'packages_unchanged'), True)
        if (candidate.get("kind") != "klokast.vm-template-candidate.v1" or candidate.get("accepted") is not False or
                candidate.get("success") is not True or candidate.get("box") != box or
                candidate.get("validation") != "base-boot-tested" or candidate.get("operation_id") != operation or
                candidate.get("inputs_sha256") != inputs["inputs_sha256"] or
                candidate.get("packages") != {v["name"]: v["version"] for v in inputs["packages"]} or
                boot_test.get("kind") != "klokast.vm-template-test-result.v1" or boot_test.get("success") is not True or
                boot_test.get("operation_id") != operation or boot_test.get("inputs_sha256") != inputs["inputs_sha256"] or
                boot_test.get("tests") != base_tests or boot_test.get("kernel_release") != candidate.get("kernel_release") or
                boot_test.get("tested_root_sha256") != candidate.get("artifacts", {}).get("root", {}).get("sha256") or
                normal.get('kind') != 'klokast.vm-template-openrc-test.v1' or normal.get('success') is not True or
                normal.get('operation_id') != operation or normal.get('inputs_sha256') != inputs['inputs_sha256'] or
                normal.get('kernel_release') != candidate.get('kernel_release') or normal.get('tests') != normal_tests or
                personalized.get('kind') != 'klokast.vm-template-personalized-test.v1' or personalized.get('success') is not True or
                personalized.get('operation_id') != operation or personalized.get('inputs_sha256') != inputs['inputs_sha256'] or
                personalized.get('kernel_release') != candidate.get('kernel_release') or personalized.get('tests') != personalized_tests or
                personalized.get('production_data_used') is not False or
                personalization.get('kind') != 'klokast.vm-template-personalize-stage.v1' or personalization.get('success') is not True or
                personalization.get('operation_id') != operation or personalization.get('inputs_sha256') != inputs['inputs_sha256'] or
                personalization.get('source_root_sha256') != candidate.get('artifacts', {}).get('root', {}).get('sha256') or
                personalization.get('production_data_used') is not False or
                maintenance.get('kind') != 'klokast.vm-backup-restore-guest.v1' or maintenance.get('success') is not True or
                maintenance.get('operation_id') != operation or maintenance.get('inputs_sha256') != inputs['inputs_sha256'] or
                not isinstance(maintenance.get('request_sha256'), str) or not re.fullmatch('[0-9a-f]{64}', maintenance['request_sha256']) or
                restored.get('kind') != 'klokast.vm-backup-restore-result.v1' or
                restored.get('request_sha256') != maintenance['request_sha256'] or
                any(restored.get(k) is not True for k in ('complete_disk_restored', 'root_filesystem_checked', 'backup_unchanged')) or
                any(restored.get(k) is not False for k in ('source_freshness_verified', 'application_consistency_verified', 'adoption_accepted')) or
                restored.get('receipt_sha256') != digest({k: v for k, v in restored.items() if k != 'receipt_sha256'}) or
                any(not isinstance(personalized.get(k), str) or not re.fullmatch('[0-9a-f]{64}', personalized[k]) or
                    personalized[k] != personalization.get(k) for k in ('request_sha256', 'receipt_sha256')) or
                lifecycle.get("stage") != "cleaned" or lifecycle.get("domain") != "vm-build-" + operation or
                test_lifecycle.get('openrc_domain') != 'vm-openrc-' + operation or
                test_lifecycle.get('personalize_domain') != 'vm-personalize-' + operation or
                test_lifecycle.get('profile_domain') != 'vm-profile-' + operation or
                test_lifecycle.get('restore_domain') != 'vm-restore-' + operation or
                test_lifecycle.get("stage") != "cleaned" or test_lifecycle.get("domain") != "vm-test-" + operation):
            raise UpdateError("candidate or cleanup evidence differs from the frozen build request")
        if app_test:
            vm_app_compatibility.verify_result(boot_test.get('application_test'), app_test['selection'])
        elif 'application_test' in boot_test:
            raise UpdateError('unexpected application evidence without an approved test selection')
        release_evidence = None
        if not app_test:
            if profile_name == 'vpn-egress-alpine-v1' and boot_test.get('service_replacement', {}).get('clean_root') is not True:
                raise UpdateError('VPN image lacks clean reconstruction qualification')
            release_evidence = no_application_release(inputs, candidate, normal, personalized, maintenance,
                                                      require_vpn_service=True)
            write(result / 'release-evidence.json', release_evidence)
        outcome = {"operation_id": operation, "inputs_sha256": inputs["inputs_sha256"], "state": "candidate-built",
                "accepted": False, "result_directory": str(result), "artifacts": candidate["artifacts"],
                'branch': branch, 'profile': inputs['profile'], 'architecture': inputs['architecture'],
                'package_manifest': str(result / 'inputs.json'),
                'artifact_directory': '/mnt/dom0_data/klokast-vm-templates/candidates/' + operation,
                "validation": candidate["validation"], "base_tests": boot_test["tests"],
                'openrc_tests': normal['tests'], 'personalized_tests': personalized['tests'],
                'maintenance_restore_verified': True,
                "application_test": boot_test.get("application_test"),
                "release_evidence_sha256": release_evidence['release_sha256'] if release_evidence else None}
        outcome['cleanup'] = {'status': 'not-run'}
        # Diagnostic application tests never select or retire the normal image.
        if not app_test:
            print('platform-update: qualified build ' + operation + '; retire unused older templates', file=sys.stderr)
            outcome['cleanup'] = cleanup_template(box, operation, result)
            if outcome['cleanup'].get('complete') is True:
                try:
                    retired = outcome['cleanup']['retired_candidates']
                    clean_template_cache(box, [operation, *retired], retired=retired)
                except (UpdateError, OSError, ValueError, KeyError, TypeError) as error:
                    outcome['cleanup'].update(status='deferred', complete=False, error=str(error))
        write(result / 'build-result.json', outcome)
        return outcome


