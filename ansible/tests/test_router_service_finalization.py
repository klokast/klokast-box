"""Common router preparation has no enrollment or root-to-root state copy."""
import ast
import copy
from importlib.machinery import SourceFileLoader
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[2]
for path in ('ansible/lib','ansible/roles/vm-update-recovery/files','ansible/roles/vm-service-update/files','ansible/roles/vm-template-builder/files'):
    sys.path.insert(0,str(ROOT/path))
import vm_service_engine as engine
import vm_update_native
import vm_router_state as state
import router_personalize

loader=SourceFileLoader('router_service_guest_test',str(ROOT/'ansible/roles/vm-template-builder/files/vm-service-finalize'))
spec=importlib.util.spec_from_loader(loader.name,loader)
guest=importlib.util.module_from_spec(spec);loader.exec_module(guest)


def contract():
    return dict(kind=state.KIND,box='boxa',filesystem_uuid='12345678-1234-1234-1234-123456789abc',
                accounts=dict(dnsmasq_uid=100,dnsmasq_gid=101,tailscale_gid=102))


def configuration():
    files={name:'# frozen recipe\n' for name in router_personalize.FILES}
    files.update({'etc/hostname':'boxa-router\n','etc/dnsmasq.conf':'dhcp-leasefile=/var/lib/misc/dnsmasq.leases\n',
                  'etc/klokast/overlay-ipv6.nft':'# Ops IPv6 downstream is disabled.\n'})
    return dict(kind='klokast.router-personalization.v1',box='boxa',role='router',inputs_sha256='a'*64,files=files,
                packages={name:'1-r0' for name in ('linux-virt','dhcpcd','dnsmasq','nftables')})


class Finalization(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup);self.root=Path(tmp.name)
        for target,name,replacement in ((vm_update_native,'secure',lambda path,*a,**kw:path),(engine,'safe_file',lambda path,*a: path),(engine.t,'read',lambda path:json.loads(path.read_text()))):
            check=patch.object(target,name,replacement);check.start();self.addCleanup(check.stop)
        self.operation='a'*24
        self.value=dict(kind='klokast.vm-service-finalize.v1',box='boxa',role='router',root_partition='',
                        source_files={},accounts={'neo':[1000,1000]},router_state=contract(),
                        router_storage={'mode':'migrate','seed_id':self.operation},router_configuration=configuration())

    def test_guest_requires_a_closed_recipe_and_never_copies_old_configuration(self):
        self.assertEqual(guest.validate(self.value),self.value)
        for change in ({'source_files':{'etc/passwd':'a'*64}},{'router_storage':{'mode':'initialize','seed_id':self.operation}},
                       {'accounts':{'neo':[1001,1001]}}):
            with self.assertRaises(RuntimeError):guest.validate(dict(self.value,**change))
        value=copy.deepcopy(self.value);value['router_configuration']['files']['root/.ssh/authorized_keys']='untrusted key'
        with self.assertRaises(ValueError):guest.validate(value)

    def test_state_ready_proof_is_bound_to_the_request_and_guest_result(self):
        request={'router_state':contract()};(self.root/'request.json').write_text(json.dumps(request))
        record={'role':'router','operation_id':self.operation,'stage':'prepared'}
        result={'kind':'klokast.infrastructure-finalize.v1','success':True,'inputs_sha256':'b'*64,'operation_id':self.operation}
        with patch.object(engine.t,'secure',side_effect=lambda path,*a,**kw:path),patch.object(engine.finalization,'read_slot',return_value=result):
            with self.assertRaisesRegex(RuntimeError,'retained-state contract'):
                engine.finalization.finalizer_result(self.root,record,'b'*64)
            self.assertFalse((self.root/'router-state-ready.json').exists())
            result['router_state']=contract()
            engine.finalization.finalizer_result(self.root,record,'b'*64)
        proof=json.loads((self.root/'router-state-ready.json').read_text())
        self.assertEqual(proof,dict(request_sha256=engine.t.digest(request),contract=contract(),operation_id=self.operation,ready=True))
        self.assertEqual(record['stage'],'finalized')

    def test_first_prepare_allocates_small_data_but_later_prepare_reuses_it(self):
        for migration in (True,False):
            with self.subTest(migration=migration):
                base=self.root/str(migration);(base/'operations').mkdir(parents=True);(base/'active').mkdir()
                xen=base/'xen';(xen/'auto').mkdir(parents=True)
                image=base/'images'/self.operation;image.mkdir(parents=True)
                for name in ('root','kernel','initramfs'):(image/name).write_bytes(b'image')
                old_root='/dev/vg0/oldroot';data='/dev/vg0/retained'
                disks=[old_root] if migration else [old_root,data]
                cfg=dict(name='router',uuid='11111111-1111-4111-8111-111111111111',type='pvh',
                    kernel='/mnt/dom0_data/old-kernel',ramdisk='/mnt/dom0_data/old-initramfs',
                    disk=['phy:'+path+',xvd'+letter+',w' for path,letter in zip(disks,'ab')],vif=['bridge=br-wan,mac=02:00:00:00:00:01'])
                (xen/'router.cfg').write_text('\n'.join(k+' = '+repr(v) for k,v in cfg.items()))
                (xen/'auto/router.cfg').symlink_to('../router.cfg')
                public=dict(self.value,image=self.operation,engine_commit='e'*40,router_source={
                    'assignment_sha256':'c'*64,'generation_sha256':'d'*64} if migration else None)
                config=base/'config.json';config.write_text(json.dumps(public));receipt=base/'receipt.json';receipt.write_text('{}')
                receipt_value={'files':{'inputs.json':{'profile':'router-alpine-v3'},'candidate.json':{'artifacts':{'root':{'bytes':5,'sha256':'f'*64}}},
                                        'release-evidence.json':{'release_sha256':'f'*64}}}
                native=SimpleNamespace(device=lambda path:path,artifact=lambda path:{'sha256':'f'*64,'bytes':5},persist=lambda:None)
                current={'config':{'c_info':{'uuid':cfg['uuid']},'disks':[{'pdev_path':path} for path in disks]}}
                def owned(path,*args):return {'uuid':path.rsplit('/',1)[1]+'-identity','bytes':(2 if path==old_root else 1)*1024**3}
                def run(argv,**kw):return SimpleNamespace(stdout=str(10*1024**3) if argv[0]=='vgs' else '')
                with patch.object(engine.t,'BASE',base),patch.object(engine.t,'XEN',xen),patch.object(engine.t,'Native',return_value=native), \
                     patch.object(engine.t,'secure',side_effect=lambda path,*a,**kw:path),patch.object(engine,'IMAGES',base/'images'), \
                     patch.object(engine,'pending',return_value=[]),patch.object(engine,'image',return_value=receipt_value), \
                     patch.object(engine,'legacy_source',return_value=public['router_source']), \
                     patch.object(engine,'owned',side_effect=owned),patch.object(engine,'domain',return_value=current), \
                     patch.object(engine,'require_memory'),patch.object(engine,'run',side_effect=run) as commands, \
                     patch.object(engine,'checksum',return_value='f'*64), \
                     patch.object(engine,'allocate',side_effect=lambda path,size,*a:{'uuid':Path(path).name+'-identity','bytes':size}) as allocate:
                    record=engine.prepare('boxa','router',self.operation,config,receipt,'a'*64)
                self.assertEqual([c.args[1] for c in allocate.call_args_list],[4*1024**3,1024**3] if migration else [4*1024**3])
                request=json.loads((base/'operations'/self.operation/'request.json').read_text())
                self.assertEqual(request['kind'],engine.t.RETAINED_KIND);self.assertEqual(request['router_state'],contract())
                self.assertEqual(record['initialize_data'],migration)
                if not migration:
                    self.assertEqual(record['data_lv'],data)
                    self.assertFalse(any('of='+data in c.args[0] for c in commands.call_args_list))

    def test_public_router_profile_requires_the_new_contract(self):
        tree=ast.parse((ROOT/'ansible/bin/platform-update').read_text())
        selectors={}
        for node in ast.walk(tree):
            if isinstance(node,ast.Call) and isinstance(node.func,ast.Attribute) and node.func.attr=='add_argument' and isinstance(node.func.value,ast.Name):
                choices=next((k.value for k in node.keywords if k.arg=='choices'),None)
                if choices is not None and isinstance(choices,ast.Tuple):selectors[(node.func.value.id,ast.literal_eval(node.args[0]))]=ast.literal_eval(choices)
        self.assertIn('router',selectors[('update_parser','--role')])
        self.assertIn('router-alpine-v3',selectors[('prepare_parser','--profile')])
        self.assertNotIn('router-alpine-v2',selectors[('prepare_parser','--profile')])

    def test_finalizer_attaches_exact_data_without_copying_it_and_has_no_network(self):
        work=self.root/'operations'/self.operation;work.mkdir(parents=True)
        root='/dev/vg0/vmupd_'+self.operation+'_root';data='/dev/vg0/retained'
        old={'name':'router','uuid':'11111111-1111-4111-8111-111111111111','type':'pvh',
             'kernel':'/mnt/dom0_data/old-kernel','ramdisk':'/mnt/dom0_data/old-initramfs',
             'disk':['phy:/dev/vg0/oldroot,xvda,w','phy:'+data+',xvdb,w']}
        (work/'old.cfg').write_text('\n'.join(k+' = '+repr(v) for k,v in old.items()))
        request={'router_state':contract()};(work/'request.json').write_text(json.dumps(request))
        record={'role':'router','operation_id':self.operation,'old_uuid':old['uuid'],
                'old_disks':dict.fromkeys(['/dev/vg0/oldroot',data],{}),'new_disks':dict.fromkeys([root,data],{}),
                'root_lv':root,'data_lv':data,'requested_configuration':self.value,'initialize_data':False}
        result={}
        def boot(work,name,identity,path,evidence,**kw):
            result.update(kind='klokast.infrastructure-finalize.v1',success=True,router_state=contract(),**evidence)
        f=engine.finalization
        with patch.object(engine.t,'BASE',self.root),patch.object(engine.t,'Transaction',return_value=SimpleNamespace(
                journal={'stage':'stopped'},guest=lambda:None)),patch.object(f,'domain',return_value=None), \
             patch.object(f,'release_slots'),patch.object(f,'attach_loop',side_effect=['/dev/loop1','/dev/loop2']), \
             patch.object(f,'detach_loop'),patch.object(f,'boot_guest',side_effect=boot), \
             patch.object(f,'read_slot',return_value=result),patch.object(f,'run') as run:
            f.finalize(work,record)
        run.assert_not_called()  # No retained-disk dd or host filesystem work.
        cfg={node.targets[0].id:ast.literal_eval(node.value) for node in ast.parse((work/'finalize.cfg').read_text()).body}
        self.assertEqual(cfg['vif'],[])
        self.assertIn('phy:/dev/vg0/oldroot,xvdd,r',cfg['disk'])
        self.assertIn('phy:'+data+',xvde,w',cfg['disk'])
        payload=json.loads((work/'config.slot').read_bytes().split(b'\0',1)[0])
        self.assertEqual(payload['router_storage'],{'mode':'attach','seed_id':self.operation})
        self.assertEqual(record['stage'],'finalized')


if __name__=='__main__':unittest.main()
