"""Behavior and hostile-content tests for the offline audit presentation."""
import copy
import importlib
import importlib.util
from html.parser import HTMLParser
import unittest

from coordguard import ArraySnapshot, Axis, Contract, Label, Limits, Metadata
from coordguard.planner import audit


class Document(HTMLParser):
    def __init__(self, html):
        super().__init__(convert_charrefs=True)
        self.tags = []
        self.attributes = []
        self.fragments = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        self.tags.append(tag)
        self.attributes.extend(attrs)

    def handle_data(self, value):
        self.fragments.append(value)

    @property
    def text(self):
        return ' '.join(' '.join(self.fragments).split())


def envelope(left, right, **kwargs):
    return {
        'schema_version': 2,
        'profile': 'coordguard-netcdf-v1',
        'left': {'snapshot': left.to_dict(), 'auxiliary': []},
        'right': {'snapshot': right.to_dict(), 'auxiliary': []},
        'audit': audit(left, right, **kwargs),
        'auxiliary_facts': [],
    }


def array(values, name='temperature', axis='x', **kwargs):
    return ArraySnapshot(name, (Axis.from_values(axis, values, **kwargs),))


class HiddenEvidence:
    """Fail if a renderer stringifies evidence outside a selected sample."""
    def __str__(self):
        raise AssertionError('Evidence outside the display sample was formatted')

    __repr__ = __str__


class HTMLReportTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec('coordguard.html_report'),
                             'The standalone HTML renderer has not been implemented')
        self.render = importlib.import_module('coordguard.html_report').render_html

    def test_complete_envelope_is_accessible_standalone_and_deterministic(self):
        report = envelope(array([0., 1., 2.]), array([0., 1.000000001, 2.]))
        before = copy.deepcopy(report)
        html = self.render(report)
        document = Document(html)
        self.assertTrue(html.lower().startswith('<!doctype html>'))
        for tag in ('html', 'title', 'main', 'h1', 'h2', 'caption', 'th'):
            self.assertIn(tag, document.tags)
        self.assertIn(('lang', 'en'), document.attributes)
        self.assertIn(('scope', 'col'), document.attributes)
        for prohibited in ('script', 'iframe', 'object', 'embed', 'link', 'img'):
            self.assertNotIn(prohibited, document.tags)
        self.assertIn('Content-Security-Policy', html)
        self.assertIn('temperature', document.text)
        self.assertIn('coordguard-netcdf-v1', document.text)
        self.assertIn('Forecast', document.text)
        self.assertIn('complete', document.text)
        self.assertIn('Contract', document.text)
        self.assertIn('fail', document.text)
        self.assertIn('Structural output slots', document.text)
        self.assertIn('nonmissing', document.text)
        self.assertIn('RAM', document.text)
        self.assertIn('scientific', document.text)
        self.assertIn('JSON', document.text)
        self.assertNotIn('application/json', html)
        self.assertEqual(self.render(report), html)
        self.assertEqual(report, before)

    def test_original_order_labels_drops_indexers_and_float_precision(self):
        report = envelope(array([2., 0., 1.]), array([0., 1.000000001, 2.]))
        html = self.render(report)
        text = Document(html).text
        for phrase in ('Left original-order labels', 'Right original-order labels',
                       'Output labels', 'Left dropped labels', 'Right dropped labels',
                       'Left indexer', 'Right indexer', 'Original index',
                       '1.000000001', 'float64', 'Right reordered'):
            self.assertIn(phrase, text)
        # The original source table preserves the left source order.
        fragment = html.split('Left original-order labels', 1)[1].split('</table>', 1)[0]
        self.assertLess(fragment.index('2.0'), fragment.index('0.0'))
        self.assertLess(fragment.index('0.0'), fragment.index('1.0'))

    def test_outer_join_distinguishes_fill_indexer_from_unknown(self):
        report = envelope(array([0, 1]), array([1, 2]), join='outer')
        text = Document(self.render(report)).text
        self.assertIn('fill', text)
        self.assertIn('Fill output slots', text)
        self.assertIn('Excluded source slots', text)

    def test_duplicate_positions_and_multiplicities_are_visible(self):
        report = envelope(array([1, 1, 2]), array([1, 1, 2]))
        text = Document(self.render(report)).text
        for phrase in ('Left duplicates', 'Right duplicates', 'Positions',
                       'Multiplicity', 'duplicate_labels'):
            self.assertIn(phrase, text)

    def test_explicit_same_axis_declaration_and_semantic_limit_are_prominent(self):
        source = array([1.], metadata=Metadata(role='spatial'))
        report = envelope(source, source, contract=Contract(
            tolerances=(('x', Label.from_value(1e-8)),), same_axis_declarations=('x',)))
        text = Document(self.render(report)).text
        for phrase in ('Explicit same-axis declarations', 'x', 'unique_within_tolerance',
                       'Absolute tolerance', 'does not establish physical equivalence',
                       'Matching', 'Topological order'):
            self.assertIn(phrase, text)
        self.assertLess(text.index('Explicit same-axis declarations'), text.index('Per-axis evidence'))

    def test_correspondence_ambiguity_and_hall_witnesses_are_visible(self):
        for left, right, tolerance, expected in (
            ([0., .1], [.04, .06], .1, ('ambiguous', 'Matching', 'Alternative')),
            ([0., 1.], [0., 10.], .1, ('impossible', 'Left set', 'Neighbors', 'Unmatched left')),
        ):
            report = envelope(array(left), array(right), contract=Contract(
                tolerances=(('x', Label.from_value(tolerance)),)))
            text = Document(self.render(report)).text
            for phrase in expected:
                self.assertIn(phrase, text)

    def test_correspondence_mapping_names_both_original_index_columns(self):
        report = envelope(array([2., 1.]), array([1., 2.]), contract=Contract(
            tolerances=(('x', Label.from_value(0.)),)))
        html = self.render(report)
        self.assertIn('Left original index', Document(html).text)
        self.assertIn('Right original index', Document(html).text)
        matching = html.split('<caption>Matching</caption>', 1)[1].split('</table>', 1)[0]
        self.assertIn('<tr><td>0</td><td>1</td></tr>', matching)
        self.assertIn('<tr><td>1</td><td>0</td></tr>', matching)

    def test_hostile_labels_metadata_names_statuses_and_diagnostics_are_escaped(self):
        hostile = '\"><script>alert(1)</script><img src=x onerror="alert(2)">&\''
        left = array([hostile], name=hostile, axis=hostile,
                     metadata=Metadata(units=hostile, crs=hostile))
        report = envelope(left, left)
        report['profile'] = hostile
        report['audit']['forecast_status'] = hostile
        report['audit']['contract_status'] = hostile
        report['audit']['diagnostics'] = [{'code': hostile, 'detail': hostile}]
        report['left']['attributes'] = {hostile: hostile}
        report['auxiliary_facts'] = [{'name': hostile, 'status': hostile}]
        html = self.render(report)
        document = Document(html)
        self.assertIn(hostile, document.text)
        self.assertIn('&lt;script&gt;', html)
        self.assertIn('&quot;', html)
        self.assertIn('&#x27;', html)
        self.assertNotIn('script', document.tags)
        self.assertNotIn('img', document.tags)
        for name, value in document.attributes:
            self.assertFalse(name.lower().startswith('on'))
            self.assertNotIn(hostile, value or '')
            if name in ('href', 'src'):
                self.assertTrue(value.startswith('#'))

    def test_sample_limit_applies_to_original_output_drops_and_indexers(self):
        report = envelope(array(list(range(100))), array(list(range(100, 200))), join='outer')
        report['left']['snapshot']['axes'][0]['labels'][2:] = [HiddenEvidence()] * 98
        report['right']['snapshot']['axes'][0]['labels'][2:] = [HiddenEvidence()] * 98
        fact = report['audit']['axes'][0]
        for key in ('labels', 'left_indexer', 'right_indexer'):
            fact[key][2:] = [HiddenEvidence()] * (len(fact[key]) - 2)
        fact['left_dropped'] = [{'index': 0, 'label': Label.from_value('first').to_dict()},
                                {'index': 1, 'label': Label.from_value('second').to_dict()},
                                HiddenEvidence()]
        text = Document(self.render(report, sample_limit=2)).text
        self.assertIn('Showing 2 of 100', text)
        self.assertIn('Showing 2 of 200', text)
        self.assertIn('Showing 2 of 3', text)
        self.assertIn('Truncated display', text)

    def test_nested_duplicate_and_correspondence_evidence_is_sampled_before_formatting(self):
        report = envelope(array([1]), array([1]))
        fact = report['audit']['axes'][0]
        guarded = [0, 1, HiddenEvidence()]
        fact['left_duplicates'] = [{'label': Label.from_value(1).to_dict(),
                                   'multiplicity': 3, 'positions': guarded}]
        fact['correspondence'] = {'status': 'ambiguous', 'witness': {
            'kind': 'ambiguous', 'matching': guarded, 'alternative': guarded,
            'topological_order': guarded, 'left_set': guarded,
            'neighbors': guarded, 'unmatched_left': guarded}}
        text = Document(self.render(report, sample_limit=2)).text
        self.assertIn('Showing 2 of 3', text)
        self.assertLess(len(text), 15000)

    def test_auxiliary_metadata_and_diagnostics_are_sampled_before_formatting(self):
        report = envelope(array([1]), array([1]))
        report['left']['attributes'] = {'units': 'kelvin', 'comments': ['first', 'second', HiddenEvidence()]}
        report['left']['auxiliary'] = [{'name': 'height', 'labels': [1, 2, HiddenEvidence()]}]
        report['auxiliary_facts'] = [{'name': 'height', 'status': 'dropped_conflict'}, HiddenEvidence()]
        report['audit']['diagnostics'] = [{'code': 'notice'}, HiddenEvidence()]
        text = Document(self.render(report, sample_limit=1)).text
        for phrase in ('kelvin', 'height', 'dropped_conflict', 'notice', 'Showing 1 of 3'):
            self.assertIn(phrase, text)

    def test_zero_sample_limit_hides_evidence_and_keeps_true_totals(self):
        report = envelope(array([1, 2, 3]), array([1, 2, 3]))
        text = Document(self.render(report, sample_limit=0)).text
        self.assertIn('Showing 0 of 3', text)
        self.assertIn('Structural output slots 3', text)
        self.assertIn('Truncated display', text)

    def test_small_complete_samples_are_not_claimed_to_be_truncated(self):
        report = envelope(array([1]), array([1]))
        text = Document(self.render(report)).text
        self.assertIn('Showing 1 of 1', text)
        self.assertNotIn('Truncated display', text)

    def test_raw_planner_reports_disclose_missing_source_snapshot(self):
        raw = audit(array([1]), array([1]))
        text = Document(self.render(raw)).text
        self.assertIn('coordguard-core-v1', text)
        self.assertIn('Original-order source labels are not included', text)
        self.assertIn('Output labels', text)

    def test_unknown_output_is_never_invented_for_operation_error_or_unsupported(self):
        reports = [
            envelope(array([1]), array([2]), join='exact'),
            envelope(array([float('nan')]), array([1.])),
            envelope(array([1]), array([1]), limits=Limits(deadline_seconds=0)),
        ]
        for report in reports:
            with self.subTest(status=report['audit']['forecast_status']):
                text = Document(self.render(report)).text
                self.assertIn(report['audit']['forecast_status'], text)
                self.assertIn('Structural output slots Unknown', text)
                self.assertIn('No complete output forecast', text)
                self.assertIn('Diagnostics', text)

    def test_invalid_input_error_report_renders_without_fabricating_a_pass(self):
        report = {'schema_version': 2, 'error': {'code': 'invalid_input', 'message': 'Cannot open <input>'}}
        text = Document(self.render(report)).text
        self.assertIn('invalid_input', text)
        self.assertIn('Cannot open <input>', text)
        self.assertIn('Forecast unavailable', text)
        self.assertIn('Contract unverified', text)
        self.assertIn('Structural output slots Unknown', text)

    def test_broadcast_and_positional_assumptions_are_explicit(self):
        broadcast = envelope(array([1, 2], axis='x'), array([1, 2], axis='longitude'))
        text = Document(self.render(broadcast)).text
        self.assertIn('named_broadcast', text)
        self.assertIn('Broadcast dimensions', text)
        self.assertIn('Structural output slots 4', text)
        unindexed = ArraySnapshot('v', (Axis('x', 2**40),))
        text = Document(self.render(envelope(unindexed, unindexed))).text
        self.assertIn('positional', text)
        self.assertIn('Unindexed', text)
        self.assertIn(str(2**40), text)
        self.assertLess(len(text), 15000)

    def test_none_and_zero_remain_distinct(self):
        report = envelope(array([]), array([]))
        text = Document(self.render(report)).text
        self.assertIn('Structural output slots 0', text)
        self.assertIn('Excluded fraction Unknown', text)

    def test_invalid_arguments_are_rejected(self):
        for value in (-1, 1.5, '2', True, None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.render({}, sample_limit=value)
        for value in (None, [], 'report'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.render(value)
        with self.assertRaises(ValueError):
            self.render({'audit': None})


if __name__ == '__main__':
    unittest.main()
