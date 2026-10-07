"""Bootstrap helpers cannot replace packaged code or escape their job root."""
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
import vm_template_inputs
from platform_updates import UpdateError


class BootstrapStagingTests(unittest.TestCase):
    def test_job_modules_cannot_escape_or_replace_package_content(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            root = directory / 'root'; root.mkdir()
            source = directory / 'helper.py'; source.write_text('pass\n')
            for name in ('../escape.py', '/absolute.py', 'x/../escape.py'):
                with self.subTest(name=name), self.assertRaises(UpdateError):
                    vm_template_inputs.stage_job_files(root, {name: source})
            (root / 'linked').symlink_to(directory)
            with self.assertRaises(UpdateError):
                vm_template_inputs.stage_job_files(root, {'linked/escape.py': source})
            vm_template_inputs.stage_job_files(root, {'usr/local/lib/helper.py': source})
            with self.assertRaises(UpdateError):
                vm_template_inputs.stage_job_files(root, {'usr/local/lib/helper.py': source})
            self.assertEqual((root / 'usr/local/lib/helper.py').read_text(), 'pass\n')
