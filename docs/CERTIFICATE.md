# Numeric correspondence certificate v1

`correspond(left_axis, right_axis, atol: Label, limits=None, budget=None)` returns a plain JSON object. Inputs admit ordinary indexed, unique, finite numeric coordinates. Integers must be exactly binary64-representable. Tolerance is a typed nonnegative finite binary32/binary64-representable number. Exact values are rational interpretations of stored IEEE bits, never rounded subtraction. The low-level mathematical relation does not certify metadata compatibility or physical equivalence; the audit contract separately gates semantic admissibility.

Certificate fields are exactly:

- schema_version: integer 1
- profile: `numeric-absolute-v1`
- left_digest, right_digest: SHA-256 of canonical typed axis JSON
- atol: `{kind,value}` typed label
- status: `unique_within_tolerance`, `ambiguous`, or `impossible`
- n_left, n_right: integer cardinalities
- edge_count, edge_digest: complete graph edge count and SHA-256, or null for immediate unequal-cardinality witness
- witness: one of the below

Complete edges satisfy abs(Fraction(left[u])-Fraction(right[v])) <= Fraction(atol). Edge digest concatenates ASCII `u:v;` in ascending u, then ascending v order. Never trust an edge digest alone: verification rebuilds all eligible edges.

Witness exact fields:

- cardinality: `{kind:'cardinality',left_size:n,right_size:m}` with n != m
- hall: `{kind:'hall',matching:[right index or null for every left],left_set:[sorted left indices],neighbors:[sorted complete neighbors],unmatched_left:[sorted unmatched left indices],unmatched_right:[sorted unmatched right indices]}`. Sets must satisfy |N(S)| < |S|. The partial matching is valid and maximum: the verifier checks its size equals n - (|S|-|N(S)|), the deficiency upper bound. Equal cardinalities required.
- unique: `{kind:'unique',matching:[right index for every left],topological_order:[permutation of left vertices]}`. For each nonmatched edge u->v, form arc u->inverse_matching[v]. Every such arc must move forward in the supplied order. This independently proves absence of an alternating cycle.
- ambiguous: `{kind:'ambiguous',matching:[...],alternative:[...]}`. Both are different complete bijections whose every edge satisfies the predicate.

On unsupported profile or limits, the solver returns `{schema_version:1,status:'unsupported'|'resource_limited',reason:<stable code>,certificate:null}`. This is not a certificate and cannot verify. Invalid configuration raises ValidationError. Verifier returns `{schema_version:1,status:'verified'|'invalid'|'unsupported'|'resource_limited',valid:boolean,reason:string}`; valid is true only for verified evidence.
