"""Verify the headless client checks IPP without a guest shell or print job."""
import contextlib
import io
from pathlib import Path
import runpy
import struct
import sys
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


if __name__=='__main__':unittest.main()
