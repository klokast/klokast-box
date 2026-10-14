"""Protected acceptance of the initial shared guest clone, using its existing assignment."""
from pathlib import Path


def assignment(read, base, box, role='dmz'):
    if role not in ('dmz','iot'):raise RuntimeError('initial assignment requires DMZ or IoT')
    path=base.parent/'klokast-infrastructure'/role/'assignment.json'
    if not path.exists():return None
    value=read(path)
    if value.get('kind')!='klokast.infrastructure-assignment.v1' or value.get('box')!=box or value.get('role')!=role:
        raise RuntimeError('initial shared guest assignment belongs to another target')
    return value


def evidence(m, box, operation, payload, role='dmz'):
    value=assignment(m.read,m.t.BASE,box,role)
    if not value or value['operation_id']!=operation or value['stage']!='ready':
        raise RuntimeError('initial shared guest finalization is incomplete')
    disks={'/dev/vg0/lv_'+role:m.owned('/dev/vg0/lv_'+role,tag=value['lv_tag']),
           value['data_lv']:m.owned(value['data_lv'],tag=value['data_tag'])}
    if disks['/dev/vg0/lv_'+role]['uuid']!=value['lv_uuid'] or disks[value['data_lv']]['uuid']!=value['data_lv_uuid']:
        raise RuntimeError('initial shared guest disk identity changed before acceptance')
    current=m.domain(role)
    if not current:raise RuntimeError('initial shared guest is not running')
    m.require_identity(current,value['uuid'])
    native=m.t.Native()
    if {native.device(d['pdev_path']) for d in current['config']['disks']}!={native.device(d) for d in disks}:
        raise RuntimeError('initial shared guest live disks differ from its assignment')
    probe=payload['probe']
    if (probe['xen_uuid']!=value['uuid'] or probe['retained_uuid']!=value['data_uuid'] or
            probe['identity']['hostname']!=box+'-'+role):
        raise RuntimeError('initial shared guest probe selects a different guest or filesystem')
    verification=value.get('reboot_verification',{'status':'required'})
    if payload['phase']=='before':
        if verification['status']=='required':
            value['reboot_verification']={'status':'pending','before':probe}
    elif payload['phase']=='after':
        if verification['status']=='pending':
            before=verification['before']
            if before['boot_id']==probe['boot_id'] or before['identity']!=probe['identity']:
                raise RuntimeError('initial shared guest reboot did not preserve identity with a new boot ID')
            record=payload['service']
            if (record['box']!=box or record['role']!=role or record['operation_id']!=operation or
                    record['new_uuid']!=value['uuid'] or record['data_uuid']!=value['data_uuid'] or
                    record['image']!=operation or record['requested_configuration']['before']!=before):
                raise RuntimeError('initial shared guest acceptance record differs from its reboot evidence')
            value['service']=dict(record,new_disks=disks)
            value['reboot_verification']=dict(verification,status='complete')
        elif verification['status']!='complete':
            raise RuntimeError('initial shared guest has no recorded reboot preflight')
    else:raise RuntimeError('unsupported initial shared guest evidence phase')
    m.write(m.t.BASE.parent/'klokast-infrastructure'/role/'assignment.json',value)
    native.persist()
    return value
