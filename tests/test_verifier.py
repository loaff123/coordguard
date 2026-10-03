"""Hand-authored proofs checked against a separate quadratic Fraction oracle."""
import copy
from fractions import Fraction
import hashlib
import importlib.util
import inspect
import itertools
import math
import struct
import unittest

from coordguard.budget import Budget, Limits
from coordguard.model import Axis, Label, Metadata


# Each test asserts evidence acceptance/rejection, never a solver's opinion.
def rational(label):
    if label.kind == 'int':
        return Fraction(int(label.value), 1)
    fmt = '>f' if label.kind == 'float32' else '>d'
    number = struct.unpack(fmt, bytes.fromhex(label.value))[0]
    return Fraction(*number.as_integer_ratio())


def oracle_edges(left, right, tolerance):
    return [(u, v) for u, a in enumerate(left.labels)
            for v, b in enumerate(right.labels)
            if abs(rational(a) - rational(b)) <= rational(tolerance)]


def certificate(left, right, tolerance, witness, status=None, edges=None):
    status = status or {'unique': 'unique_within_tolerance',
                        'ambiguous': 'ambiguous', 'hall': 'impossible',
                        'cardinality': 'impossible'}[witness['kind']]
    if witness['kind'] == 'cardinality':
        count = digest = None
    else:
        edges = oracle_edges(left, right, tolerance) if edges is None else edges
        count = len(edges)
        digest = hashlib.sha256(''.join(f'{u}:{v};' for u, v in edges).encode('ascii')).hexdigest()
    return {'schema_version': 1, 'profile': 'numeric-absolute-v1',
            'left_digest': left.digest(), 'right_digest': right.digest(),
            'atol': tolerance.to_dict(), 'status': status,
            'n_left': left.size, 'n_right': right.size,
            'edge_count': count, 'edge_digest': digest, 'witness': witness}


def exhaustive_witness(left, right, tolerance):
    """Construct a tiny proof by exhaustive search, with no production helpers."""
    n = left.size
    edges = set(oracle_edges(left, right, tolerance))
    bijections = [list(p) for p in itertools.permutations(range(n))
                  if all((u, v) in edges for u, v in enumerate(p))]
    if len(bijections) > 1:
        return {'kind': 'ambiguous', 'matching': bijections[0], 'alternative': bijections[1]}
    if bijections:
        matching = bijections[0]
        for order in itertools.permutations(range(n)):
            if all(order.index(u) < order.index(matching.index(v))
                   for u, v in edges if matching[u] != v):
                return {'kind': 'unique', 'matching': matching, 'topological_order': list(order)}
        raise AssertionError('A unique matching must admit a topological order')
    maximum = [None] * n
    for candidate in itertools.product([None] + list(range(n)), repeat=n):
        used = [v for v in candidate if v is not None]
        if len(used) == len(set(used)) and all(v is None or (u, v) in edges for u, v in enumerate(candidate)):
            if len(used) > sum(v is not None for v in maximum):
                maximum = list(candidate)
    deficiency = sum(v is None for v in maximum)
    for bits in range(1, 1 << n):
        left_set = [u for u in range(n) if bits & (1 << u)]
        neighbors = sorted({v for u, v in edges if u in left_set})
        if len(left_set) - len(neighbors) == deficiency:
            return {'kind': 'hall', 'matching': maximum, 'left_set': left_set, 'neighbors': neighbors,
                    'unmatched_left': [u for u, v in enumerate(maximum) if v is None],
                    'unmatched_right': [v for v in range(n) if v not in maximum]}
    raise AssertionError('An impossible graph must admit a maximum-deficiency Hall set')


class VerifierTests(unittest.TestCase):
    def setUp(self):
        self.left = Axis.from_values('x', [0, 2])
        self.right = Axis.from_values('x', [0, 2])
        self.tol = Label.from_value(0.0)
        self.proof = certificate(self.left, self.right, self.tol,
                                 {'kind': 'unique', 'matching': [0, 1],
                                  'topological_order': [0, 1]})

    def verify(self, left=None, right=None, proof=None, limits=None):
        # Assert an absent implementation as a red test, rather than an import error.
        self.assertIsNotNone(importlib.util.find_spec('coordguard.verify'),
                             'independent certificate verifier has not been implemented')
        from coordguard.verify import verify_correspondence
        result = verify_correspondence(left or self.left, right or self.right,
                                       self.proof if proof is None else proof, limits)
        self.assertEqual(set(result), {'schema_version', 'status', 'valid', 'reason'})
        self.assertEqual(type(result['schema_version']), int)
        self.assertEqual(result['schema_version'], 1)
        self.assertEqual(type(result['valid']), bool)
        self.assertEqual(type(result['reason']), str)
        self.assertEqual(result['valid'], result['status'] == 'verified')
        return result

    def assertValid(self, left=None, right=None, proof=None, limits=None):
        self.assertEqual(self.verify(left, right, proof, limits)['status'], 'verified')

    def assertInvalid(self, proof, left=None, right=None):
        self.assertEqual(self.verify(left, right, proof)['status'], 'invalid')

    def test_handcrafted_unique_identity(self):
        self.assertValid()

    def test_empty_unique_bijection(self):
        a = Axis.from_values('x', [])
        proof = certificate(a, a, self.tol,
                            {'kind': 'unique', 'matching': [], 'topological_order': []})
        self.assertValid(a, a, proof)

    def test_permuted_bijection_with_alternative_forward_edge(self):
        a, b, t = Axis.from_values('x', [2, 0]), Axis.from_values('x', [0, 1]), Label.from_value(1)
        proof = certificate(a, b, t, {'kind': 'unique', 'matching': [1, 0],
                                      'topological_order': [1, 0]})
        self.assertValid(a, b, proof)
        proof['witness']['topological_order'] = [0, 1]
        self.assertInvalid(proof, a, b)

    def test_ambiguous_requires_two_real_distinct_bijections(self):
        a, b, t = Axis.from_values('x', [0, 1]), Axis.from_values('x', [2, 3]), Label.from_value(3)
        proof = certificate(a, b, t, {'kind': 'ambiguous', 'matching': [0, 1], 'alternative': [1, 0]})
        self.assertValid(a, b, proof)
        for alt in ([0, 1], [0, 0], [1, None], [True, 0], [-1, 0], [2, 0]):
            changed = copy.deepcopy(proof); changed['witness']['alternative'] = alt
            self.assertInvalid(changed, a, b)

    def test_impossible_cardinality_proof(self):
        b = Axis.from_values('x', [0])
        proof = certificate(self.left, b, self.tol,
                            {'kind': 'cardinality', 'left_size': 2, 'right_size': 1})
        self.assertValid(self.left, b, proof)
        for key, value in [('left_size', True), ('right_size', 2)]:
            changed = copy.deepcopy(proof); changed['witness'][key] = value
            self.assertInvalid(changed, self.left, b)
        changed = copy.deepcopy(proof); changed['edge_count'] = 0
        self.assertInvalid(changed, self.left, b)
        proof = certificate(self.left, self.right, self.tol,
                            {'kind': 'cardinality', 'left_size': 2, 'right_size': 2})
        self.assertInvalid(proof)

    def test_hall_certificate_proves_maximum_partial_matching(self):
        a, b, t = Axis.from_values('x', [0, 1, 4]), Axis.from_values('x', [0, 4, 9]), Label.from_value(1)
        proof = certificate(a, b, t, {'kind': 'hall', 'matching': [0, None, 1],
                                      'left_set': [0, 1], 'neighbors': [0],
                                      'unmatched_left': [1], 'unmatched_right': [2]})
        self.assertValid(a, b, proof)
        mutations = {'matching': [[None, None, 1], [0, None, 0], [0, 1, None]],
                     'left_set': [[0], [1, 0], [0, 0], [0, True]],
                     'neighbors': [[], [0, 1], [False]],
                     'unmatched_left': [[], [0], [1, 1]],
                     'unmatched_right': [[], [1], [True]]}
        for key, values in mutations.items():
            for value in values:
                changed = copy.deepcopy(proof); changed['witness'][key] = value
                with self.subTest(key=key, value=value): self.assertInvalid(changed, a, b)

    def test_empty_neighbor_hall_proof(self):
        a, b = Axis.from_values('x', [0]), Axis.from_values('x', [1])
        proof = certificate(a, b, self.tol, {'kind': 'hall', 'matching': [None],
                            'left_set': [0], 'neighbors': [], 'unmatched_left': [0], 'unmatched_right': [0]})
        self.assertValid(a, b, proof)

    def test_complete_edge_reconstruction_rejects_forged_sparse_graph(self):
        a, b, t = Axis.from_values('x', [0, 1]), Axis.from_values('x', [2, 3]), Label.from_value(3)
        proof = certificate(a, b, t, {'kind': 'unique', 'matching': [0, 1],
                                      'topological_order': [0, 1]}, edges=[(0, 0), (1, 1)])
        self.assertInvalid(proof, a, b)
        proof = certificate(a, b, t, proof['witness'])
        self.assertInvalid(proof, a, b)  # Honest edge digest cannot hide an alternating cycle.

    def test_edge_digest_and_count_are_both_checked(self):
        for key, value in [('edge_digest', '0' * 64), ('edge_count', 1), ('edge_count', True),
                           ('edge_count', -1), ('edge_digest', None)]:
            changed = copy.deepcopy(self.proof); changed[key] = value
            with self.subTest(key=key, value=value): self.assertInvalid(changed)

    def test_source_digests_bind_bits_order_name_and_metadata(self):
        variants = [Axis.from_values('x', [0, 3]), Axis.from_values('x', [2, 0]),
                    Axis.from_values('y', [0, 2]),
                    Axis.from_values('x', [0, 2], metadata=Metadata(units='m'))]
        for changed in variants: self.assertInvalid(self.proof, changed, self.right)
        for key in ('left_digest', 'right_digest'):
            changed = copy.deepcopy(self.proof); changed[key] = '0' * 64
            self.assertInvalid(changed)
        a, b = Axis.from_values('x', [0.0]), Axis.from_values('x', [-0.0])
        proof = certificate(a, b, self.tol, {'kind': 'unique', 'matching': [0], 'topological_order': [0]})
        self.assertValid(a, b, proof)
        self.assertInvalid(proof, b, b)

    def test_actual_matching_edges_are_checked(self):
        for mapping in ([1, 0], [0, 0], [None, 1], [True, 1], [-1, 1], [0, 2]):
            changed = copy.deepcopy(self.proof); changed['witness']['matching'] = mapping
            self.assertInvalid(changed)

    def test_topological_order_is_strict_permutation(self):
        for order in ([0], [0, 0], [False, 1], [-1, 1], [0, 2], [0, 1, 2]):
            changed = copy.deepcopy(self.proof); changed['witness']['topological_order'] = order
            self.assertInvalid(changed)

    def test_strict_schema_and_status_witness_consistency(self):
        for key, value in [('schema_version', True), ('schema_version', 2), ('n_left', True),
                           ('n_right', 3), ('status', 'impossible'), ('atol', {'kind': 'int', 'value': '-1'})]:
            changed = copy.deepcopy(self.proof); changed[key] = value
            with self.subTest(key=key, value=value): self.assertInvalid(changed)
        for target in ('root', 'witness', 'atol'):
            changed = copy.deepcopy(self.proof)
            obj = changed if target == 'root' else changed[target]
            obj['unknown'] = []
            self.assertInvalid(changed)
        for value in (None, [], False, {'schema_version': 1}):
            self.assertInvalid(value if value is not None else {})
        for key in self.proof:
            changed = copy.deepcopy(self.proof); del changed[key]
            self.assertInvalid(changed)

    def test_unknown_profile_is_explicitly_unsupported(self):
        changed = copy.deepcopy(self.proof); changed['profile'] = 'numeric-relative-v2'
        self.assertEqual(self.verify(proof=changed)['status'], 'unsupported')

    def test_unsupported_inputs_never_verify(self):
        axes = [Axis('x', 2), Axis('x', 2, self.left.labels, index_kind='custom'),
                Axis.from_values('x', [0, 0]), Axis.from_values('x', [0.0, -0.0]),
                Axis.from_values('x', [float('nan'), 1]), Axis.from_values('x', [float('inf'), 1]),
                Axis.from_values('x', ['a', 'b']), Axis.from_values('x', [2**53 + 1, 0])]
        for axis in axes:
            changed = copy.deepcopy(self.proof); changed['left_digest'] = axis.digest()
            with self.subTest(axis=axis): self.assertEqual(self.verify(axis, self.right, changed)['status'], 'unsupported')

    def test_relation_does_not_claim_semantic_compatibility(self):
        a = Axis.from_values('x', [0, 2], metadata=Metadata(units='m', role='space'))
        b = Axis.from_values('x', [0, 2], metadata=Metadata(units='degrees', role='space'))
        proof = certificate(a, b, self.tol, self.proof['witness'])
        self.assertValid(a, b, proof)

    def test_exact_float_boundaries_subnormals_and_overflow(self):
        cases = [(0.0, 5e-324, 5e-324), (1.0, math.nextafter(1.0, 2.0), 2**-52),
                 (-1e308, 1e308, 1e308), (0.0, math.nextafter(1.0, 2.0), 1.0),
                 (1.0, math.nextafter(0.0, -1.0), 1.0)]
        for av, bv, tv in cases:
            a, b, t = Axis.from_values('x', [av]), Axis.from_values('x', [bv]), Label.from_value(tv)
            edges = oracle_edges(a, b, t)
            witness = ({'kind': 'unique', 'matching': [0], 'topological_order': [0]} if edges else
                       {'kind': 'hall', 'matching': [None], 'left_set': [0], 'neighbors': [],
                        'unmatched_left': [0], 'unmatched_right': [0]})
            with self.subTest(av=av, bv=bv, tv=tv): self.assertValid(a, b, certificate(a, b, t, witness))

    def test_float32_and_large_exact_integer_profile(self):
        for a, b, tol in [(Axis.from_values('x', [0.1], kind='float32'), Axis.from_values('x', [0.1]), Label.from_value(1e-8)),
                          (Axis.from_values('x', [2**100]), Axis.from_values('x', [float(2**100)]), self.tol)]:
            proof = certificate(a, b, tol, {'kind': 'unique', 'matching': [0], 'topological_order': [0]})
            self.assertValid(a, b, proof)

    def test_invalid_tolerances(self):
        for tolerance in (Label.from_value(-1), Label.from_value(float('inf')), Label.from_value(float('nan')),
                          Label.from_value('0'), Label.from_value(2**53 + 1)):
            changed = copy.deepcopy(self.proof); changed['atol'] = tolerance.to_dict()
            self.assertInvalid(changed)
        for value in ({'kind': 'float64', 'value': '0' * 10000}, {'kind': [], 'value': '0'},
                      {'kind': 'int', 'value': True}, {'kind': 'int', 'value': '01'}):
            changed = copy.deepcopy(self.proof); changed['atol'] = value
            self.assertInvalid(changed)

    def test_resource_limits_never_report_verified(self):
        cases = [(Limits(max_labels=3), 'label_limit'), (Limits(max_edges=1), 'edge_limit'),
                 (Limits(max_coordinate_bytes=1), 'coordinate_byte_limit'),
                 (Limits(max_working_bytes=1), 'working_memory_limit'),
                 (Limits(max_steps=1), 'work_limit'), (Limits(deadline_seconds=0), 'deadline'),
                 (Limits(max_input_bytes=1), 'input_byte_limit')]
        for limits, reason in cases:
            result = self.verify(limits=limits)
            with self.subTest(reason=reason):
                self.assertEqual(result['status'], 'resource_limited')
                self.assertEqual(result['reason'], reason)

    def test_oversized_arrays_are_rejected_before_hashing_or_sets(self):
        for key in ('matching', 'topological_order'):
            changed = copy.deepcopy(self.proof); changed['witness'][key] = [{}] * 10000
            self.assertInvalid(changed)
        changed = copy.deepcopy(self.proof); changed['witness']['matching'] = [{}, 1]
        self.assertInvalid(changed)
        changed = copy.deepcopy(self.proof); changed['witness']['matching'] = (0, 1)
        self.assertInvalid(changed)

    def test_consistent_nonmaximum_partial_matching_is_rejected(self):
        a, b, t = Axis.from_values('x', [0, 1, 4]), Axis.from_values('x', [0, 4, 9]), Label.from_value(1)
        proof = certificate(a, b, t, {'kind': 'hall', 'matching': [None, None, 1],
                                      'left_set': [0, 1], 'neighbors': [0],
                                      'unmatched_left': [0, 1], 'unmatched_right': [0, 2]})
        result = self.verify(a, b, proof)
        self.assertEqual(result['status'], 'invalid')
        self.assertEqual(result['reason'], 'invalid_maximum_matching')
        # Omitting a neighbor would make this suboptimal matching falsely meet
        # the claimed deficiency bound, unless neighbor completeness is checked.
        proof['witness']['neighbors'] = []
        result = self.verify(a, b, proof)
        self.assertEqual(result['status'], 'invalid')
        self.assertEqual(result['reason'], 'invalid_hall_neighbors')

    def test_ambiguous_alternative_must_use_actual_edges(self):
        proof = certificate(self.left, self.right, self.tol,
                            {'kind': 'ambiguous', 'matching': [0, 1], 'alternative': [1, 0]})
        result = self.verify(proof=proof)
        self.assertEqual(result['status'], 'invalid')
        self.assertEqual(result['reason'], 'invalid_matching_edge')

    def test_exhaustive_independent_witnesses_and_window_edges(self):
        checked = 0
        for n in range(5):
            for av in itertools.combinations((-2, -1, 0, 2), n):
                for bv in itertools.combinations((-1, 0, 1, 3), n):
                    for tv in (0, 0.5, 1, 2):
                        a = Axis.from_values('x', list(reversed(av)))
                        b = Axis.from_values('x', list(bv[1:] + bv[:1]))
                        t = Label.from_value(tv)
                        witness = exhaustive_witness(a, b, t)
                        with self.subTest(a=av, b=bv, tol=tv):
                            self.assertValid(a, b, certificate(a, b, t, witness))
                        checked += 1
        self.assertEqual(checked, 280)

    def test_cardinality_proof_obeys_source_limits_without_edge_enumeration(self):
        a, b = Axis.from_values('x', [0]), Axis.from_values('x', [])
        proof = certificate(a, b, self.tol, {'kind': 'cardinality', 'left_size': 1, 'right_size': 0})
        self.assertValid(a, b, proof, Limits(max_edges=0))
        result = self.verify(a, b, proof, Limits(max_labels=0))
        self.assertEqual(result['status'], 'resource_limited')
        self.assertEqual(result['reason'], 'label_limit')

    def test_malformed_witnesses_and_all_hall_array_lengths(self):
        for witness in ([], None, {'kind': []}, {'kind': 'bogus'}, {'kind': 'unique'}, {'kind': 'x' * 10000}):
            proof = copy.deepcopy(self.proof); proof['witness'] = witness
            self.assertInvalid(proof)
        a, b = Axis.from_values('x', [0]), Axis.from_values('x', [1])
        proof = certificate(a, b, self.tol, {'kind': 'hall', 'matching': [None],
                            'left_set': [0], 'neighbors': [], 'unmatched_left': [0], 'unmatched_right': [0]})
        for field in ('left_set', 'neighbors', 'unmatched_left', 'unmatched_right'):
            changed = copy.deepcopy(proof); changed['witness'][field] = [{}] * 10000
            self.assertInvalid(changed, a, b)
        proof = certificate(self.left, self.right, self.tol,
                            {'kind': 'ambiguous', 'matching': [0, 1], 'alternative': [{}] * 10000})
        self.assertInvalid(proof)

    def verify_with_budget(self, budget, limits=None):
        from coordguard.verify import verify_correspondence
        self.assertIn('budget', inspect.signature(verify_correspondence).parameters,
                      'verification must accept a shared operation Budget')
        return verify_correspondence(self.left, self.right, self.proof, limits, budget=budget)

    def test_expired_existing_budget_cannot_verify(self):
        existing = Budget(Limits(deadline_seconds=0))
        result = self.verify_with_budget(existing)
        self.assertEqual(result, {'schema_version': 1, 'status': 'resource_limited',
                                  'valid': False, 'reason': 'deadline'})

    def test_shared_budget_keeps_cumulative_edge_and_label_accounting(self):
        existing = Budget(Limits(max_labels=8, max_edges=4))
        self.assertEqual(self.verify_with_budget(existing)['status'], 'verified')
        self.assertEqual((existing.labels, existing.edges), (4, 2))
        self.assertEqual(self.verify_with_budget(existing)['status'], 'verified')
        self.assertEqual((existing.labels, existing.edges), (8, 4))
        self.assertEqual(self.verify_with_budget(existing)['status'], 'resource_limited')

    def test_shared_budget_remaining_edge_allowance_is_enforced(self):
        existing = Budget(Limits(max_edges=2))
        existing.add_edge()
        result = self.verify_with_budget(existing)
        self.assertEqual(result['status'], 'resource_limited')
        self.assertEqual(result['reason'], 'edge_limit')

    def test_budget_and_limits_are_mutually_exclusive_and_strict(self):
        result = self.verify_with_budget(Budget(), Limits())
        self.assertEqual(result, {'schema_version': 1, 'status': 'invalid',
                                  'valid': False, 'reason': 'invalid_configuration'})
        result = self.verify_with_budget({})
        self.assertEqual(result['status'], 'invalid')
        self.assertEqual(result['reason'], 'invalid_configuration')

    def test_caller_data_stays_unchanged(self):
        before = copy.deepcopy(self.proof)
        self.assertValid()
        self.assertEqual(self.proof, before)


if __name__ == '__main__':
    unittest.main()
