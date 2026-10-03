"""Independent differential contract for the one pinned xarray release.

Every successful case executes tiny real binary arithmetic inside
``xr.set_options(arithmetic_join=...)``. Expected dimension labels (including
float precision), errors, auxiliary outcomes, and alignment impacts come from
xarray, not CoordGuard's join/key/indexer helpers. Marker payloads are permitted
here: the production adapter's no-payload-read property is tested separately.

The optional suite is skipped when the xarray extra is absent. A different
installed version is deliberately skipped rather than claiming a version range.
"""
from dataclasses import replace
import itertools
import math
import struct
import tempfile
import unittest
from pathlib import Path

from coordguard import ArraySnapshot, Axis, Contract, Label
from coordguard.planner import audit

try:
    import numpy as np
    import xarray as xr
except ImportError:
    np = xr = None

PINNED_XARRAY = '2025.10.1'
PROFILE = 'xarray-netcdf-2025.10.1-v1'
JOINS = ('inner', 'outer', 'left', 'right', 'exact')
HAS_PIN = xr is not None and xr.__version__ == PINNED_XARRAY
PERMISSIVE = Contract(allow_drops=True, allow_fills=True,
                      allow_broadcast=True, allow_duplicates=True)


def _typed_scalar(value, dtype):
    """Encode NumPy storage directly, without the planner's label/key helpers."""
    if dtype.kind in 'iu':
        return {'kind': 'int', 'value': str(int(value))}
    if dtype.kind == 'f':
        bits = struct.pack('>f' if dtype.itemsize == 4 else '>d', float(value))
        return {'kind': 'float32' if dtype.itemsize == 4 else 'float64',
                'value': bits.hex()}
    if dtype.kind == 'U' or (dtype.kind == 'O' and isinstance(value, str)):
        return {'kind': 'str', 'value': str(value)}
    raise AssertionError('Unexpected test fixture label dtype: ' + str(dtype))


def _typed_values(values):
    values = np.asarray(values)
    return [_typed_scalar(value, values.dtype) for value in values.ravel()]


def _auxiliary_values(values):
    # Generated alignment fills are represented by JSON null in the report;
    # do not claim an IEEE NaN payload representation for structural absence.
    values = np.asarray(values)
    return [None if values.dtype.kind == 'f' and np.isnan(value)
            else _typed_scalar(value, values.dtype) for value in values.ravel()]


def _snapshot(array):
    """Construct the input independently of the production NetCDF adapter."""
    axes = []
    for name in array.dims:
        labels = None
        if name in array.indexes:
            labels = tuple(Label(**item) for item in _typed_values(array.coords[name].values))
        axis = Axis(name, array.sizes[name], labels)
        if labels is not None:
            axis = replace(axis, source_dtype=str(array.coords[name].dtype))
        axes.append(axis)
    return ArraySnapshot(array.name or 'field', tuple(axes), decoding_profile=PROFILE)


def _array(dimensions, *, auxiliary=None, name='field'):
    """(name, labels-or-None, size) -> small uniquely marked scientific payload."""
    dims = tuple(item[0] for item in dimensions)
    shape = tuple(item[2] for item in dimensions)
    coords = {dim: np.asarray(values) for dim, values, _ in dimensions if values is not None}
    coords.update(auxiliary or {})
    values = np.arange(1, math.prod(shape) + 1, dtype=np.float64).reshape(shape)
    return xr.DataArray(values, dims=dims, coords=coords, name=name)


def _one(values, *, dtype=None, dim='x', auxiliary=None):
    values = np.asarray(values, dtype=dtype)
    return _array(((dim, values, len(values)),), auxiliary=auxiliary)


def _arithmetic(left, right, join):
    try:
        with xr.set_options(arithmetic_join=join):
            return left + right, None
    except (ValueError, IndexError) as exc:
        return None, exc


@unittest.skipUnless(HAS_PIN, 'Requires the pinned optional xarray 2025.10.1 runtime')
class XarrayDifferentialTests(unittest.TestCase):
    def compare(self, left, right, join, *, allow_unsupported=False):
        result, error = _arithmetic(left, right, join)
        report = audit(_snapshot(left), _snapshot(right), join=join, contract=PERMISSIVE)
        if allow_unsupported:
            self.assertEqual(report['forecast_status'], 'unsupported')
            self.assertEqual(report['contract_status'], 'unverified')
            self.assertIsNone(report['output'])
            return report, result
        if error is not None:
            self.assertEqual(report['forecast_status'], 'operation_error', str(error))
            self.assertIsNone(report['output'])
            self.assertIsNone(report['impact'])
            return report, None
        self.assertEqual(report['forecast_status'], 'complete', report['diagnostics'])
        self.assertEqual(report['output'], {
            'dimensions': list(result.dims),
            'sizes': list(result.shape),
            'total_positions': int(result.size),
        })
        for fact in report['axes']:
            name = fact['name']
            expected_labels = _typed_values(result.coords[name].values) if name in result.indexes else None
            self.assertEqual(fact['labels'], expected_labels, (name, join, 'typed coordinate labels'))
            # Independent real one-dimensional alignment supplies indexer evidence.
            markers = []
            for source in (left, right):
                if name not in source.dims:
                    markers.append(None)
                    continue
                coords = {name: source.coords[name]} if name in source.indexes else {}
                markers.append(xr.DataArray(np.arange(source.sizes[name]), dims=name, coords=coords))
            if all(marker is not None for marker in markers):
                aligned_markers = xr.align(*markers, join=join, copy=False)
            else:
                aligned_markers = markers
            for side, source, marker in zip(('left', 'right'), (left, right), aligned_markers):
                indexer = fact[side + '_indexer']
                if marker is None:
                    self.assertEqual(indexer, {'kind': 'broadcast'})
                    continue
                observed = [None if np.isnan(x) else int(x) for x in marker.values]
                actual = (list(range(indexer['size'])) if isinstance(indexer, dict) else indexer)
                self.assertEqual(actual, observed, (side, name, join, 'indexer'))
                valid = [x for x in observed if x is not None]
                self.assertEqual(fact[side + '_reordered'], any(a > b for a, b in zip(valid, valid[1:])))
                expected_dropped = [i for i in range(source.sizes[name]) if i not in observed]
                self.assertEqual([item['index'] for item in fact[side + '_dropped']], expected_dropped)
        # Values are unique source-position identifiers; fills are precisely NaNs.
        # Align/broadcast are independent runtime oracles, not symbolic set math.
        aligned = xr.align(left, right, join=join, copy=False)
        expanded = xr.broadcast(*aligned)
        for side, source, actual in zip(('left', 'right'), (left, right), expanded):
            payload = np.asarray(actual.transpose(*result.dims).values)
            valid = payload[~np.isnan(payload)]
            retained = len(np.unique(valid))
            impact = report['impact'][side]
            self.assertEqual(impact['input_positions'], int(source.size))
            self.assertEqual(impact['retained_source_positions'], retained)
            self.assertEqual(impact['excluded_source_positions'], int(source.size) - retained)
            self.assertEqual(impact['contributing_output_positions'], int(valid.size))
            self.assertEqual(impact['fill_output_positions'], int(payload.size - valid.size))
            self.assertEqual(impact['broadcast_dimensions'], [d for d in result.dims if d not in source.dims])
        return report, result

    def test_unique_integer_orderings_exhaustive_tiny(self):
        options = ((), (0,), (1,), (0, 1), (1, 0), (0, 2), (2, 0), (2, 1, 0))
        count = 0
        for left, right, join in itertools.product(options, options, JOINS):
            with self.subTest(left=left, right=right, join=join):
                self.compare(_one(left, dtype='int64'), _one(right, dtype='int64'), join)
            count += 1
        self.assertEqual(count, 320)

    def test_identical_and_nonidentical_duplicate_indexes(self):
        cases = (
            ((1, 1), (1, 1)), ((2, 1, 1), (2, 1, 1)),
            ((1, 1), (1, 2)), ((1, 2), (1, 1)),
            ((1, 1), (1,)), ((1,), (1, 1)),
            ((1, 1), ()), ((), (1, 1)),
            ((1, 1), (2, 2)), ((1, 1, 2), (1, 2, 1)),
            ((1, 1, 2), (1, 2, 3)), ((1, 2, 3), (1, 1, 2)),
        )
        count = 0
        for (left, right), join in itertools.product(cases, JOINS):
            with self.subTest(left=left, right=right, join=join):
                self.compare(_one(left, dtype='int64'), _one(right, dtype='int64'), join)
            count += 1
        self.assertEqual(count, 60)

    def test_nonselected_duplicate_target_regression(self):
        # Both selected-target and nonselected-side duplicates reject. A rule
        # checking only whether the duplicate side's own labels change is wrong.
        for left, right, join in (([1, 1], [1, 2], 'left'),
                                  ([1, 2], [1, 1], 'left'),
                                  ([1, 1], [1, 2], 'right'),
                                  ([1, 2], [1, 1], 'right')):
            with self.subTest(left=left, right=right, join=join):
                result, error = _arithmetic(_one(left), _one(right), join)
                self.assertIsNone(result)
                self.assertIsInstance(error, ValueError)
                self.assertIn('duplicate', str(error))
                self.compare(_one(left), _one(right), join)

    def test_indexed_unindexed_sizes(self):
        count = 0
        for ls, rs, indexes, join in itertools.product(range(4), range(4),
                                                      ((False, False), (True, False), (False, True)), JOINS):
            with self.subTest(left_size=ls, right_size=rs, indexed=indexes, join=join):
                arrays = [_array((('x', np.arange(size)[::-1] if indexed else None, size),))
                          for size, indexed in zip((ls, rs), indexes)]
                self.compare(*arrays, join)
            count += 1
        self.assertEqual(count, 240)

    def test_indexed_duplicates_with_unindexed_positions(self):
        for side, join in itertools.product(('left', 'right'), JOINS):
            with self.subTest(indexed_side=side, join=join):
                arrays = [_one([1, 1]), _array((('x', None, 2),))]
                if side == 'right':
                    arrays.reverse()
                self.compare(*arrays, join)

    def test_scalars_empty_axes_cross_products_and_dimension_order(self):
        cases = (
            ((), ()),
            ((), (('x', [2, 1], 2),)),
            ((('x', [2, 1], 2),), ()),
            ((('x', [0, 1, 2], 3),), (('longitude', [0, 1, 2], 3),)),
            ((('x', [0, 1], 2),), (('y', [], 0),)),
            ((('x', [], 0),), (('y', [0, 1], 2),)),
            ((('x', [2, 1], 2), ('y', [0, 1], 2)),
             (('y', [1, 2], 2), ('x', [1, 2], 2))),
            ((('z', [0, 1], 2), ('x', [2, 1], 2)),
             (('x', [1, 2], 2), ('y', [3, 4, 5], 3))),
            ((('x', [], 0), ('y', [0], 1)),
             (('y', [0, 1], 2), ('z', [0, 1], 2))),
            ((('x', None, 2), ('y', [0, 1], 2)),
             (('y', [1, 0], 2), ('z', None, 3))),
        )
        count = 0
        for index, (ldims, rdims) in enumerate(cases):
            for join in JOINS:
                with self.subTest(case=index, join=join):
                    self.compare(_array(ldims), _array(rdims), join)
                count += 1
        self.assertEqual(count, 50)

    def test_exact_rejects_order_but_not_one_sided_dimensions(self):
        report, _ = self.compare(_one([2, 1]), _one([1, 2]), 'exact')
        self.assertEqual(report['forecast_status'], 'operation_error')
        report, result = self.compare(_one([0, 1, 2]), _one([0, 1, 2], dim='longitude'), 'exact')
        self.assertEqual(result.shape, (3, 3))
        self.assertEqual(report['output']['total_positions'], 9)

    def test_unicode_strings_and_no_normalization(self):
        cases = (
            (['z', 'a', 'é'], ['a', 'e\u0301', '<script>']),
            (['β', 'a'], ['a', 'β']),
            (['', 'x'], ['', 'y']),
            ([], ['λ', 'q']),
            (['a', 'a'], ['a', 'a']),
        )
        for (left, right), join in itertools.product(cases, JOINS):
            with self.subTest(left=left, right=right, join=join):
                self.compare(_one(left, dtype='U'), _one(right, dtype='U'), join)

    def test_numeric_storage_precision_and_promotion(self):
        cases = (
            ([1, 2], 'int64', [1, 2], 'float64'),
            ([1, 2], 'float64', [1, 2], 'int64'),
            ([1, 2], 'float32', [1, 2], 'float64'),
            ([1, 2], 'float64', [1, 2], 'float32'),
            ([0, .1, .2], 'float32', [0, .1, .2], 'float64'),
            ([1, 2], 'float32', [2, 3], 'int64'),
            ([1, 2], 'int64', [2, 3], 'float32'),
            ([1, 2], 'int8', [2, 3], 'float32'),
            ([1, 2], 'int16', [2, 3], 'float32'),
            ([1, 2], 'int32', [2, 3], 'float32'),
            ([1, 2], 'uint8', [2, 3], 'float32'),
            ([1, 2], 'uint32', [2, 3], 'float32'),
            ([1, 2], 'float32', [2, 3], 'float32'),
            ([0., -0.], 'float64', [-0., 0.], 'float64'),
            ([2**53 + 1, 2**53 + 2], 'int64', [2**53 + 2, 2**53 + 3], 'int64'),
            ([2**63, 2**63 + 1], 'uint64', [2**63 + 1, 2**63 + 2], 'uint64'),
            ([], 'float32', [1, 2], 'int64'),
        )
        for (left, ldtype, right, rdtype), join in itertools.product(cases, JOINS):
            with self.subTest(left_dtype=ldtype, right_dtype=rdtype, left=left, right=right, join=join):
                self.compare(_one(left, dtype=ldtype), _one(right, dtype=rdtype), join)

    def test_unsafe_mixed_integer_float_is_conservatively_unsupported(self):
        for join in JOINS:
            with self.subTest(join=join):
                report, result = self.compare(_one([2**53 + 1, 2**53 + 2], dtype='int64'),
                                               _one([2**53, 2**53 + 2], dtype='float64'),
                                               join, allow_unsupported=True)
                # The pinned runtime conflates these indexes even for exact.
                # The forecast intentionally refuses that lossy numeric profile.
                self.assertIsNotNone(result)
                self.assertIn('unsafe_mixed_integer_float', [d['code'] for d in report['diagnostics']])

    def test_signed_unsigned_dtype_promotion_is_conservatively_unsupported(self):
        for join in JOINS:
            with self.subTest(join=join):
                report, _ = self.compare(_one([-1, 2], dtype='int64'),
                                         _one([2, 2**63 + 1], dtype='uint64'),
                                         join, allow_unsupported=True)
                self.assertIn('unsafe_integer_dtype_promotion',
                              [d['code'] for d in report['diagnostics']])

    def test_nonfinite_axis_labels_do_not_receive_complete_forecasts(self):
        for value, join in itertools.product((float('nan'), float('inf'), -float('inf')), JOINS):
            with self.subTest(label=value, join=join):
                self.compare(_one([value]), _one([value]), join, allow_unsupported=True)


@unittest.skipUnless(HAS_PIN, 'Requires the pinned optional xarray 2025.10.1 runtime')
class XarrayAuxiliaryDifferentialTests(unittest.TestCase):
    """Exercise generated NetCDF4 files through the actual report entry point."""
    def test_scalar_and_one_dimensional_auxiliary_coordinates(self):
        from coordguard.report import audit_netcdf
        cases = (
            ('scalar_equal', [0, 1], [0, 1], {'q': 1}, {'q': 1}),
            ('scalar_conflict', [0, 1], [0, 1], {'q': 1}, {'q': 2}),
            ('scalar_one_sided', [0, 1], [0, 1], {'q': 7}, {}),
            ('one_dim_equal', [0, 1], [0, 1], {'q': ('x', [10, 20])}, {'q': ('x', [10, 20])}),
            ('one_dim_mixed_dtype_equal', [0, 1], [0, 1],
             {'q': ('x', np.array([10, 20], dtype='int32'))},
             {'q': ('x', np.array([10, 20], dtype='float64'))}),
            ('mixed_dtype_aligned_reordered', [1, 0], [0, 1],
             {'q': ('x', np.array([20, 10], dtype='float32'))},
             {'q': ('x', np.array([10, 20], dtype='float64'))}),
            ('one_dim_conflict', [0, 1], [0, 1], {'q': ('x', [10, 20])}, {'q': ('x', [10, 99])}),
            ('aligned_equal', [0, 1], [1, 2], {'q': ('x', [10, 20])}, {'q': ('x', [20, 30])}),
            ('aligned_conflict', [0, 1], [1, 2], {'q': ('x', [10, 20])}, {'q': ('x', [99, 30])}),
            ('aligned_reordered', [1, 0], [0, 1], {'q': ('x', [20, 10])}, {'q': ('x', [10, 20])}),
            ('one_sided_fills', [0, 1], [1, 2], {'q': ('x', [10, 20])}, {}),
            ('one_sided_int8_fills', [0, 1], [1, 2],
             {'q': ('x', np.array([10, 20], dtype='int8'))}, {}),
            ('one_sided_int16_fills', [0, 1], [1, 2],
             {'q': ('x', np.array([10, 20], dtype='int16'))}, {}),
            ('one_sided_uint8_fills', [0, 1], [1, 2],
             {'q': ('x', np.array([10, 20], dtype='uint8'))}, {}),
            ('one_sided_uint16_fills', [0, 1], [1, 2],
             {'q': ('x', np.array([10, 20], dtype='uint16'))}, {}),
            ('one_sided_float32_fills', [0, 1], [1, 2],
             {'q': ('x', np.array([10, 20], dtype='float32'))}, {}),
            ('right_one_sided_fills', [0, 1], [1, 2], {}, {'q': ('x', [20, 30])}),
            ('disjoint_empty_equal', [0, 1], [2, 3], {'q': ('x', [10, 20])}, {'q': ('x', [90, 80])}),
            ('scalar_vector_equal', [0, 1], [0, 1], {'q': 1}, {'q': ('x', [1, 1])}),
            ('vector_scalar_equal', [0, 1], [0, 1], {'q': ('x', [1, 1])}, {'q': 1}),
            ('scalar_vector_conflict', [0, 1], [0, 1], {'q': 1}, {'q': ('x', [1, 2])}),
            ('float_vector_conflict', [0, 1], [0, 1], {'q': ('x', [.1, .2])}, {'q': ('x', [.1, .3])}),
        )
        count = 0
        with tempfile.TemporaryDirectory(prefix='coordguard-xarray-diff-') as directory:
            for name, lcoords, rcoords, laux, raux in cases:
                left = _one(lcoords, dtype='int64', auxiliary=laux)
                right = _one(rcoords, dtype='int64', auxiliary=raux)
                left_path = Path(directory) / (name + '-left.nc')
                right_path = Path(directory) / (name + '-right.nc')
                left.to_netcdf(left_path, engine='h5netcdf')
                right.to_netcdf(right_path, engine='h5netcdf')
                for join in JOINS:
                    with self.subTest(case=name, join=join):
                        result, error = _arithmetic(left, right, join)
                        envelope = audit_netcdf(str(left_path), str(right_path), 'field', 'field',
                                                join=join, contract=PERMISSIVE)
                        report = envelope['audit']
                        if error is not None:
                            self.assertEqual(report['forecast_status'], 'operation_error')
                        else:
                            self.assertEqual(report['forecast_status'], 'complete', report['diagnostics'])
                            facts = [fact for fact in envelope['auxiliary_facts'] if fact['name'] == 'q']
                            self.assertEqual(len(facts), 1)
                            fact = facts[0]
                            if 'q' not in result.coords:
                                self.assertEqual(fact['status'], 'dropped_conflict')
                            else:
                                self.assertEqual(fact['status'], 'preserved')
                                self.assertEqual(fact['dimensions'], list(result.coords['q'].dims))
                                self.assertEqual(fact['labels'], _auxiliary_values(result.coords['q'].values))
                    count += 1
        self.assertEqual(count, 110)


    def test_cross_product_auxiliary_results_are_not_silently_flattened(self):
        from coordguard.report import audit_netcdf
        cases = (
            ('equal', [0, 1], [7, 7], [0, 1, 2], [7, 7, 7]),
            ('conflict', [0, 1], [7, 8], [0, 1, 2], [7, 7, 7]),
            ('empty_equal', [], [], [0, 1, 2], [7, 8, 9]),
        )
        with tempfile.TemporaryDirectory(prefix='coordguard-aux-broadcast-') as directory:
            for case, lx, lq, ry, rq in cases:
                left = _one(lx, dtype='int64', auxiliary={'q': ('x', np.array(lq, dtype='int64'))})
                right = _one(ry, dtype='int64', dim='y', auxiliary={'q': ('y', np.array(rq, dtype='int64'))})
                paths = [str(Path(directory) / (case + '-' + side + '.nc')) for side in ('left', 'right')]
                left.to_netcdf(paths[0], engine='h5netcdf')
                right.to_netcdf(paths[1], engine='h5netcdf')
                for join in JOINS:
                    with self.subTest(case=case, join=join):
                        result, error = _arithmetic(left, right, join)
                        self.assertIsNone(error)
                        envelope = audit_netcdf(*paths, 'field', 'field', join=join, contract=PERMISSIVE)
                        facts = [f for f in envelope['auxiliary_facts'] if f['name'] == 'q']
                        self.assertEqual(len(facts), 1)
                        if 'q' in result.coords:
                            self.assertEqual(result.coords['q'].ndim, 2)
                            self.assertEqual(facts[0]['status'], 'unsupported')
                            self.assertEqual(envelope['audit']['forecast_status'], 'unsupported')
                            self.assertEqual(envelope['audit']['contract_status'], 'unverified')
                            self.assertIsNone(envelope['audit']['output'])
                        else:
                            self.assertEqual(facts[0]['status'], 'dropped_conflict')
                            self.assertEqual(envelope['audit']['forecast_status'], 'complete')

    def test_scalar_coordinate_shadowed_by_opposite_dimension_index(self):
        from coordguard.report import audit_netcdf
        left = _one([0, 1], dim='y', auxiliary={'x': 8})
        right = _one([2, 1])
        with tempfile.TemporaryDirectory(prefix='coordguard-aux-shadow-') as directory:
            paths = [str(Path(directory) / (side + '.nc')) for side in ('left', 'right')]
            left.to_netcdf(paths[0], engine='h5netcdf')
            right.to_netcdf(paths[1], engine='h5netcdf')
            for join in JOINS:
                with self.subTest(join=join):
                    result, error = _arithmetic(left, right, join)
                    self.assertIsNone(error)
                    envelope = audit_netcdf(*paths, 'field', 'field', join=join, contract=PERMISSIVE)
                    self.assertEqual(envelope['audit']['forecast_status'], 'complete')
                    facts = [f for f in envelope['auxiliary_facts'] if f['name'] == 'x']
                    self.assertEqual(facts, [{'name': 'x', 'status': 'shadowed_by_dimension_index'}])
                    axis = next(f for f in envelope['audit']['axes'] if f['name'] == 'x')
                    self.assertEqual(axis['labels'], _typed_values(result.x.values))


if __name__ == '__main__':
    unittest.main()
