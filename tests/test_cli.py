import copy
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest
from coordguard import ArraySnapshot, Axis, Contract, Label


class CLITests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=pathlib.Path(self.temp.name)
        self.left=self.save('left.json',ArraySnapshot('v',(Axis.from_values('x',[0.,1.,2.]),)).to_dict())
        self.right=self.save('right.json',ArraySnapshot('v',(Axis.from_values('x',[0.,1.000000001,2.]),)).to_dict())
    def tearDown(self):self.temp.cleanup()
    def save(self,name,value):
        p=self.root/name;p.write_text(json.dumps(value));return str(p)
    def run_cli(self,*args):
        return subprocess.run([sys.executable,'-m','coordguard',*args],text=True,capture_output=True,timeout=10)

    def test_audit_repeatable_and_exit_contract(self):
        before=(pathlib.Path(self.left).read_bytes(),pathlib.Path(self.right).read_bytes())
        a=self.run_cli('audit',self.left,self.right);b=self.run_cli('audit',self.left,self.right)
        self.assertEqual(a.returncode,1,a.stderr);self.assertEqual(a.stdout,b.stdout)
        self.assertEqual(json.loads(a.stdout)['output']['total_positions'],2)
        c=self.run_cli('audit',self.left,self.left);self.assertEqual(c.returncode,0,c.stdout+c.stderr)
        self.assertEqual(before,(pathlib.Path(self.left).read_bytes(),pathlib.Path(self.right).read_bytes()))
        c=self.run_cli('audit',self.left,self.left,'--max-labels','1');self.assertEqual(c.returncode,3,c.stdout+c.stderr)
        c=self.run_cli('audit',self.left,self.left,'--deadline','0');self.assertEqual(c.returncode,3,c.stdout+c.stderr)

    def test_match_verify_offline_and_tamper(self):
        m=self.run_cli('match',self.left,self.right,'--axis','x','--atol','1e-8')
        self.assertEqual(m.returncode,0,m.stdout+m.stderr)
        proof=json.loads(m.stdout);p=self.save('proof.json',proof)
        v=self.run_cli('verify',p,'--left',self.left,'--right',self.right,'--axis','x')
        self.assertEqual(v.returncode,0,v.stdout+v.stderr)
        self.assertTrue(json.loads(v.stdout)['valid'])
        proof['witness']['matching']=[1,0,2];self.save('proof.json',proof)
        v=self.run_cli('verify',p,'--left',self.left,'--right',self.right,'--axis','x')
        self.assertEqual(v.returncode,1,v.stdout+v.stderr)

    def test_bad_files_fail_with_stable_errors(self):
        cases=['{"schema_version":1,"schema_version":1}', '{"x":NaN}', '['*40+']'*40, '{"x":'+('1'*2100)+'}', '{bad']
        for i,value in enumerate(cases):
            p=self.root/f'bad{i}.json';p.write_text(value)
            r=self.run_cli('audit',str(p),self.left)
            self.assertEqual(r.returncode,2,r.stdout+r.stderr)
            self.assertEqual(json.loads(r.stdout)['error']['code'],'invalid_input')
        r=self.run_cli('audit','https://example.invalid/a.json',self.left);self.assertEqual(r.returncode,2)
        r=self.run_cli('audit',str(self.root),self.left);self.assertEqual(r.returncode,2)
        link=self.root/'link.json';link.symlink_to(self.left)
        r=self.run_cli('audit',str(link),self.left);self.assertEqual(r.returncode,2)
        fifo=self.root/'fifo';os.mkfifo(fifo)
        r=self.run_cli('audit',str(fifo),self.left);self.assertEqual(r.returncode,2)
        r=self.run_cli('audit',self.left,self.right,'--max-input-bytes','10');self.assertEqual(r.returncode,3)

    def test_contract_file_and_unknown_fields(self):
        contract=Contract(allow_drops=True)
        path=self.save('contract.json',contract.to_dict())
        r=self.run_cli('audit',self.left,self.right,'--contract',path)
        self.assertEqual(r.returncode,0,r.stdout+r.stderr)
        malformed=copy.deepcopy(ArraySnapshot('v',(Axis.from_values('x',[1]),)).to_dict());malformed['payload']=[4]
        path=self.save('unknown.json',malformed)
        self.assertEqual(self.run_cli('audit',path,self.left).returncode,2)
        r=self.run_cli('match',self.left,self.right,'--axis','missing','--atol','1')
        self.assertEqual(r.returncode,2)
        r=self.run_cli('match',self.left,self.right,'--axis','x','--atol','nan')
        self.assertEqual(r.returncode,2)

if __name__=='__main__':unittest.main()
