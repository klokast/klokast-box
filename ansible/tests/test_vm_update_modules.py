"""Entrypoint installation and frozen interpreter contracts."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO/'ansible/lib'))
import vm_service_updates as updates


class ModuleContracts(unittest.TestCase):
    def test_service_command_runs_with_only_the_ansible_staged_modules(self):
        import yaml
        play = yaml.safe_load((REPO/'ansible/playbooks/74-vm-service-update.yml').read_text())[0]
        stage = play['vars']['service_helper_sources']
        writer = play['tasks'][1]['block'][1]['ansible.builtin.command']['argv'][2]
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            files = {item['name']: (REPO/'ansible/playbooks'/item['source']).read_text() for item in stage}
            files.update({'config.json': json.dumps({'role': 'bak'}), 'receipt.json': json.dumps({'files': {}})})
            staged = subprocess.run([sys.executable, '-c', writer, str(directory)],
                                    input=json.dumps(files), text=True, capture_output=True, timeout=30)
            self.assertEqual(staged.returncode, 0, staged.stderr)
            self.assertEqual({p.name: p.read_text() for p in directory.iterdir()}, files)
            self.assertTrue(all(p.stat().st_mode & 0o777 == 0o600 for p in directory.iterdir()))
            result = subprocess.run([sys.executable, str(directory/'vm-service-update'), '--help'],
                                    cwd='/', text=True, capture_output=True, timeout=30,
                                    env={k:v for k,v in os.environ.items() if k != 'PYTHONPATH'})
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('reboot-complete', result.stdout)

    def test_helper_staging_refuses_path_escape_and_existing_files(self):
        import yaml
        play = yaml.safe_load((REPO/'ansible/playbooks/74-vm-service-update.yml').read_text())[0]
        writer = play['tasks'][1]['block'][1]['ansible.builtin.command']['argv'][2]
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)/'stage'; directory.mkdir()
            kept = directory/'kept'; kept.write_text('original')
            for files in ({'../outside': 'bad'}, {'/absolute': 'bad'}, {'..': 'bad'},
                          {'kept': 'changed'}, {'valid': None}):
                with self.subTest(files=files):
                    result = subprocess.run([sys.executable, '-c', writer, str(directory)],
                                            input=json.dumps(files), text=True, capture_output=True, timeout=30)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertEqual(kept.read_text(), 'original')
                    self.assertEqual(list(directory.iterdir()), [kept])
                    self.assertEqual(list(Path(temporary).iterdir()), [directory])

    def test_installed_transaction_cli_imports_only_its_adjacent_modules(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            source = REPO/'ansible/roles/vm-update-recovery/files'
            for name in ('vm-update-transaction', 'vm_update_engine.py', 'vm_update_native.py'):
                shutil.copyfile(source/name, directory/name)
            result = subprocess.run([sys.executable, '-I', str(directory/'vm-update-transaction'), '--help'],
                                    cwd='/', text=True, capture_output=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('replacement-source-status', result.stdout)

    def test_frozen_worker_does_not_use_inherited_python_modules(self):
        with tempfile.TemporaryDirectory() as temporary:
            work = Path(temporary)
            public = work/'public'
            (public/'ansible/bin').mkdir(parents=True)
            (public/'ansible/lib').mkdir()
            shutil.copyfile(REPO/'ansible/bin/vm-service-resume', public/'ansible/bin/vm-service-resume')
            (public/'ansible/lib/ops_replacement_inputs.py').write_text(
                'from pathlib import Path\n'
                'def recover(record, **kw):\n'
                '    return Path(__file__).resolve().parents[3], {"service_configuration": {}}\n')
            (public/'ansible/lib/vm_service_updates.py').write_text(
                'from pathlib import Path\nROOT = Path("/unused")\n'
                'def authority(box, role): return "running"\n'
                'def finish(*args, **kw): return {"module": str(Path(__file__).resolve())}\n')
            poison = work/'poison'; poison.mkdir()
            (poison/'vm_service_updates.py').write_text('raise RuntimeError("current checkout imported")\n')
            with patch.dict(os.environ, {'PYTHONPATH': str(poison)}):
                result = updates.finish_frozen('boxa', 'vpn-egress', work,
                    {'service_configuration': {}}, {}, 'a'*24, 'b'*24, 'c'*64)
            self.assertEqual(result['module'], str(public/'ansible/lib/vm_service_updates.py'))

    def test_command_help_and_retired_contracts(self):
        for command, selectors in (('platform-update', ['update', '--help']),
                                   ('platform-vpn-egress', ['--help'])):
            result = subprocess.run([sys.executable, str(REPO/'ansible/bin'/command), *selectors],
                                    text=True, capture_output=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
        result = subprocess.run([sys.executable, str(REPO/'ansible/bin/platform-update'), 'replace'],
                                text=True, capture_output=True, timeout=30)
        self.assertEqual(result.returncode, 2)
        self.assertIn('invalid choice', result.stderr)

    def test_rebuild_resume_retirement_and_nightly_selectors_are_mutually_exclusive(self):
        for command,args in (
            ('platform-update',['update','--box','boxa','--role','vpn-egress','--rebuild','--resume','a'*24]),
            ('platform-update',['update','--box','boxa','--role','vpn-egress','--retire-older','--rebuild']),
            ('ops-controller-nightly',['--services','--service-role','vpn-egress'])):
            result=subprocess.run([sys.executable,str(REPO/'ansible/bin'/command),*args],
                                  text=True,capture_output=True,timeout=30)
            self.assertEqual(result.returncode,2,result.stderr)
            self.assertIn('not allowed',result.stderr)
