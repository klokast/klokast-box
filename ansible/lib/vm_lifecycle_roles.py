"""Fixed Platform VM image and storage policy, shared by lifecycle executors."""

PROFILES = {
    'bak': 'shared-alpine-v1',
    'dmz': 'shared-alpine-v1',
    'iot': 'shared-alpine-v1',
    'vpn-egress': 'vpn-egress-alpine-v1',
    'router': 'router-alpine-v3',
    'ops': 'ops-alpine-v1',
    'air': 'air-alpine-v1',
}
INFRASTRUCTURE = ('ops', 'air')
RETAINED = ('bak', 'dmz', 'iot', 'router', *INFRASTRUCTURE)
ROOT_GIB = {'bak': 8, 'dmz': 8, 'iot': 8, 'router': 4,
            'vpn-egress': 8, 'ops': 100, 'air': 50}
DATA_GIB = 50


def profile(role):
    try:
        return PROFILES[role]
    except KeyError:
        raise RuntimeError('unsupported Platform VM role: ' + str(role)) from None
