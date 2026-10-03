# NetCDF/report candidate validation

Measured 2026-10-03 on Linux x86_64, Python 3.12.14; host-reported AMD EPYC 9V74 80-Core Processor in a shared environment. This is an unpublished candidate; no repository, registry, Site, tag, or release was created.

## Exact supported runtime

- xarray 2025.10.1
- NumPy 2.3.5
- pandas 2.2.3
- h5netcdf 1.6.3
- h5py 3.14.0

Additional observed packages: packaging 26.3, python-dateutil 2.9.0.post0, pytz 2026.3.post1, tzdata 2026.3, six 1.17.0, setuptools 84.0.0. Dependency installation used official PyPI releases. Initial exploration of xarray 2025.6.1 found no create_default_indexes switch; it is not supported. No second supported xarray release was validated, so there is no version-range claim.

## Reproduce

```sh
python -m pip install '.[netcdf]'
python -m unittest discover -v
python -m scripts.stress_oracles
python -m scripts.benchmark_netcdf --count 1000
python -m scripts.benchmark_netcdf --count 300 --dense
```

The final pinned suite passes **178 test methods with no skips**. A base runtime without the NetCDF extra passes **109 tests and skips 69 optional methods** (178 discovered). Optional skips are visible; a base-only run is not evidence of NetCDF validation.

Existing mathematical evidence remains: 2,016 exhaustive permutation-oracle cases plus six n=6/7 cases; 280 independently constructed proofs; 375 Cartesian join cases. The separate stress script passes 700 exact-rational numerical cases, 260 malformed proof mutations, and 1,200 heterogeneous Cartesian cases.

## Differential compatibility and independent verification

The pinned xarray suite executes **951 tiny comparisons**:

- 821 core arithmetic cases across all five joins, reordered/identical/empty indexes, identical/nonidentical duplicate indexes, selected and nonselected duplicate targets, indexed/unindexed axes, zero/scalar/named-dimension cross-products, Unicode strings, storage dtypes, precision and conservative refusals
- 110 generated NetCDF scalar/1D auxiliary comparisons, including alignment before equality, left storage, one-sided fills, small-integer fill promotion and scalar/vector equivalence
- 15 different-dimension auxiliary result guards and 5 scalar-name/index shadowing cases

Findings changed the runtime profile: nonidentical duplicate indexes error even when a duplicate target appears unchanged; identical numeric keys preserve left coordinate storage for every join; empty outer joins preserve the nonempty order; numeric and fill dtypes depend on original dtype widths. The historical manifest profile remains separately documented and conservative.

The independent operation checker has 21 dedicated tests, including 360 tiny profile/join cases, huge symbolic dimensions, strict bool/int distinction, source membership/order/duplicates/drop/indexer tampering, impact counts, semantic/contract status, source digests and numeric certificates. An additional review run checked all 821 xarray-core reports and 3,000 randomized honest reports against the independent checker. It imports neither the planner nor correspondence solver.

Full replay re-extracts source records and compares complete evidence type-strictly. It independently checks operation facts and numeric certificates. Auxiliary forecasts and selected-field/auxiliary semantic annotations are checked by source-bound replay only. Resource counters are diagnostic and excluded from proof verification. Explicit verifier working ceilings can exceed source-audit limits to admit retained report/parser overhead. Resource-limited producer reports cannot be certified complete.

## Real-file safety and adversarial review

A lazy spy around the actual xarray h5netcdf array wrapper fails on selected scientific-payload access while allowing a complete extraction. Before/after SHA-256 hashes prove no file mutation, separately from no-read tests. A native HDF5 alias spy covers payload access via a different variable name.

Seven independent review regressions reproduce and now prevent:

1. A 16 MiB variable-length ignored attribute being materialized under a 2 MiB working cap
2. An auxiliary hard link reading the selected scientific payload through an alias
3. A shape-(1,) coordinate decompressing a 128 MiB chunk before admission
4. Plain HDF5 inventing dimension names through the backend's phony-dimension default
5. Report tampering by bool/int/float equality substitutions
6. Null HDF5 attribute/dataset dataspaces escaping as uncaught Python exceptions
7. Verifier overhead making a complete low-budget source report impossible to verify even with an explicitly higher ceiling

Other tests cover regular-file/symlink/URL restrictions, missing coordinate references, variable-length coordinate refusal before xarray opens, bounded fixed-width numeric strings, packed/nonstandard/calendar/curvilinear semantics, coordinate fill values, time origins, s/ms/us/ns resolution, a 2**40-second date outside nanosecond range, historical standard-calendar refusal, malformed reports, HTML script text, tiny caps and deadlines. Native dimension scales must refer to admitted root objects. All supported coordinate storage and decompression chunks are admitted before values are requested.

This is not an OS sandbox or a formally verified HDF5 parser. Native HDF5 metadata/conversion allocations and dependency-import overhead are outside the Python working estimate. Deadlines are cooperative and cannot interrupt a native call. Untrusted files require a separate process/container memory/time boundary. The program performs no network requests, executes no user-supplied code, and provides no repair/apply command.

## End-to-end examples

Generated public NetCDF fixtures passed CLI audit → complete JSON/standalone HTML → source re-extraction/full verify. Repeated canonical JSON was byte-identical, and input SHA-256 hashes stayed unchanged. Results:

- Near-equal: 2 inner slots, unique optional correspondence, audit/replay exit 1
- Named cross-product: dimensions x=3 and longitude=3, 9 slots, audit/replay exit 1
- Ambiguous: 0 exact inner slots, two valid tolerance bijections, audit/replay exit 1
- Exact self-pair: 3 slots, audit/replay exit 0

Payload-only source changes intentionally do not invalidate a coordinate audit. Coordinate or inspected-metadata changes invalidate replay.

HTML has 19 tests for escaping, strict sample limits, exact float text/bits, original indices, nested evidence, unknown-versus-zero, contracts, blockers and incomplete/error states. A one-million-element sample stress produced 14,233 HTML bytes with approximately 26 KiB incremental traced allocation at sample_limit=2. Screenshot/browser visual verification was unavailable, so no visual rendering claim is made.

## Measured benchmarks

Raw machine-readable observations are in evidence/benchmark-netcdf-near.json and evidence/benchmark-netcdf-dense.json.

- Near-equal, 1,000 labels per side: 1,000 candidate edges, 0.0729 s wall time, 96,724 KiB peak process RSS; unique correspondence; 12,351,456-byte working estimate
- Dense ambiguous, 300 labels per side: 90,000 candidate edges, 0.2604 s wall time, 94,852 KiB peak process RSS; ambiguous; 16,575,424-byte working estimate

RSS includes imports and fixture generation; estimates cover tracked application allocations. They measure different scopes and are not interchangeable. These shared-host observations are not throughput guarantees or claimed speed targets.

## Adversarial release corrections

An additional independent release audit found cross-process and boundary-type defects not exercised by the initial tiny matrix. The corrected candidate adds 27 regression methods (178 total), observed failing before their fixes, and deliberately narrows unsupported cases instead of substituting guessed semantics.

- Canonical auxiliary semantic diagnostics retain declared left-source order. Reports produced with PYTHONHASHSEED=1,2,3 are byte-identical and verify in the other processes
- Mixed datetime64 resolutions are unsupported for axes and same-name auxiliaries. Tests cover safe-range promotion and real pandas overflow; neither can receive a false passing report. Omitted decoded CF calendars compare as effective standard while raw metadata remains unchanged
- Empty source indexes retain known dtype/family information. Small/large integer plus float32 promotion is width-aware, and datetime/numeric/text family conflicts remain unsupported even when one side is empty
- Raw byte-string coordinates are unsupported. Bounded CF character arrays decoded to Unicode remain admitted; byte labels are never silently converted into text identities
- Scalar/vector auxiliary equality retains the broadcast left operand's storage. A same-name auxiliary is shadowed only by an actual output index, not by an unindexed dimension
- Unsafe auxiliary integer/float equality, signed/unsigned mixing, and integer-to-float fill conversion are unsupported with specific diagnostics. Same-resolution auxiliary times with different decoded origins are allowed
- DIMENSION_LIST requires the exact variable-length-reference shape/type. Null references, non-scale targets and scalar scales fail closed with structured invalid-input diagnostics; callbacks return errors across the native boundary without leaking Python tracebacks
- Root/attribute names must be UTF-8 text and are capped and charged as soon as exposed, before retaining them or inspecting referenced objects. Non-UTF8 native byte names return structured invalid-input errors. Fixed-width and variable-length text share the per-element cap. Initial native name exposure cannot be preempted and remains within the documented external-resource-boundary requirement

The auxiliary release tests exercise 14 methods, including numeric widths/operand order, all five joins, empty aligned vectors, indexed versus unindexed shadowing, unsafe casts, Unicode/numeric/datetime families, time origins and calendar mismatches. Full replay checks every new unsupported annotation; no comparison was weakened to force a pass.

## Distribution and remaining release work

Isolated source-distribution/wheel builds pass; an offline no-dependency wheel install and installed-module CLI smoke test pass. Packages include MIT and dependency notices, exact optional pins, full source tests/examples in the sdist, and no private working files, Git history, cached interpreter files, credentials or vendored dependencies. CI definitions are present, but remote CI has not run because no repository was published.

The explicit safe source selection is PUBLIC_FILES.txt. Historical implementation instructions and obsolete proposal CLI documents are omitted. Publication must use a fresh clean export/root commit, never a raw development checkout or its history. Remaining release gates: choose/verify public naming and repository destination, authorize publication, then verify remote CI for that published commit. A second xarray release is required before widening version support. HTML visual rendering remains unverified. The conservative root-group, fixed-width, standard-calendar, ordinary-index scope is intentional; unsupported cases exit 3 rather than receive guessed semantics. Native allocation/deadline limits and auxiliary replay scope must remain visible in any release claims.
