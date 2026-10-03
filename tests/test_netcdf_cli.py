import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
try:
 import xarray as xr
 import numpy as np
 OPTIONAL=xr.__version__=='2025.10.1'
except ImportError:OPTIONAL=False

@unittest.skipUnless(OPTIONAL,'requires pinned NetCDF extra')
class NetCDFCLITests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
  for name,x in [('a',[0.,1.,2.]),('b',[0.,1.000000001,2.])]:
   xr.Dataset({'v':('x',[10,20,30])},coords={'x':x}).to_netcdf(self.root/(name+'.nc'),engine='h5netcdf')
 def runcli(self,*args):
  return subprocess.run([sys.executable,'-m','coordguard',*map(str,args)],text=True,capture_output=True)
 def test_json_html_verify_contract_exit_and_immutability(self):
  a=self.root/'a.nc';b=self.root/'b.nc';original=a.read_bytes();html=self.root/'audit.html'
  r=self.runcli('audit-nc',a,b,'--left-variable','v','--right-variable','v','--html',html)
  self.assertEqual(r.returncode,1,r.stderr);report=json.loads(r.stdout)
  self.assertEqual(report['audit']['output']['total_positions'],2)
  self.assertIn('CoordGuard',html.read_text())
  p=self.root/'report.json';p.write_text(r.stdout)
  v=self.runcli('verify-report',p,'--left',a,'--right',b)
  self.assertEqual(v.returncode,1,v.stderr);self.assertTrue(json.loads(v.stdout)['valid'])
  self.assertEqual(a.read_bytes(),original)
 def test_success_and_invalid_unsupported_resource_exits(self):
  a=self.root/'a.nc';b=self.root/'b.nc'
  self.assertEqual(self.runcli('audit-nc',a,a,'--left-variable','v','--right-variable','v').returncode,0)
  self.assertEqual(self.runcli('audit-nc',a,b,'--left-variable','missing','--right-variable','v').returncode,2)
  r=self.runcli('audit-nc',a,b,'--left-variable','v','--right-variable','v','--max-labels','1')
  self.assertEqual(r.returncode,3);self.assertEqual(json.loads(r.stdout)['forecast_status'],'resource_limited')
  xr.Dataset({'v':('x',[1,2])},coords={'x':['a','b']}).to_netcdf(self.root/'strings.nc',engine='h5netcdf')
  r=self.runcli('audit-nc',self.root/'strings.nc',a,'--left-variable','v','--right-variable','v')
  self.assertEqual(r.returncode,3);self.assertEqual(json.loads(r.stdout)['forecast_status'],'unsupported')
 def test_html_cannot_overwrite_input_or_existing_file(self):
  a=self.root/'a.nc';raw=a.read_bytes()
  r=self.runcli('audit-nc',a,a,'--left-variable','v','--right-variable','v','--html',a)
  self.assertEqual(r.returncode,2);self.assertEqual(a.read_bytes(),raw)
