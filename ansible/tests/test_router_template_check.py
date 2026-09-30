"""An accepted template check must keep its exact protected source."""
from contextlib import nullcontext
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from test_router_template_cli import load_cli
from test_router_updates import ENGINE, PROFILE, release
import test_router_updates as update_fixtures
from test_router_generations import generation, reseal


class TemplateCheckTests(unittest.TestCase):
    def setUp(self):
        self.cli = load_cli()
        self.original_load = self.cli.transport.load
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.state = Path(temporary.name)
        self.operation = 'd' * 24
        self.directory = self.state / self.operation
        self.directory.mkdir()
        self.release = release()
        self.source = generation()
        self.source.update(engine_commit=ENGINE,
                           release_sha256=self.release['receipt_sha256'],
                           packages=self.release['runtime_packages'],
                           kernel_release=self.release['kernel_release'],
                           tailscale={key:self.release['inputs']['tailscale'][key] for key in (
                               'version','sha256','tailscale_sha256','tailscaled_sha256','openrc_sha256')})
        for name in ('kernel','initramfs'):
            self.source['boot'][name]['sha256'] = self.release['artifacts'][name]
        reseal(self.source)
        self.assignment = self.cli.router_generations.seal({
            'kind':'klokast.router-assignment.v1', 'box':'boxa', 'role':'router',
            'current_sha256':self.source['record_sha256'], 'previous_sha256':None,
            'operation_id':self.source['generation_id'], 'engine_commit':ENGINE,
            'policy_sha256':'e'*64, 'evidence_sha256':'f'*64})
        self.accepted = {'kind':'klokast.router-accepted-source.v1', 'box':'boxa',
                         'assignment':self.assignment, 'generation':self.source}
        self.template = self.state / self.source['template_operation']
        self.template.mkdir()
        for name, value in (('profile',PROFILE),('release',self.release),
                            ('candidate',{'test':'qualified-template'})):
            (self.template / (name + '.json')).write_text(json.dumps(value))
        for target in ('router','dom0'):
            (self.directory / ('boxa-' + target + '.json')).write_text('{}')
        self.events = []

    def command(self, argv, **kwargs):
        arguments = [str(value) for value in argv]
        if arguments[0] == 'git':
            return ENGINE if 'rev-parse' in arguments else ''
        if arguments[0] == 'ansible-playbook':
            self.events.append(Path(next(value for value in arguments if value.endswith('.yml'))).name)
            return ''
        raise AssertionError(arguments)

    def load(self, path):
        if path == self.cli.PROFILE:
            return copy.deepcopy(PROFILE)
        return self.original_load(path)

    def check(self, accepted=None):
        now = self.cli.dt.datetime.now(self.cli.dt.timezone.utc)
        live = {'observed_at':self.cli.router_updates.timestamp(now), 'box':'boxa',
                'role':'router', 'generation':self.source['record_sha256'],
                'packages':self.source['packages'], 'kernel_release':self.source['kernel_release'],
                'boot_artifacts':{name:item['sha256'] for name,item in self.source['boot'].items()},
                'configuration_verified':True, 'overlay_ipv6_enabled':False}
        policy = {'enabled':True, 'targets':{'boxa':['router']}, 'exclusions':[],
                  'branch-policy':'tested-stable', 'branch-delay-days':21,
                  'report-max-age-hours':30}
        schedule = {'kind':'klokast.vm-update-schedule.v1', 'activated':True}
        policy_source = {'kind':'klokast.vm-update-policy-source.v1'}
        sources = [self.accepted, self.accepted if accepted is None else accepted]
        with patch.object(self.cli, 'STATE', self.state), \
                patch.object(self.cli.transport, 'require_controller'), \
                patch.object(self.cli.transport, 'approved_engine', return_value=ENGINE), \
                patch.object(self.cli.transport, 'installation_lock', return_value=nullcontext()), \
                patch.object(self.cli.transport, 'command', side_effect=self.command), \
                patch.object(self.cli.transport, 'load', side_effect=self.load), \
                patch.object(self.cli, 'inspect', return_value={
                    'evidence_directory':str(self.directory), 'operation':self.operation}), \
                patch.object(self.cli, 'accepted_source_at', side_effect=sources), \
                patch.object(self.cli, 'check_policy_at', return_value=(
                    schedule,policy_source,policy,'e'*64)), \
                patch.object(self.cli, 'check_upstream_candidates', return_value=(None,{},{})), \
                patch.object(self.cli.router_template_inputs, 'release', return_value=self.release), \
                patch.object(self.cli.router_updates, 'template_live', return_value=live):
            return self.cli.check_template('boxa')

    def test_check_reads_exact_historical_release_and_verifies_live_router(self):
        result = self.check()
        self.assertEqual(result['status'],'deferred')
        self.assertEqual(self.events,['74-router-accepted-verification.yml'])
        self.assertTrue((self.directory / 'check.json').is_file())

    def test_changed_accepted_source_refuses_decision(self):
        with self.assertRaisesRegex(self.cli.UpdateError, 'accepted source changed'):
            self.check({'changed':True})
        self.assertFalse((self.directory / 'check.json').exists())

    def test_missing_historical_release_refuses_decision(self):
        (self.template / 'release.json').unlink()
        with self.assertRaises(self.cli.UpdateError):
            self.check()
        self.assertFalse(self.events)

    def test_update_decision_records_exact_frozen_source(self):
        cache = self.state / 'cache'
        cache.mkdir()
        source = cache / ('a' * 24)
        source.mkdir()
        inputs = self.release['inputs']
        (source / 'inputs.json').write_text(json.dumps(inputs))
        report = self.cli.router_updates.seal({
            'kind':self.cli.router_updates.DECISION, 'role':'router', 'box':'boxa',
            'status':'update-required', 'policy_sha256':'e'*64,
            'accepted_sha256':'f'*64, 'selected_branch':inputs['branch'],
            'candidate_inputs_sha256':inputs['inputs_sha256'],
            'effective_inputs_sha256':self.cli.router_updates.effective_inputs(inputs),
            'checked_at':self.cli.router_updates.timestamp(
                self.cli.dt.datetime.now(self.cli.dt.timezone.utc))}, 'report_sha256')
        candidates = {inputs['branch']:{'status':'verified', 'inputs':inputs}}
        sources = {inputs['branch']:str(source)}
        policy = {'report-max-age-hours':30}
        with patch.object(self.cli, 'CACHE', cache), patch.object(
            self.cli.transport, 'load', side_effect=self.load):
            self.cli.record_check_source(self.directory, report, candidates, sources,
                                         box='boxa', engine=ENGINE, policy=policy)
            binding = json.loads((self.directory / 'candidate-source.json').read_text())
            self.assertEqual(binding['source_operation'], source.name)
            self.assertEqual(binding['report_sha256'], report['report_sha256'])
            self.assertEqual(binding['inputs_sha256'], inputs['inputs_sha256'])
            (self.directory / 'candidate-source.json').unlink()
            with self.assertRaisesRegex(self.cli.UpdateError, 'source changed'):
                self.cli.record_check_source(self.directory, report,
                    {inputs['branch']:{'status':'verified', 'inputs':{'changed':True}}},
                    sources, box='boxa', engine=ENGINE, policy=policy)
            self.assertFalse((self.directory / 'candidate-source.json').exists())

    def test_replacement_preflight_rechecks_current_assignment_and_policy(self):
        self.directory.chmod(0o700)
        cache = self.state / 'cache'
        cache.mkdir()
        source = cache / ('a' * 24)
        source.mkdir(mode=0o700)
        inputs = self.release['inputs']
        (source / 'inputs.json').write_text(json.dumps(inputs))
        accepted = {'box':'boxa','role':'router','generation':self.source['record_sha256'],
                    'release':self.release}
        report = self.cli.router_updates.seal({
            'kind':self.cli.router_updates.DECISION,'box':'boxa','role':'router',
            'status':'update-required','policy_sha256':'e'*64,
            'accepted_sha256':self.cli.router_generations.digest(accepted),
            'selected_branch':inputs['branch'],
            'candidate_inputs_sha256':inputs['inputs_sha256'],
            'effective_inputs_sha256':self.cli.router_updates.effective_inputs(inputs),
            'checked_at':self.cli.router_updates.timestamp(
                self.cli.dt.datetime.now(self.cli.dt.timezone.utc))},'report_sha256')
        binding = {'kind':'klokast.router-check-source.v1','box':'boxa',
                   'report_sha256':report['report_sha256'],
                   'source_operation':source.name,'inputs_sha256':inputs['inputs_sha256'],
                   'engine_commit':ENGINE}
        (self.directory / 'check.json').write_text(json.dumps(report))
        (self.directory / 'candidate-source.json').write_text(json.dumps(binding))
        policy = {'enabled':True,'targets':{'boxa':['router']},'exclusions':[],
                  'branch-policy':'tested-stable','report-max-age-hours':30}
        schedule = {'kind':'klokast.vm-update-schedule.v1','activated':True}
        signed = {'kind':'klokast.vm-update-policy-source.v1'}
        with patch.object(self.cli,'STATE',self.state), patch.object(self.cli,'CACHE',cache), \
                patch.object(self.cli.transport,'require_controller'), \
                patch.object(self.cli.transport,'approved_engine',return_value=ENGINE), \
                patch.object(self.cli.transport,'installation_lock',return_value=nullcontext()), \
                patch.object(self.cli.transport,'command',side_effect=self.command), \
                patch.object(self.cli.transport,'load',side_effect=self.load), \
                patch.object(self.cli,'accepted_source_at',return_value=self.accepted), \
                patch.object(self.cli,'check_policy_at',return_value=(schedule,signed,policy,'e'*64)):
            result = self.cli.preflight_replacement('boxa',self.operation)
            self.assertEqual(result['status'],'validated-input-source')
            self.assertFalse(result['replacement_authorized'])
            self.assertEqual(result['source_operation'],source.name)
            policy['enabled'] = False
            with self.assertRaisesRegex(self.cli.UpdateError,'current replacement policy'):
                self.cli.preflight_replacement('boxa',self.operation)

    def test_replacement_context_requires_fresh_live_router_before_grant(self):
        self.directory.chmod(0o700)
        cache = self.state / 'cache'
        cache.mkdir()
        frozen = cache / ('a' * 24)
        frozen.mkdir(mode=0o700)
        inputs = self.release['inputs']
        (frozen / 'inputs.json').write_text(json.dumps(inputs))
        accepted = {'box':'boxa','role':'router','generation':self.source['record_sha256'],
                    'release':self.release}
        report = self.cli.router_updates.seal({
            'kind':self.cli.router_updates.DECISION,'box':'boxa','role':'router',
            'status':'update-required','policy_sha256':'e'*64,
            'accepted_sha256':self.cli.router_generations.digest(accepted),
            'selected_branch':inputs['branch'],
            'candidate_inputs_sha256':inputs['inputs_sha256'],
            'effective_inputs_sha256':self.cli.router_updates.effective_inputs(inputs),
            'checked_at':self.cli.router_updates.timestamp(
                self.cli.dt.datetime.now(self.cli.dt.timezone.utc))},'report_sha256')
        binding = {'kind':'klokast.router-check-source.v1','box':'boxa',
            'report_sha256':report['report_sha256'],'source_operation':frozen.name,
            'inputs_sha256':inputs['inputs_sha256'],'engine_commit':ENGINE}
        (self.directory / 'check.json').write_text(json.dumps(report))
        (self.directory / 'candidate-source.json').write_text(json.dumps(binding))
        policy = {'enabled':True,'targets':{'boxa':['router']},'exclusions':[],
                  'branch-policy':'tested-stable','report-max-age-hours':30}
        schedule = {'kind':'klokast.vm-update-schedule.v1','activated':True}
        signed = {'kind':'klokast.vm-update-policy-source.v1'}
        evidence = self.state / ('f'*24)
        evidence.mkdir(mode=0o700)
        live = {'overlay_ipv6_enabled':False,'configuration_verified':True}
        with patch.object(self.cli,'STATE',self.state), patch.object(self.cli,'CACHE',cache), \
                patch.object(self.cli.transport,'approved_engine',return_value=ENGINE), \
                patch.object(self.cli.transport,'command',side_effect=self.command), \
                patch.object(self.cli.transport,'load',side_effect=self.load), \
                patch.object(self.cli,'inspect',return_value={
                    'evidence_directory':str(self.directory),'operation':self.operation}), \
                patch.object(self.cli,'accepted_source_at',return_value=self.accepted), \
                patch.object(self.cli,'check_policy_at',return_value=(schedule,signed,policy,'e'*64)), \
                patch.object(self.cli.router_template_inputs,'release',return_value=self.release), \
                patch.object(self.cli.router_updates,'template_live',return_value=live) as live_check:
            context = self.cli.replacement_context('boxa',self.operation,ENGINE,PROFILE,
                                                   evidence,'fresh')
            self.assertEqual(context['assignment'],self.assignment)
            self.assertEqual(context['binding'],binding)
            self.assertIn('74-router-accepted-verification.yml',self.events)
            live_check.return_value = {'overlay_ipv6_enabled':True,
                                       'configuration_verified':True}
            with self.assertRaisesRegex(self.cli.UpdateError,'cannot reconstruct'):
                self.cli.replacement_context('boxa',self.operation,ENGINE,PROFILE,evidence,'overlay')
            live_check.side_effect = self.cli.UpdateError('live router drift')
            with self.assertRaisesRegex(self.cli.UpdateError,'live router drift'):
                self.cli.replacement_context('boxa',self.operation,ENGINE,PROFILE,evidence,'drift')

    def test_live_template_requires_protected_disk_boot_and_configuration(self):
        now = self.cli.dt.datetime.now(self.cli.dt.timezone.utc)
        observed = self.cli.router_updates.timestamp(now)
        configuration = {'/etc/network/interfaces':{
            'sha256':self.source['configuration_files']['etc/network/interfaces']}}
        guest = {'observed_at':observed, 'alpine_branch':self.source['alpine_branch'],
                 'packages':self.source['packages'], 'kernel_release':self.source['kernel_release'],
                 'service_accounts':self.source['accounts'],
                 'configuration_files':configuration, 'include_files':{}}
        xen = self.source['xen']
        dom0 = {'observed_at':observed,
                'logical_volumes':{'report':[{'lv':[{
                    'lv_path':self.source['disk']['path'], 'lv_uuid':self.source['disk']['uuid'],
                    'lv_size':str(self.source['disk']['bytes']), 'origin':''}]}]},
                'xen':{'name':'router', 'uuid':xen['uuid'], 'type':'pvh',
                       'memory':xen['memory'], 'vcpus':xen['vcpus'],
                       'kernel':self.source['boot']['kernel']['path'],
                       'ramdisk':self.source['boot']['initramfs']['path'],
                       'extra':'console=hvc0 root=/dev/xvda3 rw modules=ext4',
                       'disk':['phy:' + self.source['disk']['path'] + ',xvda,w'],
                       'vif':xen['vif'], 'on_crash':'destroy', 'on_reboot':'restart'},
                'xen_runtime':{'uuid':xen['uuid']},
                'configuration_sha256':hashlib.sha256(
                    self.cli.router_generations.configuration(self.source).encode()).hexdigest(),
                'boot_artifacts':{'kernel':self.source['boot']['kernel'],
                                  'ramdisk':self.source['boot']['initramfs']}}
        args = dict(box='boxa', assignment=self.assignment, source=self.source,
                    guest=guest, dom0=dom0, now=now, accepted_manifest_verified=True)
        with patch.object(self.cli.router_updates, 'legacy_baseline_findings', return_value=[]):
            self.assertEqual(self.cli.router_updates.template_live(**args)['generation'],
                             self.source['record_sha256'])
            for changed in (
                    {**dom0, 'configuration_sha256':'0'*64},
                    {**dom0, 'boot_artifacts':{**dom0['boot_artifacts'], 'kernel':{
                        **dom0['boot_artifacts']['kernel'], 'sha256':'0'*64}}},
                    {**dom0, 'logical_volumes':{'report':[{'lv':[{
                        **dom0['logical_volumes']['report'][0]['lv'][0], 'lv_uuid':'other'}]}]}}):
                with self.assertRaises(self.cli.UpdateError):
                    self.cli.router_updates.template_live(**{**args,'dom0':changed})
            with self.assertRaises(self.cli.UpdateError):
                self.cli.router_updates.template_live(**{**args,
                    'guest':{**guest,'configuration_files':{
                        '/etc/network/interfaces':{'sha256':'0'*64}}}})
            with self.assertRaises(self.cli.UpdateError):
                self.cli.router_updates.template_live(**{**args,
                    'accepted_manifest_verified':False})

    def test_live_template_inspection_blocks_unreconstructable_state(self):
        guest, dom0 = update_fixtures.LegacyBaselineTests().fixture()
        now = self.cli.dt.datetime.now(self.cli.dt.timezone.utc)
        observed = self.cli.router_updates.timestamp(now)
        guest.update(observed_at=observed, packages=self.source['packages'],
                     kernel_release=self.source['kernel_release'],
                     service_accounts=self.source['accounts'])
        guest['state_paths']['/var/lib/tailscale/tailscaled.state']['gid'] = self.source['accounts']['tailscale_gid']
        guest['state_paths']['/var/lib/misc/dnsmasq.leases'].update(
            uid=self.source['accounts']['dnsmasq_uid'],
            gid=self.source['accounts']['dnsmasq_gid'])
        for path, item in guest['configuration_files'].items():
            item['sha256'] = self.source['configuration_files'][path.lstrip('/')]
        includes = {key:value for key,value in self.source['configuration_files'].items()
                    if key.startswith('etc/klokast/app-resources/')}
        guest['expected_includes']['files'] = {'/' + key:value for key,value in includes.items()}
        guest['include_files'] = {'/' + key:{'sha256':value,
            'metadata':{'present':True,'regular':True,'links':1,'bytes':100,
                        'mode':'0o644','uid':0,'gid':0}} for key,value in includes.items()}
        dom0.update(observed_at=observed, accepted_record_present=True,
                    configuration_sha256=hashlib.sha256(
                        self.cli.router_generations.configuration(self.source).encode()).hexdigest(),
                    xen_runtime={'uuid':self.source['xen']['uuid']})
        xen = self.source['xen']
        dom0['xen'] = {'name':'router','uuid':xen['uuid'],'type':'pvh',
                       'memory':xen['memory'],'vcpus':xen['vcpus'],
                       'kernel':self.source['boot']['kernel']['path'],
                       'ramdisk':self.source['boot']['initramfs']['path'],
                       'extra':'console=hvc0 root=/dev/xvda3 rw modules=ext4',
                       'disk':['phy:' + self.source['disk']['path'] + ',xvda,w'],
                       'vif':xen['vif'],'on_crash':'destroy','on_reboot':'restart'}
        dom0['logical_volumes']['report'][0]['lv'] = [{
            'lv_path':self.source['disk']['path'],'lv_uuid':self.source['disk']['uuid'],
            'lv_size':str(self.source['disk']['bytes']),'origin':''}]
        dom0['boot_artifacts'] = {'kernel':self.source['boot']['kernel'],
                                  'ramdisk':self.source['boot']['initramfs']}
        args = dict(box='boxa', assignment=self.assignment, source=self.source,
                    guest=guest, dom0=dom0, now=now, accepted_manifest_verified=True)
        self.assertEqual(self.cli.router_updates.template_live(**args)['generation'],
                         self.source['record_sha256'])
        guest['overlay_ipv6_enabled'] = True
        with self.assertRaisesRegex(self.cli.UpdateError, 'cannot reconstruct'):
            self.cli.router_updates.template_live(**args)
        guest['overlay_ipv6_enabled'] = False
        guest['state_paths']['/var/lib/dhcpcd/secret']['regular'] = False
        with self.assertRaisesRegex(self.cli.UpdateError, 'identity or lease'):
            self.cli.router_updates.template_live(**args)


if __name__ == '__main__':
    unittest.main()
