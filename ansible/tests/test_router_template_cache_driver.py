"""The cache driver rechecks native references before controller retirement."""
import contextlib
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from test_router_template_cli import load_cli


class CacheDriverTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.module=load_cli();self.state=Path(self.tmp.name)/'state';self.state.mkdir(mode=0o700)
        self.operation='a'*24;self.directory=self.state/self.operation;self.directory.mkdir(mode=0o700)
        self.engine='b'*40;self.events=[]
        self.result={'box':'boxa','operation_id':self.operation,'status':'transfer-files-retired'}
        self.module.transport.write(self.directory/'cache-cleanup-complete.json',self.result)
        self.stack=contextlib.ExitStack();self.addCleanup(self.stack.close)
        for owner,name,value in ((self.module,'STATE',self.state),
                (self.module.transport,'require_controller',lambda:None),
                (self.module.transport,'approved_engine',lambda:self.engine),
                (self.module.transport,'router_driver_lock',lambda _:contextlib.nullcontext()),
                (self.module.transport,'installation_lock',lambda:contextlib.nullcontext()),
                (self.module,'daily_record',lambda:(None,None)),
                (self.module.transport,'command',self.command)):
            self.stack.enter_context(mock.patch.object(owner,name,value))

    def command(self,argv,**kwargs):
        argv=[str(x) for x in argv]
        if argv[0]=='git':return self.engine if 'rev-parse' in argv else ''
        values=self.module.transport.load(Path(argv[-1][1:]))
        if any(x.endswith('/74-router-template-cleanup.yml') for x in argv):
            self.assertTrue(values['router_cleanup_references_only'])
            self.events.append('native-unused')
        elif any(x.endswith('/74-router-template-cache-cleanup.yml') for x in argv):
            self.assertEqual(self.events,['native-unused'])
            self.assertEqual(values['router_cache_operation'],self.operation)
            self.events.append('controller-retire')
        else:raise AssertionError(argv)
        return ''

    def test_native_reference_check_precedes_exact_controller_retirement(self):
        self.assertEqual(self.module.cleanup_template_cache('boxa',self.operation),self.result)
        self.assertEqual(self.events,['native-unused','controller-retire'])

    def test_daily_reference_refuses_before_any_native_or_controller_work(self):
        with mock.patch.object(self.module,'daily_record',return_value=(None,{'template_operation':self.operation})):
            with self.assertRaisesRegex(self.module.UpdateError,'daily preparation retains'):
                self.module.cleanup_template_cache('boxa',self.operation)
        self.assertEqual(self.events,[])

    def test_native_reference_failure_prevents_controller_retirement(self):
        def refused(argv,**kwargs):
            if str(argv[0])=='git':return self.command(argv,**kwargs)
            raise self.module.UpdateError('native template remains referenced')
        with mock.patch.object(self.module.transport,'command',side_effect=refused):
            with self.assertRaisesRegex(self.module.UpdateError,'remains referenced'):
                self.module.cleanup_template_cache('boxa',self.operation)
        self.assertEqual(self.events,[])


if __name__=='__main__':unittest.main()
