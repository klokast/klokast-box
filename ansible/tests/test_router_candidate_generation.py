"""A candidate record must bind one qualified release, clone and topology."""
import copy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
import router_candidate_generation as candidate
import router_generations as generations
import router_personalize
from router_transaction import TransactionError
from test_router_generations import generation
from test_router_updates import release, PROFILE, ENGINE


class CandidateGenerationTests(unittest.TestCase):
    def fixture(self):
        operation = 'f' * 24
        old = generation('legacy')
        selected = release()
        prepared = {'kind':'klokast.router-candidate-files.v1', 'box':'boxa', 'role':'router',
                    'mode':'replacement', 'operation_id':operation, 'engine_commit':ENGINE,
                    'inputs_sha256':selected['inputs']['inputs_sha256'],
                    'packages':dict(selected['runtime_packages']), 'accounts':old['accounts'],
                    'configuration_files':dict.fromkeys(router_personalize.FILES, 'e'*64),
                    'identity_absent':True, 'replacement_authorized':False, 'service_syntax':True}
        disk = {'kind':'klokast.router-candidate-disk.v1', 'operation_id':operation,
                'path':'/dev/vg0/routergen_'+operation, 'tag':'routergen_'+operation,
                'uuid':'new-candidate-uuid', 'stage':'cloned',
                'template_sha256':selected['artifacts']['os']}
        directory = '/mnt/dom0_data/klokast-router-updates/generations/' + operation
        boot = {name:{'path':directory+'/'+name, 'sha256':selected['artifacts'][name], 'bytes':1234}
                for name in ('kernel','initramfs')}
        return dict(box='boxa', operation=operation, old=old, release=selected,
                    profile=PROFILE, prepared=prepared, disk_record=disk, boot=boot,
                    xen_uuid='33333333-1111-4111-8111-111111111111', approved_engine=ENGINE)

    def test_proposed_generation_uses_old_topology_and_new_exact_assets(self):
        args=self.fixture()
        result=candidate.assemble(**args)
        self.assertEqual(generations.generation(result,'boxa'),result)
        self.assertEqual(result['disk']['path'],args['disk_record']['path'])
        self.assertEqual(result['xen']['vif'],args['old']['xen']['vif'])
        self.assertEqual(result['packages'],args['release']['runtime_packages'])
        self.assertEqual(result['origin'],'template')

    def test_mismatched_preparation_disk_boot_or_xen_is_rejected(self):
        changes = (
            lambda v:v['prepared'].update(mode='initial-install'),
            lambda v:v['prepared'].update(identity_absent=False),
            lambda v:v['prepared'].update(service_syntax=False),
            lambda v:v['prepared']['packages'].update(tailscale='changed'),
            lambda v:v['disk_record'].update(stage='allocated'),
            lambda v:v['disk_record'].update(template_sha256='0'*64),
            lambda v:v['boot']['kernel'].update(sha256='0'*64),
            lambda v:v.update(xen_uuid=v['old']['xen']['uuid']),
            lambda v:v.update(box='boxb'),
        )
        for change in changes:
            args=copy.deepcopy(self.fixture())
            change(args)
            with self.subTest(change=change), self.assertRaises((TransactionError, generations.GenerationError)):
                candidate.assemble(**args)


if __name__ == '__main__':
    unittest.main()
