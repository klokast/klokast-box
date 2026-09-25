"""Closed router release and decision contracts. No Platform mutations.

Evidence is not authority. Callers must read accepted assignments from protected
box state and policy from the verified Instance reader. A report cannot authorize
initial installation, adoption, or replacement.
"""
import datetime as dt
import re

from platform_updates import UpdateError, branch_number, digest, fresh, timestamp
from platform_update_metadata import adjacent_stable_branch

PROFILE = 'router-alpine-v1'
RELEASE = 'klokast.router-release.v1'
DECISION = 'klokast.router-update-check.v1'
HASH = re.compile(r'[0-9a-f]{64}')
NAME = re.compile(r'[a-zA-Z0-9][a-zA-Z0-9+_.-]*')
VERSION = re.compile(r'[a-zA-Z0-9][a-zA-Z0-9+_.~-]*')
BOX = re.compile(r'[a-z0-9][a-z0-9-]{0,30}')


def match(pattern, value):
    return isinstance(value, str) and pattern.fullmatch(value) is not None


def seal(value, field='receipt_sha256'):
    if field in value:
        raise UpdateError('cannot seal a record that already has a checksum')
    return {**value, field: digest(value)}


def closed(value, fields, label):
    if not isinstance(value, dict) or set(value) != set(fields.split()):
        raise UpdateError(label + ' has missing or unknown fields')


def verify_seal(value, field='receipt_sha256'):
    if (not isinstance(value, dict) or not match(HASH, value.get(field)) or
            value[field] != digest({k: v for k, v in value.items() if k != field})):
        raise UpdateError('record checksum does not match its contents')


def validate_profile(profile):
    closed(profile, 'kind profile architecture roles branch_policy state_contract packages '
           'repositories repository_origin release_metadata', 'router profile')
    if (profile['kind'] != 'klokast.vm-template-profile.v1' or profile['profile'] != PROFILE or
            profile['architecture'] != 'x86_64' or profile['roles'] != ['router'] or
            profile['branch_policy'] != 'tested-stable' or
            profile['state_contract'] != 'klokast.router-state.v1' or
            profile['repositories'] != ['main', 'community'] or
            profile['repository_origin'] != 'https://dl-cdn.alpinelinux.org/alpine' or
            profile['release_metadata'] != 'https://alpinelinux.org/releases.json'):
        raise UpdateError('unsupported router profile, architecture, or upstream origin')
    packages = profile['packages']
    if (not isinstance(packages, list) or not packages or
            any(not match(NAME, p) for p in packages) or len(set(packages)) != len(packages) or
            not {'alpine-base', 'linux-virt', 'mkinitfs', 'tailscale', 'tailscale-openrc',
                 'dhcpcd', 'dnsmasq', 'iproute2', 'nftables', 'python3', 'e2fsprogs'} <= set(packages)):
        raise UpdateError('router profile has an incomplete package request set')


def validate_inputs(inputs, profile, engine):
    """Check receipt structure; native APK must separately verify payload bytes."""
    validate_profile(profile)
    closed(inputs, 'kind engine_commit profile profile_sha256 branch architecture world repositories '
           'keys indexes packages inputs_sha256', 'router inputs')
    verify_seal(inputs, 'inputs_sha256')
    branch_number(inputs['branch'])
    if (not match(re.compile(r'[0-9a-f]{40}'), engine) or inputs['engine_commit'] != engine or
            inputs['kind'] != 'klokast.vm-template-inputs.v1' or inputs['profile'] != PROFILE or
            inputs['profile_sha256'] != digest(profile) or inputs['architecture'] != 'x86_64' or
            inputs['world'] != sorted(profile['packages']) or inputs['repositories'] != [
                profile['repository_origin'] + '/' + inputs['branch'] + '/' + r
                for r in profile['repositories']]):
        raise UpdateError('router inputs differ from the engine, profile, or repositories')
    for field in ('keys', 'indexes'):
        values = inputs[field]
        if (not isinstance(values, dict) or not values or len(values) > 64 or
                any(not isinstance(k, str) or '/' in k or not match(HASH, v) for k, v in values.items())):
            raise UpdateError('router inputs have invalid signing key or index evidence')
    if len(inputs['indexes']) != 2:
        raise UpdateError('router inputs require both authenticated repository indexes')
    packages = inputs['packages']
    if not isinstance(packages, list) or not 1 <= len(packages) <= 512:
        raise UpdateError('router package closure is missing or too large')
    names = []
    for p in packages:
        closed(p, 'name version origin architecture file bytes sha256', 'router package')
        if (not match(NAME, p['name']) or not match(VERSION, p['version']) or
                not match(NAME, p['origin']) or p['architecture'] not in ('x86_64', 'noarch') or
                not match(HASH, p['sha256']) or type(p['bytes']) is not int or
                not 0 < p['bytes'] <= 512 * 1024 * 1024 or
                p['file'] != 'packages/' + p['name'] + '-' + p['version'] + '.apk'):
            raise UpdateError('router package has an invalid identity')
        names.append(p['name'])
    if names != sorted(set(names)) or not set(inputs['world']) <= set(names):
        raise UpdateError('router package closure is incomplete or duplicated')


def effective_inputs(inputs):
    # APK index changes, signing-key rotation, and an announcement alone do not
    # change installed bytes. Engine and profile changes do affect the recipe.
    return digest({k: inputs[k] for k in ('engine_commit', 'profile', 'profile_sha256',
                                        'branch', 'architecture', 'world', 'packages')})


def validate_release(receipt, profile, engine):
    closed(receipt, 'kind profile engine_commit inputs kernel_release artifacts generic_tests '
           'receipt_sha256', 'router release')
    verify_seal(receipt)
    validate_inputs(receipt['inputs'], profile, engine)
    if (receipt['kind'] != RELEASE or receipt['profile'] != PROFILE or
            receipt['engine_commit'] != engine or not match(VERSION, receipt['kernel_release'])):
        raise UpdateError('router release has a different engine, profile, or kernel')
    closed(receipt['artifacts'], 'os kernel initramfs', 'router artifacts')
    if any(not match(HASH, value) for value in receipt['artifacts'].values()):
        raise UpdateError('router release artifact hashes are invalid')
    closed(receipt['generic_tests'], 'identity_absent exact_packages kernel_modules openrc', 'generic tests')
    if any(value is not True for value in receipt['generic_tests'].values()):
        raise UpdateError('router generic template has not passed every native test')


def dispatch(role):
    if role == 'router':
        return PROFILE
    if role in ('bak', 'dmz', 'iot'):
        return 'shared-alpine-v1'
    raise UpdateError('unsupported VM update role')


def lifecycle(mode, *, box, role, existing_disk, installation, accepted, bootstrap_authorized,
              replacement_authorized):
    """A missing assignment never makes an existing disk a blank target."""
    if role != 'router' or not match(BOX, box):
        raise UpdateError('router lifecycle requires an exact box and router role')
    if mode == 'initial-install':
        if not bootstrap_authorized or replacement_authorized or accepted is not None:
            raise UpdateError('initial installation requires bootstrap authority and no accepted assignment')
        if existing_disk is not None:
            if (not isinstance(installation, dict) or installation.get('box') != box or
                    installation.get('role') != role or installation.get('disk') != existing_disk or
                    installation.get('stage') not in ('allocated', 'built', 'enrolled', 'verified')):
                raise UpdateError('existing router disk has no matching interrupted-install record')
            if installation['stage'] in ('enrolled', 'verified') and not installation.get('machine_id'):
                raise UpdateError('interrupted installation has lost its enrolled machine identity')
            return 'resume'
        if installation is not None:
            raise UpdateError('recorded installation disk is missing; reconstruction requires review')
        return 'allocate'
    if mode == 'replacement':
        if (not replacement_authorized or bootstrap_authorized or not isinstance(accepted, dict) or
                accepted.get('box') != box or accepted.get('role') != role or
                existing_disk is None or accepted.get('disk') != existing_disk):
            raise UpdateError('replacement requires exact accepted assignment and replacement authority')
        return 'prepare'
    raise UpdateError('router lifecycle mode must be initial-install or replacement')


def available_branches(releases, current, now, delay):
    """Retain availability separately from the adjacent-branch decision."""
    if not isinstance(releases, dict) or not isinstance(releases.get('release_branches'), list):
        raise UpdateError('Alpine release metadata is unavailable')
    current_number = branch_number(current)
    entries, seen = [], set()
    for row in releases['release_branches']:
        if not isinstance(row, dict):
            raise UpdateError('Alpine branch metadata is invalid')
        branch = row.get('rel_branch')
        if not isinstance(branch, str) or not re.fullmatch(r'v[0-9]+\.[0-9]+', branch):
            continue
        if branch in seen:
            raise UpdateError('Alpine branch metadata is duplicated')
        seen.add(branch)
        if branch_number(branch) < current_number:
            continue
        try:
            patches = [v['version'] for v in row['releases']
                       if re.fullmatch(re.escape(branch[1:]) + r'\.[0-9]+', v['version']) and
                       dt.date.fromisoformat(v['date']) <= now.date()]
            support = {'main': row['eol_date'], **{r['name']: r['eol_date'] for r in row['repos'] if r.get('eol_date')}}
            support = {r: {'ends': support[r], 'supported': now.date() < dt.date.fromisoformat(support[r])}
                       for r in ('main', 'community')}
        except (KeyError, TypeError, ValueError) as error:
            raise UpdateError('Alpine release or repository support evidence is incomplete') from error
        if patches:
            entries.append({'branch': branch, 'latest_patch': max(patches, key=lambda p: tuple(map(int, p.split('.')))),
                            'support': support})
    if current not in {r['branch'] for r in entries}:
        raise UpdateError('accepted router branch is absent from current Alpine metadata')
    eligible = adjacent_stable_branch(current, releases, now, delay)
    for entry in entries:
        entry['eligible'] = entry['branch'] in (current, eligible)
    return sorted(entries, key=lambda r: branch_number(r['branch'])), eligible


def package_difference(old, new, compare):
    old, new = ({p['name']: p for p in rows} for rows in (old, new))
    result = []
    for name in sorted(old.keys() | new.keys()):
        before, after = old.get(name), new.get(name)
        if before == after:
            continue
        if before and after:
            order = compare(before['version'], after['version'])
            if order not in ('<', '=', '>'):
                raise UpdateError('APK returned an invalid package version comparison')
            if order == '>':
                raise UpdateError('unexplained package downgrade: ' + name)
            if order == '=' and before['sha256'] != after['sha256']:
                raise UpdateError('package bytes changed without a version change: ' + name)
        result.append({'name': name, 'old': before['version'] if before else None,
                       'new': after['version'] if after else None,
                       'change': 'added' if before is None else 'removed' if after is None else 'updated'})
    return result


def check(*, box, role, accepted, live, metadata, candidates, policy, policy_sha256, profile, engine,
          now, compare):
    """Classify complete evidence. Any missing evidence prevents `unchanged`."""
    report = {'kind': DECISION, 'box': box, 'role': role, 'checked_at': timestamp(now),
              'policy_sha256': policy_sha256, 'accepted_sha256': None, 'status': 'failed',
              'reason': '', 'availability': [], 'selected_branch': None,
              'candidate_inputs_sha256': None, 'effective_inputs_sha256': None,
              'package_difference': [], 'source_sha256': None}
    try:
        if role != 'router' or not match(BOX, box) or not match(HASH, policy_sha256):
            raise UpdateError('router check requires an exact router target and policy receipt')
        if (not isinstance(policy, dict) or policy.get('branch-policy') != 'tested-stable' or
                type(policy.get('branch-delay-days')) is not int or not 0 <= policy['branch-delay-days'] <= 365 or
                type(policy.get('report-max-age-hours')) is not int or not 24 <= policy['report-max-age-hours'] <= 168 or
                role not in policy.get('targets', {}).get(box, [])):
            raise UpdateError('router target or branch policy is outside declared update intent')
        if accepted is None:
            report.update(status='deferred', reason='router baseline requires supervised adoption')
            return seal(report, 'report_sha256')
        closed(accepted, 'box role generation release', 'accepted router evidence')
        if accepted['box'] != box or accepted['role'] != role or not match(HASH, accepted['generation']):
            raise UpdateError('accepted generation belongs to another box or role')
        release = accepted['release']
        # An accepted recipe may use an earlier engine. Validate it against its
        # recorded engine; compare the candidate with the newly approved engine.
        validate_release(release, profile, release['engine_commit'])
        report['accepted_sha256'] = digest(accepted)
        if (not isinstance(live, dict) or not fresh(live.get('observed_at'), now, dt.timedelta(minutes=15)) or
                live.get('box') != box or live.get('role') != role or live.get('generation') != accepted['generation'] or
                live.get('packages') != {p['name']: p['version'] for p in release['inputs']['packages']} or
                live.get('kernel_release') != release['kernel_release'] or
                live.get('boot_artifacts') != {k: release['artifacts'][k] for k in ('kernel', 'initramfs')} or
                live.get('configuration_verified') is not True):
            raise UpdateError('live router evidence is missing, stale, or differs from its accepted release')
        if live.get('overlay_ipv6_enabled') is not False:
            raise UpdateError('enabled or unknown overlay IPv6 state cannot be reconstructed')
        if (not isinstance(metadata, dict) or
                not fresh(metadata.get('observed_at'), now, dt.timedelta(hours=policy['report-max-age-hours'])) or
                not match(HASH, metadata.get('sha256'))):
            report.update(status='deferred', reason='fresh Alpine release metadata is unavailable')
            return seal(report, 'report_sha256')
        report['source_sha256'] = metadata['sha256']
        availability, eligible = available_branches(metadata.get('releases'), release['inputs']['branch'], now,
                                                   policy['branch-delay-days'])
        report['availability'] = availability
        branch = eligible or release['inputs']['branch']
        report['selected_branch'] = branch
        evidence = candidates.get(branch)
        if not isinstance(evidence, dict) or evidence.get('status') == 'unavailable':
            report.update(status='deferred', reason='fresh authenticated package closure is unavailable')
            return seal(report, 'report_sha256')
        if evidence.get('status') != 'verified':
            raise UpdateError('APK signature verification or dependency resolution failed')
        if not fresh(evidence.get('observed_at'), now, dt.timedelta(hours=policy['report-max-age-hours'])):
            report.update(status='deferred', reason='authenticated package closure is stale')
            return seal(report, 'report_sha256')
        inputs = evidence['inputs']
        validate_inputs(inputs, profile, engine)
        if inputs['branch'] != branch:
            raise UpdateError('candidate package closure belongs to a different branch')
        report['candidate_inputs_sha256'] = inputs['inputs_sha256']
        report['effective_inputs_sha256'] = effective_inputs(inputs)
        report['package_difference'] = package_difference(release['inputs']['packages'], inputs['packages'], compare)
        changed = effective_inputs(release['inputs']) != effective_inputs(inputs)
        excluded = any(r.get('box') == box and r.get('role') == role for r in policy.get('exclusions', []))
        if policy.get('enabled') is not True or excluded:
            report.update(status='deferred', reason='router update policy is disabled or target is excluded')
        elif changed:
            report.update(status='update-required', reason='eligible router build inputs changed')
        elif any(not r['eligible'] for r in availability):
            report.update(status='deferred', reason='current inputs are unchanged; a future branch is held')
        else:
            report.update(status='unchanged', reason='all effective router build inputs match')
    except (UpdateError, ValueError, TypeError, KeyError) as error:
        report.update(status='failed', reason=str(error))
    return seal(report, 'report_sha256')


def require_preparation(report, *, box, policy_sha256, accepted_sha256, inputs, now, max_age_hours):
    verify_seal(report, 'report_sha256')
    if (report.get('kind') != DECISION or report.get('role') != 'router' or report.get('box') != box or
            report.get('status') != 'update-required' or report.get('policy_sha256') != policy_sha256 or
            report.get('accepted_sha256') != accepted_sha256 or
            report.get('candidate_inputs_sha256') != inputs.get('inputs_sha256') or
            report.get('effective_inputs_sha256') != effective_inputs(inputs) or
            not fresh(report.get('checked_at'), now, dt.timedelta(hours=max_age_hours))):
        raise UpdateError('router preparation decision is stale or no longer matches policy, target, or inputs')
