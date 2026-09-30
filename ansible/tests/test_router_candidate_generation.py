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
                    'tailscale':{key:selected['inputs']['tailscale'][key] for key in (
                        'version', 'sha256', 'tailscale_sha256', 'tailscaled_sha256', 'openrc_sha256')},
                    'configuration_files':dict.fromkeys(router_personalize.FILES, 'e'*64),
                    'identity_absent':True, 'replacement_authorized':False, 'service_syntax':True}
        disk = {'kind':'klokast.router-candidate-disk.v1', 'operation_id':operation,
                'path':'/dev/vg0/routergen_'+operation, 'tag':'routergen_'+operation,
                'uuid':'new-candidate-uuid', 'stage':'cloned',
                'template_sha256':selected['artifacts']['os']}
        directory = '/mnt/dom0_data/klokast-router-updates/generations/' + operation
        boot = {name:{'path':directory+'/'+name, 'sha256':selected['artifacts'][name], 'bytes':1234}
                for name in ('kernel','initramfs')}
        return dict(box='boxa', operation=operation, template_operation='e'*24,
                    old=old, release=selected,
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
        self.assertEqual(result['template_operation'], args['template_operation'])
        self.assertEqual(result['release_sha256'], args['release']['receipt_sha256'])

    def test_mismatched_preparation_disk_boot_or_xen_is_rejected(self):
        changes = (
            lambda v:v['prepared'].update(mode='initial-install'),
            lambda v:v['prepared'].update(identity_absent=False),
            lambda v:v['prepared'].update(service_syntax=False),
            lambda v:v['prepared']['packages'].update(tailscale='changed'),
            lambda v:v['prepared']['tailscale'].update(tailscaled_sha256='0'*64),
            lambda v:v['disk_record'].update(stage='allocated'),
            lambda v:v['disk_record'].update(template_sha256='0'*64),
            lambda v:v['boot']['kernel'].update(sha256='0'*64),
            lambda v:v.update(xen_uuid=v['old']['xen']['uuid']),
            lambda v:v.update(box='boxb'),
            lambda v:v.update(template_operation='not-an-operation'),
        )
        for change in changes:
            args=copy.deepcopy(self.fixture())
            change(args)
            with self.subTest(change=change), self.assertRaises((TransactionError, generations.GenerationError)):
                candidate.assemble(**args)

    def test_initial_generation_uses_same_template_and_enrolled_finalization(self):
        args = self.fixture()
        selected = args['release']
        prepared = args['prepared']
        prepared['mode'] = 'initial-install'
        prepared['packages'] = {p['name']:p['version'] for p in selected['inputs']['packages']}
        finalized = {'kind':'klokast.router-finalization.v1',
            'packages':copy.deepcopy(selected['runtime_packages']),
            'removed_packages':sorted(prepared['packages'].keys() - selected['runtime_packages'].keys()),
            'tests':selected['runtime_tests'], 'enrolled_state_preserved':True}
        values = dict(box='boxa', operation=args['operation'],
            template_operation=args['template_operation'],release=selected,
            profile=PROFILE, prepared=prepared, finalized=finalized,
            disk_record=args['disk_record'], boot=args['boot'], xen=args['old']['xen'],
            selection_sha256='c'*64, enrollment_sha256='d'*64, approved_engine=ENGINE)
        record = candidate.assemble_initial(**values)
        self.assertEqual(generations.generation(record, 'boxa'), record)
        self.assertEqual(record['packages'], selected['runtime_packages'])
        self.assertEqual(record['tailscale'], prepared['tailscale'])
        self.assertEqual(record['template_operation'], values['template_operation'])
        self.assertEqual(record['release_sha256'], selected['receipt_sha256'])
        for change in (lambda v:v['prepared'].update(mode='replacement'),
                       lambda v:v['finalized'].update(enrolled_state_preserved=False),
                       lambda v:v['finalized']['packages'].update(openssh='1-r0'),
                       lambda v:v.update(selection_sha256='0'),
                       lambda v:v.update(enrollment_sha256='0'),
                       lambda v:v.update(template_operation='not-an-operation'),
                       lambda v:v['disk_record'].update(stage='allocated')):
            invalid = copy.deepcopy(values)
            change(invalid)
            with self.subTest(change=change), self.assertRaises((TransactionError, generations.GenerationError)):
                candidate.assemble_initial(**invalid)


if __name__ == '__main__':
    unittest.main()
