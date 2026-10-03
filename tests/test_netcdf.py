import hashlib
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from coordguard import ArraySnapshot, Label, Limits

try:
    import numpy as np
    import xarray as xr
    import h5netcdf
    import h5py
    OPTIONAL = xr.__version__ == '2025.10.1'
except ImportError:
    OPTIONAL = False

@unittest.skipUnless(OPTIONAL, 'requires pinned NetCDF extra')
class NetCDFTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'data.nc'
    def fixture(self, values=(0., 1., 2.), attrs=None, aux=None):
        ds = xr.Dataset({'v': ('x', np.zeros(len(values)))}, coords={'x': ('x', np.array(values), attrs or {})})
        for name, spec in (aux or {}).items(): ds = ds.assign_coords({name: spec})
        ds.to_netcdf(self.path, engine='h5netcdf')
    def extract(self, **kwargs):
        from coordguard.netcdf import extract_netcdf
        return extract_netcdf(str(self.path), 'v', **kwargs)
    def test_real_file_without_payload_reads_or_writes(self):
        self.fixture(aux={'height': ('x', [1,2,3]), 'run': 4})
        before = hashlib.sha256(self.path.read_bytes()).hexdigest()
        from xarray.backends.h5netcdf_ import H5NetCDFArrayWrapper
        original = H5NetCDFArrayWrapper.__getitem__
        reads=[]
        def spy(obj, key):
            reads.append(obj.variable_name)
            if obj.variable_name == 'v': raise AssertionError('scientific payload read')
            return original(obj, key)
        with patch.object(H5NetCDFArrayWrapper, '__getitem__', spy):
            result=self.extract()
        self.assertEqual(result['snapshot']['axes'][0]['size'],3)
        self.assertEqual(set(reads), {'x','height','run'})
        self.assertEqual({x['name'] for x in result['auxiliary']}, {'height','run'})
        self.assertEqual(hashlib.sha256(self.path.read_bytes()).hexdigest(),before)
        self.assertEqual(result['settings']['create_default_indexes'],False)
        self.assertEqual(result['settings']['decode_cf'],False)
    def test_caps_precede_any_coordinate_materialization(self):
        self.fixture()
        from xarray.backends.h5netcdf_ import H5NetCDFArrayWrapper
        with patch.object(H5NetCDFArrayWrapper,'__getitem__',side_effect=AssertionError('read before cap')):
            from coordguard.errors import ResourceLimit
            with self.assertRaises(ResourceLimit): self.extract(limits=Limits(max_labels=2))
            with self.assertRaises(ResourceLimit): self.extract(limits=Limits(max_coordinate_bytes=8))
            with self.assertRaises(ResourceLimit): self.extract(limits=Limits(max_working_bytes=1048577))
    def test_unbounded_vlen_rejected_before_read(self):
        self.fixture(values=('a','b'))
        from xarray.backends.h5netcdf_ import H5NetCDFArrayWrapper
        with patch.object(H5NetCDFArrayWrapper,'__getitem__',side_effect=AssertionError('vlen materialized')):
            from coordguard.netcdf import UnsupportedInput
            with self.assertRaisesRegex(UnsupportedInput,'unbounded_coordinate_dtype'):self.extract()
    def test_standard_time_decodes_exactly_retains_origin(self):
        self.fixture(values=(0,1),attrs={'units':'days since 2000-01-01','calendar':'standard'})
        result=self.extract(); axis=ArraySnapshot.from_dict(result['snapshot']).axes[0]
        self.assertEqual([x.kind for x in axis.labels],['datetime','datetime'])
        self.assertEqual(axis.labels[0].value,'s:946684800')
        self.assertEqual(axis.metadata.units,'days since 2000-01-01')
        self.assertEqual(result['coordinates'][0]['encoding']['units'],'days since 2000-01-01')
    def test_datetime_resolutions_far_range_and_nat(self):
        cases=[('milliseconds',1,'ms:1'),('microseconds',1,'us:1'),('nanoseconds',1,'ns:1'),('seconds',2**40,'s:'+str(2**40))]
        for units,tick,expected in cases:
            with self.subTest(units=units):
                self.fixture(values=(0,tick),attrs={'units':units+' since 1970-01-01','calendar':'standard'})
                r=self.extract();self.assertEqual(r['snapshot']['axes'][0]['labels'][1]['value'],expected)
        self.fixture(values=(0,float('nan')),attrs={'units':'seconds since 1970-01-01','calendar':'standard'})
        self.assertTrue(self.extract()['snapshot']['semantic_blockers'])
        self.fixture(values=(0,1),attrs={'units':'days since 1500-01-01','calendar':'standard'})
        self.assertTrue(self.extract()['snapshot']['semantic_blockers'])

    def test_nonstandard_calendar_and_multidimensional_aux_are_blockers(self):
        self.fixture(values=(0,1),attrs={'units':'days since 2000-01-01','calendar':'360_day'})
        result=self.extract()
        self.assertTrue(any('unsupported_calendar' in x for x in result['snapshot']['semantic_blockers']))
        ds=xr.Dataset({'v': (('x','y'),np.zeros((2,2)))},coords={'lat':(('x','y'),np.zeros((2,2)))})
        ds.to_netcdf(self.path,engine='h5netcdf')
        self.assertTrue(any('multidimensional_coordinate' in x for x in self.extract()['snapshot']['semantic_blockers']))
    def test_missing_coordinate_reference_not_silently_ignored(self):
        self.fixture()
        with h5netcdf.File(self.path,'a') as f: f.variables['v'].attrs['coordinates']='missing'
        result=self.extract()
        self.assertIn('missing_coordinate_reference:missing',result['snapshot']['semantic_blockers'])
    def test_fixed_width_string_coordinates_preserve_numeric_text(self):
        ds=xr.Dataset({'v':('x',[1,2])},coords={'x':('x',['01','02'])})
        ds.to_netcdf(self.path,engine='h5netcdf',encoding={'x':{'dtype':'S1'}})
        r=self.extract();a=ArraySnapshot.from_dict(r['snapshot']).axes[0]
        self.assertEqual(a.labels,(Label.from_value('01'),Label.from_value('02')))
        self.assertEqual(r['snapshot']['semantic_blockers'],[])

    def test_regular_files_only_and_selected_variable_required(self):
        from coordguard.errors import ValidationError
        self.fixture()
        from coordguard.netcdf import extract_netcdf
        for path in ['https://example.com/a.nc',str(Path(self.tmp.name)),str(Path(self.tmp.name)/'missing')]:
            with self.assertRaises(ValidationError):extract_netcdf(path,'v')
        link=Path(self.tmp.name)/'link.nc';link.symlink_to(self.path)
        with self.assertRaises(ValidationError):extract_netcdf(str(link),'v')
        with self.assertRaises(ValidationError):extract_netcdf(str(self.path),'missing')
    def test_coordinate_fill_value_blocks_and_external_link_rejected(self):
        self.fixture(values=(0.,float('nan')))
        result=self.extract()
        self.assertTrue(any('invalid_coordinate_label' in x for x in result['snapshot']['semantic_blockers']))
        with h5py.File(self.path,'a') as f:f['external']=h5py.ExternalLink('other.nc','v')
        from coordguard.errors import ValidationError
        with self.assertRaises(ValidationError): self.extract()

if __name__=='__main__':unittest.main()
