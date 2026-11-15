import os
import subprocess
import tempfile
import sys
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'lib'))
import machine_status as status

HEAD='hostname boxa-dmz\nclock 2000\nuptime 500\nrelease 3.24.4\nidentity 1700\ndatabase 1200\nimage 1200\n'
OUTPUT=HEAD+'world\nbusybox\nzlib\npackages\nP:zlib\nV:1.3.1-r2\nt:900\nP:busybox\nV:1.37.0-r30\nt:800\nupstream\n'
INSTANCE={'boxes':{'boxa':{'vpn-egress':{'clients':[]}},'boxb':{'substrate':{'shared-guests':{'iot':{'runtime-state':'stopped'}}}},'boxc':{}},
          'controllers':['boxa','boxb'],'airunners':['boxb-air']}


class MachineStatus(unittest.TestCase):
    def test_lists_each_dom0_both_controllers_and_the_declared_guests(self):
        rows=status.machines(INSTANCE)
        self.assertEqual(sorted(rows),['boxa-bak','boxa-dmz','boxa-dom0','boxa-iot','boxa-ops','boxa-router','boxa-vpn-egress',
            'boxb-air','boxb-bak','boxb-dmz','boxb-dom0','boxb-iot','boxb-ops','boxb-router',
            'boxc-bak','boxc-dmz','boxc-dom0','boxc-iot','boxc-router'])
        self.assertEqual({target for target,state in rows.items() if state!='running'},{'boxb-iot'})
        # The same role on different boxes is listed together.
        self.assertEqual(list(rows)[:8],['boxb-air','boxa-bak','boxb-bak','boxc-bak','boxa-dmz','boxb-dmz','boxc-dmz','boxa-dom0'])
        self.assertEqual(list(rows)[-3:],['boxb-router','boxc-router','boxa-vpn-egress'])

    def test_parse_returns_the_times_and_the_sorted_packages(self):
        value=status.parse('boxa-dmz',OUTPUT)
        self.assertEqual({k:v for k,v in value.items() if k!='packages'},
            {'alpine':'3.24.4','booted_at':1500,'updated_at':1700,'image_built_at':1200})
        self.assertEqual(value['packages'],[{'name':'busybox','version':'1.37.0-r30','built_at':800},{'name':'zlib','version':'1.3.1-r2','built_at':900}])
        # An in-place upgrade changes the package database after the identity. A dom0 has no image marker.
        value=status.parse('boxa-dmz',OUTPUT.replace('database 1200','database 1900').replace('image 1200\n',''))
        self.assertEqual((value['updated_at'],value['image_built_at']),(1900,None))

    def test_parse_refuses_values_that_the_machine_must_not_state(self):
        for output in (OUTPUT.replace('boxa-dmz','boxa-ops'),OUTPUT.replace('clock 2000','clock soon'),OUTPUT.replace('uptime 500\n',''),
                       OUTPUT.replace('P:zlib','P:\x1b[31mzlib'),OUTPUT+'P:zlib\nV:2\n',OUTPUT+'V:2\n',OUTPUT+'P:orphan\n',
                       OUTPUT+'x:1\n',HEAD+'packages\n',HEAD,OUTPUT.replace('release 3.24.4','release 3.24.4\nrelease 3.24.5')):
            with self.subTest(output=output[-40:]),self.assertRaises(RuntimeError):status.parse('boxa-dmz',output)

    def test_only_explicit_packages_and_upstream_tools_are_counted(self):
        output=OUTPUT.replace('world\nbusybox\nzlib\n','world\nbusybox@main>=1.0\n')
        output+='P:tailscale\nV:1.92.1\nP:codex\nV:0.120.0\nP:claude-code\nV:2.1.0\n'
        packages=status.parse('boxa-dmz',output)['packages']
        self.assertEqual([p['name'] for p in packages],['busybox','claude-code','codex','tailscale'])
        self.assertEqual(status.summary({'packages':packages})['packages'],4)
        self.assertNotIn('built_at',packages[1])
        for invalid in ('busybox;id','busybox\x1b[31m','busybox>='):
            with self.subTest(invalid=invalid),self.assertRaises(RuntimeError):
                status.parse('boxa-dmz',output.replace('busybox@main>=1.0',invalid))

    def test_posix_probe_reads_world_and_versions_without_duplicate_tailscale(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            script=status.PROBE.replace('$(hostname)','boxa-dmz')
            files={'/etc/apk/world':'busybox>=1.0\n',
                   '/lib/apk/db/installed':'P:busybox\nV:1.37.0-r30\nt:800\n\nP:zlib\nV:1.3-r0\n',
                   '/etc/alpine-release':'3.24.4\n','/etc/hostname':'boxa-dmz\n',
                   '/etc/klokast-template.json':None}
            for index,(path,content) in enumerate(files.items()):
                replacement=root/str(index)
                if content is not None:replacement.write_text(content)
                script=script.replace(path,str(replacement))
            for tool,version in (('tailscale','1.92.1\n  commit: abc'),('codex','codex-cli 0.120.0'),('claude','2.1.0 (Claude Code)')):
                binary=root/tool;binary.write_text("#!/bin/sh\nprintf '%s\\n' '"+version+"'\n");binary.chmod(0o755)
            env=dict(os.environ,PATH=str(root)+':'+os.environ['PATH'])
            def probe():
                result=subprocess.run(['sh','-s'],input=script,text=True,capture_output=True,env=env,check=True)
                return status.parse('boxa-dmz',result.stdout)['packages']
            self.assertEqual([(p['name'],p['version']) for p in probe()],
                [('busybox','1.37.0-r30'),('claude-code','2.1.0'),('codex','0.120.0'),('tailscale','1.92.1')])
            (root/'0').write_text('busybox\ntailscale\n')
            with (root/'1').open('a') as stream:stream.write('P:tailscale\nV:1.90.0-r0\n')
            self.assertEqual([p for p in probe() if p['name']=='tailscale'],[{'name':'tailscale','version':'1.90.0-r0'}])

    def test_probe_is_unprivileged_and_uses_the_account_of_the_role(self):
        done=subprocess.CompletedProcess([],0,OUTPUT,'')
        with patch.object(status.subprocess,'run',return_value=done) as run,patch.object(status.socket,'gethostname',return_value='boxa-ops.example'):
            status.probe('boxa-dmz');self.assertEqual(run.call_args.args[0],['tailscale','ssh','neo@boxa-dmz','sh','-s'])
            self.assertNotIn('doas',run.call_args.kwargs['input'])
            with self.assertRaises(RuntimeError):status.probe('boxb-ops')
            self.assertEqual(run.call_args.args[0],['tailscale','ssh','smith@boxb-ops','sh','-s'])
            with self.assertRaises(RuntimeError):status.probe('boxa-ops')
            self.assertEqual(run.call_args.args[0],['sh','-s'])

    def test_report_does_not_contact_a_stopped_machine_and_names_each_failure(self):
        def probe(target):
            if target=='boxa-dmz':return status.parse(target,OUTPUT)
            raise RuntimeError('no answer in 30 seconds')
        with patch.object(status.platform_source,'snapshot',return_value={'instance':INSTANCE}),patch.object(status,'probe',side_effect=probe) as probed:
            listed=status.report()['machines'];rows={row['target']:row for row in listed}
            self.assertEqual([row['target'] for row in listed],list(status.machines(INSTANCE)))
            self.assertNotIn(('boxb-iot',),[call.args for call in probed.call_args_list])
            self.assertEqual(rows['boxb-iot'],{'target':'boxb-iot','declared':'stopped','state':'stopped'})
            self.assertEqual((rows['boxa-dmz']['state'],rows['boxa-dmz']['packages']),('reachable',2))
            self.assertEqual((rows['boxa-bak']['state'],rows['boxa-bak']['error']),('unreachable','no answer in 30 seconds'))
            self.assertEqual(len(status.report('boxa-dmz')['packages']),2)
            with self.assertRaisesRegex(RuntimeError,'not a declared machine'):status.report('boxc-air')


if __name__=='__main__':unittest.main()
