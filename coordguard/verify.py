"""Independent, bounded checks of numeric-absolute-v1 proof objects.

This module never calls the matching solver or imports its graph enumerator.
Complete candidate edges are rebuilt with rational sliding windows. The result
establishes only the stated numeric relation, not scientific or unit validity.
"""
import hashlib
import json
import re

from .budget import Budget
from .errors import ResourceLimit, ValidationError
from .model import Axis, Label, representable_binary64


_CERTIFICATE_FIELDS = (
    'schema_version', 'profile', 'left_digest', 'right_digest', 'atol',
    'status', 'n_left', 'n_right', 'edge_count', 'edge_digest', 'witness',
)
_WITNESS_FIELDS = {
    'cardinality': ('kind', 'left_size', 'right_size'),
    'hall': ('kind', 'matching', 'left_set', 'neighbors', 'unmatched_left', 'unmatched_right'),
    'unique': ('kind', 'matching', 'topological_order'),
    'ambiguous': ('kind', 'matching', 'alternative'),
}
_WITNESS_STATUS = {
    'cardinality': 'impossible', 'hall': 'impossible',
    'unique': 'unique_within_tolerance', 'ambiguous': 'ambiguous',
}


class _EvidenceError(Exception):
    def __init__(self, reason, status='invalid'):
        self.reason, self.status = reason, status


def _require(condition, reason):
    if not condition:
        raise _EvidenceError(reason)


def _object(value, fields, reason):
    # Check cardinality before inspecting keys; never construct a set from an
    # untrusted object or iterate an arbitrarily large mapping.
    _require(type(value) is dict and len(value) == len(fields), reason)
    _require(all(key in value for key in fields), reason)


def _integer(value, lower, upper, reason):
    _require(type(value) is int and lower <= value <= upper, reason)


def _digest(value, reason):
    _require(type(value) is str and len(value) == 64 and
             re.fullmatch('[0-9a-f]{64}', value) is not None, reason)


def _indices(value, bound, budget, *, exact=False, partial=False, ordered=False):
    """Validate an array before allocating any set, index table, or copy."""
    _require(type(value) is list, 'invalid_witness_array')
    _require(len(value) == bound if exact else len(value) <= bound,
             'invalid_witness_array')
    budget.reserve(16 * len(value))
    previous = -1
    for index in value:
        budget.tick()
        if partial and index is None:
            continue
        _integer(index, 0, bound - 1, 'invalid_witness_index')
        if ordered:
            _require(index > previous, 'invalid_witness_order')
            previous = index


def _parse(certificate, n, m, budget):
    _object(certificate, _CERTIFICATE_FIELDS, 'invalid_certificate_schema')
    _require(type(certificate['schema_version']) is int and certificate['schema_version'] == 1,
             'invalid_schema_version')
    profile = certificate['profile']
    _require(type(profile) is str and len(profile) <= 128, 'invalid_profile')
    if profile != 'numeric-absolute-v1':
        raise _EvidenceError('unsupported_profile', 'unsupported')
    _integer(certificate['n_left'], 0, n, 'invalid_cardinalities')
    _integer(certificate['n_right'], 0, m, 'invalid_cardinalities')
    _require(certificate['n_left'] == n and certificate['n_right'] == m,
             'invalid_cardinalities')
    _digest(certificate['left_digest'], 'invalid_source_digest')
    _digest(certificate['right_digest'], 'invalid_source_digest')
    tolerance = certificate['atol']
    _object(tolerance, ('kind', 'value'), 'invalid_tolerance')
    _require(type(tolerance['kind']) is str and len(tolerance['kind']) <= 7 and
             type(tolerance['value']) is str and len(tolerance['value']) <= 79,
             'invalid_tolerance')
    budget.tick(80)
    try:
        tolerance = Label.from_dict(tolerance)
        _require(tolerance.numeric and tolerance.finite and representable_binary64(tolerance)
                 and tolerance.number() >= 0, 'invalid_tolerance')
    except ValidationError as exc:
        raise _EvidenceError('invalid_tolerance') from exc
    witness = certificate['witness']
    _require(type(witness) is dict and 1 <= len(witness) <= 6, 'invalid_witness_schema')
    kind = witness.get('kind')
    _require(type(kind) is str and len(kind) <= 11, 'invalid_witness_kind')
    _require(kind in _WITNESS_FIELDS, 'invalid_witness_kind')
    _object(witness, _WITNESS_FIELDS[kind], 'invalid_witness_schema')
    _require(type(certificate['status']) is str and
             certificate['status'] == _WITNESS_STATUS[kind], 'invalid_status')
    if kind == 'cardinality':
        _integer(witness['left_size'], 0, n, 'invalid_cardinality_witness')
        _integer(witness['right_size'], 0, m, 'invalid_cardinality_witness')
        _require(witness['left_size'] == n and witness['right_size'] == m and n != m,
                 'invalid_cardinality_witness')
        _require(certificate['edge_count'] is None and certificate['edge_digest'] is None,
                 'invalid_cardinality_edges')
    else:
        _require(n == m, 'invalid_cardinalities')
        _integer(certificate['edge_count'], 0, n * m, 'invalid_edge_count')
        _digest(certificate['edge_digest'], 'invalid_edge_digest')
        _indices(witness['matching'], n, budget, exact=True, partial=kind == 'hall')
        if kind == 'unique':
            _indices(witness['topological_order'], n, budget, exact=True)
        elif kind == 'ambiguous':
            _indices(witness['alternative'], n, budget, exact=True)
        else:
            for key in ('left_set', 'neighbors', 'unmatched_left', 'unmatched_right'):
                _indices(witness[key], n, budget, ordered=True)
    # Only fully shape-checked, shallow JSON values reach the encoder. Its
    # chunks bound certificate bytes without constructing another full string.
    budget.reserve(32768)
    input_bytes = 0
    encoder = json.JSONEncoder(sort_keys=True, separators=(',', ':'), ensure_ascii=True, allow_nan=False)
    for chunk in encoder.iterencode(certificate):
        budget.tick(len(chunk))
        input_bytes += len(chunk)
        if input_bytes > budget.limits.max_input_bytes:
            raise ResourceLimit('input_byte_limit', 'Certificate exceeds input byte limit')
    return tolerance.number(), witness


def _numbers(axis, budget):
    if axis.index_kind != 'ordinary1d':
        raise _EvidenceError('unsupported_index', 'unsupported')
    if axis.labels is None:
        raise _EvidenceError('unindexed_axis', 'unsupported')
    budget.reserve(65536 + 128 * axis.size)
    values = []
    for label in axis.labels:
        budget.add_label(label)
        budget.tick()
        if not label.numeric or not label.finite:
            raise _EvidenceError('unsupported_numeric_labels', 'unsupported')
        if not representable_binary64(label):
            raise _EvidenceError('unsupported_integer_profile', 'unsupported')
        values.append(label.number())
    return values


def _sorted_numbers(values, budget):
    budget.reserve(128 * len(values))
    budget.tick(max(1, len(values)) * max(1, len(values).bit_length()))
    ordered = sorted((value, index) for index, value in enumerate(values))
    budget.check()
    for index in range(1, len(ordered)):
        budget.tick()
        if ordered[index - 1][0] == ordered[index][0]:
            raise _EvidenceError('duplicate_coordinates', 'unsupported')
    return ordered


def _source_digest(axis, budget):
    # Numeric labels are short. Text metadata has model-level byte bounds;
    # charge serialization, escaping and transient JSON structures up front.
    budget.reserve(262144 + 512 * axis.size)
    text_size = len(axis.name)
    for value in axis.metadata.to_dict().values():
        if value is not None:
            text_size += len(value)
    budget.tick(6 * text_size + 128 * axis.size + 1)
    digest = axis.digest()
    budget.check()
    return digest


def _complete_edges(left, right, tolerance, budget):
    """Independently enumerate all eligible edges, preserving original indices.

    Both endpoints of the rational interval move monotonically over the
    sorted right axis. No rounded floating subtraction or dense matrix occurs.
    Row sorting restores the specified (original left, original right) digest
    order after window enumeration.
    """
    n = len(left)
    budget.reserve(128 * n)
    rows = [[] for _ in range(n)]
    low = high = 0
    for value, original_left in left:
        budget.tick()
        lower, upper = value - tolerance, value + tolerance
        while low < len(right):
            budget.tick()
            if right[low][0] >= lower:
                break
            low += 1
        if high < low:
            high = low
        while high < len(right):
            budget.tick()
            if right[high][0] > upper:
                break
            high += 1
        row = rows[original_left]
        for position in range(low, high):
            budget.add_edge()
            other, original_right = right[position]
            # The full exact predicate remains explicit rather than trusting
            # the interval enumeration itself as a claimed candidate list.
            _require(abs(value - other) <= tolerance, 'invalid_reconstructed_edge')
            row.append(original_right)
        budget.tick(max(1, len(row)) * max(1, len(row).bit_length()))
        row.sort()
        budget.check()
    digest = hashlib.sha256()
    for u, row in enumerate(rows):
        budget.tick()
        for v in row:
            budget.tick()
            digest.update(f'{u}:{v};'.encode('ascii'))
    return rows, digest.hexdigest()


def _matching(mapping, left, right, tolerance, budget, *, partial=False):
    n = len(left)
    budget.reserve(32 * n)
    inverse = [-1] * n
    count = 0
    for u, v in enumerate(mapping):
        budget.tick()
        if partial and v is None:
            continue
        # Index shape/type/range was checked before this allocation.
        _require(inverse[v] == -1, 'invalid_matching_bijection')
        _require(abs(left[u] - right[v]) <= tolerance, 'invalid_matching_edge')
        inverse[v] = u
        count += 1
    _require(partial or count == n, 'invalid_matching_bijection')
    return inverse, count


def _unique(witness, inverse, rows, budget):
    budget.reserve(32 * len(rows))
    rank = [-1] * len(rows)
    for position, vertex in enumerate(witness['topological_order']):
        budget.tick()
        _require(rank[vertex] == -1, 'invalid_topological_order')
        rank[vertex] = position
    for u, row in enumerate(rows):
        budget.tick()
        for v in row:
            budget.tick()
            partner = inverse[v]
            if partner != u:
                _require(rank[u] < rank[partner], 'invalid_uniqueness_witness')


def _hall(witness, inverse, count, rows, budget):
    n = len(rows)
    unmatched = 0
    for u, v in enumerate(witness['matching']):
        budget.tick()
        if v is None:
            _require(unmatched < len(witness['unmatched_left']) and
                     witness['unmatched_left'][unmatched] == u, 'invalid_unmatched_left')
            unmatched += 1
    _require(unmatched == len(witness['unmatched_left']), 'invalid_unmatched_left')
    unmatched = 0
    for v, u in enumerate(inverse):
        budget.tick()
        if u == -1:
            _require(unmatched < len(witness['unmatched_right']) and
                     witness['unmatched_right'][unmatched] == v, 'invalid_unmatched_right')
            unmatched += 1
    _require(unmatched == len(witness['unmatched_right']), 'invalid_unmatched_right')
    budget.reserve(16 * n)
    actual_neighbors = [False] * n
    for u in witness['left_set']:
        budget.tick()
        for v in rows[u]:
            budget.tick()
            actual_neighbors[v] = True
    found = 0
    for v, present in enumerate(actual_neighbors):
        budget.tick()
        if present:
            _require(found < len(witness['neighbors']) and
                     witness['neighbors'][found] == v, 'invalid_hall_neighbors')
            found += 1
    _require(found == len(witness['neighbors']), 'invalid_hall_neighbors')
    deficiency = len(witness['left_set']) - found
    _require(deficiency > 0, 'invalid_hall_deficiency')
    # Hall deficiency bounds every matching by n-deficiency. Equality with
    # this valid partial matching proves maximal cardinality independently.
    _require(count == n - deficiency, 'invalid_maximum_matching')


def _verify(left_axis, right_axis, certificate, budget):
    budget.tick()
    _require(type(left_axis) is Axis and type(right_axis) is Axis, 'invalid_axis')
    n, m = left_axis.size, right_axis.size
    tolerance, witness = _parse(certificate, n, m, budget)
    if n + m > budget.limits.max_labels:
        raise ResourceLimit('label_limit', 'Total inspected label limit exceeded')
    left, right = _numbers(left_axis, budget), _numbers(right_axis, budget)
    left_sorted, right_sorted = _sorted_numbers(left, budget), _sorted_numbers(right, budget)
    _require(_source_digest(left_axis, budget) == certificate['left_digest'], 'source_digest_mismatch')
    _require(_source_digest(right_axis, budget) == certificate['right_digest'], 'source_digest_mismatch')
    if witness['kind'] == 'cardinality':
        return
    previous_edges = budget.edges
    rows, edge_digest = _complete_edges(left_sorted, right_sorted, tolerance, budget)
    _require(budget.edges - previous_edges == certificate['edge_count'], 'edge_count_mismatch')
    _require(edge_digest == certificate['edge_digest'], 'edge_digest_mismatch')
    kind = witness['kind']
    inverse, count = _matching(witness['matching'], left, right, tolerance, budget, partial=kind == 'hall')
    if kind == 'unique':
        _unique(witness, inverse, rows, budget)
    elif kind == 'hall':
        _hall(witness, inverse, count, rows, budget)
    else:
        _matching(witness['alternative'], left, right, tolerance, budget)
        different = False
        for original, alternative in zip(witness['matching'], witness['alternative']):
            budget.tick()
            different = different or original != alternative
        _require(different, 'identical_ambiguity_witnesses')
    budget.check()


def verify_correspondence(left_axis, right_axis, certificate, limits=None, *, budget=None):
    """Return a strict verification result; malformed evidence never verifies.

    Resource limits bound certificate parsing, all inspected coordinates,
    sorting, reconstructed edges, witness work and cooperative elapsed time.
    Unknown profiles or ineligible axes are explicitly unsupported. Nothing is
    mutated, and no uniqueness assertion relies on a solver-produced edge set.
    An optional shared Budget preserves cumulative accounting and the original
    operation deadline; it is mutually exclusive with limits.
    """
    status, reason = 'verified', 'certificate_valid'
    try:
        if budget is not None:
            if type(budget) is not Budget or limits is not None:
                raise ValidationError('Supply either limits or a Budget, not both')
        else:
            budget = Budget(limits)
        _verify(left_axis, right_axis, certificate, budget)
    except _EvidenceError as exc:
        status, reason = exc.status, exc.reason
    except ResourceLimit as exc:
        status, reason = 'resource_limited', exc.code
    except ValidationError:
        status, reason = 'invalid', 'invalid_configuration'
    return {'schema_version': 1, 'status': status, 'valid': status == 'verified', 'reason': reason}
