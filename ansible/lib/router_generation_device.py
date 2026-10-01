"""Keep one protected Tailnet device ID for each retained router generation."""
import router_generations as generations
import router_records as records
from router_transaction import TransactionError


def validate(value, box, generation):
    generations.check_seal(value)
    if (set(value) != {'kind','box','generation_sha256','machine_id','hostname',
            'source_sha256','record_sha256'} or
            value['kind'] != 'klokast.router-generation-device.v1' or
            value['box'] != box or value['generation_sha256'] != generation or
            not generations.matches('[A-Za-z0-9_-]{1,128}',value['machine_id']) or
            (value['hostname'] != box+'-router' and not (
                isinstance(value['hostname'],str) and
                value['hostname'].startswith(box+'-router-') and
                generations.matches('[0-9a-f]{24}',value['hostname'][len(box)+8:]))) or
            not generations.matches('[0-9a-f]{64}',value['source_sha256'])):
        raise TransactionError('router generation device record is incomplete or selects another generation')
    return value


def path(storage, generation):
    if not generations.matches('[0-9a-f]{64}',generation):
        raise TransactionError('router device selector is invalid')
    return storage.base/'records'/('device-'+generation+'.json')


def read(storage, generation):
    target = path(storage,generation)
    if not target.exists() and not target.is_symlink():
        return None
    value = validate(records.read(target),storage.box,generation)
    owner = storage.generation(generation)
    if value['hostname'] not in (storage.box+'-router',
            generations.tailnet_hostname(storage.box,owner['generation_id'])):
        raise TransactionError('router device name differs from its retained generation')
    return value


def remember(storage, generation, machine_id, hostname, source):
    if not generations.matches('[0-9a-f]{64}',source):
        raise TransactionError('router device source checksum is invalid')
    expected = validate(generations.seal({'kind':'klokast.router-generation-device.v1',
        'box':storage.box,'generation_sha256':generation,
        'machine_id':machine_id,'hostname':hostname,'source_sha256':source}),
        storage.box,generation)
    owner = storage.generation(generation)
    if hostname not in (storage.box+'-router',
            generations.tailnet_hostname(storage.box,owner['generation_id'])):
        raise TransactionError('router Tailnet name selects another generation')
    prior = read(storage,generation)
    if prior is not None:
        if prior['machine_id'] != machine_id or prior['hostname'] != hostname:
            raise TransactionError('router generation has a different retained Tailnet device')
        return prior
    directory = records.secure(storage.base/'records',directory=True)
    existing = list(directory.iterdir())
    if len(existing) > 4096:
        raise TransactionError('router device inventory exceeds its protected bound')
    for item in existing:
        name = item.name
        if (name.startswith('device-') and name.endswith('.json') and
                generations.matches('[0-9a-f]{64}',name[7:-5])):
            other = read(storage,name[7:-5])
            if other['machine_id'] == machine_id:
                raise TransactionError('another retained router generation already owns this Tailnet device')
    records.write(path(storage,generation),expected)
    return expected


def cleanup_api(plan, api_list):
    """Check retained registrations before selecting any obsolete provider ID."""
    generations.check_seal(plan)
    rows = api_list.get('devices') if isinstance(api_list, dict) else None
    if (plan.get('kind') != 'klokast.router-cleanup-plan.v1' or
            not isinstance(rows, list) or len(rows) > 10000 or
            any(not isinstance(row, dict) for row in rows)):
        raise TransactionError('router cleanup requires a bounded complete Tailnet device list')
    for resource in plan['keep']:
        device = resource['device']
        if device is None:
            raise TransactionError('router cleanup has no exact retained device identity')
        found = [row for row in rows if row.get('nodeId') == device['machine_id']]
        if len(found) != 1 or found[0].get('hostname') != device['hostname']:
            raise TransactionError('router cleanup cannot find its exact retained device registration')
    return rows


def cleanup_target(plan, controller_status, api_list):
    """Select one recorded offline obsolete node; never infer ownership by name."""
    rows = cleanup_api(plan, api_list)
    if (not isinstance(controller_status, dict) or controller_status.get('BackendState') != 'Running' or
            not isinstance(controller_status.get('Peer'), dict) or
            not isinstance(controller_status.get('Self'), dict)):
        raise TransactionError('router cleanup requires live controller Tailnet status')
    peers = list(controller_status['Peer'].values())
    current = plan['keep'][0]['device']
    live = [peer for peer in peers if isinstance(peer, dict) and peer.get('ID') == current['machine_id']]
    if len(live) != 1 or live[0].get('Online') is not True or live[0].get('HostName') != current['hostname']:
        raise TransactionError('router cleanup requires its exact current router online')
    if not plan['retire']:
        return None
    target = plan['retire'][0]
    device = target['device']
    if device is None:
        # This only proves absence, not ownership. Native retirement also
        # requires a never-started candidate and no enrollment attempt.
        hostname = generations.tailnet_hostname(plan['box'], target['generation']['generation_id'])
        if any(row.get('hostname') == hostname for row in rows):
            raise TransactionError('router cleanup found an unrecorded candidate registration; reconcile enrollment')
        return None
    if (device['machine_id'] == controller_status.get('Self', {}).get('ID') or
            any(device['machine_id'] == item['device']['machine_id'] for item in plan['keep'])):
        raise TransactionError('router cleanup target is a retained or controller device')
    found = [row for row in rows if row.get('nodeId') == device['machine_id']]
    if len(found) > 1:
        raise TransactionError('router cleanup found duplicate API records for the obsolete device')
    if not found:
        return None
    row = found[0]
    matched = [peer for peer in peers if isinstance(peer, dict) and peer.get('ID') == device['machine_id']]
    addresses = row.get('addresses')
    if (len(matched) != 1 or matched[0].get('Online') is not False or
            matched[0].get('HostName') != device['hostname'] or row.get('hostname') != device['hostname'] or
            row.get('id') is None or type(row['id']) not in (str, int) or
            not generations.matches('[A-Za-z0-9_-]{1,128}', str(row['id'])) or
            sum(str(item.get('id')) == str(row['id']) for item in rows) != 1 or
            not isinstance(row.get('tags'), list) or 'tag:vm' not in row['tags'] or
            not isinstance(addresses, list) or not 1 <= len(addresses) <= 256 or
            any(not isinstance(address, str) for address in addresses) or
            not isinstance(matched[0].get('TailscaleIPs'), list) or
            any(not isinstance(address, str) for address in matched[0]['TailscaleIPs']) or
            set(addresses) != set(matched[0]['TailscaleIPs'])):
        raise TransactionError('router cleanup requires the exact offline obsolete provider and peer identities')
    return str(row['id'])


def cleanup_absent(plan, api_list):
    rows = cleanup_api(plan, api_list)
    if plan['retire']:
        target = plan['retire'][0]
        device = target['device']
        if device is None:
            hostname = generations.tailnet_hostname(plan['box'], target['generation']['generation_id'])
            remains = any(row.get('hostname') == hostname for row in rows)
        else:
            remains = any(row.get('nodeId') == device['machine_id'] for row in rows)
        if remains:
            raise TransactionError('router obsolete device remains after exact revocation')
    return True
