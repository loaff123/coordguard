import copy
import hashlib
from pathlib import Path
import tempfile
import unittest
from coordguard import Contract,Label
try:
    import numpy as np
    import xarray as xr
    OPTIONAL=xr.__version__=='2025.10.1'
except ImportError:OPTIONAL=False

@unittest.skipUnless(OPTIONAL,'requires pinned netcdf extra')
class ReportTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.a=str(Path(self.tmp.name)/'a.nc');self.b=str(Path(self.tmp.name)/'b.nc')
    def fixtures(self,attrs=None,rightattrs=None,aux=False):
        for path,values,meta in [(self.a,[0.,1.,2.],attrs or {}),(self.b,[0.,1.000000001,2.],rightattrs or {})]:
            ds=xr.Dataset({'v':('x',np.arange(3.))},coords={'x':('x',values,meta)})
            if aux:ds=ds.assign_coords(height=('x',[1.,2.,3.]))
            ds.to_netcdf(path,engine='h5netcdf')
    def test_report_determinism_replay_and_tampering(self):
        from coordguard.report import audit_netcdf,verify_report
        self.fixtures(aux=True)
        before=[hashlib.sha256(Path(p).read_bytes()).hexdigest() for p in [self.a,self.b]]
        r=audit_netcdf(self.a,self.b,'v','v',contract=Contract(tolerances=(('x',Label.from_value(1e-8)),)))
        self.assertEqual(r,audit_netcdf(self.a,self.b,'v','v',contract=Contract(tolerances=(('x',Label.from_value(1e-8)),))))
        self.assertEqual(r['audit']['output']['total_positions'],2)
        self.assertTrue(verify_report(r,self.a,self.b)['valid'])
        self.assertFalse(verify_report(dict(r,extra=True),self.a,self.b)['valid'])
        for mutate in [lambda x:x['audit']['output'].__setitem__('total_positions',99),
                       lambda x:x['left']['snapshot']['axes'][0]['labels'][0].__setitem__('value','0000000000000001'),
                       lambda x:x['audit']['claims'].__setitem__('physical_equivalence',True),
                       lambda x:x['auxiliary_facts'][0].__setitem__('status','dropped_conflict')]:
            bad=copy.deepcopy(r);mutate(bad);self.assertFalse(verify_report(bad,self.a,self.b)['valid'])
        self.assertEqual(before,[hashlib.sha256(Path(p).read_bytes()).hexdigest() for p in [self.a,self.b]])
    def test_different_origins_same_dates(self):
        from coordguard.report import audit_netcdf
        for path,values,units in [(self.a,[0,1],'days since 2000-01-01'),(self.b,[1,2],'days since 1999-12-31')]:
            xr.Dataset({'v':('time',[2,3])},coords={'time':('time',values,{'units':units,'calendar':'standard'})}).to_netcdf(path,engine='h5netcdf')
        r=audit_netcdf(self.a,self.b,'v','v',join='exact')
        self.assertEqual(r['audit']['contract_status'],'pass')
    def test_units_declaration_does_not_override_mismatch(self):
        from coordguard.report import audit_netcdf
        self.fixtures(attrs={'units':'m'},rightattrs={'units':'degrees'})
        r=audit_netcdf(self.a,self.b,'v','v',contract=Contract(tolerances=(('x',Label.from_value(1e-8)),),same_axis_declarations=('x',)))
        self.assertEqual(r['audit']['contract_status'],'unverified')
    def test_selected_field_and_auxiliary_metadata_conflicts_block_contract(self):
        from coordguard.report import audit_netcdf
        for path,units in [(self.a,'m'),(self.b,'degrees')]:
            ds=xr.Dataset({'v':('x',[1,2],{'units':units})},coords={'x':[0,1],'height':('x',[5,6],{'units':units})})
            ds.to_netcdf(path,engine='h5netcdf')
        r=audit_netcdf(self.a,self.b,'v','v')
        self.assertEqual(r['audit']['contract_status'],'unverified')
        self.assertTrue(any(x['code']=='selected_units_mismatch' for x in r['audit']['semantic_blockers']))
        self.assertTrue(any(x['code']=='auxiliary_units_mismatch' for x in r['audit']['semantic_blockers']))

    def test_verifier_charges_retained_parser_memory(self):
        from coordguard.report import audit_netcdf,verify_report
        from coordguard.budget import Budget,Limits
        self.fixtures();r=audit_netcdf(self.a,self.b,'v','v')
        reader=Budget();reader.reserve(reader.limits.max_working_bytes-reader.working_bytes-1024)
        verdict=verify_report(r,self.a,self.b,budget=reader)
        self.assertFalse(verdict['valid'])
        self.assertEqual(verdict['status'],'resource_limited')

    def test_changed_source_detected(self):
        from coordguard.report import audit_netcdf,verify_report
        self.fixtures();r=audit_netcdf(self.a,self.b,'v','v')
        xr.Dataset({'v':('x',[1,2,3])},coords={'x':[7,8,9]}).to_netcdf(self.b,engine='h5netcdf')
        self.assertFalse(verify_report(r,self.a,self.b)['valid'])
    def test_payload_changes_do_not_claim_integrity(self):
        import h5netcdf
        from coordguard.report import audit_netcdf,verify_report
        self.fixtures();r=audit_netcdf(self.a,self.b,'v','v')
        with h5netcdf.File(self.b,'a') as f:f.variables['v'][:]=[99,88,77]
        self.assertTrue(verify_report(r,self.a,self.b)['valid'])
