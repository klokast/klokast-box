import importlib.util
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

PATH = Path(__file__).resolve().parents[1] / 'roles/vm-template-builder/files/vm_controller_state.py'
spec=importlib.util.spec_from_file_location('controller_credentials',PATH)
state=importlib.util.module_from_spec(spec);spec.loader.exec_module(state)


class CredentialPersistence(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name);self.old=self.root/'old';self.new=self.root/'new'
        self.old.mkdir();self.new.mkdir()
        self.relative='etc/klokast/tailscale-policy.env'
        self.source=self.old/self.relative;self.source.parent.mkdir(parents=True)
        self.source.write_bytes(b'synthetic credential');self.source.chmod(0o600)

    def root_metadata(self):
        original=Path.lstat
        def observed(path,*args,**kwargs):
            result=original(path,*args,**kwargs)
            if path==self.source:
                values=list(result);values[4]=0;values[5]=0
                return os.stat_result(values)
            return result
        return patch.object(Path,'lstat',observed)

    def test_same_controller_copy_keeps_modes_and_atomic_updates(self):
        marker=self.source.parent/'controller-ha.json';marker.write_text('old active marker')
        with self.root_metadata(),patch('os.fchown'):
            state.preserve_credentials(self.old,self.new)
        target=self.new/self.relative
        self.assertEqual(target.read_bytes(),b'synthetic credential')
        self.assertEqual(target.stat().st_mode&0o777,0o600)
        self.assertFalse((self.new/'etc/klokast/controller-ha.json').exists())
        changed=target.with_name('rotated');changed.write_bytes(b'new credential');changed.chmod(0o600)
        changed.replace(target)
        self.assertEqual(target.read_bytes(),b'new credential')
        self.assertNotIn('etc/klokast',state.PRESERVE)

    def test_unsafe_modes_and_links_are_rejected(self):
        self.source.chmod(0o644)
        with self.root_metadata(),self.assertRaisesRegex(RuntimeError,'unsafe metadata'):
            state.preserve_credentials(self.old,self.new)
        self.source.unlink();self.source.symlink_to('/dev/null')
        with self.root_metadata(),self.assertRaisesRegex(RuntimeError,'unsafe metadata'):
            state.preserve_credentials(self.old,self.new)

    def test_destination_links_cannot_redirect_copy(self):
        (self.new/'etc').symlink_to(self.old/'etc')
        with self.root_metadata(),self.assertRaisesRegex(RuntimeError,'escapes'):
            state.preserve_credentials(self.old,self.new)
        self.assertEqual(self.source.read_bytes(),b'synthetic credential')


if __name__=='__main__':unittest.main()
