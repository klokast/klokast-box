"""Router state is copied once; roots are disposable and never own identity."""
import importlib.util
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('vm_router_state_test', ROOT / 'ansible/roles/vm-template-builder/files/vm_router_state.py')
s = importlib.util.module_from_spec(spec); spec.loader.exec_module(s)


class RetainedState(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name); self.source = self.base/'old'; self.data = self.base/'data'
        self.source.mkdir(); self.data.mkdir()
        for name, value in [('ROOT_UID', os.getuid()), ('ROOT_GID', os.getgid())]:
            p = patch.object(s, name, value); p.start(); self.addCleanup(p.stop)
        self.contract = {'kind': s.KIND, 'box': 'boxa', 'filesystem_uuid': '12345678-1234-1234-1234-123456789abc',
                         'accounts': {'dnsmasq_uid': os.getuid() or 100, 'dnsmasq_gid': os.getgid() or 100,
                                      'tailscale_gid': (os.getgid() or 100) + 1}}
        self.seed = 'a'*24
        for name, value in s.FILES.items():
            if value[2] or name in ('etc/machine-id', 'etc/ssh/ssh_host_ed25519_key',
                                    'var/lib/tailscale/ssh/ssh_host_ed25519_key'):
                s.write(self.source, name, (name+'-native-state\n').encode())
        s.write(self.source, 'var/lib/dhcpcd/eth0.lease', b'disposable WAN cache')
        s.write(self.source, 'etc/nftables.nft', b'untrusted old firewall')
        s.initialize(self.data, self.contract, self.seed)

    def seed_data(self):
        return s.migrate(self.source, self.data, self.contract, self.seed)

    def root(self, name):
        root = self.base/name; root.mkdir()
        a = self.contract['accounts']
        s.write(root, 'etc/passwd', ('root:x:0:0:root:/root:/bin/ash\n'
                f"dnsmasq:x:{a['dnsmasq_uid']}:{a['dnsmasq_gid']}:dns:/var/empty:/sbin/nologin\n").encode(), 0o644)
        s.write(root, 'etc/group', (f"dnsmasq:x:{a['dnsmasq_gid']}:\n"
                f"tailscale:x:{a['tailscale_gid']}:\n").encode(), 0o644)
        s.write(root, 'etc/dnsmasq.conf', b'dhcp-leasefile=/var/lib/misc/dnsmasq.leases\n', 0o644)
        return root

    def test_allowlist_preserves_bytes_metadata_and_excludes_cache_and_configuration(self):
        value = self.seed_data()
        self.assertEqual(s.evidence(s.snapshot(self.source, self.contract)),
                         s.evidence(s.snapshot(self.data, self.contract, retained=True)))
        self.assertEqual(value['stage'], 'ready')
        self.assertFalse((self.data/'dhcpcd/eth0.lease').exists())
        self.assertFalse((self.data/'etc').exists())
        self.assertEqual((self.data/'dnsmasq').stat().st_mode & 0o777, 0o700)
        self.assertEqual(self.data.stat().st_mode & 0o777, 0o711)

    def test_each_interrupted_copy_can_resume_from_the_same_stopped_root(self):
        for index, stage in enumerate(['seeding', *s.snapshot(self.source, self.contract), 'ready']):
            with self.subTest(stage=stage):
                data = self.base/str(index); data.mkdir(); s.initialize(data, self.contract, self.seed)
                def crash(current):
                    if current == stage: raise InterruptedError('copy interrupted')
                with self.assertRaises(InterruptedError):
                    s.migrate(self.source, data, self.contract, self.seed, checkpoint=crash)
                self.assertEqual(s.migrate(self.source, data, self.contract, self.seed)['stage'], 'ready')

    def test_interrupted_seed_refuses_changed_source(self):
        with self.assertRaises(InterruptedError):
            s.migrate(self.source, self.data, self.contract, self.seed,
                      checkpoint=lambda stage: (_ for _ in ()).throw(InterruptedError()))
        s.write(self.source, 'var/lib/tailscale/tailscaled.state', b'refreshed old identity')
        with self.assertRaisesRegex(RuntimeError, 'source changed'):
            self.seed_data()

    def test_latest_mutable_state_survives_attachment_to_two_new_roots(self):
        self.seed_data()
        for name in ('tailscale/tailscaled.state', 'dhcpcd/duid', 'dhcpcd/secret', 'dnsmasq/dnsmasq.leases'):
            s.write(self.data, name, b'latest state after production boot')
        for name in ('new-root', 'next-root'):
            root = self.root(name)
            s.configure(root, self.contract, s.attach(self.data, self.contract))
            s.configure(root, self.contract, s.attach(self.data, self.contract))
            self.assertIn(self.contract['filesystem_uuid'], (root/'etc/fstab').read_text())
            self.assertEqual((root/'etc/conf.d/tailscale').read_text().count('rc_need='), 1)
            self.assertEqual((root/'etc/conf.d/dnsmasq').read_text().count('leasefile='),1)
            self.assertIn('leasefile="/srv/retained/dnsmasq/dnsmasq.leases"',(root/'etc/conf.d/dnsmasq').read_text())
        self.assertEqual((self.data/'tailscale/tailscaled.state').read_bytes(), b'latest state after production boot')
        with self.assertRaisesRegex(RuntimeError, 'refusing to replay'):
            self.seed_data()

    def test_missing_wrong_or_uninitialized_data_never_falls_back_to_root_identity(self):
        root = self.root('root')
        s.write(root, s.ROOT_RECORD, json.dumps(self.contract).encode())
        with patch.object(s, 'account_check'):
            for rows in ([], [[str(self.data), str(self.data), 'ext4', 'ro']]):
                with self.assertRaisesRegex(RuntimeError, 'missing, read-only or incorrect'):
                    s.verify(root=root, data=self.data, mounts=rows)
            rows = [['/dev/xvdb', str(self.data), 'ext4', 'rw']]
            with patch.object(s, 'filesystem_uuid', return_value='wrong'):
                with self.assertRaisesRegex(RuntimeError, 'missing, read-only or incorrect'):
                    s.verify(root=root, data=self.data, mounts=rows)
            with patch.object(s, 'filesystem_uuid', return_value=self.contract['filesystem_uuid']):
                with self.assertRaisesRegex(RuntimeError, 'not initialized'):
                    s.verify(root=root, data=self.data, mounts=rows)
        self.assertFalse((root/'var/lib/tailscale/tailscaled.state').exists())

    def test_missing_bind_and_changed_host_key_block_boot(self):
        self.seed_data(); root = self.root('root')
        s.write(root, s.ROOT_RECORD, json.dumps(self.contract).encode())
        with patch.object(s, 'account_check'), patch.object(s, 'filesystem_uuid', return_value=self.contract['filesystem_uuid']):
            with self.assertRaisesRegex(RuntimeError, 'bind mount'):
                s.verify(root=root, data=self.data, mounts=[['/dev/xvdb', str(self.data), 'ext4', 'rw']])
        s.write(self.data, 'identity/ssh/ssh_host_ed25519_key', b'other host')
        with self.assertRaisesRegex(RuntimeError, 'identity changed'):
            s.attach(self.data, self.contract)

    def test_links_hardlinks_private_modes_and_missing_state_are_rejected(self):
        key = self.source/'var/lib/tailscale/tailscaled.state'; original = key.read_bytes()
        key.unlink(); key.symlink_to(self.source/'etc/machine-id')
        with self.assertRaises(OSError): self.seed_data()
        key.unlink(); os.link(self.source/'etc/machine-id', key)
        with self.assertRaisesRegex(RuntimeError, 'unsafe'): self.seed_data()
        key.unlink(); s.write(self.source, 'var/lib/tailscale/tailscaled.state', original, 0o644)
        with self.assertRaisesRegex(RuntimeError, 'private mode'): self.seed_data()
        key.unlink()
        with self.assertRaisesRegex(RuntimeError, 'required router state'): self.seed_data()

    def test_optional_machine_id_and_ssh_directories_can_be_absent(self):
        (self.source/'etc/machine-id').unlink()
        for directory in ('etc/ssh', 'var/lib/tailscale/ssh'):
            for path in (self.source/directory).iterdir(): path.unlink()
            (self.source/directory).rmdir()
        self.seed_data()
        self.assertNotIn('etc/machine-id', s.attach(self.data, self.contract)['files'])

    def test_wan_cache_is_discarded_without_resetting_dhcp_identity_or_server_leases(self):
        self.seed_data();before=s.evidence(s.snapshot(self.data,self.contract,retained=True))
        s.write(self.data,'dhcpcd/eth0.lease',b'stale address')
        s.write(self.data,'dhcpcd/eth0.lease6',b'stale prefix')
        s.discard_wan_leases(self.data)
        self.assertEqual({p.name for p in (self.data/'dhcpcd').iterdir()},{'duid','secret'})
        self.assertEqual(s.evidence(s.snapshot(self.data,self.contract,retained=True)),before)

    def test_unknown_or_nonblank_data_is_never_formatted(self):
        system=self.base/'system'
        for name,value in [('hypervisor/type','xen'),('hypervisor/uuid','11111111-1111-4111-8111-111111111111'),
                           ('class/block/xvde/ro','0'),('class/block/xvde/size',str(s.SIZE//512))]:
            path=system/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text(value)
        (system/'class/net/lo').mkdir(parents=True)
        device=self.base/'device';device.write_bytes(b'unknown disk contents')
        actual_path=Path
        def paths(value):return system/str(value)[5:] if str(value).startswith('/sys/') else actual_path(value)
        with patch.object(s,'Path',side_effect=paths),patch.object(s.os,'geteuid',return_value=0),patch.object(s,'run') as run:
            with patch.object(s,'filesystem_uuid',return_value='wrong'):
                with self.assertRaisesRegex(RuntimeError,'refusing to format'):
                    s.prepare(self.contract,'migrate',self.seed,source=self.source,data=self.data)
            with patch.object(s,'filesystem_uuid',return_value=''),patch.object(s,'open',side_effect=lambda *a,**kw:open(device,'rb'),create=True):
                with self.assertRaisesRegex(RuntimeError,'not blank'):
                    s.prepare(self.contract,'migrate',self.seed,source=self.source,data=self.data)
            run.assert_not_called()

    def test_reused_filesystem_and_image_identity_are_rejected(self):
        other = dict(self.contract, filesystem_uuid='ffffffff-1234-1234-1234-123456789abc')
        with self.assertRaisesRegex(RuntimeError, 'another initialization'):
            s.initialize(self.data, other, self.seed)
        self.seed_data(); root = self.root('bad-image')
        s.write(root, 'etc/machine-id', b'image must be anonymous')
        with patch.object(s, 'account_check'), self.assertRaisesRegex(RuntimeError, 'image contains a host identity'):
            s.configure(root, self.contract, s.attach(self.data, self.contract))

    def test_numeric_account_conflicts_block_attachment(self):
        root = self.root('bad-accounts')
        s.account_check(root, self.contract)
        with (root/'etc/group').open('a') as stream:
            stream.write('intruder:x:'+str(self.contract['accounts']['tailscale_gid'])+':\n')
        with self.assertRaisesRegex(RuntimeError, 'group differs'):
            s.account_check(root, self.contract)

    def test_approved_service_ids_are_recreated_without_importing_source_accounts(self):
        root=self.root('new-accounts'); contract=dict(self.contract,accounts=dict(dnsmasq_uid=2100,dnsmasq_gid=2101,tailscale_gid=2102))
        s.recreate_accounts(root,contract);s.recreate_accounts(root,contract)
        s.account_check(root,contract)
        self.assertIn('dnsmasq:x:2100:2101:dns:/var/empty:/sbin/nologin',(root/'etc/passwd').read_text())
        with (root/'etc/group').open('a') as stream:stream.write('conflict:x:2201:\n')
        before=(root/'etc/passwd').read_bytes()
        contract=dict(contract,accounts=dict(contract['accounts'],dnsmasq_gid=2201))
        with self.assertRaisesRegex(RuntimeError,'conflicts'):s.recreate_accounts(root,contract)
        self.assertEqual((root/'etc/passwd').read_bytes(),before)
        info = SimpleNamespace(st_uid=65534, st_gid=65534, st_mode=0o100600)
        with self.assertRaisesRegex(RuntimeError, 'ownership'):
            s.ownership('var/lib/dhcpcd/duid', info, self.contract['accounts'])

    def test_reconstruction_uses_frozen_configuration_and_detects_partial_accounts(self):
        import test_router_service_finalization as fixture
        root=self.root('reconstructed')
        with (root/'etc/group').open('a') as stream:stream.write('wheel:x:10:root\n')
        s.write(root,'etc/shadow',b'root:!:0:0:99999:7:::\n')
        config=fixture.configuration()
        s.write(root,'etc/klokast-template.json',json.dumps({'profile':'router-alpine-v3','inputs_sha256':config['inputs_sha256']}).encode())
        packages='\n\n'.join('P:'+name+'\nV:'+version for name,version in config['packages'].items())+'\n'
        s.write(root,'lib/apk/db/installed',packages.encode())
        for service in fixture.router_personalize.SERVICES:
            s.write(root,'etc/init.d/'+service,b'#!/sbin/openrc-run\n',0o755)
        value={'router_configuration':config,'router_state':self.contract,'router_storage':{'mode':'migrate','seed_id':self.seed}}
        with patch.object(s,'isolated'),patch.object(s,'prepare') as prepare,patch.object(s.os,'chown'):
            s.reconstruct(value,self.source,root=root)
            s.reconstruct(value,self.source,root=root)
            self.assertEqual((root/'etc/nftables.nft').read_text(),config['files']['etc/nftables.nft'])
            self.assertEqual(prepare.call_args.kwargs['source'],self.source)
            self.assertIn('neo:!:0:0:99999:7:::',(root/'etc/shadow').read_text())
            self.assertFalse((root/'var/lib/tailscale/tailscaled.state').exists())
            # A failed partial reconstruction must not be reported as ready.
            s.write(root,'etc/shadow',b'root:!:0:0:99999:7:::\n')
            prepare.reset_mock()
            with self.assertRaisesRegex(RuntimeError,'management account is incomplete'):
                s.reconstruct(value,self.source,root=root)
            prepare.assert_not_called()


if __name__ == '__main__': unittest.main()
