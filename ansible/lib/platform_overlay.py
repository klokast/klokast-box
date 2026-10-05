"""Read a preserved native ops IPv6 configuration within Instance box scope."""
from pathlib import Path
import platform_source as source
import router_overlay_ipv6

ROOT = Path('/var/lib/klokast/network/overlay')


def read(box):
    view = source.snapshot()
    if box not in view['instance']['boxes']:
        raise source.SourceError('overlay box is not declared in Instance')
    value = source.read_json(ROOT / (box + '.json'))
    router_overlay_ipv6.validate_source(value, box)
    if value['peer_box'] not in view['instance']['boxes']:
        raise source.SourceError('overlay peer is not declared in Instance')
    if 'overlay' not in view['instance']['boxes'][box]['connectivity']:
        raise source.SourceError('overlay capability is not declared in Instance')
    return value
