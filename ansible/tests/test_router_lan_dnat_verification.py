"""Router verification permits the exact compiled LAN rule and rejects other DNAT."""
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import yaml


class DNATVerification(unittest.TestCase):
    def test_exact_compiled_rule_is_the_only_exception(self):
        repo=Path(__file__).resolve().parents[2]
        tasks=yaml.safe_load((repo/'ansible/roles/router-verification/tasks/main.yml').read_text())
        task=next(t for t in tasks if t.get('name')=='Verify DNAT is limited to the exact compiled LAN print rule')
        script=task['ansible.builtin.command']['argv'][2]
        rule='iifname "eth0" ip saddr 192.168.1.0/24 fib daddr type local tcp dport 631 dnat to 192.168.100.10:18631 comment "klokast-lan-print-ipp"'
        with tempfile.TemporaryDirectory() as temporary:
            path=Path(temporary)/'lan-print-nat.nft'
            for expected,actual,ok in [('', '', True),('',rule,False),(rule,'\t'+rule+'\n',True),
                    (rule,rule+'\n'+rule,False),(rule,rule.replace('192.168.1.0/24','0.0.0.0/0'),False),
                    (rule,rule.replace(':18631',':22'),False),(rule,'',False)]:
                path.write_text(expected)
                result=subprocess.run([sys.executable,'-c',script,str(path)],input=actual,text=True,capture_output=True)
                self.assertEqual(result.returncode==0,ok,(actual,result.stderr))
