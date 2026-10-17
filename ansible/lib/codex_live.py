"""Bounded JSON-RPC over the native, runner-local Codex Unix WebSocket."""
import base64
import hashlib
import json
import os
from pathlib import Path
import secrets
import socket
import struct
import subprocess
import time


class LiveRPC:
    def __init__(self, binary, timeout=30):
        self.binary=binary
        self.timeout=timeout
        self.number=0

    def __enter__(self):
        status=json.loads(subprocess.check_output(
            [self.binary,'app-server','daemon','version'],text=True,timeout=15))
        if status.get('status')!='running':
            raise RuntimeError('the native Codex daemon is not running')
        path=Path(status['socketPath'])
        if path.stat().st_uid!=os.geteuid():
            raise RuntimeError('the native Codex socket belongs to another account')
        self.socket=socket.socket(socket.AF_UNIX)
        self.socket.settimeout(self.timeout)
        self.socket.connect(str(path))
        self.stream=self.socket.makefile('rb')
        key=base64.b64encode(secrets.token_bytes(16)).decode()
        self.socket.sendall(('GET / HTTP/1.1\r\nHost: localhost\r\nUpgrade: websocket\r\n'
            'Connection: Upgrade\r\nSec-WebSocket-Version: 13\r\nSec-WebSocket-Key: '+key+'\r\n\r\n').encode())
        try:
            response=self.stream.readline(4096)
            if not response.startswith(b'HTTP/1.1 101 '):
                raise RuntimeError('native Codex WebSocket upgrade was refused')
            headers={}
            for _ in range(32):
                line=self.stream.readline(4096)
                if line==b'\r\n':break
                name,value=line.decode().split(':',1);headers[name.lower()]=value.strip()
            else:raise RuntimeError('native Codex upgrade headers exceed their bound')
            expected=base64.b64encode(hashlib.sha1((key+'258EAFA5-E914-47DA-95CA-C5AB0DC85B11').encode()).digest()).decode()
            if headers.get('sec-websocket-accept')!=expected:
                raise RuntimeError('native Codex WebSocket upgrade did not match')
            self.call('initialize',{'clientInfo':{'name':'klokast-workspace','version':'1'},
                'capabilities':{'experimentalApi':True}})
            self.send({'method':'initialized'})
            return self
        except BaseException:
            self.__exit__();raise

    def frame(self,opcode,data):
        mask=secrets.token_bytes(4)
        size=len(data)
        header=bytes([0x80|opcode,0x80|min(size,126 if size<65536 else 127)])
        if size>=65536:header+=struct.pack('!Q',size)
        elif size>=126:header+=struct.pack('!H',size)
        self.socket.sendall(header+mask+bytes(v^mask[i%4] for i,v in enumerate(data)))

    def send(self,value):
        self.frame(1,json.dumps(value).encode())

    def read_exact(self,size):
        value=self.stream.read(size)
        if len(value)!=size:raise RuntimeError('native Codex control connection closed')
        return value

    def receive(self):
        message=bytearray()
        while True:
            first,second=self.read_exact(2);opcode=first&15;size=second&127
            if size==126:size=struct.unpack('!H',self.read_exact(2))[0]
            elif size==127:size=struct.unpack('!Q',self.read_exact(8))[0]
            if second&128 or size>16*1024*1024 or len(message)+size>16*1024*1024:
                raise RuntimeError('native Codex control frame is unsupported or too large')
            data=self.read_exact(size)
            if opcode==8:raise RuntimeError('native Codex control connection closed')
            if opcode==9:self.frame(10,data);continue
            if opcode==10:continue
            if opcode not in (0,1):raise RuntimeError('native Codex returned a non-JSON control frame')
            message.extend(data)
            if first&128:return json.loads(message)

    def call(self,method,params=None):
        self.number+=1
        request={'id':self.number,'method':method,'params':params or {}}
        self.send(request)
        deadline=time.monotonic()+self.timeout
        while time.monotonic()<deadline:
            self.socket.settimeout(max(.1,deadline-time.monotonic()))
            reply=self.receive()
            if reply.get('id')!=self.number:continue
            if 'error' in reply:
                raise RuntimeError('native Codex refused '+method+' (code '+str(reply['error'].get('code'))+'): '+
                                   str(reply['error'].get('message',''))[:400])
            return reply['result']
        raise RuntimeError('native Codex control request timed out: '+method)

    def __exit__(self,*args):
        self.stream.close();self.socket.close()
