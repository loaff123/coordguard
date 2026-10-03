# Validation record: offline core 0.1.0

Measured on 2026-10-03, Linux x86_64, Python 3.12.14, standard library only. Host-reported CPU: AMD EPYC 9V74 80-Core Processor; this was a shared execution environment, not dedicated hardware. No dependency installs or network-service calls were needed for execution.

## Reproduce

```sh
python3 -m unittest discover -v
python3 scripts/stress_oracles.py
python3 scripts/benchmark.py
python3 -m compileall -q coordguard tests examples scripts
```

65 unittest methods pass. Test method count is distinct from enumerated cases:

- 2,016 exhaustive subset/order/tolerance cases on a five-value grid, with all permutations enumerated independently. Every solver witness is checked by the separate verifier
- Six additional n=6/n=7 permutation cases, plus subnormal, signed-zero, extreme finite and adjacent-boundary fixtures
- 280 exhaustive independently constructed proof cases; graph predicates and witness enumeration in the test oracle do not call the production solver
- Explicit Unicode/string joins and 201-bit pure integer joins preserve exact labels without coercion
- 375 tiny Cartesian join cases with explicit output-cell enumeration and source projections
- 31 verifier-focused methods, including strict malformed-field/index rejection, complete-edge forgery, all proof classes, source digest changes and every resource limit category
- CLI subprocesses cover deterministic repeated JSON, unchanged input hashes, successful and failing exits, independent proof verification, unknown/duplicate fields, excessive nesting, symlinks, directories, pipes and oversized input

The separately authored seeded stress script also passes:

- 700 bit-exact numeric permutation/certificate cases
- 260 malformed proof-field mutations without unhandled exceptions
- 1,200 heterogeneous Cartesian cases with independently varied dimensions, sizes, scalar and empty arrays

These bounded finite grids and random seeds are not exhaustive over all numeric arrays. Mathematical certificates are checked against complete admitted graphs, not sampling.

## Independent review and repaired regressions

Fresh review found, and tests first reproduced:

1. Known latitude/longitude role mismatch could pass the semantic contract
2. The parser's initial byte-only reserve undercharged many tiny JSON containers
3. Digest serialization undercharged heavily escaped metadata
4. Invalid model field/cardinality checks allocated copies before rejection
5. An equal-key right join preserved the left label's type/bits
6. Unsafe mixed integer/float axes bypassed admission on positional paths
7. Huge integer deadlines and invalid supplied budgets raised unstable exceptions

An additional regression prevents explicit integer-to-float label construction from silently dropping integer bits. Nine regression test methods now pass after repairs. Metadata has a pre-serialization text charge; the JSON lexical scan charges containers/tokens before decoding; constructors reject cardinalities before copying. No unresolved findings are deferred in this core delivery.

## Small measured benchmarks

Each case ran in a fresh subprocess. RSS is Linux ru_maxrss for the entire process, including interpreter, imports and snapshots; timing measures correspondence only, after fixture creation. These are single-run observations, not broad performance guarantees.

| Case | Labels per operand | Candidate edges | Result | Seconds | Peak RSS KiB |
|---|---:|---:|---|---:|---:|
| Near-equal unique | 2,000 | 2,000 | unique_within_tolerance | 0.0453 | 15,616 |
| Dense ambiguous | 400 | 160,000 | ambiguous | 0.3388 | 16,128 |
| Explicit 10,000-edge cap | 1,000 | incomplete | resource_limited / edge_limit | 0.0276 | 14,208 |

The dense case does not allocate a Cartesian scientific payload. The capped case stops while enumerating edges and does not claim a complete graph. Coordinate-byte and working-memory accounting remain distinct from measured RSS.

## Demonstrated CLI outputs

- Near-equal inner: complete, 2 positions, exit 1
- Near-equal outer: complete, 4 positions, exit 1
- Near-equal exact: operation_error, no output shape, exit 1
- x/longitude broadcast: complete, 3×3=9 positions, exit 1
- Numeric tolerance unique: unique_within_tolerance, exit 0
- Numeric tolerance ambiguous: ambiguous, exit 1
- Independent unique-proof verification: verified/valid=true, exit 0
- All six generated fixture hashes stayed unchanged

## Not yet validated or implemented

No xarray runtime, actual NetCDF adapter, decoder, backend no-payload-read spy, datetime instant comparison, auxiliary-coordinate merge, HTML renderer, complete audit-report replay, multi-version compatibility or published package/site is claimed. Those statements describe the historical stage-one evidence only. Current adapter/report behavior and release limits are documented in STAGE2_VALIDATION.md.
