"""Independent operation checks must reject self-consistent report tampering."""
import copy
from dataclasses import replace
import importlib.util
import inspect
import itertools
import unittest

from coordguard import ArraySnapshot, Axis, Contract, Label, Metadata
from coordguard.budget import Budget, Limits
from coordguard.planner import audit

RUNTIME = 'xarray-netcdf-2025.10.1-v1'
JOINS = ('inner', 'outer', 'left', 'right', 'exact')


def snapshot(*axes, profile='typed-manifest-v1', blockers=()):
    return ArraySnapshot('field', tuple(axes), profile, blockers)


def axis(values, name='x', **kwargs):
    return Axis.from_values(name, values, **kwargs)


class ReportChecksTests(unittest.TestCase):
    def check(self, left, right, report, limits=None):
        self.assertIsNotNone(importlib.util.find_spec('coordguard.report_checks'),
                             'independent operation checker is not implemented')
        from coordguard.report_checks import check_operation_facts
        result = check_operation_facts(left, right, report, budget=Budget(limits))
        self.assertEqual(set(result), {'valid', 'reason'})
        self.assertIs(type(result['valid']), bool)
        self.assertIs(type(result['reason']), str)
        return result

    def assertValid(self, left, right, report=None, **kwargs):
        report = audit(left, right, **kwargs) if report is None else report
        result = self.check(left, right, report)
        self.assertTrue(result['valid'], result)

    def assertInvalid(self, left, right, report):
        self.assertFalse(self.check(left, right, report)['valid'])

    def test_honest_tiny_profiles_all_joins(self):
        values = ([], [0], [1], [0, 1], [1, 0], [1, 1])
        for profile, a, b, join in itertools.product(('typed-manifest-v1', RUNTIME), values, values, JOINS):
            left, right = snapshot(axis(a), profile=profile), snapshot(axis(b), profile=profile)
            with self.subTest(profile=profile, a=a, b=b, join=join):
                self.assertValid(left, right, join=join)

    def test_symbolic_broadcast_positional_zero_and_scalar(self):
        cases = [(snapshot(), snapshot()),
                 (snapshot(Axis('x', 2**40)), snapshot(Axis('x', 2**40))),
                 (snapshot(Axis('x', 2)), snapshot(Axis('x', 3))),
                 (snapshot(Axis('x', 2)), snapshot(axis([2, 1]))),
                 (snapshot(axis([1, 2])), snapshot(axis([], 'y'))),
                 (snapshot(axis([2, 1]), axis([5], 'z')), snapshot(axis([1, 3]), Axis('y', 4)))]
        for left, right in cases:
            for join in JOINS:
                self.assertValid(left, right, join=join)

    def test_output_and_impact_counts_are_recomputed(self):
        left, right = snapshot(axis([2, 1])), snapshot(axis([1, 3]), Axis('y', 3))
        report = audit(left, right, join='outer')
        mutations = [('output', 'dimensions', ['y', 'x']), ('output', 'sizes', [2, 3]),
                     ('output', 'total_positions', 6), ('output', 'total_positions', True)]
        for obj, field, value in mutations:
            bad = copy.deepcopy(report); bad[obj][field] = value
            self.assertInvalid(left, right, bad)
        for side in ('left', 'right'):
            for field in report['impact'][side]:
                bad = copy.deepcopy(report)
                value = bad['impact'][side][field]
                bad['impact'][side][field] = (['invented'] if not value else []) if isinstance(value, list) else None if isinstance(value, dict) else value + 1
                self.assertInvalid(left, right, bad)

    def test_indexer_relation_order_coverage_and_types(self):
        left, right = snapshot(axis([2, 1, 4])), snapshot(axis([1, 3, 2]))
        report = audit(left, right, join='outer')
        mutations = {'left_indexer': [[1, 0, None], [1, 0, 2, None], [True, 0, None, 2],
                                     [1, 0, None, -1], [1, 0, None, 3], [1, 1, None, 2]],
                     'right_indexer': [[0, 2, 1, 0], [0, 1, 2, None]],
                     'output_size': [3, True], 'left_reordered': [False, 1],
                     'left_dropped': [[{'index': 0, 'label': Label.from_value(2).to_dict()}]],
                     'labels': [[Label.from_value(v).to_dict() for v in (2, 1, 3, 4)]]}
        for field, values in mutations.items():
            for value in values:
                bad = copy.deepcopy(report); bad['axes'][0][field] = value
                with self.subTest(field=field, value=value): self.assertInvalid(left, right, bad)

    def test_coherent_missing_join_member_cannot_hide_coverage(self):
        left, right = snapshot(axis([1, 2])), snapshot(axis([1, 3]))
        bad = audit(left, right, join='outer')
        fact = bad['axes'][0]
        fact.update(output_size=2, labels=[Label.from_value(x).to_dict() for x in (1, 2)],
                    left_indexer=[0, 1], right_indexer=[0, None],
                    right_dropped=[{'index': 1, 'label': Label.from_value(3).to_dict()}])
        bad['output'].update(sizes=[2], total_positions=2)
        self.assertInvalid(left, right, bad)

    def test_dropped_positions_require_exact_source_coverage(self):
        left, right = snapshot(axis([4, 1, 2])), snapshot(axis([1]))
        report = audit(left, right)
        for value in ([], [{'index': 0, 'label': Label.from_value(2).to_dict()}],
                      [{'index': 0, 'label': Label.from_value(4).to_dict()}]):
            bad = copy.deepcopy(report); bad['axes'][0]['left_dropped'] = value
            self.assertInvalid(left, right, bad)

    def test_duplicate_labels_positions_and_multiplicity_checked(self):
        left = snapshot(axis([1, 1, 2, 2, 2]))
        report = audit(left, left)
        for field, value in [('left_duplicates', []), ('right_duplicates', []),
                             ('mode', 'indexed_join'), ('left_indexer', [0, 0, 2, 3, 4])]:
            bad = copy.deepcopy(report); bad['axes'][0][field] = value
            self.assertInvalid(left, left, bad)
        for key, value in [('positions', [0]), ('multiplicity', 1), ('multiplicity', True),
                           ('label', Label.from_value(2).to_dict())]:
            bad = copy.deepcopy(report); bad['axes'][0]['left_duplicates'][0][key] = value
            self.assertInvalid(left, left, bad)

    def test_runtime_label_bits_dtype_promotion_and_empty_order(self):
        cases = [(axis([2, 1], kind='float32'), axis([3., 1.], kind='float64')),
                 (replace(axis([]), source_dtype='float64'), replace(axis([2, 1]), source_dtype='int64')),
                 (axis([0.0, -1.0], kind='float32'), axis([-0.0, -1.0], kind='float64')),
                 (replace(axis([1]), source_dtype='int64'), replace(axis([1]), source_dtype='uint64')),
                 (axis([2**53 + 1]), axis([float(2**53)]))]
        for a, b in cases:
            left, right = snapshot(a, profile=RUNTIME), snapshot(b, profile=RUNTIME)
            for join in JOINS: self.assertValid(left, right, join=join)
        left = snapshot(cases[2][0], profile=RUNTIME); right = snapshot(cases[2][1], profile=RUNTIME)
        bad = audit(left, right, join='right')
        bad['axes'][0]['labels'] = [x.to_dict() for x in right.axes[0].labels]
        self.assertInvalid(left, right, bad)

    def test_status_changes_cannot_certify_an_operation_error(self):
        left, right = snapshot(axis([1])), snapshot(axis([2]))
        report = audit(left, right, join='exact')
        self.assertValid(left, right, report)
        for status in ('complete', 'unsupported'):
            bad = copy.deepcopy(report); bad['forecast_status'] = status
            self.assertInvalid(left, right, bad)
        for field, value in [('status', 'complete'), ('reason', None), ('output_size', 1)]:
            bad = copy.deepcopy(report); bad['axes'][0][field] = value
            self.assertInvalid(left, right, bad)

    def test_source_axes_and_digest_binding(self):
        left, right = snapshot(axis([1]), Axis('z', 2)), snapshot(axis([1]))
        report = audit(left, right)
        for axes in ([], report['axes'][:-1], list(reversed(report['axes'])), report['axes'] + report['axes']):
            bad = copy.deepcopy(report); bad['axes'] = axes
            self.assertInvalid(left, right, bad)
        bad = copy.deepcopy(report); bad['left_digest'] = '0' * 64
        self.assertInvalid(left, right, bad)
        changed = snapshot(axis([2]), Axis('z', 2))
        self.assertInvalid(changed, right, report)

    def test_semantic_contradictions_and_contract_violations(self):
        cases = [(snapshot(axis([1], metadata=Metadata(units='m'))), snapshot(axis([1], metadata=Metadata(units='km')))),
                 (snapshot(axis([1], metadata=Metadata(crs='a'))), snapshot(axis([1]))),
                 (snapshot(axis([1]), blockers=('auxiliary_not_modeled',)), snapshot(axis([1]))),
                 (snapshot(axis([1], metadata=Metadata(role='spatial'))), snapshot(axis([1], metadata=Metadata(role='spatial'))))]
        contract = Contract(tolerances=(('x', Label.from_value(0)),))
        for left, right in cases:
            report = audit(left, right, contract=contract)
            self.assertValid(left, right, report)
            bad = copy.deepcopy(report); bad['contract_status'] = 'pass'
            self.assertInvalid(left, right, bad)
            if report['semantic_blockers']:
                bad = copy.deepcopy(report); bad['semantic_blockers'] = []
                self.assertInvalid(left, right, bad)
        left, right = snapshot(axis([1, 2])), snapshot(axis([1, 3]))
        report = audit(left, right)
        for field, value in [('violations', []), ('contract_status', 'pass')]:
            bad = copy.deepcopy(report); bad[field] = value
            self.assertInvalid(left, right, bad)

    def test_valid_numeric_correspondence_and_tampered_certificate(self):
        left, right = snapshot(axis([0., 2.])), snapshot(axis([0.01, 2.01]))
        report = audit(left, right, contract=Contract(tolerances=(('x', Label.from_value(0.02)),)))
        self.assertValid(left, right, report)
        for key, value in [('edge_count', 0), ('edge_digest', '0' * 64),
                           ('atol', Label.from_value(0.03).to_dict()), ('left_digest', '0' * 64)]:
            bad = copy.deepcopy(report); bad['axes'][0]['correspondence'][key] = value
            self.assertInvalid(left, right, bad)
        bad = copy.deepcopy(report); bad['axes'][0]['correspondence']['witness']['matching'] = [1, 0]
        self.assertInvalid(left, right, bad)
        bad = copy.deepcopy(report); bad['axes'][0]['correspondence_status'] = 'exact'
        self.assertInvalid(left, right, bad)
        bad = copy.deepcopy(report); bad['axes'][0]['correspondence'] = None
        self.assertInvalid(left, right, bad)

    def test_unsupported_correspondence_cannot_invent_certificate(self):
        for a in (axis(['a']), axis([1, 1]), Axis('x', 2)):
            left = snapshot(a)
            report = audit(left, left, contract=Contract(tolerances=(('x', Label.from_value(0)),)))
            self.assertValid(left, left, report)
            bad = copy.deepcopy(report); bad['axes'][0]['correspondence_status'] = 'unique_within_tolerance'
            self.assertInvalid(left, left, bad)

    def test_honest_unsupported_and_resource_reports_never_have_output(self):
        left = snapshot(axis([float('nan')]))
        report = audit(left, left)
        self.assertValid(left, left, report)
        for field, value in [('output', {}), ('impact', {}), ('contract_status', 'pass')]:
            bad = copy.deepcopy(report); bad[field] = value
            self.assertInvalid(left, left, bad)
        left = snapshot(axis([1, 2]))
        for limits in (Limits(max_labels=1), Limits(max_steps=1), Limits(deadline_seconds=0), Limits(max_edges=0)):
            report = audit(left, left, limits=limits, contract=Contract(tolerances=(('x', Label.from_value(0)),)))
            self.assertEqual(report['forecast_status'], 'resource_limited')
            self.assertValid(left, left, report)
            bad = copy.deepcopy(report); bad['output'] = {'dimensions': ['x'], 'sizes': [2], 'total_positions': 2}
            self.assertInvalid(left, left, bad)

    def test_datetime_runtime_semantics(self):
        a = Axis('time', 1, (Label('datetime', 's:946684800'),), Metadata(units='days since 2000-01-01', calendar='standard', role='time', time_encoding='datetime64[s]'))
        b = Axis('time', 1, (Label('datetime', 'ms:946684800000'),), Metadata(units='days since 1999-12-31', calendar='standard', role='time', time_encoding='datetime64[ms]'))
        left, right = snapshot(a, profile=RUNTIME), snapshot(b, profile=RUNTIME)
        self.assertValid(left, right, join='exact')
        self.assertValid(snapshot(a), snapshot(b))

    def test_checker_budget_exhaustion_never_returns_valid(self):
        left = snapshot(axis(list(range(20))))
        report = audit(left, left)
        for limits in (Limits(max_labels=1), Limits(max_steps=1), Limits(max_coordinate_bytes=1),
                       Limits(max_working_bytes=1024 * 1024 + 100), Limits(deadline_seconds=0)):
            self.assertFalse(self.check(left, left, report, limits)['valid'])

    def test_bounded_shape_and_strict_types(self):
        left = snapshot(axis([0, 1]))
        report = audit(left, left)
        for field, value in [('schema_version', True), ('join', []), ('axes', {'x': []}),
                             ('forecast_status', []), ('contract', None)]:
            bad = copy.deepcopy(report); bad[field] = value
            self.assertInvalid(left, left, bad)
        bad = copy.deepcopy(report); bad['axes'][0]['labels'] = [None] * 10000
        self.assertInvalid(left, left, bad)
        bad = copy.deepcopy(report); bad['axes'][0]['left_indexer']['size'] = True
        self.assertInvalid(left, left, bad)
        bad = copy.deepcopy(report); bad['axes'][0]['labels'][0]['value'] = {'self': None}
        bad['axes'][0]['labels'][0]['value']['self'] = bad
        self.assertInvalid(left, left, bad)

    def test_adapter_metadata_and_auxiliary_annotations_are_conservative(self):
        left = snapshot(axis([1, 2]), profile=RUNTIME)
        for blocker in ({'code': 'selected_units_mismatch'},
                        {'code': 'auxiliary_calendar_mismatch', 'coordinate': 'height'}):
            report = audit(left, left)
            report['semantic_blockers'].append(blocker)
            report['contract_status'] = 'unverified'
            self.assertValid(left, left, report)
            bad = copy.deepcopy(report); bad['contract_status'] = 'pass'
            self.assertInvalid(left, left, bad)
        right = snapshot(axis([3, 4]), profile=RUNTIME)
        error = audit(left, right, join='exact')
        error['semantic_blockers'].append({'code': 'selected_units_mismatch'})
        self.assertValid(left, right, error)
        bad = copy.deepcopy(error); bad['contract_status'] = 'unverified'
        self.assertInvalid(left, right, bad)
        report = audit(left, left)
        report['semantic_blockers'].append({'code': 'multidimensional_auxiliary_result'})
        report.update(forecast_status='unsupported', contract_status='unverified', output=None, impact=None)
        self.assertValid(left, left, report)
        report = audit(left, left)
        report['diagnostics'].append({'code': 'conflicting_auxiliary_dropped', 'coordinate': 'height'})
        report['violations'].append({'code': 'dropped_auxiliary_coordinate', 'coordinate': 'height'})
        report['contract_status'] = 'fail'
        self.assertValid(left, left, report)

    def test_handwritten_scalar_report_without_producer(self):
        left, right = snapshot(), snapshot()
        impact = {'input_positions': 1, 'retained_source_positions': 1, 'excluded_source_positions': 0,
                  'contributing_output_positions': 1, 'fill_output_positions': 0,
                  'broadcast_dimensions': [], 'excluded_fraction': {'numerator': 0, 'denominator': 1}}
        report = {'schema_version': 1, 'profile': 'coordguard-core-v1', 'operation': 'binary', 'join': 'inner',
                  'forecast_status': 'complete', 'contract_status': 'pass', 'left_digest': left.digest(),
                  'right_digest': right.digest(), 'contract': Contract().to_dict(), 'axes': [],
                  'output': {'dimensions': [], 'sizes': [], 'total_positions': 1},
                  'impact': {'left': impact, 'right': impact}, 'violations': [], 'semantic_blockers': [],
                  'diagnostics': [], 'limits': Limits().to_dict(), 'resources': None,
                  'claims': {'payload_inspected': False, 'physical_equivalence': False, 'xarray_runtime_validated': False}}
        self.assertValid(left, right, report)

    def test_ambiguous_impossible_and_cardinality_certificates(self):
        for values, tolerance in (([2, 3], 3), ([5, 6], 0), ([0], 0)):
            left, right = snapshot(axis([0, 1])), snapshot(axis(values))
            report = audit(left, right, contract=Contract(tolerances=(('x', Label.from_value(tolerance)),)))
            self.assertValid(left, right, report)
            bad = copy.deepcopy(report); bad['violations'] = []
            self.assertInvalid(left, right, bad)

    def test_module_does_not_import_or_call_producer(self):
        self.assertIsNotNone(importlib.util.find_spec('coordguard.report_checks'))
        import coordguard.report_checks as checks
        source = inspect.getsource(checks)
        self.assertNotIn('from .planner', source)
        self.assertNotIn('from .matching', source)
        self.assertNotIn('import planner', source)
        self.assertNotIn('import matching', source)


if __name__ == '__main__':
    unittest.main()
