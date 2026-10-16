"""Checked service root retirement; records remain audit evidence."""
import json
import os
from pathlib import Path
from types import SimpleNamespace

import ops_retirement
from vm_lifecycle_roles import RETAINED


def adapter(m):
    return SimpleNamespace(run=m.run, XEN=m.t.XEN, MIB=m.MIB,
        safe_file=lambda p, limit: m.t.secure(p, limit=limit),
        same_device=lambda a,b: m.t.Native().device(a) == m.t.Native().device(b))


def released(m, work, record):
    path = work/'retired.json'
    if not path.exists(): return False
    proof = m.read(path)
    request = m.read(work/'request.json') if record.get('kind') == 'klokast.vm-service-update.v1' else None
    if (proof.get('kind') != ('klokast.'+record['role']+'-generation-retired.v1' if record.get('role') in RETAINED else 'klokast.vpn-generation-retired.v1') or
            proof.get('record_sha256') != m.t.digest(record) or
            proof.get('request_sha256') != (m.t.digest(request) if request else None) or
            not m.t.HEX.fullmatch(proof.get('plan_sha256', ''))):
        raise RuntimeError('service retirement proof differs from its audit records')
    root = proof.get('root_lv')
    expected = ['/dev/vg0/vmupd_'+record['operation_id']+'_root'] if request and record.get('role') in RETAINED else (list(record.get('new_disks', {})) if request else [record.get('root_lv','/dev/vg0/lv_'+record['role'])])
    if request and root not in record.get('new_disks',{}):
        raise RuntimeError('retirement root is not owned by its service record')
    if expected != [root] or not proof.get('lv_uuid'):
        raise RuntimeError('retirement proof selects an unexpected service root')
    if Path(root).exists() or Path(root).is_symlink():
        raise RuntimeError('retired service LV name is present again; preserve image references')
    for name in proof['files']:
        if name not in ('kernel', 'initramfs', 'new.cfg', 'vpn-egress.cfg', 'bak.cfg', 'dmz.cfg', 'iot.cfg', 'router.cfg', 'ops.cfg', 'air.cfg'):
            raise RuntimeError('retirement proof names an unexpected boot file')
        if (work/name).exists() or (work/name).is_symlink():
            raise RuntimeError('retired service boot file remains; preserve image references')
    return True


def resource(m, work, record, request=None, *, boot_sha256=None):
    if released(m, work, record): return None
    if request:
        if (m.read(work/'journal.json')['stage'] != 'complete' or
                record['reboot_verification']['status'] != 'complete' or
                request['new_disks'] != record['new_disks'] or request['new_uuid'] != record['new_uuid'] or
                len(record['new_disks']) not in ((1,2) if record.get('role') in ('bak','iot') else (2,) if record.get('role') in ('dmz','router','ops','air') else (1,))):
            raise RuntimeError('service generation has incomplete or conflicting ownership evidence')
        root = '/dev/vg0/vmupd_'+record['operation_id']+'_root'
        if root in request.get('retained_disks', {}):
            raise RuntimeError('generation retirement must never select a retained disk')
        identity = record['new_disks'][root]
        tag = 'klokast.vm-update.'+record['operation_id']+'.root'
        names = ('kernel','initramfs','new.cfg')
        expected = {Path(k).name:v for k,v in request['new_artifacts'].items()}
        expected['new.cfg'] = {'sha256':request['new_config_sha256']}
    else:
        if record.get('stage') != 'ready' or record.get('role') not in ('vpn-egress','bak','dmz','iot','ops','air'):
            raise RuntimeError('original service assignment is not ready')
        root = record.get('root_lv','/dev/vg0/lv_'+record['role']); identity = {'uuid':record['lv_uuid']}
        tag = record['lv_tag']; names = ('kernel','initramfs',record['role']+'.cfg')
        expected = record['artifacts']
        if not boot_sha256: raise RuntimeError('original service boot assignment has no protected checksum')
        expected = dict(expected, **{record['role']+'.cfg': {'sha256':boot_sha256}})
    actual = m.owned(root, tag=tag)
    if actual['uuid'] != identity['uuid'] or ('bytes' in identity and actual['bytes'] != identity['bytes']):
        raise RuntimeError('retirement LV UUID or size differs')
    state = {'root_lv':root,'lv_uuid':identity['uuid'],'lv_tag':tag,'work':str(work)}
    ops_retirement.no_references(adapter(m), state)
    files = {}
    for name in names:
        path = work/name; m.t.secure(path, limit=256*m.MIB)
        if m.checksum(path) != expected[name]['sha256']:
            raise RuntimeError('service retirement boot checksum differs')
        files[name] = {'sha256':expected[name]['sha256'],'bytes':path.stat().st_size}
    return dict(state, bytes=actual['bytes'], files=files,
                record_sha256=m.t.digest(record), request_sha256=m.t.digest(request) if request else None)


def plan(m, box, role='vpn-egress'):
    observed = m.status(box, role, lock_held=True)
    if observed['pending']: raise RuntimeError('incomplete service operation blocks generation retirement')
    assignment = observed['assignment']; current = observed['installed']
    if not current: return {'kind':('klokast.'+role+'-retirement.v1' if role in RETAINED else 'klokast.vpn-retirement.v1'),'box':box,'role':role,'current':None,'items':[],'records':{},'unknown_unchanged':[]}
    if role in RETAINED and assignment is None:
        return {'kind':'klokast.'+role+'-retirement.v1','box':box,'role':role,'current':None,'items':[],
                'records':{},'unknown_unchanged':[]}
    if assignment['configuration_drift'] or assignment['autostart_drift'] or assignment['runtime'] != 'running':
        raise RuntimeError('service current assignment differs; refuse generation retirement')
    records = {}
    for path in sorted((m.t.BASE/'operations').glob('*/service.json')):
        value = m.read(path)
        if value.get('role') != role: continue
        if value.get('box') != box or value.get('operation_id') != path.parent.name:
            raise RuntimeError('service operation belongs to another target')
        if value['new_uuid'] in records:raise RuntimeError('service generation UUID is repeated')
        records[value['new_uuid']] = (path.parent, value)
    chain = []; selected = current; seen = set()
    while selected:
        if selected['new_uuid'] in seen: raise RuntimeError('service generation history has a cycle')
        seen.add(selected['new_uuid']); chain.append(records[selected['new_uuid']])
        previous = records.get(selected['old_uuid'])
        if previous:
            if previous[1]['new_disks'] != selected['old_disks']:
                raise RuntimeError('service predecessor disk identity differs')
            selected = previous[1]
        else: break
    items=[]; unknown=[]; bindings={}
    for work, record in chain:
        bindings[str(work/'service.json')] = record
    for work, record in chain[2:]:
        try:
            item=resource(m,work,record,m.read(work/'request.json'))
            if item:items.append(item)
        except (RuntimeError,OSError,KeyError) as error:
            unknown.append({'resource':str(work),'reason':str(error)})
    original = m.t.BASE.parent/'klokast-infrastructure'/role
    if role != 'router' and (original/'assignment.json').exists():
        record=m.read(original/'assignment.json'); oldest=chain[-1][1]
        root=record.get('root_lv','/dev/vg0/lv_'+role)
        if record.get('uuid') == oldest['old_uuid'] and oldest['old_disks'].get(root,{}).get('uuid') == record.get('lv_uuid'):
            bindings[str(original/'assignment.json')]=record
            old_request=m.read(chain[-1][0]/'request.json')
            checksum=old_request['old_config_sha256']
            generations=0
            while record:
                work=Path(record.get('work',str(original)))
                if len(chain)>=2 or generations>0:
                    try:
                        item=resource(m,work,record,boot_sha256=checksum)
                        if item:items.append(item)
                    except (RuntimeError,OSError,KeyError) as error:
                        unknown.append({'resource':str(work),'reason':str(error)})
                record=record.get('previous') if role=='ops' else None
                generations+=1
                if generations>64:raise RuntimeError('controller history exceeds the retirement bound')
                checksum=record.get('boot_sha256') if record else None
        else:unknown.append({'resource':str(original),'reason':'original assignment is not the recorded predecessor'})
    known={path for _,record in chain for side in ('old_disks','new_disks') for path in record[side]}
    volumes=json.loads(m.run(['lvs','--reportformat','json','-o','lv_path,lv_uuid','vg0']).stdout)
    for report in volumes['report']:
        for row in report['lv']:
            path=row['lv_path'].strip()
            if path.startswith('/dev/vg0/vmupd_') and path not in known:
                unknown.append({'resource':path,'reason':'outside accepted service history; left unchanged'})
    return {'kind':('klokast.'+role+'-retirement.v1' if role in RETAINED else 'klokast.vpn-retirement.v1'),'box':box,'role':role,'current':assignment,'items':items,'records':bindings,'unknown_unchanged':unknown}


def apply(m, proposal):
    observed=m.status(proposal['box'],proposal.get('role','vpn-egress'),lock_held=True)
    if observed['pending'] or observed['assignment'] != proposal['current']:
        raise RuntimeError('service assignment changed during retirement')
    for path,value in proposal['records'].items():
        if m.read(Path(path)) != value:raise RuntimeError('service audit record changed during retirement')
    for item in proposal['items']:
        root=Path(item['root_lv']);work=Path(item['work'])
        ops_retirement.no_references(adapter(m), item)
        exists=root.exists() or root.is_symlink()
        if exists:m.owned(str(root),{'uuid':item['lv_uuid'],'bytes':item['bytes']},item['lv_tag'])
        for name, expected in item['files'].items():
            path=work/name
            if path.exists() or path.is_symlink():
                m.t.secure(path,limit=256*m.MIB)
                if m.checksum(path)!=expected['sha256'] or path.stat().st_size!=expected['bytes']:
                    raise RuntimeError('planned service boot file changed during deletion')
        if exists:m.run(['lvremove','--yes',str(root)])
        for name in item['files']:
            (work/name).unlink(missing_ok=True);m.t.syncdir(work)
    ops_retirement.persist(adapter(m))
    for item in proposal['items']:
        work=Path(item['work']);m.t.store(work/'retired.json',dict(item,kind=('klokast.'+proposal['role']+'-generation-retired.v1' if proposal.get('role') in RETAINED else 'klokast.vpn-generation-retired.v1'),plan_sha256=proposal['plan_sha256']))
    return {'state':'complete','complete':True,'plan_sha256':proposal['plan_sha256'],
            'removed_disks':[v['root_lv'] for v in proposal['items']],'unknown_unchanged':proposal['unknown_unchanged']}


def retire(m, box, dry_run=False, *, role='vpn-egress'):
    directory=m.t.BASE/('retirement-'+role if role in RETAINED else 'retirement');directory.mkdir(mode=0o700,exist_ok=True);m.t.secure(directory,True)
    pending=[p for p in directory.glob('*/plan.json') if not (p.parent/'complete.json').exists()]
    if len(pending)>1:raise RuntimeError('multiple interrupted service retirement plans require inspection')
    proposal=m.read(pending[0]) if pending else plan(m,box,role)
    if 'plan_sha256' not in proposal:proposal['plan_sha256']=m.t.digest(proposal)
    if proposal['box']!=box or proposal.get('role','vpn-egress')!=role or proposal['plan_sha256']!=m.t.digest({k:v for k,v in proposal.items() if k!='plan_sha256'}):
        raise RuntimeError('saved service deletion plan checksum differs')
    if dry_run:return dict(proposal,state='planned')
    work=directory/proposal['plan_sha256'];work.mkdir(mode=0o700,exist_ok=True)
    if (work/'complete.json').exists():return m.read(work/'complete.json')
    if not (work/'plan.json').exists():m.t.store(work/'plan.json',proposal)
    result=apply(m,proposal);m.t.store(work/'complete.json',result)
    return result
