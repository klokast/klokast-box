"""Match an archived test identity to one offline Tailscale API device."""
import router_generations as generations
from router_transaction import TransactionError


def api_devices(value):
    devices = value.get('devices') if isinstance(value, dict) else None
    if not isinstance(devices, list) or len(devices) > 10000:
        raise TransactionError('cold device cleanup lacks a bounded Tailnet device list')
    return devices


def test_device(source, controller_status, api_list):
    """Return one provider ID, or None when the archived test ID is absent."""
    generations.check_seal(source)
    if (source.get('kind') != 'klokast.router-cold-device-source.v1' or
            source.get('status') != 'revocation-required' or
            not generations.matches('[A-Za-z0-9_-]{1,128}', source.get('machine_id')) or
            source['machine_id'] == source.get('original_machine_id') or
            not isinstance(controller_status, dict) or
            controller_status.get('BackendState') != 'Running' or
            not isinstance(controller_status.get('Peer'), dict)):
        raise TransactionError('cold device cleanup lacks its exact retained test identity')
    peers = list(controller_status['Peer'].values())
    originals = [peer for peer in peers if isinstance(peer, dict) and
                 peer.get('ID') == source['original_machine_id']]
    if len(originals) != 1 or originals[0].get('Online') is not True:
        raise TransactionError('cold device cleanup requires the original router online')
    candidates = [device for device in api_devices(api_list) if isinstance(device, dict) and
                  device.get('nodeId') == source['machine_id']]
    if len(candidates) > 1:
        raise TransactionError('cold device cleanup found duplicate API records for the test identity')
    if not candidates:
        return None
    device = candidates[0]
    matching = [peer for peer in peers if isinstance(peer, dict) and
                peer.get('ID') == source['machine_id']]
    if len(matching) != 1 or matching[0].get('Online') is not False:
        raise TransactionError('cold device cleanup requires the test identity offline')
    address = device.get('addresses')
    peer_ips = matching[0].get('TailscaleIPs')
    if (not generations.matches('[A-Za-z0-9_-]{1,128}', str(device.get('id', ''))) or
            device.get('hostname') != source['hostname'] or
            matching[0].get('HostName') != source['hostname'] or
            not isinstance(address, list) or not address or
            not isinstance(peer_ips, list) or not peer_ips or
            set(address) != set(peer_ips)):
        raise TransactionError('cold device cleanup API record differs from the archived offline peer')
    return str(device['id'])


def absent(source, api_list):
    """Prove that no API device still has the archived node ID."""
    matches = [device for device in api_devices(api_list) if isinstance(device, dict) and
               device.get('nodeId') == source['machine_id']]
    if matches:
        raise TransactionError('cold test device remains in the Tailnet API after deletion')
    originals = [device for device in api_devices(api_list) if isinstance(device, dict) and
                 device.get('nodeId') == source['original_machine_id']]
    if len(originals) != 1:
        raise TransactionError('cold device cleanup cannot find the retained original in the Tailnet API')
    return True
