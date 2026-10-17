"""Run the controller repository shell against isolated transport fixtures."""
import importlib.util
from importlib.machinery import SourceFileLoader
import os
from pathlib import Path
import shlex
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[2]
loader=SourceFileLoader('repository_ha',str(ROOT/'ansible/bin/ops-controller-ha'))
spec=importlib.util.spec_from_loader(loader.name,loader)
ha=importlib.util.module_from_spec(spec);loader.exec_module(ha)


class RepositoryTransport(unittest.TestCase):
    def test_public_proxy_is_bounded_optional_and_never_used_for_instance_git(self):
        cases=[('https://github.com/klokast/klokast-box.git','0','1',True,True),
               ('https://github.com/klokast/klokast-box.git','1','0',True,False),
               ('https://github.com/klokast/klokast-box.git','0','0',False,True),
               ('git@github.com:fixture/klokast-instance.git','0','1',False,False)]
        for origin,direct,allowed,reachable,proxied in cases:
            with self.subTest(origin=origin,direct=direct,allowed=allowed),tempfile.TemporaryDirectory() as temporary:
                root=Path(temporary);(root/'.git').mkdir();binary=root/'bin';binary.mkdir()
                scripts={
                    'hostname':'#!/bin/sh\nprintf "fixture-ops\\n"\n',
                    'git':'''#!/bin/sh
set -eu
case "$*" in
  'remote get-url origin') printf '%s\\n' "$FIXTURE_ORIGIN" ;;
  'rev-parse HEAD') printf '%s\\n' "$FIXTURE_HEAD" ;;
  'symbolic-ref --quiet --short HEAD') printf 'main\\n' ;;
  'status --porcelain') : ;;
  'ls-remote origin refs/heads/main')
    [ "$FIXTURE_DIRECT" = 1 ] || [ "${FIXTURE_PROXIED:-0}" = 1 ] || exit 1
    printf '%s\\trefs/heads/main\\n' "$FIXTURE_HEAD" ;;
  *) exit 91 ;;
esac
''',
                    'proxy':'''#!/bin/sh
set -eu
[ "$*" = 'with-proxy --box fixture -- git ls-remote origin refs/heads/main' ] || exit 92
printf 'proxy\\n' >> "$FIXTURE_LOG"
[ "$FIXTURE_ALLOW" = 1 ] || exit 1
export FIXTURE_PROXIED=1
shift 4
exec "$@"
'''}
                for name,script in scripts.items():
                    p=binary/name;p.write_text(script);p.chmod(0o700)
                log=root/'transport.log'
                def controller(config,box,script,args,**kwargs):
                    script=script.replace('/home/smith/src/klokast/klokast-box/ansible/bin/platform-vpn-egress',shlex.quote(str(binary/'proxy')))
                    return subprocess.run(['sh','-s','--',*args],input=script,text=True,capture_output=True,
                        timeout=kwargs['timeout'],env=dict(os.environ,PATH=str(binary)+os.pathsep+os.environ['PATH'],
                            FIXTURE_ORIGIN=origin,FIXTURE_HEAD='a'*40,FIXTURE_ALLOW=allowed,
                            FIXTURE_DIRECT=direct,FIXTURE_LOG=str(log)))
                with patch.object(ha,'controller_sh',side_effect=controller):
                    value=ha.repository_status({},'fixture',str(root))
                self.assertEqual(value['up_to_date'],reachable)
                self.assertEqual(value['remote_reachable'],reachable)
                self.assertEqual(log.exists(),proxied)
                if proxied:self.assertEqual(log.read_text(),'proxy\n')


if __name__=='__main__':unittest.main()
