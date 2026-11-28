"""The runner and exit appliance health checks read state only and report each problem as a check."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'lib'))
import air_health
import exit_appliance
import health_checks
import machine_status

NOW=health_checks.parse_time('2026-11-28T00:00:00Z')
DEVICE={'hostname':'k001-air','tags':['tag:airunner'],'connectedToControl':True,'lastSeen':'2026-11-27T23:59:00Z',
        'keyExpiryDisabled':True,'expires':'2027-04-15T03:17:43Z'}


def states(checks):return {c['name']:c['state'] for c in checks}


class TailnetIdentity(unittest.TestCase):
    def test_one_connected_identity_with_the_declared_tags(self):
        self.assertEqual(states(health_checks.tailnet_identity('k001-air',['tag:airunner'],[DEVICE],NOW)),
                         {'Tailnet identity':'ok','Tailscale control':'ok','Tailscale key':'ok'})

    def test_each_identity_problem_is_a_check(self):
        self.assertEqual(health_checks.tailnet_identity('k001-air',['tag:airunner'],[DEVICE,DEVICE],NOW)[0]['state'],'failed')
        self.assertEqual(health_checks.tailnet_identity('k001-air',['tag:airunner'],[],NOW)[0]['state'],'failed')
        found=states(health_checks.tailnet_identity('k001-air',['tag:airunner'],
            [dict(DEVICE,tags=['tag:airunner','tag:ops'],connectedToControl=False,keyExpiryDisabled=False,expires='2026-12-10T00:00:00Z')],NOW))
        self.assertEqual(found,{'Tailnet identity':'failed','Tailscale control':'failed','Tailscale key':'warning'})
        expired=health_checks.tailnet_identity('k001-air',['tag:airunner'],
            [dict(DEVICE,keyExpiryDisabled=False,expires='2026-11-01T00:00:00Z')],NOW)
        self.assertEqual(expired[2]['state'],'failed')


class Runner(unittest.TestCase):
    STATUS={'available':True,'readiness_errors':[],'maintenance':False,'reservation':None,'workspace':True,
            'sessions':[{'name':'main','attached':1,'activity':0}],'attached':1,'busy_pids':[12],'codex_threads':[]}
    OUTPUT='disk / 4%\ndisk /srv/retained 91%\nweb 200 1.33\nweb 000 0\n'

    def test_workspace_state(self):
        self.assertEqual(states(air_health.workspace_checks(self.STATUS)),
                         {'Workspace':'ok','Maintenance':'ok','Sessions':'ok','Agent work':'ok'})
        closed=air_health.workspace_checks(dict(self.STATUS,available=False,readiness_errors=['tmux is missing'],
                                                maintenance=True,reservation={'owner':'air','operation':'a'*24}))
        self.assertEqual([c['state'] for c in closed[:2]],['failed','warning'])
        self.assertEqual(closed[1]['detail'],'closed by runner replacement operation '+'a'*24)

    def test_disk_and_sequential_web_answers(self):
        found=air_health.probe_checks(self.OUTPUT,'through k001-vpn-egress')
        self.assertEqual(states(found),{'Disk /':'ok','Disk /srv/retained':'warning','Web traffic: GitHub API':'ok','Web traffic: Google':'failed'})
        self.assertEqual(found[2]['detail'],'HTTP 200 in 1.3 s through k001-vpn-egress')
        self.assertEqual(found[3]['detail'],'no answer through k001-vpn-egress')
        with self.assertRaises(RuntimeError):air_health.probe_checks('disk / 4%\nweb 200 1\n','direct')

    def test_report_names_the_web_path_and_continues_after_a_failed_read(self):
        registry={'airunners':['k001-air'],'boxes':{'k001':{'vpn_egress':{'clients':['k001-air']}},'k002':{}}}
        run=subprocess.CompletedProcess([],0,self.OUTPUT.replace('000 0','204 2.1'),'')
        with patch.object(air_health.air_maintenance,'remote',side_effect=RuntimeError('runner maintenance failed')), \
                patch.object(air_health,'tailnet_devices',return_value=[DEVICE]), \
                patch.object(air_health.subprocess,'run',return_value=run) as called:
            checks=air_health.report('k001',registry)['checks']
        self.assertEqual(checks[0]['detail'],'declared runner; web traffic through k001-vpn-egress')
        self.assertEqual(states(checks)['Workspace'],'failed');self.assertEqual(states(checks)['Web traffic: Google'],'ok')
        self.assertEqual(called.call_args[0][0][:5],['tailscale','ssh','neo@k001-air','sh','-s'])
        self.assertEqual(air_health.report('k002',registry)['checks'][0]['state'],'failed')


OPERATION='8fddb1f889560300d38c842f';IMAGE='4a59'*16
INSTALLED=json.dumps({'hostname':'k002-exit','image':IMAGE,'operation_id':OPERATION,'state':'installed'})
RESULT={'success':True,'operation_id':OPERATION,'inputs_sha256':IMAGE,'packages':{'busybox':'1.37.0-r30','python3':'3.14.10-r2'}}
RECORD='installed\n'+INSTALLED+'\nbuilt\n1763192562\nresult\n'+json.dumps(RESULT)+'\n'


class ApplianceImage(unittest.TestCase):
    def test_a_newer_build_records_the_sealed_packages(self):
        sealed=RECORD.replace(json.dumps(RESULT),json.dumps(dict(RESULT,runtime_packages={'busybox':'1.37.0-r30','tailscale':'1.104.7'})))
        value=machine_status.appliance_image('k002-exit',sealed)
        self.assertEqual(value['package_source'],'sealed image')
        self.assertEqual(value['packages'],[{'name':'busybox','version':'1.37.0-r30'},{'name':'tailscale','version':'1.104.7'}])
        self.assertEqual((value['image'],value['operation']),(IMAGE[:24],OPERATION))

    def test_the_record_must_name_the_installed_image_and_valid_packages(self):
        for output in (RECORD.replace('k002-exit','k002-print'),RECORD.replace('"success": true','"success": false'),
                       RECORD.replace('"installed"}','"installing"}'),RECORD.replace('python3','python3;id'),
                       RECORD.replace('1763192562','soon'),RECORD.replace(json.dumps(RESULT),json.dumps(dict(RESULT,operation_id='0'*24))),'',
                       RECORD.replace(json.dumps(RESULT),json.dumps(dict(RESULT,packages={})))):
            with self.subTest(output=output[-60:]),self.assertRaises(RuntimeError):machine_status.appliance_image('k002-exit',output)

    def test_the_probe_reads_only_the_fixed_files_of_the_kind(self):
        script=machine_status.APPLIANCE_PROBE.replace('@KIND@','print')
        self.assertEqual(script.count('doas -n'),3);self.assertIn('base=/mnt/dom0_data/klokast-print\n',script)


class Sealed(unittest.TestCase):
    def test_the_sealed_list_omits_stripped_packages(self):
        sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'roles/appliance/files'))
        import appliance_common
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);(root/'lib/apk/db').mkdir(parents=True)
            (root/'lib/apk/db/installed').write_text('C:Q1\nP:busybox\nV:1.37.0-r30\nF:bin\nR:busybox\n\nP:apk-tools\nV:3.0.1-r0\n\nP:cups\nV:2.4.16-r0\n')
            self.assertEqual(appliance_common.sealed_packages(root,['apk-tools']),{'busybox':'1.37.0-r30','cups':'2.4.16-r0'})


class Exit(unittest.TestCase):
    def test_xen_guest_and_autostart(self):
        self.assertEqual(states(health_checks.xen_checks('exit',  'guests\nexit\nrouter\nautostart\nexit\n')),
                         {'Xen guest':'ok','Xen autostart':'ok'})
        self.assertEqual(states(health_checks.xen_checks('exit','guests\nrouter\nautostart\n')),
                         {'Xen guest':'failed','Xen autostart':'warning'})

    def test_status_of_an_appliance_reads_only_its_declaration_and_health(self):
        plan={'app_vm_specs':[{'app':'exit-node','node_hostname':'k002-exit','runtime_state':'running'}]}
        checks=[health_checks.check('Xen guest','ok','exit runs')]
        with patch.object(machine_status,'resource_plan',return_value=plan),patch.object(machine_status,'health',return_value=checks), \
                patch.object(machine_status,'probe') as probe, \
                patch.object(machine_status,'run_probe',return_value=RECORD) as dom0, \
                patch.object(machine_status.platform_source,'snapshot',return_value={'instance':{'boxes':{},'controllers':[]}}):
            row=machine_status.report('k002-exit')
            probe.assert_not_called()
            self.assertEqual({k:row[k] for k in ('target','declared','state','health')},
                             {'target':'k002-exit','declared':'running','state':'appliance','health':checks})
            self.assertEqual(dom0.call_args[0][0],'k002-dom0');self.assertIn('/mnt/dom0_data/klokast-exit',dom0.call_args[0][1])
            self.assertEqual((row['package_source'],row['image_built_at'],len(row['packages'])),('build closure',1763192562,2))
            with self.assertRaisesRegex(RuntimeError,'not a declared machine'):machine_status.report('k001-exit')


if __name__=='__main__':unittest.main()
