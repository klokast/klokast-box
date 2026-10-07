"""Controller phase routing keeps one first-install identity across retries."""
from contextlib import nullcontext
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from router_provision_fixtures import load_cli
from router_release_fixtures import ENGINE


class ProvisioningPhaseTests(unittest.TestCase):
    def setUp(self):
        self.cli = load_cli()
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.state, self.cache = self.root / 'state', self.root / 'cache'
        self.state.mkdir(); self.cache.mkdir()
        self.operation, self.source, self.template = 'd'*24, 'c'*24, 'b'*24
        self.source_dir = self.cache / self.source
        self.source_dir.mkdir()
        self.selected = {'inputs_directory':str(self.source_dir),
                         'selection_sha256':'1'*64}
        self.built = {'operation':self.template,'engine_approved':True,
                      'selection_sha256':'1'*64,'release_sha256':'2'*64}
        self.prepared = {'evidence_directory':str(self.state / self.operation)}
        self.empty = {'assignment':None,'installation':None,'evidence_directory':str(self.state)}
        self.initial = {'operation_id':self.operation,'engine_commit':ENGINE,
                        'selection_sha256':'1'*64,'release_sha256':'2'*64,
                        'stage':'prepared'}
        self.status = Mock(side_effect=[self.empty,
            {**self.empty,'installation':self.initial}])
        self.build = Mock(return_value=self.built)
        self.resolve = Mock(return_value=self.selected)
        self.prepare = Mock(return_value=self.prepared)
        self.serial = 0
        def token(size):
            self.serial += 1
            return self.operation if self.serial == 2 else f'{self.serial:024x}'
        def command(argv,**kwargs):
            args = [str(item) for item in argv]
            if args[0] == 'git':
                return ENGINE if 'rev-parse' in args else ''
            self.assertEqual(Path(args[0]).name,'ansible-playbook')
            self.assertIn('74-router-recovery-setup.yml',[Path(arg).name for arg in args])
            return ''
        for target,name,value in ((self.cli,'STATE',self.state),
                                  (self.cli,'CACHE',self.cache),
                                  (self.cli,'provisioning_status',self.status),
                                  (self.cli,'resolve_initial',self.resolve),
                                  (self.cli,'build_template',self.build),
                                  (self.cli,'prepare_initial',self.prepare),
                                  (self.cli.secrets,'token_hex',token),
                                  (self.cli.transport,'require_controller',Mock()),
                                  (self.cli.transport,'approved_engine',Mock(return_value=ENGINE)),
                                  (self.cli.transport,'installation_lock',lambda:nullcontext()),
                                  (self.cli.transport,'command',command)):
            context=patch.object(target,name,value)
            context.start(); self.addCleanup(context.stop)
        context=patch.dict(os.environ,{'KLOKAST_INSTALLATION_LOCK_FD':'9'})
        context.start(); self.addCleanup(context.stop)

    def test_prepare_reserves_one_operation_before_native_preparation(self):
        result=self.cli.provision_initial_phase('boxa','prepare')
        self.assertEqual(result['operation_id'],self.operation)
        pointer=json.loads((self.state / 'initial-provision-boxa.json').read_text())
        self.assertEqual(pointer['operation_id'],self.operation)
        self.assertEqual(self.prepare.call_args.args,
                         ('boxa',self.source_dir,self.template,self.operation))

    def test_prebuilt_reservation_needs_no_build_or_local_service_guest(self):
        pointer = self.cli.router_generations.seal({
            'kind': 'klokast.router-initial-provision-pointer.v1', 'box': 'boxa',
            'engine_commit': ENGINE, 'operation_id': self.operation,
            'source_operation': self.source, 'template_operation': self.template,
            'selection_sha256': '1' * 64, 'release_sha256': '2' * 64})
        self.cli.transport.write(self.state / 'initial-provision-boxa.json', pointer)
        result = self.cli.provision_initial_phase('boxa', 'prepare')
        self.assertEqual(result['operation_id'], self.operation)
        self.resolve.assert_not_called()
        self.build.assert_not_called()
        self.prepare.assert_called_once_with('boxa', self.source_dir, self.template, self.operation)

    def test_retry_uses_reserved_operation_after_uncertain_preparation(self):
        self.prepare.side_effect=[RuntimeError('controller lost preparation result'),self.prepared]
        with self.assertRaisesRegex(RuntimeError,'lost preparation result'):
            self.cli.provision_initial_phase('boxa','prepare')
        self.status.side_effect=[self.empty,{**self.empty,'installation':self.initial}]
        result=self.cli.provision_initial_phase('boxa','prepare')
        self.assertEqual(result['operation_id'],self.operation)
        self.resolve.assert_called_once()
        self.build.assert_called_once()
        self.assertEqual(self.prepare.call_count,2)
        self.assertEqual(self.prepare.call_args.args[-1],self.operation)

    def test_accept_requires_the_recorded_first_install(self):
        self.status.side_effect=[self.empty]
        with self.assertRaisesRegex(self.cli.UpdateError,'recorded phase 30'):
            self.cli.provision_initial_phase('boxa','accept')
        self.resolve.assert_not_called()
        self.build.assert_not_called()

    def test_enrolled_retry_skips_new_boot_and_enrollment(self):
        self.cli.provision_initial_phase('boxa','prepare')
        enrolled={**self.initial,'stage':'enrolled'}
        self.status.side_effect=[{**self.empty,'installation':enrolled}]
        preserved=self.cli.provision_initial_phase('boxa','prepare')
        self.assertEqual(preserved['status'],'initial-progress-preserved')
        self.prepare.assert_called_once()
        accepted={'current_sha256':'3'*64}
        self.status.side_effect=[{**self.empty,'installation':enrolled},
                                 {**self.empty,'assignment':accepted,
                                  'installation':{**self.initial,'stage':'verified'}}]
        with patch.object(self.cli,'start_initial') as start, \
             patch.object(self.cli,'enroll_initial') as enroll, \
             patch.object(self.cli,'finalize_initial') as finalize, \
             patch.object(self.cli,'boot_final_initial') as boot, \
             patch.object(self.cli,'verify_initial') as verify, \
             patch.object(self.cli,'accept_initial',return_value={
                 'generation_sha256':'3'*64,'evidence_directory':str(self.state)}) as accept:
            result=self.cli.provision_initial_phase('boxa','accept')
        self.assertEqual(result['status'],'initial-accepted')
        start.assert_not_called()
        enroll.assert_not_called()
        for task in (finalize,boot,verify,accept):
            task.assert_called_once_with('boxa',self.operation)


if __name__ == '__main__':
    unittest.main()
