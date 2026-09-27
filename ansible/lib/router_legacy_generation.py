"""Propose a truthful legacy baseline from fresh read-only router inspection.

This module does not adopt a router or write dom0 state. A supervised issuer
must recheck source identity and authority before it publishes the record.
"""
import router_generations as generations
import router_updates
import copy
from router_transaction import TransactionError


def assemble(*, box, operation, engine_commit, guest, dom0):
    if (not generations.matches('[0-9a-f]{24}', operation) or
            not generations.matches('[0-9a-f]{40}', engine_commit)):
        raise TransactionError('legacy router baseline needs one exact operation and engine')
    findings = router_updates.legacy_baseline_findings(guest, dom0, box)
    if findings:
        raise TransactionError('legacy router baseline inspection has blocking findings: ' + ', '.join(findings))
    selected = dom0['xen']['disk'][0]
    if selected != 'phy:/dev/vg0/lv_router,xvda,w':
        raise TransactionError('legacy router baseline requires the exact production LV')
    rows = [row for row in dom0['logical_volumes']['report'][0]['lv']
            if row.get('lv_path') == '/dev/vg0/lv_router']
    if (len(rows) != 1 or set(rows[0]) != {'lv_path', 'lv_uuid', 'lv_size', 'origin'} or
            not generations.matches('[A-Za-z0-9-]{1,64}', rows[0]['lv_uuid']) or
            int(rows[0]['lv_size']) != 2147483648 or rows[0]['origin']):
        raise TransactionError('legacy router LV has an unknown size, origin, or identity')
    files = {path.lstrip('/'): item['sha256'] for collection in
             (guest['configuration_files'], guest['include_files'])
             for path, item in collection.items()}
    xen = dom0['xen']
    record = generations.seal({
        'kind':'klokast.router-generation.v1', 'box':box, 'role':'router',
        'generation_id':operation, 'origin':'legacy', 'engine_commit':engine_commit,
        'alpine_branch':guest['alpine_branch'],
        'disk':{'path':'/dev/vg0/lv_router', 'uuid':rows[0]['lv_uuid'], 'bytes':2147483648},
        'boot':{'kernel':dom0['boot_artifacts']['kernel'],
                'initramfs':dom0['boot_artifacts']['ramdisk']},
        'xen':{'uuid':dom0['xen_runtime']['uuid'], 'memory':xen['memory'],
               'vcpus':xen['vcpus'], 'vif':copy.deepcopy(xen['vif'])},
        'packages':copy.deepcopy(guest['packages']), 'kernel_release':guest['kernel_release'],
        'accounts':copy.deepcopy(guest['service_accounts']), 'configuration_files':files,
        'evidence_sha256':generations.digest({'guest':guest, 'dom0':dom0})})
    return generations.generation(record, box)
