"""Bounded operation-evidence checks independent of the operation producer.

Source keys, complete coverage, declared join order, and Cartesian cardinalities
are checked directly. Numeric correspondence proofs go through the independent
certificate verifier. This module imports neither a join producer nor a solver.
Auxiliary-coordinate annotations are admitted only as conservative blockers; the
NetCDF envelope verifier separately binds those annotations to source replay.
"""
from math import prod

from .budget import Budget
from .errors import ResourceLimit, ValidationError
from .model import ArraySnapshot, Contract, Label, representable_binary64
from .verify import verify_correspondence

_RUNTIME = 'xarray-netcdf-2025.10.1-v1'
_JOINS = ('inner', 'outer', 'left', 'right', 'exact')
_RESOURCE_REASONS = ('deadline', 'work_limit', 'working_memory_limit', 'label_limit',
                     'coordinate_byte_limit', 'edge_limit', 'input_byte_limit')
_REPORT_FIELDS = ('schema_version', 'profile', 'operation', 'join', 'forecast_status',
                  'contract_status', 'left_digest', 'right_digest', 'contract', 'axes',
                  'output', 'impact', 'violations', 'semantic_blockers', 'diagnostics',
                  'limits', 'resources', 'claims')
_AXIS_FIELDS = ('name', 'left_size', 'right_size', 'mode', 'status', 'reason',
                'output_size', 'labels', 'left_indexer', 'right_indexer', 'left_dropped',
                'right_dropped', 'left_duplicates', 'right_duplicates',
                'left_reordered', 'right_reordered', 'correspondence_status', 'correspondence')
_UNSUPPORTED_AUXILIARY = ('auxiliary_unsafe_mixed_integer_float',
    'auxiliary_unsafe_integer_dtype_promotion', 'auxiliary_unsafe_integer_fill_promotion',
    'auxiliary_mixed_datetime_resolutions', 'auxiliary_incompatible_label_families')
_SPATIAL_ROLES = ('spatial', 'latitude', 'longitude', 'projection_x_coordinate', 'projection_y_coordinate')


class _Invalid(Exception):
    pass


def _require(condition, reason):
    if not condition:
        raise _Invalid(reason)


def _object(value, fields, reason):
    _require(type(value) is dict and len(value) == len(fields), reason)
    _require(all(field in value for field in fields), reason)


def _equal(actual, expected, budget, reason):
    """Type-strict comparison bounded by trusted expected shape, not input depth."""
    budget.tick()
    _require(type(actual) is type(expected), reason)
    if type(expected) is dict:
        _object(actual, expected, reason)
        for key in expected:
            _equal(actual[key], expected[key], budget, reason)
    elif type(expected) is list:
        _require(len(actual) == len(expected), reason)
        for a, b in zip(actual, expected):
            _equal(a, b, budget, reason)
    else:
        _require(actual == expected, reason)


def _source(axis, budget, runtime):
    """Normalize admitted source labels once, retaining every original position."""
    if axis is None:
        return {'axis': None, 'keys': None, 'positions': {}, 'error': None}
    budget.reserve(65536 + 768 * (0 if axis.labels is None else axis.size))
    error = None
    meta = axis.metadata
    decoded_time = (runtime and meta.time_encoding in ('datetime64[s]', 'datetime64[ms]',
                    'datetime64[us]', 'datetime64[ns]') and axis.labels is not None)
    if decoded_time:
        for label in axis.labels:
            budget.tick()
            if label.kind != 'datetime':
                decoded_time = False
                break
    if axis.index_kind != 'ordinary1d':
        error = 'unsupported_index_kind'
    elif decoded_time and meta.calendar not in (None,'standard','gregorian','proleptic_gregorian'):
        error = 'unsupported_calendar'
    elif not decoded_time and (meta.calendar is not None or meta.time_encoding is not None
                              or meta.role == 'time' or (meta.units and ' since ' in meta.units)):
        error = 'time_semantics_not_implemented'
    if error or axis.labels is None:
        return {'axis': axis, 'keys': None, 'positions': {}, 'error': error}
    keys, positions, families = [], {}, set()
    has_float = unsafe_int = False
    for index, label in enumerate(axis.labels):
        budget.tick()
        if not label.finite:
            error = 'nonfinite_or_null_label'
            break
        if label.kind == 'datetime' and not decoded_time:
            error = 'datetime_not_implemented'
            break
        key = label.key()
        keys.append(key)
        positions.setdefault(key, []).append(index)
        families.add(key[0])
        has_float |= label.kind in ('float32', 'float64')
        unsafe_int |= label.kind == 'int' and not representable_binary64(label)
    if not error:
        if len(families) > 1:
            error = 'mixed_string_numeric_index'
        elif has_float and unsafe_int:
            error = 'unsafe_mixed_integer_float'
    return {'axis': axis, 'keys': None if error else keys, 'positions': positions,
            'error': error, 'has_float': has_float, 'unsafe_int': unsafe_int}


def _duplicates(source, budget):
    axis = source['axis']
    result = []
    for positions in source['positions'].values():
        budget.tick()
        if len(positions) > 1:
            result.append({'label': axis.labels[positions[0]].to_dict(),
                           'positions': positions, 'multiplicity': len(positions)})
    return result


def _classification(a, b, join, runtime):
    """Classify operation admission before inspecting any claimed result rows."""
    left, right, lk, rk = a['axis'], b['axis'], a['keys'], b['keys']
    if a['error'] or b['error']:
        return None, 'unsupported', a['error'] or b['error']
    if left is None or right is None:
        return 'named_broadcast', 'complete', None
    if lk is None or rk is None:
        if left.size != right.size:
            return None, 'operation_error', 'positional_size_mismatch'
        return ('positional' if lk is None and rk is None else 'positional_with_index'), 'complete', None
    def source_family(axis,keys):
        if keys:return keys[0][0]
        dtype=axis.source_dtype or ''
        if dtype.startswith('datetime64[') or (axis.metadata.time_encoding or '').startswith('datetime64['):return 'datetime'
        if dtype.startswith(('int','uint','float')):return 'number'
        if dtype=='object' or dtype.startswith(('<U','>U','U','str')):return 'str'
        return None
    lf,rf=source_family(left,lk),source_family(right,rk)
    if lf is not None and rf is not None and lf!=rf:
        return None, 'unsupported', 'incompatible_label_families'
    if runtime and left.metadata.time_encoding and right.metadata.time_encoding and left.metadata.time_encoding != right.metadata.time_encoding:
        return None, 'unsupported', 'mixed_datetime_resolutions'
    signed = lambda dtype: dtype is not None and dtype.startswith('i')
    unsigned = lambda dtype: dtype is not None and dtype.startswith('u')
    if runtime and ((signed(left.source_dtype) and unsigned(right.source_dtype)) or
                    (unsigned(left.source_dtype) and signed(right.source_dtype))):
        return None, 'unsupported', 'unsafe_integer_dtype_promotion'
    if (a.get('has_float') or b.get('has_float')) and (a.get('unsafe_int') or b.get('unsafe_int')):
        return None, 'unsupported', 'unsafe_mixed_integer_float'
    if join == 'exact' and lk != rk:
        return None, 'operation_error', 'exact_indexes_differ'
    if lk == rk:
        return 'identical_index', 'complete', None
    if len(a['positions']) != len(lk) or len(b['positions']) != len(rk):
        return None, ('operation_error' if runtime else 'unsupported'), 'nonidentical_duplicate_indexes'
    return 'indexed_join', 'complete', None


def _join_keys(a, b, join, runtime, budget):
    """Construct complete declared order, independent of any reported indexer."""
    left, right = a['keys'], b['keys']
    budget.reserve(128 * (len(left) + len(right)))
    budget.tick(len(left) + len(right))
    if join == 'left':
        return left
    if join == 'right':
        return right
    if join == 'inner':
        return [key for key in left if key in b['positions']]
    if runtime and (not left or not right):
        return left if left else right
    # Deduplicate with the already complete right source map rather than any
    # claimed join membership. Sorting follows the declared outer-join profile.
    union = left + [key for key in right if key not in a['positions']]
    budget.tick(max(1, len(union)) * max(1, len(union).bit_length()))
    union.sort()
    budget.check()
    return union


def _promoted_kind(a, b, join, runtime):
    if not runtime or join not in ('inner', 'outer'):
        return None
    kinds = set()
    for source in (a, b):
        kinds.update(label.kind for label in source['axis'].labels)
        if (source['axis'].source_dtype or '').startswith(('int','uint')):
            kinds.add('int')
        if source['axis'].source_dtype in ('float32', 'float64'):
            kinds.add(source['axis'].source_dtype)
    if not kinds.intersection(('float32', 'float64')):
        return None
    small = all(source['axis'].source_dtype in ('int8', 'uint8', 'int16', 'uint16', 'float32')
                for source in (a, b))
    return 'float64' if 'float64' in kinds or ('int' in kinds and not small) else 'float32'


def _indexed_rows(fact, a, b, join, runtime, budget):
    joined = _join_keys(a, b, join, runtime, budget)
    size = len(joined)
    _equal(fact['output_size'], size, budget, 'axis_output_size_mismatch')
    _require(type(fact['labels']) is list and len(fact['labels']) == size, 'joined_labels_mismatch')
    for side in ('left', 'right'):
        _require(type(fact[side + '_indexer']) is list and len(fact[side + '_indexer']) == size,
                 'indexer_cardinality_mismatch')
    budget.reserve(256 * (a['axis'].size + b['axis'].size + size))
    retained = {'left': set(), 'right': set()}
    previous = {'left': None, 'right': None}
    reordered = {'left': False, 'right': False}
    promoted = _promoted_kind(a, b, join, runtime)
    for row, key in enumerate(joined):
        budget.tick()
        for side, source in (('left', a), ('right', b)):
            positions = source['positions'].get(key)
            expected = positions[0] if positions else None
            _equal(fact[side + '_indexer'][row], expected, budget, 'indexer_relation_or_order_mismatch')
            if expected is not None:
                retained[side].add(expected)
                if previous[side] is not None and expected < previous[side]:
                    reordered[side] = True
                previous[side] = expected
        source = b if join == 'right' or key not in a['positions'] else a
        label = source['axis'].labels[source['positions'][key][0]]
        if promoted:
            label = Label.from_value(float(label.number()), promoted)
        _equal(fact['labels'][row], label.to_dict(), budget, 'joined_labels_mismatch')
    for side, source in (('left', a), ('right', b)):
        dropped = []
        for index, label in enumerate(source['axis'].labels):
            budget.tick()
            if index not in retained[side]:
                dropped.append({'index': index, 'label': label.to_dict()})
        _equal(fact[side + '_dropped'], dropped, budget, 'dropped_source_positions_mismatch')
        _equal(fact[side + '_reordered'], reordered[side], budget, 'reordered_flag_mismatch')
    return size, {side: len(indices) for side, indices in retained.items()}


def _axis(fact, name, a, b, join, runtime, budget):
    _object(fact, _AXIS_FIELDS, 'invalid_axis_fact_schema')
    left, right = a['axis'], b['axis']
    _equal(fact['name'], name, budget, 'axis_order_or_name_mismatch')
    for side, source in (('left', a), ('right', b)):
        axis = source['axis']
        _equal(fact[side + '_size'], None if axis is None else axis.size, budget, 'source_size_mismatch')
        expected = [] if a['error'] or b['error'] else _duplicates(source, budget)
        _equal(fact[side + '_duplicates'], expected, budget, 'duplicate_evidence_mismatch')
    mode, status, reason = _classification(a, b, join, runtime)
    _equal(fact['mode'], mode, budget, 'axis_mode_mismatch')
    # A CRS mismatch can invalidate an otherwise structurally complete relation.
    if left is not None and right is not None and left.metadata.crs != right.metadata.crs:
        status, reason = 'unsupported', 'crs_compatibility_not_implemented'
    _equal(fact['status'], status, budget, 'axis_status_mismatch')
    _equal(fact['reason'], reason, budget, 'axis_reason_mismatch')
    if mode is None:
        for field in ('output_size', 'labels', 'left_indexer', 'right_indexer',
                      'left_dropped', 'right_dropped', 'left_reordered', 'right_reordered'):
            _equal(fact[field], None, budget, 'fabricated_incomplete_axis_output')
        return None, {'left': 0, 'right': 0}, status
    if mode == 'indexed_join':
        size, retained = _indexed_rows(fact, a, b, join, runtime, budget)
        return size, retained, status
    own = left if left is not None else right
    if mode == 'identical_index':
        labels_from = right if join == 'right' and not runtime else left
    elif mode == 'positional_with_index':
        labels_from = left if left.indexed else right
    else:
        labels_from = own
    size = own.size
    _equal(fact['output_size'], size, budget, 'axis_output_size_mismatch')
    if labels_from.labels is None:
        _equal(fact['labels'], None, budget, 'joined_labels_mismatch')
    else:
        _require(type(fact['labels']) is list and len(fact['labels']) == size, 'joined_labels_mismatch')
        for actual, label in zip(fact['labels'], labels_from.labels):
            _equal(actual, label.to_dict(), budget, 'joined_labels_mismatch')
    for side, axis in (('left', left), ('right', right)):
        idx = {'kind': 'broadcast'} if axis is None else {'kind': 'identity', 'size': size}
        _equal(fact[side + '_indexer'], idx, budget, 'symbolic_indexer_mismatch')
        _equal(fact[side + '_dropped'], [], budget, 'dropped_source_positions_mismatch')
        _equal(fact[side + '_reordered'], False, budget, 'reordered_flag_mismatch')
    return size, {side: (size if axis is not None else 0) for side, axis in (('left', left), ('right', right))}, status


def _diagnostic(code, dimension=None, side=None):
    item = {'code': code}
    if dimension is not None:
        item['dimension'] = dimension
    if side is not None:
        item['side'] = side
    return item


def _semantics(name, left, right, requested, declarations):
    if left is None or right is None:
        return [_diagnostic('correspondence_needs_shared_dimension', name)] if requested else []
    result = []
    for field in ('units', 'location', 'crs', 'calendar', 'role'):
        if field == 'units' and all(axis.metadata.time_encoding and axis.metadata.time_encoding.startswith('datetime64[')
                                    for axis in (left, right)):
            continue
        a, b = getattr(left.metadata, field), getattr(right.metadata, field)
        if field == 'calendar' and left.metadata.time_encoding and right.metadata.time_encoding:
            a, b = a or 'standard', b or 'standard'
        if a is not None and b is not None and a != b:
            result.append(_diagnostic(field + '_mismatch', name))
    if requested and any(axis.metadata.role in _SPATIAL_ROLES for axis in (left, right)):
        if name not in declarations and any(axis.metadata.units is None or axis.metadata.location is None for axis in (left, right)):
            result.append(_diagnostic('spatial_same_axis_declaration_required', name))
    return result


def _numeric_exclusion(source, budget):
    axis = source['axis']
    if axis.labels is None or axis.index_kind != 'ordinary1d':
        return 'index_profile'
    seen = set()
    budget.reserve(128 * axis.size)
    for label in axis.labels:
        budget.tick()
        if not representable_binary64(label):
            return 'nonfinite_or_nonbinary64_numeric'
        number = label.number()
        if number in seen:
            return 'duplicate_labels'
        seen.add(number)
    return None


def _correspondence(fact, a, b, tolerance, blockers, budget, limited):
    evidence = fact['correspondence']
    claimed = fact['correspondence_status']
    if tolerance is None:
        expected = 'exact' if fact['mode'] == 'identical_index' else 'not_requested'
        _equal(claimed, expected, budget, 'unrequested_correspondence_status')
        _equal(evidence, None, budget, 'unrequested_correspondence_evidence')
        return None
    if blockers or a['axis'] is None or b['axis'] is None or fact['status'] == 'unsupported':
        _equal(claimed, 'unsupported', budget, 'blocked_correspondence_status')
        _equal(evidence, None, budget, 'blocked_correspondence_evidence')
        return None
    # A cap may interrupt before requesting this axis's proof. This is only
    # admissible in a report already marked non-certifying and output-free.
    if limited and evidence is None:
        expected = 'exact' if fact['mode'] == 'identical_index' else 'not_requested'
        _equal(claimed, expected, budget, 'interrupted_correspondence_status')
        return None
    if limited and type(evidence) is dict and evidence.get('status') == 'resource_limited':
        _object(evidence, ('schema_version', 'status', 'reason', 'certificate'), 'invalid_limited_correspondence')
        _equal(evidence['schema_version'], 1, budget, 'invalid_limited_correspondence')
        _require(type(evidence['reason']) is str and evidence['reason'] in _RESOURCE_REASONS, 'invalid_resource_reason')
        _equal(evidence['certificate'], None, budget, 'fabricated_limited_certificate')
        _equal(claimed, 'resource_limited', budget, 'correspondence_status_mismatch')
        return None
    reason = _numeric_exclusion(a, budget) or _numeric_exclusion(b, budget)
    if reason:
        _equal(claimed, 'unsupported', budget, 'correspondence_status_mismatch')
        _equal(evidence, {'schema_version': 1, 'status': 'unsupported', 'reason': reason, 'certificate': None},
               budget, 'unsupported_correspondence_evidence')
        return 'correspondence_' + reason
    _require(type(evidence) is dict, 'missing_correspondence_certificate')
    _equal(evidence.get('atol'), tolerance.to_dict(), budget, 'certificate_tolerance_mismatch')
    checked = verify_correspondence(a['axis'], b['axis'], evidence, budget=budget)
    _require(checked['valid'], 'invalid_correspondence_certificate:' + checked['reason'])
    _equal(claimed, evidence['status'], budget, 'correspondence_status_mismatch')
    return 'correspondence_' + claimed if claimed in ('impossible', 'ambiguous') else None


def _annotations(report, budget):
    """Separate only documented adapter additions; replay verifies their truth."""
    additions = {'semantic_blockers': [], 'violations': [], 'diagnostics': []}
    for field in additions:
        value = report[field]
        _require(type(value) is list and len(value) <= 4096, 'invalid_' + field)
        budget.reserve(1024 * len(value))
        for item in value:
            budget.tick()
            _require(type(item) is dict and 1 <= len(item) <= 3, 'invalid_' + field)
            code = item.get('code')
            _require(type(code) is str and len(code) <= 128, 'invalid_' + field)
            if field == 'semantic_blockers' and code in ('multidimensional_auxiliary_result',
                    'selected_units_mismatch', 'selected_standard_name_mismatch',
                    'selected_location_mismatch', 'selected_crs_mismatch'):
                _object(item, ('code',), 'invalid_auxiliary_annotation')
                additions[field].append(item)
            elif field == 'semantic_blockers' and code in ('auxiliary_units_mismatch',
                    'auxiliary_role_mismatch', 'auxiliary_location_mismatch',
                    'auxiliary_crs_mismatch', 'auxiliary_calendar_mismatch') + _UNSUPPORTED_AUXILIARY:
                _object(item, ('code', 'coordinate'), 'invalid_auxiliary_annotation')
                _require(type(item['coordinate']) is str and 0 < len(item['coordinate']) <= 4096, 'invalid_auxiliary_annotation')
                additions[field].append(item)
            elif (field, code) in (('violations', 'dropped_auxiliary_coordinate'), ('diagnostics', 'conflicting_auxiliary_dropped')):
                _object(item, ('code', 'coordinate'), 'invalid_auxiliary_annotation')
                _require(type(item['coordinate']) is str and 0 < len(item['coordinate']) <= 4096, 'invalid_auxiliary_annotation')
                additions[field].append(item)
    return additions


def _impact(snapshot, names, dimensions, total, side):
    axes = {axis.name: axis for axis in snapshot.axes}
    retained, contributed, broadcast = 1, 1, []
    for name, size, counts in zip(names, dimensions['sizes'], dimensions['retained']):
        if name in axes:
            retained *= counts[side]
            contributed *= counts[side]
        else:
            broadcast.append(name)
            contributed *= size
            if size == 0:
                retained = 0
    original = prod(axis.size for axis in snapshot.axes)
    excluded = original - retained
    return {'input_positions': original, 'retained_source_positions': retained,
            'excluded_source_positions': excluded, 'contributing_output_positions': contributed,
            'fill_output_positions': total - contributed, 'broadcast_dimensions': broadcast,
            'excluded_fraction': None if original == 0 else {'numerator': excluded, 'denominator': original}}


def _check(left, right, report, budget):
    _require(type(left) is ArraySnapshot and type(right) is ArraySnapshot, 'invalid_source_snapshot')
    _object(report, _REPORT_FIELDS, 'invalid_report_schema')
    _equal(report['schema_version'], 1, budget, 'invalid_report_version')
    _equal(report['profile'], 'coordguard-core-v1', budget, 'invalid_report_profile')
    _equal(report['operation'], 'binary', budget, 'invalid_operation')
    _require(type(report['join']) is str and report['join'] in _JOINS, 'invalid_join')
    status = report['forecast_status']
    _require(type(status) is str and status in ('complete', 'operation_error', 'unsupported', 'resource_limited'), 'invalid_forecast_status')
    limited = status == 'resource_limited'
    contract = Contract.from_dict(report['contract'])
    runtime = left.decoding_profile == right.decoding_profile == _RUNTIME
    _equal(report['claims'], {'payload_inspected': False, 'physical_equivalence': False,
                             'xarray_runtime_validated': runtime}, budget, 'invalid_claims')
    # The two snapshots, not a report array, bound source work and allocations.
    source_maps = []
    blockers, diagnostics, violations = [], [], []
    expected_status = 'complete'
    for side, snapshot in (('left', left), ('right', right)):
        budget.reserve(262144 + sum(512 * axis.size for axis in snapshot.axes if axis.indexed))
        for axis in snapshot.axes:
            budget.inspect_axis(axis, ('operation_check', side, axis.name))
        if not limited or report[side + '_digest'] is not None:
            _equal(report[side + '_digest'], snapshot.digest(), budget, 'source_digest_mismatch')
        if snapshot.decoding_profile not in ('typed-manifest-v1', _RUNTIME):
            expected_status = 'unsupported'
            diagnostics.append(_diagnostic('unsupported_decoding_profile', side=side))
        for blocker in snapshot.semantic_blockers:
            blockers.append({'code': 'declared_unmodeled_semantics', 'detail': blocker, 'side': side})
            expected_status = 'unsupported'
        source_maps.append({axis.name: _source(axis, budget, runtime) for axis in snapshot.axes})
    a, b = source_maps
    names = list(a) + [name for name in b if name not in a]
    tolerances = dict(contract.tolerances)
    _require(all(name in names for name in tolerances) and all(name in names for name in contract.same_axis_declarations),
             'contract_dimension_absent')
    facts = report['axes']
    _require(type(facts) is list and (len(facts) <= len(names) if limited else len(facts) == len(names)), 'axis_coverage_mismatch')
    additions = _annotations(report, budget)
    dimensions = {'sizes': [], 'retained': []}
    absent = {'axis': None, 'keys': None, 'positions': {}, 'error': None}
    for name, fact in zip(names, facts):
        sa, sb = a.get(name, absent), b.get(name, absent)
        size, retained, axis_status = _axis(fact, name, sa, sb, report['join'], runtime, budget)
        dimensions['sizes'].append(size)
        dimensions['retained'].append(retained)
        semantic = _semantics(name, sa['axis'], sb['axis'], name in tolerances, contract.same_axis_declarations)
        blockers.extend(semantic)
        if axis_status != 'complete':
            diagnostics.append(_diagnostic(fact['reason'], name))
            if expected_status != 'unsupported':
                expected_status = axis_status
        correspondence = _correspondence(fact, sa, sb, tolerances.get(name), semantic, budget, limited)
        if correspondence:
            target = violations if fact['correspondence_status'] in ('impossible', 'ambiguous') else blockers
            target.append(_diagnostic(correspondence, name))
        if not contract.allow_duplicates:
            for side in ('left', 'right'):
                if fact[side + '_duplicates']:
                    violations.append(_diagnostic('duplicate_labels', name, side))
    if limited:
        _equal(report['contract_status'], 'unverified', budget, 'limited_report_cannot_certify')
        _equal(report['output'], None, budget, 'fabricated_limited_output')
        _equal(report['impact'], None, budget, 'fabricated_limited_impact')
        _require(any(item.get('code') in _RESOURCE_REASONS for item in report['diagnostics']), 'missing_resource_diagnostic')
        return
    if expected_status == 'complete':
        total = prod(dimensions['sizes'])
        output = {'dimensions': names, 'sizes': dimensions['sizes'], 'total_positions': total}
        impact = {side: _impact(snapshot, names, dimensions, total, side)
                  for side, snapshot in (('left', left), ('right', right))}
        for side in ('left', 'right'):
            for field, allowed, code in (('excluded_source_positions', contract.allow_drops, 'dropped_positions'),
                                          ('fill_output_positions', contract.allow_fills, 'fill_positions'),
                                          ('broadcast_dimensions', contract.allow_broadcast, 'named_dimension_broadcast')):
                if impact[side][field] and not allowed:
                    violations.append(_diagnostic(code, side=side))
        contract_status = 'unverified' if blockers or any(f['correspondence_status'] == 'unsupported' for f in facts) else 'fail' if violations else 'pass'
    else:
        output = impact = None
        contract_status = 'fail' if expected_status == 'operation_error' else 'unverified'
    if additions['semantic_blockers'] and expected_status == 'complete':
        contract_status = 'unverified'
    if any(item['code'] == 'multidimensional_auxiliary_result' or item['code'] in _UNSUPPORTED_AUXILIARY for item in additions['semantic_blockers']):
        _require(expected_status == 'complete', 'auxiliary_result_without_complete_alignment')
        expected_status, contract_status, output, impact = 'unsupported', 'unverified', None, None
    _require(not additions['violations'] or not contract.allow_drops, 'allowed_auxiliary_drop_marked_violation')
    if additions['violations'] and contract_status == 'pass':
        contract_status = 'fail'
    _equal(report['forecast_status'], expected_status, budget, 'forecast_status_mismatch')
    _equal(report['contract_status'], contract_status, budget, 'contract_status_mismatch')
    _equal(report['output'], output, budget, 'output_cardinality_or_dimensions_mismatch')
    _equal(report['impact'], impact, budget, 'impact_cardinality_mismatch')
    for field, expected in (('semantic_blockers', blockers), ('violations', violations), ('diagnostics', diagnostics)):
        _equal(report[field], expected + additions[field], budget, field + '_mismatch')
    budget.check()


def check_operation_facts(left: ArraySnapshot, right: ArraySnapshot, report: dict, *, budget: Budget) -> dict:
    """Verify operation evidence, without upgrading forecast or contract status.

    ``valid`` means the recorded claims passed these independent checks, not
    that the operation succeeds. Unsupported/resource-limited reports remain
    non-certifying. Resource exhaustion in this checker always returns false.
    Inputs are not modified. ``budget`` is cumulative and must be a Budget.
    Producer resource counters/limits and auxiliary-coordinate truth belong to
    the containing envelope's strict, source-bound replay, not this checker.
    """
    try:
        _require(type(budget) is Budget, 'invalid_budget')
        budget.tick()
        _check(left, right, report, budget)
    except _Invalid as exc:
        return {'valid': False, 'reason': str(exc)}
    except ResourceLimit as exc:
        return {'valid': False, 'reason': exc.code}
    except (ValidationError, TypeError, ValueError, KeyError, IndexError, OverflowError):
        return {'valid': False, 'reason': 'invalid_report_or_source'}
    return {'valid': True, 'reason': 'operation_facts_valid'}
