"""Verify the headless client checks IPP without a guest shell or print job."""
import contextlib
import io
from pathlib import Path
import runpy
import shutil
import struct
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

SCRIPT=Path(__file__).resolve().parents[2]/'apps/print-server/bin/print-serverctl'


def formats_reply():
    def field(name,value):
        name,value=name.encode(),value.encode()
        return b'\x49'+struct.pack('!H',len(name))+name+struct.pack('!H',len(value))+value
    return b'\x02\x00\x00\x00\x00\x00\x00\x01\x04'+field('document-format-supported','application/pdf')+field('','image/urf')+b'\x03'


class PrintClientTest(unittest.TestCase):
    def test_verify_queries_queue_and_denies_administration_without_submitting(self):
        requests=[]
        class Connection:
            def __init__(self,*args,**kwargs):self.method=None
            def request(self,method,path,body=None,headers=None):
                self.method=method;requests.append((method,path,body))
            def getresponse(self):
                return types.SimpleNamespace(status=403 if self.method=='GET' else 200,read=lambda:b'' if self.method=='GET' else formats_reply())
            def close(self):pass
        module=runpy.run_path(str(SCRIPT))
        with patch.object(sys,'argv',[str(SCRIPT),'verify','--box','boxa']), \
                patch.object(module['subprocess'],'check_output',side_effect=['{"hostname":"boxa-print","lan_subnet":""}','example.ts.net\n']), \
                patch.object(module['http'].client,'HTTPConnection',Connection), \
                patch.object(module['socket'],'create_connection',side_effect=ConnectionRefusedError), \
                contextlib.redirect_stdout(io.StringIO()):
            module['main']()
        self.assertEqual(len(requests),2)
        self.assertEqual(requests[0][:2],('GET','/admin/'))
        self.assertEqual(struct.unpack_from('!H',requests[1][2],2)[0],11)

    def test_failed_command_gives_one_line_and_no_traceback(self):
        module=runpy.run_path(str(SCRIPT))
        failure=module['subprocess'].CalledProcessError(1,['/repo/ansible/bin/platform-print-appliance','preflight','--box','boxa'])
        self.assertEqual(module['refusal'](failure),'platform-print-appliance failed')
        self.assertEqual(module['refusal'](RuntimeError('print IPP query failed')),'print IPP query failed')
        # A relocated copy calls a stub and not the Platform tool.
        with tempfile.TemporaryDirectory() as temporary:
            copy=Path(temporary)/'apps/print-server/bin/print-serverctl'
            tool=Path(temporary)/'ansible/bin/platform-print-appliance'
            for path in (copy,tool):path.parent.mkdir(parents=True)
            shutil.copy2(SCRIPT,copy)
            tool.write_text('#!/bin/sh\necho "stub: no print appliance" >&2\nexit 1\n');tool.chmod(0o700)
            completed=subprocess.run([sys.executable,str(copy),'verify','--box','boxa'],
                                     capture_output=True,text=True,timeout=60)
        self.assertEqual(completed.returncode,1)
        self.assertEqual(completed.stderr,'stub: no print appliance\nprint-serverctl: platform-print-appliance failed\n')


if __name__=='__main__':unittest.main()
