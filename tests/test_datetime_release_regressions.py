"""Adversarial datetime profile checks against real pinned xarray behavior."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from coordguard import Contract
from coordguard.budget import Budget
try:
    import xarray as xr
    OPTIONAL = xr.__version__ == '2025.10.1'
except ImportError:
    OPTIONAL = False

@unittest.skipUnless(OPTIONAL, 'requires pinned NetCDF extra')
class DatetimeReleaseRegressions(unittest.TestCase):
    def fixture_pair(self,root,left_values,left_units,right_values,right_units,left_calendar='standard',right_calendar='standard'):
        paths=[Path(root)/(side+'.nc') for side in ('left','right')]
        for path,values,units,calendar in zip(paths,(left_values,right_values),(left_units,right_units),(left_calendar,right_calendar)):
            attrs={'units':units+' since 1970-01-01'}
            if calendar is not None:attrs['calendar']=calendar
            xr.Dataset({'v':('time',[1.]*len(values))},coords={'time':('time',values,attrs)}).to_netcdf(path,engine='h5netcdf')
        return list(map(str,paths))

    def test_mixed_datetime_resolution_never_certifies_overflow_or_promotion(self):
        from coordguard.report import audit_netcdf,verify_report
        from coordguard.model import ArraySnapshot
        from coordguard.report_checks import check_operation_facts
        for case,left,right,runit in [('overflow',[0,2**40],[0,1],'nanoseconds'),('promotion',[0,1],[1000,2001],'milliseconds')]:
            with self.subTest(case=case),tempfile.TemporaryDirectory() as root:
                paths=self.fixture_pair(root,left,'seconds',right,runit)
                coder=xr.coders.CFDatetimeCoder(use_cftime=False,time_unit='s')
                with xr.open_dataset(paths[0],engine='h5netcdf',decode_times=coder) as a,xr.open_dataset(paths[1],engine='h5netcdf',decode_times=coder) as b:
                    for join in ('inner','outer','left','right','exact'):
                        report=audit_netcdf(*paths,'v','v',join=join,contract=Contract(allow_drops=True,allow_fills=True))
                        self.assertEqual(report['audit']['forecast_status'],'unsupported')
                        self.assertEqual(report['audit']['axes'][0]['reason'],'mixed_datetime_resolutions')
                        self.assertIsNone(report['audit']['output'])
                        checked=check_operation_facts(ArraySnapshot.from_dict(report['left']['snapshot']),ArraySnapshot.from_dict(report['right']['snapshot']),report['audit'],budget=Budget())
                        self.assertTrue(checked['valid'],checked)
                        self.assertEqual(verify_report(report,*paths)['audit_exit_code'],3)
                        if case=='overflow' and join!='exact':
                            with xr.set_options(arithmetic_join=join),self.assertRaises(ValueError):a.v+b.v
                        elif case=='promotion' and join in ('inner','outer'):
                            with xr.set_options(arithmetic_join=join):actual=a.v+b.v
                            self.assertEqual(str(actual.time.dtype),'datetime64[ms]')
                proof=Path(root)/'report.json';proof.write_text(json.dumps(report))
                verified=subprocess.run([sys.executable,'-m','coordguard','verify-report',str(proof),'--left',paths[0],'--right',paths[1]],text=True,capture_output=True)
                self.assertEqual(verified.returncode,3,verified.stderr)

    def test_omitted_cf_calendar_uses_effective_standard_semantics(self):
        from coordguard.report import audit_netcdf,verify_report
        with tempfile.TemporaryDirectory() as root:
            paths=self.fixture_pair(root,[0,1],'days',[0,1],'days',None,'proleptic_gregorian')
            report=audit_netcdf(*paths,'v','v',join='exact')
            self.assertEqual(report['audit']['contract_status'],'unverified')
            self.assertIn({'code':'calendar_mismatch','dimension':'time'},report['audit']['semantic_blockers'])
            self.assertIsNone(report['left']['snapshot']['axes'][0]['metadata']['calendar'])
            self.assertEqual(verify_report(report,*paths)['audit_exit_code'],3)
            paths=self.fixture_pair(root,[0,1],'days',[0,1],'days',None,'standard')
            self.assertEqual(audit_netcdf(*paths,'v','v',join='exact')['audit']['contract_status'],'pass')

    def test_empty_datetime_index_keeps_its_family(self):
        from coordguard.report import audit_netcdf,verify_report
        import numpy as np
        with tempfile.TemporaryDirectory() as root:
            left=Path(root)/'left.nc';right=Path(root)/'right.nc'
            xr.Dataset({'v':('x',[])},coords={'x':('x',np.array([],dtype='int64'),{'units':'seconds since 1970-01-01','calendar':'standard'})}).to_netcdf(left,engine='h5netcdf')
            xr.Dataset({'v':('x',[1,2])},coords={'x':[0,1]}).to_netcdf(right,engine='h5netcdf')
            for join in ('inner','outer','left','right','exact'):
                report=audit_netcdf(str(left),str(right),'v','v',join=join,contract=Contract(allow_drops=True,allow_fills=True))
                self.assertEqual(report['audit']['forecast_status'],'unsupported')
                self.assertEqual(report['audit']['axes'][0]['reason'],'incompatible_label_families')
                self.assertEqual(verify_report(report,str(left),str(right))['audit_exit_code'],3)
