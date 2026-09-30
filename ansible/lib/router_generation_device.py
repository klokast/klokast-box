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
