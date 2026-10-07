"""First-router input and release contracts; retained record formats stay compatible."""
import datetime as dt
import re

from platform_updates import UpdateError, branch_number, digest
from platform_update_metadata import newest_stable_branch

PROFILE = 'router-alpine-v2'
RELEASE = 'klokast.router-release.v2'
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


def validate_initial_selection(selection, resolved, schedule, engine, releases, metadata_sha256, now):
    """Bind a first-install input choice to the checked-in bootstrap defaults.

    This validates the saved choice. The issuer must still check current
    upstream eligibility and native release evidence before using it.
    """
    closed(selection, 'kind engine_commit observed_at branch branch_delay_days schedule_sha256 '
           'metadata_sha256 inputs_sha256 replacement_authorized receipt_sha256',
           'router first-install selection')
    verify_seal(selection)
    policy = schedule.get('policy') if isinstance(schedule, dict) else None
    if (selection['kind'] != 'klokast.router-bootstrap-input-selection.v1' or
            not match(re.compile(r'[0-9a-f]{40}'), engine) or selection['engine_commit'] != engine or
            selection['replacement_authorized'] is not False or
            not isinstance(policy, dict) or schedule.get('kind') != 'klokast.vm-update-schedule.v1' or
            policy.get('branch-policy') != 'tested-stable' or
            type(policy.get('branch-delay-days')) is not int or
            not 0 <= policy['branch-delay-days'] <= 365 or
            type(policy.get('report-max-age-hours')) is not int or
            not 24 <= policy['report-max-age-hours'] <= 168 or
            selection['branch_delay_days'] != policy['branch-delay-days'] or
            selection['schedule_sha256'] != digest(schedule) or
            not match(HASH, metadata_sha256) or selection['metadata_sha256'] != metadata_sha256 or
            not isinstance(resolved, dict) or selection['branch'] != resolved.get('branch') or
            not match(HASH, selection['inputs_sha256']) or
            selection['inputs_sha256'] != resolved.get('inputs_sha256')):
        raise UpdateError('router first-install choice differs from the verified policy or frozen inputs')
    branch_number(selection['branch'])
    if not isinstance(now, dt.datetime) or now.tzinfo is None:
        raise UpdateError('router first-install choice needs current UTC time')
    try:
        observed = dt.datetime.strptime(selection['observed_at'], '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=dt.timezone.utc)
    except (TypeError, ValueError) as error:
        raise UpdateError('router first-install choice has no valid UTC time') from error
    if observed > now or now - observed > dt.timedelta(hours=policy['report-max-age-hours']):
        raise UpdateError('router first-install choice is stale or from the future')
    if select_branch('initial-install', releases, now, policy) != selection['branch']:
        raise UpdateError('router first-install branch is no longer eligible under the verified policy')
    return selection


def validate_profile(profile, *, historical=False):
    closed(profile, 'kind profile architecture roles branch_policy state_contract packages '
           'repositories repository_origin release_metadata', 'router profile')
    if (profile['kind'] != 'klokast.vm-template-profile.v1' or profile['profile'] != PROFILE or
            profile['architecture'] != 'x86_64' or profile['roles'] != ['router'] or
            profile['branch_policy'] != 'tested-stable' or
            profile['state_contract'] not in (
                ('klokast.router-state.v1', 'klokast.router-state.v2') if historical else
                ('klokast.router-state.v2',)) or
            profile['repositories'] != ['main', 'community'] or
            profile['repository_origin'] != 'https://dl-cdn.alpinelinux.org/alpine' or
            profile['release_metadata'] != 'https://alpinelinux.org/releases.json'):
        raise UpdateError('unsupported router profile, architecture, or upstream origin')
    packages = profile['packages']
    if (not isinstance(packages, list) or not packages or
            any(not match(NAME, p) for p in packages) or len(set(packages)) != len(packages) or
            not {'alpine-base', 'linux-virt', 'mkinitfs', 'openssh',
                 'dhcpcd', 'dnsmasq', 'iproute2', 'nftables', 'python3', 'e2fsprogs'} <= set(packages)):
        raise UpdateError('router profile has an incomplete package request set')
    if {'tailscale', 'tailscale-openrc'} & set(packages):
        raise UpdateError('upstream Tailscale must not be requested from Alpine APK')


def validate_inputs(inputs, profile, engine, *, historical=False):
    """Check receipt structure; native APK must separately verify payload bytes."""
    validate_profile(profile, historical=historical)
    closed(inputs, 'kind engine_commit profile profile_sha256 branch architecture world repositories '
           'keys indexes packages tailscale inputs_sha256', 'router inputs')
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
    import router_tailscale
    router_tailscale.validate(inputs['tailscale'])


def effective_inputs(inputs):
    # APK index changes, signing-key rotation, and an announcement alone do not
    # change installed bytes. Engine and profile changes do affect the recipe.
    component = inputs['tailscale']
    return digest({**{k: inputs[k] for k in ('engine_commit', 'profile', 'profile_sha256',
                                           'branch', 'architecture', 'world', 'packages')},
                   'tailscale': {k: component[k] for k in ('version', 'sha256',
                                                           'tailscale_sha256', 'tailscaled_sha256',
                                                           'openrc_sha256')}})


def validate_release(receipt, profile, engine, *, historical=False):
    closed(receipt, 'kind profile engine_commit inputs kernel_release artifacts generic_tests '
           'runtime_packages runtime_tests receipt_sha256', 'router release')
    verify_seal(receipt)
    validate_inputs(receipt['inputs'], profile, engine, historical=historical)
    if (receipt['kind'] != RELEASE or receipt['profile'] != PROFILE or
            receipt['engine_commit'] != engine or not match(VERSION, receipt['kernel_release'])):
        raise UpdateError('router release has a different engine, profile, or kernel')
    closed(receipt['artifacts'], 'os kernel initramfs', 'router artifacts')
    if any(not match(HASH, value) for value in receipt['artifacts'].values()):
        raise UpdateError('router release artifact hashes are invalid')
    closed(receipt['generic_tests'], 'identity_absent exact_packages upstream_tailscale kernel_modules openrc', 'generic tests')
    if any(value is not True for value in receipt['generic_tests'].values()):
        raise UpdateError('router generic template has not passed every native test')
    import router_finalize
    try:
        router_finalize.validate_packages({p['name']: p['version'] for p in receipt['inputs']['packages']},
                                          receipt['runtime_packages'], receipt['inputs']['world'])
    except ValueError as error:
        raise UpdateError(str(error)) from error
    closed(receipt['runtime_tests'], 'frozen_packages no_openssh_server locked_root pinned_world', 'router runtime tests')
    if any(value is not True for value in receipt['runtime_tests'].values()):
        raise UpdateError('router release lacks complete native runtime finalization evidence')


def rendered_includes(compiled, box):
    """Select reconstructable files from compiler output, never a running router."""
    if (not isinstance(compiled, dict) or compiled.get('compiler') != 'platform-resources' or
            not match(HASH, compiled.get('registry_sha256')) or
            not isinstance(compiled.get('box_configs'), dict) or box not in compiled['box_configs'] or
            not isinstance(compiled.get('app_resource_effective_files'), list)):
        raise UpdateError('router include inspection requires complete current resource compiler output')
    files = {
        '/etc/klokast/app-resources/router-forward.nft':
            '# Legacy aggregate include retired by keyed platform-resources.\n',
        '/etc/klokast/app-resources/router-forward.d/000-empty.nft':
            '# Empty placeholder so nft include globs always match.\n',
    }
    for row in compiled['app_resource_effective_files']:
        if not isinstance(row, dict):
            raise UpdateError('resource compiler has an invalid effective file')
        if row.get('node') != box or row.get('host_role') != 'router':
            continue
        name, content = row.get('filename'), row.get('content')
        if (row.get('kind') != 'router-forward' or not isinstance(name, str) or
                not re.fullmatch(r'[a-zA-Z0-9_-]+\.nft', name) or
                not isinstance(content, str) or not 0 < len(content.encode()) <= 128 * 1024):
            raise UpdateError('resource compiler has an unsupported router include')
        path = '/etc/klokast/app-resources/router-forward.d/' + name
        if path in files:
            raise UpdateError('resource compiler has duplicate router includes')
        files[path] = content
    if len(files) > 1024 or sum(len(content.encode()) for content in files.values()) > 512 * 1024:
        raise UpdateError('compiled router includes exceed the bounded personalization contract')
    return files


def select_branch(mode, releases, now, policy, *, current=None):
    """Select inputs, not execution authority, for the common router builder."""
    if (not isinstance(policy, dict) or policy.get('branch-policy') != 'tested-stable' or
            type(policy.get('branch-delay-days')) is not int or
            not 0 <= policy['branch-delay-days'] <= 365):
        raise UpdateError('router input selection requires the configured stable branch policy and delay')
    delay = policy['branch-delay-days']
    if mode == 'initial-install' and current is None:
        return newest_stable_branch(releases, now, delay)
    raise UpdateError('router branch selection supports first installation only')
