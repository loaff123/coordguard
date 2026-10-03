"""Escaped, self-contained presentation of coordinate-only audit evidence.

This module is a view, not a verifier. Sequence samples are selected before
formatting; the complete bounded evidence belongs in the separate JSON report.
No scripts, external resources, payload access, or third-party dependencies.
"""
from html import escape
from itertools import islice
import struct


_STYLE = """
:root { color-scheme: light; --ink:#172b3a; --muted:#526371; --line:#dbe2e7;
  --paper:#fff; --ground:#f3f6f8; --accent:#195b65; }
* { box-sizing:border-box; }
body { margin:0; background:var(--ground); color:var(--ink);
  font:16px/1.55 system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; }
main { max-width:1140px; margin:0 auto; padding:40px 24px 64px; }
header { border-top:5px solid var(--accent); padding:28px 0 22px; }
h1 { font-size:2.2rem; line-height:1.15; letter-spacing:-.04em; margin:8px 0 16px; }
h2 { font-size:1.35rem; line-height:1.3; margin:0 0 18px; }
h3 { font-size:1.05rem; margin:22px 0 10px; }
p { margin:8px 0 14px; }
.eyebrow { color:var(--accent); font-size:.76rem; letter-spacing:.15em; font-weight:750; }
.lede { max-width:80ch; color:var(--muted); }
.panel { background:var(--paper); border:1px solid var(--line); border-radius:12px;
  padding:24px; margin:20px 0; }
.grid { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:20px; }
.statusline { display:flex; gap:12px 28px; flex-wrap:wrap; margin:20px 0 4px; }
.badge { display:inline-block; border-radius:5px; padding:2px 8px; font-weight:650;
  background:#edf1f4; color:#364a5c; border:1px solid #cfd8df; overflow-wrap:anywhere; }
.good { background:#eaf4ee; color:#20543b; border-color:#bedbc8; }
.warn { background:#fff3dc; color:#754400; border-color:#e5cb91; }
.bad { background:#fbece9; color:#8b2d22; border-color:#ebc4bd; }
.notice { border-left:4px solid #ac731a; background:#fff8e9; padding:16px 20px; }
.muted, .sample, .type { color:var(--muted); font-size:.875rem; }
.sample { margin:5px 0 12px; }
.sample strong { color:#754400; }
.hero-metric { font-size:2rem; font-weight:700; letter-spacing:-.035em; }
.record { margin:0; }
.record > div { display:grid; grid-template-columns:minmax(130px,1fr) minmax(0,2fr);
  gap:12px; padding:7px 0; border-bottom:1px solid #edf0f2; }
dt { font-weight:600; font-size:.9rem; }
dd { margin:0; min-width:0; overflow-wrap:anywhere; }
code { font-family:ui-monospace,SFMono-Regular,Consolas,monospace; font-size:.88em;
  white-space:pre-wrap; overflow-wrap:anywhere; }
.typed { display:inline-flex; flex-wrap:wrap; gap:6px; align-items:baseline; }
.bits { display:block; color:var(--muted); font-size:.75rem; }
.scroll { overflow:auto; }
table { width:100%; border-collapse:collapse; margin:8px 0 2px; font-size:.9rem; }
caption { text-align:left; color:var(--ink); font-weight:650; margin:12px 0 7px; }
th, td { text-align:left; padding:8px 10px; vertical-align:top; border-bottom:1px solid var(--line);
  overflow-wrap:anywhere; }
th { background:#f2f5f7; font-weight:650; }
td:first-child, th:first-child { width:110px; }
.record table { font-size:.85rem; }
.evidence-item { padding:12px 0; border-bottom:1px solid var(--line); }
details { margin-top:18px; }
summary { cursor:pointer; color:var(--accent); font-weight:650; padding:7px 0; }
summary:focus-visible, a:focus-visible { outline:3px solid #317c91; outline-offset:4px; }
a { color:var(--accent); text-underline-offset:3px; }
footer { color:var(--muted); font-size:.875rem; margin-top:30px; }
@media (max-width:720px) { main { padding:18px 14px 36px; } .panel { padding:18px; }
  .grid { grid-template-columns:1fr; } h1 { font-size:1.85rem; }
  .record > div { grid-template-columns:1fr; gap:3px; } }
@media print { body { background:#fff; } main { padding:0; max-width:none; }
  .panel { border-radius:0; break-inside:avoid; } details { display:block; }
  .scroll { overflow:visible; } }
"""


def _text(value):
    """Only scalar values may be converted directly to escaped text."""
    if value is None:
        return 'Unknown'
    if isinstance(value, str):
        return escape(value, quote=True)
    if type(value) is bool:
        return 'Yes' if value else 'No'
    if type(value) in (int, float):
        return escape(str(value), quote=True)
    return 'Unsupported display value'


def _heading(key):
    return key.replace('_', ' ').capitalize() if isinstance(key, str) else 'Field'


def _mapping(value):
    return value if isinstance(value, dict) else {}


def _sequence(value):
    return isinstance(value, (list, tuple))


def _badge(status):
    # The class is selected from constants. A status never enters an attribute.
    kind = ('good' if status in ('complete', 'pass', 'exact', 'unique_within_tolerance')
            else 'bad' if status in ('fail', 'operation_error', 'ambiguous', 'impossible')
            else 'warn' if status in ('unsupported', 'resource_limited', 'unverified')
            else '')
    return '<span class="badge ' + kind + '">' + _text(status) + '</span>'


class _View:
    def __init__(self, limit):
        self.limit = limit

    def sample_note(self, total):
        shown = min(total, self.limit)
        message = f'Showing {shown} of {total} items.'
        if shown < total:
            message += (f' <strong>Truncated display; {total - shown} omitted.</strong>'
                        ' Full evidence is in the separate JSON report.')
        return '<p class="sample">' + message + '</p>'

    def value(self, value, depth=0):
        if isinstance(value, dict):
            if depth >= 8:
                return '<span class="muted">Nested evidence omitted; see the JSON report.</span>'
            if 'kind' in value and 'value' in value:
                return self.label(value)
            return self.record(value, depth=depth + 1)
        if _sequence(value):
            if depth >= 8:
                return '<span class="muted">Nested evidence omitted; see the JSON report.</span>'
            return self.items(value, depth=depth + 1)
        return _text(value)

    def label(self, label):
        if not isinstance(label, dict):
            return self.value(label)
        kind, raw = label.get('kind'), label.get('value')
        shown = raw
        bits = ''
        if kind in ('float32', 'float64') and isinstance(raw, str):
            try:
                if len(raw) != (8 if kind == 'float32' else 16):
                    raise ValueError('Unexpected bit width')
                shown = repr(struct.unpack('>f' if kind == 'float32' else '>d', bytes.fromhex(raw))[0])
                bits = '<code class="bits">bits: ' + _text(raw) + '</code>'
            except (ValueError, struct.error):
                # A renderer must not upgrade malformed evidence to valid data.
                bits = '<span class="bits">Invalid floating-point representation</span>'
        return ('<span class="typed"><code>' + _text(shown) + '</code>'
                '<span class="type">' + _text(kind) + '</span></span>' + bits)

    def record(self, data, *, depth=0, titles=None):
        if not isinstance(data, dict):
            return self.value(data, depth)
        rows = []
        # Model objects have bounded field counts. Also bound unfamiliar metadata.
        for key, value in islice(data.items(), 64):
            title = (titles or {}).get(key, _heading(key))
            rows.append('<div><dt>' + _text(title) + '</dt><dd>' +
                        self.value(value, depth) + '</dd></div>')
        if len(data) > 64:
            rows.append('<div><dt>Truncated display</dt><dd>Showing 64 of ' +
                        _text(len(data)) + ' fields; see the JSON report.</dd></div>')
        return '<dl class="record">' + ''.join(rows) + '</dl>'

    def items(self, values, *, depth=0):
        if not _sequence(values):
            return self.value(values, depth)
        parts = [self.sample_note(len(values))]
        for index in range(min(len(values), self.limit)):
            parts.append('<div class="evidence-item">' + self.value(values[index], depth) + '</div>')
        return ''.join(parts)

    def table(self, title, headers, values, cells):
        if not _sequence(values):
            return '<h3>' + _text(title) + '</h3><p class="muted">Unknown / not available.</p>'
        rows = []
        for index in range(min(len(values), self.limit)):
            rows.append('<tr>' + ''.join('<td>' + cell + '</td>' for cell in cells(index, values[index])) + '</tr>')
        return ('<div class="scroll"><table><caption>' + _text(title) + '</caption><thead><tr>' +
                ''.join('<th scope="col">' + _text(header) + '</th>' for header in headers) +
                '</tr></thead><tbody>' + ''.join(rows) + '</tbody></table></div>' + self.sample_note(len(values)))

    def labels(self, title, labels, *, original=False):
        return self.table(title, ('Original index' if original else 'Output index', 'Typed label'),
                          labels, lambda i, label: (_text(i), self.label(label)))

    def drops(self, title, values):
        return self.table(title, ('Original index', 'Dropped label'), values,
                          lambda i, item: (_text(_mapping(item).get('index')),
                                           self.label(_mapping(item).get('label'))))

    def indexer(self, title, indexer):
        if isinstance(indexer, dict):
            return '<h3>' + _text(title) + '</h3>' + self.record(indexer)
        return self.table(title, ('Output index', 'Source index'), indexer,
                          lambda i, source: (_text(i), '<em>fill</em>' if source is None else self.value(source)))

    def duplicates(self, title, values):
        return self.table(title, ('Label', 'Multiplicity', 'Positions'), values,
                          lambda i, item: (self.label(_mapping(item).get('label')),
                                           _text(_mapping(item).get('multiplicity')),
                                           self.value(_mapping(item).get('positions'))))

    def correspondence(self, evidence):
        if not isinstance(evidence, dict):
            return self.value(evidence)
        summary = {key: value for key, value in islice(evidence.items(), 64) if key != 'witness'}
        result = self.record(summary, titles={'atol': 'Absolute tolerance'})
        witness = evidence.get('witness')
        if isinstance(witness, dict):
            result += '<h3>Witness</h3>'
            for key, value in islice(witness.items(), 64):
                if key in ('matching', 'alternative') and _sequence(value):
                    result += self.table(_heading(key), ('Left original index', 'Right original index'), value,
                                         lambda i, right: (_text(i), '<em>unmatched</em>' if right is None else self.value(right)))
                else:
                    result += self.record({key: value})
        elif witness is not None:
            result += self.value(witness)
        return result

    def source(self, side, source, audit):
        source = _mapping(source)
        snapshot = _mapping(source.get('snapshot'))
        data = {'variable': snapshot.get('name'), 'decoding_profile': snapshot.get('decoding_profile'),
                'coordinate_digest': audit.get(side + '_digest')}
        # Avoid copying or serializing snapshot labels as part of source metadata.
        for key, value in islice(source.items(), 64):
            if key not in ('snapshot', 'auxiliary'):
                data[key] = value
        result = '<div><h3>' + _text(side.capitalize() + ' input') + '</h3>' + self.record(data)
        if source.get('auxiliary'):
            result += '<details><summary>Inspected auxiliary coordinates</summary>' + self.items(source['auxiliary']) + '</details>'
        return result + '</div>'

    def axis_source(self, side, axis):
        title = side.capitalize()
        if axis is None:
            return '<p class="muted">' + title + ' source axis not included or absent.</p>'
        if axis.get('labels') is None:
            result = '<h3>' + title + ' original-order labels</h3><p>Unindexed: positional alignment only.</p>'
        else:
            result = self.labels(title + ' original-order labels', axis.get('labels'), original=True)
        result += self.record({'index_kind': axis.get('index_kind'), 'metadata': axis.get('metadata')})
        return result

    def axis(self, index, fact, sources):
        fact = _mapping(fact)
        name = fact.get('name')
        parts = ['<section class="panel" id="axis-' + str(index) + '"><h3>Axis <code>' +
                 _text(name) + '</code></h3>', '<p>Forecast ' + _badge(fact.get('status')) +
                 ' · Correspondence ' + _badge(fact.get('correspondence_status')) + '</p>',
                 self.record({key: fact.get(key) for key in (
                     'mode', 'reason', 'left_size', 'right_size', 'output_size', 'left_reordered', 'right_reordered')})]
        if fact.get('mode') in ('positional', 'positional_with_index'):
            parts.append('<p class="notice">Unindexed dimensions align by position when their sizes agree. '
                         'Matching lengths do not establish label or physical equivalence.</p>')
        parts.append('<details open><summary>Coordinate evidence</summary><div class="grid">')
        for side in ('left', 'right'):
            source_axis = next((axis for axis in sources[side] if _mapping(axis).get('name') == name), None)
            parts.append('<div>' + self.axis_source(side, source_axis) + '</div>')
        parts.append('</div>' + self.labels('Output labels', fact.get('labels')) + '<div class="grid">')
        for side in ('left', 'right'):
            title = side.capitalize()
            parts.append('<div>' + self.drops(title + ' dropped labels', fact.get(side + '_dropped')) +
                         self.indexer(title + ' indexer', fact.get(side + '_indexer')) +
                         self.duplicates(title + ' duplicates', fact.get(side + '_duplicates')) + '</div>')
        parts.append('</div></details>')
        evidence = fact.get('correspondence')
        if evidence is not None:
            parts.append('<h3>Correspondence evidence</h3><p class="muted">This discrete relation '
                         'does not establish physical equivalence or authorize relabeling.</p>' +
                         self.correspondence(evidence))
        parts.append('</section>')
        return ''.join(parts)


def render_html(report: dict, *, sample_limit: int = 20) -> str:
    """Render a full NetCDF envelope or a raw planner/error report as HTML.

    ``sample_limit`` is a nonnegative per-sequence display limit, not an audit
    limit. Zero hides evidence entries while retaining counts and statuses.
    Sequence elements outside the sample are never formatted. Source snapshots
    are necessary to show original-order source labels; no labels are invented
    when the caller supplies a raw core report. This function does not validate
    certificates or silently turn partial forecasts into complete forecasts.
    """
    if not isinstance(report, dict):
        raise ValueError('report must be a dictionary')
    if type(sample_limit) is not int or sample_limit < 0:
        raise ValueError('sample_limit must be a nonnegative integer')
    audit = report.get('audit', report)
    if not isinstance(audit, dict):
        raise ValueError('report.audit must be a dictionary')
    view = _View(sample_limit)
    output = _mapping(audit.get('output'))
    contract = _mapping(audit.get('contract'))
    forecast = audit.get('forecast_status', 'unavailable')
    contract_status = audit.get('contract_status', 'unverified')
    parts = ['<!doctype html><html lang="en"><head><meta charset="utf-8">',
             '<meta name="viewport" content="width=device-width,initial-scale=1">',
             '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; '
             'style-src \'unsafe-inline\'; base-uri \'none\'; form-action \'none\'">',
             '<title>CoordGuard coordinate audit</title><style>', _STYLE,
             '</style></head><body><main id="main"><header><div class="eyebrow">COORDGUARD / AUDIT</div>',
             '<h1>Before the arrays meet</h1><p class="lede">Coordinate-only evidence for the declared '
             'binary operation. Structural slots are not nonmissing observations, exact RAM use, '
             'or a guarantee of scientific validity. Scientific payload values are not inspected.</p>',
             '<div class="statusline"><div>Forecast ', _badge(forecast), '</div><div>Contract ',
             _badge(contract_status), '</div></div></header>',
             '<section class="panel"><h2>Audit at a glance</h2>',
             '<dl class="record"><div><dt>Structural output slots</dt><dd class="hero-metric">',
             _text(output.get('total_positions')), '</dd></div></dl>',
             view.record({'profile': report.get('profile', audit.get('profile')),
                          'operation': audit.get('operation'), 'join': audit.get('join'),
                          'output_dimensions': output.get('dimensions'), 'output_sizes': output.get('sizes')})]
    if not output:
        parts.append('<p class="notice">No complete output forecast is available. Unknown counts '
                     'remain unknown; the audit does not imply that this operation can execute.</p>')
    parts.append('</section>')
    error = report.get('error', audit.get('error'))
    if error is not None:
        parts.append('<section class="panel notice"><h2>Input or audit error</h2>' + view.value(error) + '</section>')
    declarations = contract.get('same_axis_declarations', [])
    parts.append('<section class="panel notice"><h2>Explicit same-axis declarations</h2>')
    if declarations:
        parts.append('<p>The caller explicitly declared these dimensions to refer to the same intended axis. '
                     'This is a supplied assumption, not evidence of physical equivalence.</p>' + view.value(declarations))
    else:
        parts.append('<p>None supplied.</p>')
    parts.append('</section><section class="panel"><h2>Inputs and inspected metadata</h2><div class="grid">')
    sources = {}
    for side in ('left', 'right'):
        source = _mapping(report.get(side))
        snapshot = _mapping(source.get('snapshot'))
        axes = snapshot.get('axes', [])
        sources[side] = axes if _sequence(axes) else []
        parts.append(view.source(side, source, audit))
    parts.append('</div><p class="muted">Digests cover inspected coordinates and metadata only. '
                 'They do not establish scientific payload integrity or complete-file identity.</p>')
    if not any(_mapping(report.get(side)).get('snapshot') for side in ('left', 'right')):
        parts.append('<p class="notice">Original-order source labels are not included in this raw planner report. '
                     'Use the full report envelope to inspect them.</p>')
    parts.append('</section><section class="panel"><h2>Structural impact</h2><div class="grid">')
    impact = _mapping(audit.get('impact'))
    titles = {'input_positions': 'Input source slots', 'retained_source_positions': 'Retained source slots',
              'excluded_source_positions': 'Excluded source slots',
              'contributing_output_positions': 'Contributing output slots', 'fill_output_positions': 'Fill output slots',
              'broadcast_dimensions': 'Broadcast dimensions', 'excluded_fraction': 'Excluded fraction'}
    for side in ('left', 'right'):
        values = _mapping(impact.get(side))
        parts.append('<div><h3>' + side.capitalize() + ' operand</h3>' +
                     view.record({key: values.get(key) for key in titles}, titles=titles) + '</div>')
    parts.append('</div></section>')
    for field, title in (('violations', 'Contract violations'), ('semantic_blockers', 'Semantic blockers'),
                         ('diagnostics', 'Diagnostics')):
        values = audit.get(field)
        parts.append('<section class="panel"><h2>' + title + '</h2>' +
                     (view.items(values) if values else '<p class="muted">None recorded.</p>') + '</section>')
    parts.append('<section aria-labelledby="axes-title"><h2 id="axes-title">Per-axis evidence</h2>'
                 '<p class="muted">Source and dropped labels retain original order. Indexers map output '
                 'positions to original source indices; fill denotes a newly introduced structural slot. '
                 'Evidence lists are displayed up to ' + _text(sample_limit) + ' items each.</p>')
    axes = audit.get('axes', [])
    if _sequence(axes):
        # Every modeled dimension gets a summary; only its evidence arrays are sampled.
        for index in range(min(len(axes), 64)):
            parts.append(view.axis(index, axes[index], sources))
        if len(axes) > 64:
            parts.append('<p class="notice">Truncated display: showing 64 of ' + _text(len(axes)) +
                         ' axes. See the JSON report.</p>')
    parts.append('</section><section class="panel"><h2>Auxiliary-coordinate findings</h2>')
    auxiliary = report.get('auxiliary_facts')
    parts.append(view.items(auxiliary) if auxiliary else '<p class="muted">None recorded.</p>')
    parts.append('</section><section class="panel"><h2>Contract and execution details</h2>' +
                 view.record({'contract': contract, 'claims': audit.get('claims'),
                              'limits': audit.get('limits'), 'resources': audit.get('resources')}) + '</section>')
    parts.append('<footer><p>This standalone HTML is a presentation of the supplied report, not independent '
                 'verification. Keep the separate JSON report for complete bounded evidence and replay. '
                 'Displayed samples do not replace exact audit results. No input data is modified.</p>'
                 '<p>Offline document · no JavaScript · no external resources</p></footer></main></body></html>')
    return ''.join(parts)
