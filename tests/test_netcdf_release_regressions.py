"""Malformed native metadata and exposed-name admission regressions."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from coordguard import Limits
from coordguard.budget import Budget
from coordguard.errors import ResourceLimit, ValidationError
try:
    import h5py
    import numpy as np
    import xarray as xr
    OPTIONAL = xr.__version__ == '2025.10.1'
except ImportError:
    OPTIONAL = False


@unittest.skipUnless(OPTIONAL, 'requires pinned NetCDF extra')
class NativeMetadataRegressions(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'source.nc'
        xr.Dataset({'v': ('x', [1,2])}, coords={'x': [0,1]}).to_netcdf(self.path, engine='h5netcdf')

    def test_dimension_list_requires_exact_vlen_reference_schema(self):
        from coordguard.netcdf import extract_netcdf
        for kind in ('integer','fixed_reference','null_reference','not_a_scale'):
            with self.subTest(kind=kind):
                with h5py.File(self.path,'a') as source:
                    del source['v'].attrs['DIMENSION_LIST']
                    if kind == 'integer':source['v'].attrs['DIMENSION_LIST'] = np.array([1],dtype='i8')
                    elif kind == 'fixed_reference':source['v'].attrs.create('DIMENSION_LIST',np.array([source['x'].ref]),dtype=h5py.ref_dtype)
                    else:
                        if kind == 'not_a_scale':source.create_dataset('w',data=1)
                        ref=source['w'].ref if kind == 'not_a_scale' else h5py.Reference()
                        refs=np.empty(1,dtype=object);refs[0]=np.array([ref],dtype=h5py.ref_dtype)
                        source['v'].attrs.create('DIMENSION_LIST',refs,dtype=h5py.vlen_dtype(h5py.ref_dtype))
                with patch.object(xr,'open_dataset',side_effect=AssertionError('malformed metadata reached xarray')):
                    with self.assertRaises(ValidationError):extract_netcdf(str(self.path),'v')

    def test_fixed_width_metadata_text_has_the_same_element_cap(self):
        from coordguard.netcdf import extract_netcdf
        with h5py.File(self.path,'a') as source:source.attrs['ignored'] = np.bytes_(b'q'*5000)
        with patch.object(xr,'open_dataset',side_effect=AssertionError('oversized fixed text reached xarray')):
            with self.assertRaises(ResourceLimit):extract_netcdf(str(self.path),'v')

    def test_root_name_cap_precedes_derived_object_inspection(self):
        from coordguard.netcdf import extract_netcdf
        long_name='a'*(4*1024*1024)
        with h5py.File(self.path,'a') as source:source.create_dataset(long_name,data=1)
        with patch.object(xr,'open_dataset',side_effect=AssertionError('oversized root name reached xarray')):
            with self.assertRaises(ResourceLimit):extract_netcdf(str(self.path),'v',limits=Limits(max_working_bytes=2*1024*1024))

    def test_root_names_are_charged_to_aggregate_metadata_and_working_caps(self):
        from coordguard.netcdf import extract_netcdf
        with h5py.File(self.path,'a') as source:
            for i in range(260):source.create_dataset(str(i)+'q'*4090,data=1)
        with self.assertRaises(ResourceLimit):extract_netcdf(str(self.path),'v')

    def test_attribute_name_cap_precedes_attribute_value_access(self):
        from coordguard.netcdf import extract_netcdf
        with h5py.File(self.path,'a') as source:source.attrs['a'*5000]=1
        with patch.object(xr,'open_dataset',side_effect=AssertionError('oversized attribute name reached xarray')):
            with self.assertRaises(ResourceLimit):extract_netcdf(str(self.path),'v')

    def test_scalar_dimension_scale_is_rejected_before_xarray(self):
        from coordguard.netcdf import extract_netcdf
        with h5py.File(self.path,'w') as source:
            scale=source.create_dataset('x',data=0);scale.make_scale('x')
            variable=source.create_dataset('v',data=[1,2]);variable.dims[0].attach_scale(scale)
        with patch.object(xr,'open_dataset',side_effect=AssertionError('scalar scale reached xarray')):
            with self.assertRaises(ValidationError):extract_netcdf(str(self.path),'v')

    def test_raw_bytes_and_unicode_never_share_label_identity(self):
        import h5netcdf
        from coordguard.report import audit_netcdf,verify_report
        right=Path(self.tmp.name)/'right.nc'
        with h5netcdf.File(self.path,'w') as source:
            source.dimensions={'x':2}
            source.create_variable('x',('x',),dtype='S3')[:]=[b'01',b'02']
            source.create_variable('v',('x',),dtype='i8')[:]=[1,2]
        xr.Dataset({'v':('x',[1,2])},coords={'x':['01','02']}).to_netcdf(right,engine='h5netcdf',encoding={'x':{'dtype':'S1'}})
        for join in ('inner','outer','left','right','exact'):
            report=audit_netcdf(str(self.path),str(right),'v','v',join=join)
            self.assertEqual(report['audit']['forecast_status'],'unsupported')
            self.assertIn('unsupported_byte_string_coordinate:x',report['left']['snapshot']['semantic_blockers'])
            self.assertEqual(verify_report(report,str(self.path),str(right))['audit_exit_code'],3)

    def test_empty_integer_dtype_is_not_erased_during_promotion(self):
        from coordguard.report import audit_netcdf,verify_report
        from coordguard import Contract
        right=Path(self.tmp.name)/'right.nc'
        for dtype,expected in [('int8','float32'),('int16','float32'),('int32','float64'),('uint32','float64'),('int64','float64')]:
            with self.subTest(dtype=dtype):
                a=xr.DataArray([],dims='x',coords={'x':np.array([],dtype=dtype)},name='v')
                b=xr.DataArray([1.],dims='x',coords={'x':np.array([1.],dtype='float32')},name='v')
                a.to_netcdf(self.path,engine='h5netcdf');b.to_netcdf(right,engine='h5netcdf')
                with xr.set_options(arithmetic_join='outer'):actual=a+b
                self.assertEqual(str(actual.x.dtype),expected)
                report=audit_netcdf(str(self.path),str(right),'v','v',join='outer',contract=Contract(allow_fills=True))
                self.assertEqual(report['audit']['axes'][0]['labels'][0]['kind'],expected)
                self.assertTrue(verify_report(report,str(self.path),str(right))['valid'])

    def test_non_utf8_native_names_return_structured_invalid_input(self):
        import json
        import subprocess
        import sys
        from coordguard.netcdf import extract_netcdf
        for kind in ('root','attribute'):
            with self.subTest(kind=kind):
                xr.Dataset({'v':('x',[1,2])},coords={'x':[0,1]}).to_netcdf(self.path,engine='h5netcdf')
                with h5py.File(self.path,'a') as source:
                    if kind=='root':source.create_dataset(b'\xff',data=1)
                    else:source.attrs[b'\xff']=1
                with patch.object(xr,'open_dataset',side_effect=AssertionError('nontext native name reached xarray')):
                    with self.assertRaises(ValidationError):extract_netcdf(str(self.path),'v')
                run=subprocess.run([sys.executable,'-m','coordguard','audit-nc',str(self.path),str(self.path),'--left-variable','v','--right-variable','v'],text=True,capture_output=True)
                self.assertEqual(run.returncode,2,run.stderr)
                self.assertEqual(json.loads(run.stdout)['error']['code'],'invalid_input')
                self.assertNotIn('Traceback',run.stderr)
