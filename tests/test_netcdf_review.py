"""Independent adversarial NetCDF admission regressions."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from coordguard.errors import ValidationError, ResourceLimit
from coordguard.budget import Limits
try:
    import h5py
    import numpy as np
    import xarray as xr
    OPTIONAL = xr.__version__ == '2025.10.1'
except ImportError:
    OPTIONAL = False


@unittest.skipUnless(OPTIONAL, 'requires pinned NetCDF extra')
class NetCDFReviewTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'data.nc'
        xr.Dataset({'v': ('x', [111, 222, 333])}, coords={'x': [0, 1, 2]}).to_netcdf(self.path, engine='h5netcdf')

    def test_oversized_vlen_attribute_rejected_before_python_materialization(self):
        with h5py.File(self.path, 'a') as f:
            f.attrs['ignored_blob'] = 'x' * (16 * 1024 * 1024)
            self.assertLess(f.attrs.get_id('ignored_blob').get_storage_size(), 1024)
        original = h5py.AttributeManager.__getitem__
        def spy(attrs, key):
            if key == 'ignored_blob':
                raise AssertionError('Unbounded vlen attribute was materialized')
            return original(attrs, key)
        from coordguard.netcdf import extract_netcdf
        with patch.object(h5py.AttributeManager, '__getitem__', spy):
            with self.assertRaises((ValidationError, ResourceLimit)):
                extract_netcdf(str(self.path), 'v', limits=Limits(max_working_bytes=2 * 1024 * 1024))

    def test_selected_payload_alias_is_rejected_before_native_read(self):
        with h5py.File(self.path, 'a') as f:
            f['aux'] = f['v']
            f['v'].attrs['coordinates'] = 'aux'
            selected_address = h5py.h5o.get_info(f['v'].id).addr
        original = h5py.Dataset.__getitem__
        def spy(ds, key, *args, **kwargs):
            if h5py.h5o.get_info(ds.id).addr == selected_address:
                raise AssertionError('Selected scientific payload was read through an alias')
            return original(ds, key, *args, **kwargs)
        from coordguard.netcdf import extract_netcdf
        with patch.object(h5py.Dataset, '__getitem__', spy):
            with self.assertRaises((ValidationError, ResourceLimit)):
                extract_netcdf(str(self.path), 'v')

    def test_coordinate_chunk_allocation_admitted_before_payload_access(self):
        import h5netcdf
        # A tiny logical shape can require a much larger filter buffer.
        # Constructing 8 MiB here keeps the test fixture inexpensive.
        self.path.unlink()
        with h5netcdf.File(self.path, 'w') as f:
            f.dimensions = {'x': None}
            f.resize_dimension('x', 1)
            x = f.create_variable('x', ('x',), dtype='i8', chunks=(1024 * 1024,), compression='gzip')
            x[:] = [0]
            f.create_variable('v', ('x',), dtype='i8')[:] = [1]
        from xarray.backends.h5netcdf_ import H5NetCDFArrayWrapper
        from coordguard.netcdf import extract_netcdf
        with patch.object(H5NetCDFArrayWrapper, '__getitem__', side_effect=AssertionError('Chunk decompressed before cap')):
            with self.assertRaises(ResourceLimit):
                extract_netcdf(str(self.path), 'v', limits=Limits(max_working_bytes=2 * 1024 * 1024))

    def test_plain_hdf5_cannot_invent_dimension_names(self):
        self.path.unlink()
        with h5py.File(self.path, 'w') as f:
            f.create_dataset('v', data=[1, 2])
            f.create_dataset('x', data=[0, 1])
        from coordguard.netcdf import extract_netcdf
        with self.assertRaises(ValidationError):
            extract_netcdf(str(self.path), 'v')

    def test_full_report_replay_is_type_strict(self):
        import copy
        from coordguard.report import audit_netcdf, verify_report
        xr.Dataset({'v': ('x', [5])}, coords={'x': [1]}).to_netcdf(self.path, engine='h5netcdf')
        path = str(self.path)
        report = audit_netcdf(path, path, 'v', 'v')
        changes = (
            lambda r: r['left']['snapshot']['axes'][0].__setitem__('size', True),
            lambda r: r.__setitem__('schema_version', 2.0),
            lambda r: r['claims'].__setitem__('payload_inspected', 0),
        )
        for mutate in changes:
            bad = copy.deepcopy(report)
            mutate(bad)
            self.assertFalse(verify_report(bad, path, path)['valid'])

    def test_null_hdf5_dataspaces_return_structured_input_error(self):
        import contextlib
        import io
        import json
        from coordguard.cli import main
        for case in ('attribute', 'dataset'):
            with self.subTest(case=case):
                xr.Dataset({'v': ('x', [1, 2])}, coords={'x': [0, 1]}).to_netcdf(self.path, engine='h5netcdf')
                with h5py.File(self.path, 'a') as f:
                    if case == 'attribute':
                        f.attrs['null_metadata'] = h5py.Empty('f')
                    else:
                        f.create_dataset('null_variable', data=h5py.Empty('f'))
                output = io.StringIO()
                with contextlib.redirect_stdout(output):
                    status = main(['audit-nc', str(self.path), str(self.path), '--left-variable', 'v', '--right-variable', 'v'])
                self.assertEqual(status, 2)
                self.assertEqual(json.loads(output.getvalue())['error']['code'], 'invalid_input')

    def test_verifier_memory_ceiling_can_cover_replay_overhead(self):
        from coordguard.report import audit_netcdf, verify_report
        xr.Dataset({'v': ('x', np.zeros(1000))}, coords={'x': np.arange(1000)}).to_netcdf(self.path, engine='h5netcdf')
        path = str(self.path)
        report = audit_netcdf(path, path, 'v', 'v', limits=Limits(max_working_bytes=16 * 1024 * 1024))
        self.assertEqual(report['audit']['forecast_status'], 'complete')
        checked = verify_report(report, path, path, limits=Limits(max_working_bytes=64 * 1024 * 1024))
        self.assertTrue(checked['valid'], checked)
