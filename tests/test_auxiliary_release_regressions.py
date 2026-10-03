"""Source-round-tripped regressions against actual pinned xarray arithmetic."""
from pathlib import Path
import struct
import tempfile
import unittest

from coordguard import Contract
from coordguard.report import audit_netcdf, verify_report

try:
    import h5netcdf
    import numpy as np
    import xarray as xr
    OPTIONAL = xr.__version__ == '2025.10.1'
except ImportError:
    OPTIONAL = False

JOINS = ('inner', 'outer', 'left', 'right', 'exact')
PERMISSIVE = Contract(allow_drops=True, allow_fills=True, allow_broadcast=True)


def _labels(coordinate):
    """Observe result storage directly, independently of production Label code."""
    values = coordinate.values.ravel()
    if coordinate.dtype.kind in 'iu':
        return [{'kind': 'int', 'value': str(int(value))} for value in values]
    if coordinate.dtype.kind == 'M':
        unit = np.datetime_data(coordinate.dtype)[0]
        return [{'kind': 'datetime', 'value': unit + ':' + str(int(value))}
                for value in coordinate.values.astype('int64').ravel()]
    if coordinate.dtype.kind == 'f':
        kind, packing = ('float32', '>f') if coordinate.dtype.itemsize == 4 else ('float64', '>d')
        return [None if np.isnan(value) else {'kind': kind, 'value': struct.pack(packing, float(value)).hex()}
                for value in values]
    raise AssertionError('Unexpected fixture dtype: ' + str(coordinate.dtype))


def _array(index, auxiliary, *, dimension='x'):
    return xr.DataArray(np.zeros(len(index)), dims=dimension,
                        coords={dimension: np.asarray(index, dtype='int64'), **auxiliary}, name='v')


@unittest.skipUnless(OPTIONAL, 'requires pinned NetCDF extra')
class AuxiliaryReleaseRegressionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.paths = [str(Path(self.tmp.name) / (side + '.nc')) for side in ('left', 'right')]

    def compare(self, left, right, *, name='q', joins=JOINS, unsupported=None, replay=False, expected_contract='pass'):
        for source, path in zip((left, right), self.paths):
            encoding = {coord_name: {'dtype': 'S1'} for coord_name, coord in source.coords.items()
                        if coord.dtype.kind == 'U'}
            source.to_netcdf(path, engine='h5netcdf', encoding=encoding)
        # Use exactly the files audited, including the storage dtype after reopening.
        with xr.open_dataset(self.paths[0], engine='h5netcdf', decode_times=xr.coders.CFDatetimeCoder(use_cftime=False, time_unit='s')) as left_file, \
             xr.open_dataset(self.paths[1], engine='h5netcdf', decode_times=xr.coders.CFDatetimeCoder(use_cftime=False, time_unit='s')) as right_file:
            left, right = left_file.v.load(), right_file.v.load()
            for join in joins:
                with self.subTest(join=join):
                    try:
                        with xr.set_options(arithmetic_join=join):
                            result = left + right
                    except ValueError:
                        result = None
                    envelope = audit_netcdf(*self.paths, 'v', 'v', join=join, contract=PERMISSIVE)
                    report = envelope['audit']
                    if result is None:
                        self.assertEqual(report['forecast_status'], 'operation_error')
                        continue
                    fact, = [fact for fact in envelope['auxiliary_facts'] if fact['name'] == name]
                    if unsupported:
                        self.assertEqual(fact, {'name': name, 'status': 'unsupported', 'reason': unsupported})
                        self.assertEqual(report['forecast_status'], 'unsupported')
                        self.assertEqual(report['contract_status'], 'unverified')
                        self.assertIsNone(report['output'])
                        self.assertIsNone(report['impact'])
                        self.assertIn({'code': 'auxiliary_' + unsupported, 'coordinate': name}, report['semantic_blockers'])
                        self.assertNotIn({'code': 'multidimensional_auxiliary_result'}, report['semantic_blockers'])
                        self.assertFalse(any(item.get('code') == 'conflicting_auxiliary_dropped'
                                             for item in report['diagnostics']))
                    else:
                        self.assertEqual(report['forecast_status'], 'complete', report['diagnostics'])
                        self.assertEqual(report['contract_status'], expected_contract, report['semantic_blockers'])
                        if name in result.indexes:
                            self.assertEqual(fact, {'name': name, 'status': 'shadowed_by_dimension_index'})
                        elif name not in result.coords:
                            self.assertEqual(fact['status'], 'dropped_conflict')
                        else:
                            coordinate = result.coords[name]
                            self.assertEqual(fact['status'], 'preserved')
                            self.assertEqual(fact['dimensions'], list(coordinate.dims))
                            self.assertEqual(fact['source_dtype'], str(coordinate.dtype))
                            self.assertEqual(fact['labels'], _labels(coordinate))
                    if replay:
                        verdict = verify_report(envelope, *self.paths)
                        self.assertTrue(verdict['valid'], verdict)

    def test_scalar_vector_merge_retains_left_numeric_storage(self):
        # Choosing the vector's storage instead of broadcasting the left operand
        # changes both the declared dtype and the exact encoded result labels.
        for scalar_dtype, vector_dtype in (('float64', 'int64'), ('float32', 'float64'),
                                           ('int16', 'int64'), ('uint8', 'uint32')):
            for reverse in (False, True):
                with self.subTest(scalar=scalar_dtype, vector=vector_dtype, reverse=reverse):
                    arrays = [_array([0, 1], {'q': np.asarray(10, dtype=scalar_dtype)}),
                              _array([0, 1], {'q': ('x', np.array([10, 10], dtype=vector_dtype))})]
                    self.compare(*(arrays[::-1] if reverse else arrays))

    def test_scalar_broadcast_preserves_left_signed_zero_bits(self):
        self.compare(_array([0, 1], {'q': np.float32(-0.)}),
                     _array([0, 1], {'q': ('x', np.array([0., 0.], dtype='float64'))}), replay=True)

    def test_scalar_vector_comparison_after_reindex_fills_and_empty_outputs(self):
        for lindex, rindex in (([0, 1], [1, 2]), ([], []), ([], [0, 1]), ([0], [1])):
            for reverse in (False, True):
                with self.subTest(left_index=lindex, right_index=rindex, reverse=reverse):
                    arrays = [_array(lindex, {'q': np.float64(10)}),
                              _array(rindex, {'q': ('x', np.full(len(rindex), 10, dtype='int16'))})]
                    self.compare(*(arrays[::-1] if reverse else arrays))

    def test_one_sided_fills_preserve_actual_promoted_dtype(self):
        for dtype in ('int8', 'uint16', 'int32', 'int64', 'uint64', 'float32', 'float64'):
            for reverse in (False, True):
                with self.subTest(dtype=dtype, reverse=reverse):
                    arrays = [_array([0, 1], {'q': ('x', np.array([10, 20], dtype=dtype))}),
                              _array([1, 2], {})]
                    self.compare(*(arrays[::-1] if reverse else arrays))

    def test_dimension_name_shadows_auxiliary_only_when_output_is_indexed(self):
        for indexed in (False, True):
            for scalar in (False, True):
                for xsize in (0, 3):
                    for ysize in (0, 2):
                        for reverse in (False, True):
                            with self.subTest(indexed=indexed, scalar=scalar, xsize=xsize,
                                              ysize=ysize, reverse=reverse):
                                aux = np.int64(8) if scalar else ('y', np.arange(ysize, dtype='int16'))
                                left = _array(list(range(ysize)), {'x': aux}, dimension='y')
                                right = xr.DataArray(np.zeros(xsize), dims='x',
                                                     coords={'x': np.arange(xsize)} if indexed else {}, name='v')
                                arrays = (right, left) if reverse else (left, right)
                                self.compare(*arrays, name='x', joins=('inner', 'outer'), replay=xsize == 3 and ysize == 2)

    def test_unsafe_mixed_numeric_auxiliary_equality_is_unsupported(self):
        for scalar in (False, True):
            for reverse in (False, True):
                with self.subTest(scalar=scalar, reverse=reverse):
                    integer = np.int64(2**53 + 1)
                    aux = integer if scalar else ('x', np.array([integer, integer], dtype='int64'))
                    arrays = [_array([0, 1], {'q': aux}),
                              _array([0, 1], {'q': ('x', np.array([2**53, 2**53], dtype='float64'))})]
                    self.compare(*(arrays[::-1] if reverse else arrays), joins=('inner',),
                                 unsupported='unsafe_mixed_integer_float', replay=True)

    def test_unsafe_mixed_numeric_guard_applies_after_alignment(self):
        # Only the shared index is compared: the unsafe value outside it is gone.
        self.compare(_array([0, 1], {'q': ('x', np.array([2**53 + 1, 10], dtype='int64'))}),
                     _array([1, 2], {'q': ('x', np.array([10., 11.], dtype='float64'))}), joins=('inner',))
        # Empty broadcast comparison has no unsafe equality decision to make.
        self.compare(_array([0], {'q': np.int64(2**53 + 1)}),
                     _array([1], {'q': ('x', np.array([0.], dtype='float64'))}), joins=('inner',))
        # The shared label still contains the lossy comparison in both orders.
        arrays = [_array([0, 1], {'q': np.int64(2**53 + 1)}),
                  _array([1, 2], {'q': ('x', np.array([2**53, 2**53 + 2], dtype='float64'))})]
        for reverse in (False, True):
            self.compare(*(arrays[::-1] if reverse else arrays), joins=('inner',),
                         unsupported='unsafe_mixed_integer_float', replay=True)

    def test_mixed_signed_unsigned_auxiliary_comparison_is_conservatively_unsupported(self):
        for reverse in (False, True):
            arrays = [_array([0, 1], {'q': np.int64(10)}),
                      _array([0, 1], {'q': ('x', np.array([10, 10], dtype='uint64'))})]
            self.compare(*(arrays[::-1] if reverse else arrays), joins=('inner',),
                         unsupported='unsafe_integer_dtype_promotion', replay=True)

    def test_binary64_representable_large_integer_is_not_rejected(self):
        for reverse in (False, True):
            arrays = [_array([0, 1], {'q': np.int64(2**60)}),
                      _array([0, 1], {'q': ('x', np.array([2**60, 2**60], dtype='float64'))})]
            self.compare(*(arrays[::-1] if reverse else arrays), joins=('inner',))

    def test_unsafe_auxiliary_integer_fill_conversion_is_unsupported(self):
        for dtype, value in (('int64', 2**53 + 1), ('uint64', 2**63 + 1)):
            for reverse in (False, True):
                arrays = [_array([0, 1], {'q': ('x', np.array([value, value], dtype=dtype))}),
                          _array([1, 2], {})]
                self.compare(*(arrays[::-1] if reverse else arrays), joins=('outer',),
                             unsupported='unsafe_integer_fill_promotion', replay=True)
        # An exact large binary64 value remains safe even when fill changes dtype.
        self.compare(_array([0, 1], {'q': ('x', np.array([2**60, 2**60], dtype='int64'))}),
                     _array([1, 2], {}), joins=('outer',))

    def test_decoded_auxiliary_time_origins_and_default_calendars_are_equivalent(self):
        for scalar in (False, True):
            for reverse in (False, True):
                left_values = np.int64(0) if scalar else np.array([0, 1], dtype='int64')
                right_values = np.int64(1) if scalar else np.array([1, 2], dtype='int64')
                dims = () if scalar else ('x',)
                arrays = [_array([0, 1], {'q': (dims, left_values, {'units': 'days since 2000-01-01'})}),
                          _array([0, 1], {'q': (dims, right_values, {'units': 'days since 1999-12-31', 'calendar': 'standard'})})]
                self.compare(*(arrays[::-1] if reverse else arrays), joins=('exact',), replay=True)

    def test_decoded_auxiliary_calendar_mismatch_remains_unverified(self):
        left = _array([0, 1], {'q': ('x', [0, 1], {'units': 'days since 2000-01-01'})})
        right = _array([0, 1], {'q': ('x', [0, 1], {'units': 'days since 2000-01-01', 'calendar': 'proleptic_gregorian'})})
        self.compare(left, right, joins=('exact',), expected_contract='unverified', replay=True)

    def test_mixed_auxiliary_datetime_resolutions_are_unsupported(self):
        for size in (0, 2):
            for reverse in (False, True):
                arrays = [_array(list(range(size)), {'q': ('x', np.arange(size), {'units': 'seconds since 2000-01-01'})}),
                          _array(list(range(size)), {'q': ('x', np.arange(size) * 1000, {'units': 'milliseconds since 2000-01-01'})})]
                self.compare(*(arrays[::-1] if reverse else arrays), joins=('exact',),
                             unsupported='mixed_datetime_resolutions', replay=True)

    def test_incompatible_auxiliary_families_are_unsupported_including_empty(self):
        for size in (0, 2):
            auxiliaries = {
                'numeric': ('x', np.arange(size, dtype='float64')),
                'datetime': ('x', np.arange(size), {'units': 'seconds since 2000-01-01'}),
                'text': ('x', np.array(['0'] * size, dtype='U1')),
            }
            for first, second in (('numeric', 'datetime'), ('numeric', 'text'), ('datetime', 'text')):
                for reverse in (False, True):
                    with self.subTest(size=size, first=first, second=second, reverse=reverse):
                        arrays = [_array(list(range(size)), {'q': auxiliaries[first]}),
                                  _array(list(range(size)), {'q': auxiliaries[second]})]
                        self.compare(*(arrays[::-1] if reverse else arrays), joins=('exact',),
                                     unsupported='incompatible_label_families', replay=True)
