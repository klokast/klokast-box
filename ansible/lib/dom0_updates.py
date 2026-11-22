"""Active-peer coordination of diskless updates, with explicit recovery."""
import contextlib
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import time

import air_maintenance
import infrastructure_images
import machine_status
import ops_replacement_inputs
import platform_resource_runtime
import platform_source
from platform_maintenance import progress
from platform_update_runtime import REPO, command, load, write
from platform_updates import UpdateError
from vm_lifecycle_roles import PROFILES

STATE = Path('/var/lib/klokast/updates/discovery/dom0-operations')
# A reboot normally completes in a few minutes; a host that does not answer
# within this bound needs console inspection.
VERIFY_SECONDS = 1200
# The bound for the declared guests to start and connect to Tailscale after
# the host answers.
GUEST_SECONDS = 900


def busy(value):
    """Describe in words why the runner cannot stop now."""
    causes = []
    if value.get('available') is not True:
        causes.append('the workspace is unavailable')
    if value.get('attached'):
        causes.append('%d attached tmux client(s)' % value['attached'])
    if value.get('busy_work'):
        causes.append('running work: ' + '; '.join(str(v) for v in value['busy_work'][:5]))
    if value.get('reason'):
        causes.append(str(value['reason']))
    if value.get('readiness_errors'):
        causes.append('; '.join(str(v) for v in value['readiness_errors'][:5]))
    if value.get('reservation') or value.get('pending') or value.get('maintenance'):
        causes.append('maintenance operation ' + str((value.get('reservation') or {}).get('operation', 'unknown')) +
                      ' is not finished')
    return ', '.join(causes) or 'the activity check did not report an idle runner'


def preflight(box, *, operation=None, view=None):
    """Check the runner that the host reboot stops. Other runners keep working."""
    view = view or platform_source.snapshot()
    if box not in view['instance']['boxes'] or box not in view['controllers'].values():
        raise RuntimeError('dom0 update requires a declared controller box')
    if box + '-air' not in view['instance'].get('airunners', []):
        return {'state': 'ready', 'box': box}
    value = air_maintenance.remote(box, 'image-idle')
    reservation = value.get('reservation')
    own = reservation and operation and reservation.get('operation') == operation
    if (value.get('idle') is not True or value.get('available') is not True or
            ((value.get('pending') or value.get('maintenance') or reservation) and not own)):
        raise RuntimeError('an agent session or other work is open on ' + box + '-air, which the ' + box +
                           '-dom0 reboot stops: ' + busy(value) + '. Close it, then run the command again.')
    return {'state': 'ready', 'box': box}


def unreachable(error):
    """Shorten the usual verification failure; keep other causes."""
    text = str(error)
    if 'exit status 4' in text or 'UNREACHABLE' in text or 'could not be sent to remote host' in text:
        return 'no SSH answer through Tailscale'
    return text[-300:]


def authority(box):
    view = platform_source.snapshot()
    if box not in view['instance']['boxes'] or view['controllers'].get('standby') != box:
        raise RuntimeError('dom0 update requires the declared standby controller box; move authority off the target first')
    return view


def step(box, operation, action, directory, image=None, sha256=None, guests=()):
    authority(box)
    result_file = directory / (action + '-result.json')
    arguments = directory / (action + '-args.json')
    write(arguments, {'dom0_update_box': box, 'dom0_update_operation': operation,
          'dom0_update_action': action, 'dom0_update_image': image or '', 'dom0_update_sha256': sha256 or '',
          'dom0_update_guests': list(guests), 'dom0_update_result_file': str(result_file)})
    with (directory / (action + '.log')).open('a') as log:
        command(['ansible-playbook', '-vv', '-i', REPO / 'ansible/execution-inventory/hosts',
                 REPO / 'ansible/playbooks/74-dom0-update.yml', '--limit', box + '-dom0',
                 '-e', '@' + str(arguments)], timeout=300, log=log)
    return {'state': 'started'} if action == 'reboot' else load(result_file)


def selected_image(box, image):
    # Public build evidence stays on its owning controller. Only a small
    # public selection receipt crosses this link; the bundle remains on dom0.
    script = '''set -eu
cd /home/smith/src/klokast/klokast-box
python3 - "$1" "$2" <<'PY'
import json,re,sys
from pathlib import Path
box,image=sys.argv[1:]
if not re.fullmatch('[0-9a-f]{24}',image):raise SystemExit('invalid dom0 image ID')
root=Path('/var/lib/klokast/updates/discovery/dom0-builds')/image
value=json.loads((root/'build-result.json').read_text())
if value.get('box')!=box or value.get('state')!='bundle-built':raise SystemExit('dom0 build is incomplete or belongs to another box')
print(json.dumps(value))
PY
'''
    result = subprocess.run(['tailscale', 'ssh', 'smith@' + box + '-ops', 'sh', '-s', '--', box, image],
                            input=script, text=True, capture_output=True, timeout=90)
    if result.returncode:
        raise RuntimeError('cannot read the selected same-box dom0 build: ' + result.stderr[-500:])
    return json.loads(result.stdout)


def health(box, directory, label):
    with (directory / (label + '-health.log')).open('a') as log:
        command([REPO / 'ansible/bin/platform-check', '--box', box, '--target', 'dom0'], timeout=360, log=log)


def connected():
    """Name the Tailscale machines that are connected to the control server now."""
    result = subprocess.run(['doas', '/usr/local/sbin/ts-devices-list'], capture_output=True, text=True, timeout=60)
    if result.returncode:
        raise RuntimeError('cannot list the Tailscale machines: ' + result.stderr.strip()[-300:])
    return {device.get('hostname', '').split('.')[0] for device in json.loads(result.stdout).get('devices', [])
            if device.get('connectedToControl') is True}


def reconcile_guests(box, operation, directory, view):
    """Make the guests of the host equal to the declaration. Return the warnings for the job result.

    It starts the declared guests that Xen did not start at boot, waits until each one is online,
    and then shuts down the guests that run but that the Platform does not declare.
    """
    host = box + '-dom0'
    guests = machine_status.declared_guests(view['instance'], machine_status.resource_plan(), box)
    deadline = time.monotonic() + GUEST_SECONDS
    resume = ' Resume operation %s when they answer: kk reboot %s --resume %s' % (operation, host, operation)
    shown = None
    progress('starting the VMs of %s: %s' % (host, ', '.join(sorted(guests.values()))))
    while True:
        result = step(box, operation, 'start-guests', directory, guests=sorted(guests))
        if result['state'] != 'waiting':
            break
        if time.monotonic() >= deadline:
            raise RuntimeError('Xen did not start the VMs of %s within %d minutes: %s. Inspect the host console.%s' % (
                host, GUEST_SECONDS // 60, result['reason'], resume))
        text = 'waiting for Xen to start the VMs of ' + host + ': ' + result['reason']
        if text != shown:
            progress(text); shown = text
        time.sleep(10)
    if result['failed']:
        raise RuntimeError('Xen could not start ' + '; '.join(guests[name] + ' (' + error + ')' for name, error in
                                                         sorted(result['failed'].items())) +
                           ' on ' + host + '. Full log on the controller: ' + str(directory / 'start-guests.log') + '.' +
                           resume)
    warnings = []
    started = [guests[name] for name in result['started']]
    if started:
        progress('started ' + ', '.join(started) + ', which Xen did not start at boot')
        warnings.append('The job started ' + ', '.join(started) + ', which Xen did not start at boot.')
    # Xen starts the guests of /etc/xen/auto at each boot, also after a power cut.
    for name in sorted(set(guests) - set(result['autostart'])):
        warnings.append('Xen does not start %s at boot: /etc/xen/auto/%s.cfg is missing on %s.' % (guests[name], name, host))
    for name in sorted(set(result['autostart']) - set(guests)):
        warnings.append('/etc/xen/auto/%s.cfg on %s starts the VM %s at each boot, but the Platform does not '
                        'declare it.' % (name, host, name))
    error = None
    while True:
        # A machine that stops is disconnected from Tailscale within seconds,
        # so a connected machine runs its new boot.
        try:
            online = connected() & set(guests.values()); error = None
        except (RuntimeError, OSError, ValueError, subprocess.SubprocessError) as failure:
            online = set(); error = failure
        waiting = sorted(set(guests.values()) - online)
        if not waiting:
            progress('all %d VMs of %s are online: %s' % (len(guests), host, ', '.join(sorted(guests.values()))))
            break
        if time.monotonic() >= deadline:
            raise RuntimeError('%s did not come online within %d minutes after the reboot of %s%s. Check that %s '
                               'in xl list on %s, and read its console with xl console NAME.%s' % (
                                   ', '.join(waiting), GUEST_SECONDS // 60, host,
                                   ' (last cause: ' + str(error)[-300:] + ')' if error else '',
                                   'it runs' if len(waiting) == 1 else 'they run', host, resume))
        text = '%d of %d VMs of %s are online; waiting for %s' % (
            len(guests) - len(waiting), len(guests), host, ', '.join(waiting))
        if text != shown:
            progress(text); shown = text
        time.sleep(10)
    undeclared = sorted(set(result['running']) - set(guests))
    if undeclared:
        progress('stopping ' + ', '.join(undeclared) + ' on ' + host + ', which the Platform does not declare')
        stopped = step(box, operation, 'stop-guests', directory, guests=undeclared)
        if stopped['failed']:
            raise RuntimeError('cannot stop ' + '; '.join(name + ' (' + cause + ')' for name, cause in
                                                     sorted(stopped['failed'].items())) +
                               ' on ' + host + ', which the Platform does not declare. The job never force-destroys '
                               'a VM: inspect it on the host console. Full log on the controller: ' +
                               str(directory / 'stop-guests.log') + '. Then resume operation ' + operation + '.')
        if stopped['stopped']:
            warnings.append('The job stopped the VM ' + ', '.join(stopped['stopped']) + ' on ' + host +
                            ', which the Platform does not declare.')
    return warnings


def execute(box, *, image=None, resume=None, operation=None, dry_run=False, abort=False, status=False, lock_held=False,
            verify_reboot=False, manual=False):
    # operation names a new or a pending transaction; resume requires a pending one.
    view = authority(box)
    host = box + '-dom0'
    for value in (image, resume, operation):
        if value and not re.fullmatch('[0-9a-f]{24}', value):
            raise RuntimeError('dom0 image and operation IDs must have 24 hexadecimal characters')
    if dry_run:
        return {'state': 'planned', 'box': box, 'role': 'dom0', 'image': image,
                'reboot': True, 'coordinator': view['controllers']['active'], 'one_box_per_run': True}
    os.umask(0o077)
    with contextlib.nullcontext() if lock_held else platform_resource_runtime.vm_update_installation_lock():
        operation = resume or operation or secrets.token_hex(12)
        directory = STATE / operation
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        progress('reading the update state of ' + host)
        state = step(box, operation, 'status', directory)
        if status:
            return state
        pending = state.get('pending')
        if pending and pending['operation'] != operation:
            raise RuntimeError('unfinished dom0 update; resume with: ansible/bin/platform-update update --box ' + box +
                               ' --role dom0 --resume ' + pending['operation'])
        has_air = box + '-air' in view['instance'].get('airunners', [])
        accepted = state.get('accepted') or {}
        if not pending and accepted.get('operation') == operation:
            # An earlier run accepted the host after the VMs were online again;
            # only the steps after the accept remain.
            progress(host + ' passed and recorded the ' + ('verified reboot' if accepted.get('reboot_test') else
                     'update to image ' + str(accepted.get('image'))) + ' in an earlier run; its VMs do not stop again')
            if has_air:
                progress('restoring the ' + box + '-air workspace')
                air_maintenance.release(box, operation)
            return {'state': 'complete', 'role': 'dom0', 'box': box, 'operation': operation,
                    'image': accepted.get('image'), 'replaced': not accepted.get('reboot_test', False),
                    'reboot_verified': True, 'warnings': []}
        if resume and not pending:
            raise RuntimeError('no matching pending dom0 update exists')
        if abort:
            result = step(box, operation, 'abort', directory)
            if has_air: air_maintenance.release(box, operation)
            return result
        # A reboot or a selected image does not wait for a build, so the runner
        # closes to new sessions right after its check.
        closed = bool(not pending and manual and has_air and (verify_reboot or image))
        if closed:
            progress('checking that no agent session is open on ' + box + '-air')
            preflight(box, operation=operation, view=view)
            progress('saving the ' + box + '-air workspace and closing it to new sessions')
            air_maintenance.before_stop(box, operation, image=True)
        try:
            if not pending and verify_reboot:
                progress('checking the health of ' + host + ' before the reboot')
                health(box, directory, 'before-reboot-test')
                pending = step(box, operation, 'test-reboot', directory)
            if not pending:
                if image is None and not state.get('accepted'):
                    return {'state': 'skipped', 'role': 'dom0', 'box': box,
                            'reason': 'first hardware qualification requires an explicit --image'}
                if manual and not closed:
                    progress('checking that no agent session is open on ' + box + '-air')
                    preflight(box, operation=operation, view=view)
                elif not manual and has_air and not air_maintenance.remote(box, 'image-idle')['idle']:
                    return {'state': 'skipped', 'box': box, 'role': 'dom0', 'reason': 'runner busy'}
                progress('preparing the dom0 image on ' + box + '-ops; a new build can take an hour' if not image else
                         'reading the selected dom0 build ' + image)
                built = selected_image(box, image) if image else infrastructure_images.local_action(
                    box, 'dom0-alpine-v1', ops_replacement_inputs.revision(REPO, True), 'prepare')
                image = built['operation_id']
                progress('checking the health of ' + host + ' before the update')
                health(box, directory, 'before')
                progress('copying the boot files of image ' + image + ' to the EFI partition of ' + host)
                pending = step(box, operation, 'prepare', directory, image, built['bundle']['sha256'])
                if pending['state'] == 'unchanged':
                    if closed: air_maintenance.release(box, operation)
                    return dict(pending, role='dom0', replaced=False)
        except BaseException:
            # The host has no pending change yet, so the runner opens again.
            if closed and not pending: air_maintenance.release(box, operation)
            raise
        # Reserve launcher access and checkpoint the runner before any shutdown.
        # Resume after reboot uses the saved reservation rather than requiring idle.
        if manual and not closed and pending['state'] in ('prepared', 'selecting', 'selected'):
            progress('checking again that no agent session is open on ' + box + '-air')
            preflight(box, operation=operation, view=view)
        if has_air and not closed and pending['state'] in ('prepared', 'selecting', 'selected'):
            progress('saving the ' + box + '-air workspace and closing it to new sessions')
            air_maintenance.before_stop(box, operation, image=True)
        if pending['state'] in ('prepared', 'selecting'):
            # A reboot test and a resumed selection keep the current boot entries.
            progress('setting ' + host + ' to start image ' + pending['image'] + ' at the next boot only; '
                     'if that boot fails, the next one starts the current image again')
        pending = step(box, operation, 'select', directory)
        if manual and pending['state'] == 'selected':
            preflight(box, operation=operation, view=authority(box))
        if pending['state'] in ('selected', 'rebooting'):
            progress('stopping the VMs of ' + host)
            step(box, operation, 'reboot', directory)
        started = time.monotonic()
        error = None
        shown = None
        while time.monotonic() - started < VERIFY_SECONDS:
            minutes = int(time.monotonic() - started) // 60
            if minutes != shown:
                # One stage line each minute shows that the wait continues and
                # why the last verification failed.
                progress('waiting for %s to pass the verification after the reboot: %d of %d minutes%s' % (
                    host, minutes, VERIFY_SECONDS // 60, '' if error is None else '; last cause: ' + unreachable(error)))
                shown = minutes
            try:
                accepted = step(box, operation, 'verify', directory)
                break
            except (UpdateError, RuntimeError, OSError, subprocess.SubprocessError) as failure:
                error = failure
                time.sleep(10)
        else:
            # A trial selection is one-time: the next boot starts the accepted generation.
            recover = ('A power cycle of the box starts the accepted generation again'
                       if pending.get('trial') else 'Check the dom0 console')
            raise RuntimeError(host + ' did not pass the verification within %d minutes after the reboot (last cause: %s). '
                               'The host and its guests can run without Tailscale. %s. Resume operation '
                               '%s when the host answers. Full log on the controller: %s' % (
                                   VERIFY_SECONDS // 60, unreachable(error), recover, operation, directory / 'verify.log'))
        if accepted['state'] == 'fallback':
            progress('recording the rollback: ' + host + ' started its current image again')
            step(box, operation, 'abort', directory)
            reconcile_guests(box, operation, directory, view)
            progress('checking the health of ' + host + ' after the rollback')
            health(box, directory, 'after')
            if has_air:
                progress('restoring the ' + box + '-air workspace')
                air_maintenance.release(box, operation)
            raise RuntimeError(host + ' did not complete the trial boot of image %s and runs its accepted generation '
                               'again. The update is rolled back. Full log on the controller: %s' % (
                                   accepted['image'], directory / 'verify.log'))
        warnings = reconcile_guests(box, operation, directory, view)
        progress('checking the health of ' + host + ' after the reboot')
        health(box, directory, 'after')
        progress('recording the verified reboot of ' + host if pending.get('reboot_test') else
                 'making image ' + pending['image'] + ' the default boot image of ' + host)
        accepted = step(box, operation, 'accept', directory)
        if has_air:
            progress('restoring the ' + box + '-air workspace')
            air_maintenance.release(box, operation)
        return {'state': 'complete', 'role': 'dom0', 'box': box, 'operation': operation,
                'image': accepted['image'], 'replaced': not accepted.get('reboot_test', False),
                'reboot_verified': True, 'warnings': warnings}
