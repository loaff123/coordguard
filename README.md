# CoordGuard

**Before combining two labeled arrays, see which positions disappear, become fills, or expand through dimension-name broadcasting.** Optionally prove whether an explicit absolute tolerance admits zero, one, or several complete one-to-one numeric correspondences.

CoordGuard is an offline, read-only coordinate audit. It extracts bounded coordinates and metadata from two selected NetCDF4 variables, predicts a binary xarray alignment, and writes complete JSON plus a standalone HTML summary. It never reads selected scientific payloads, changes data, converts units, guesses dimension renames, interpolates, reprojects, or certifies physical equivalence.

This is an unpublished 0.2.0 candidate. Only the exact dependency stack below is supported; no version-range claim is made. The package name is not reserved on a registry.

## Install from this source

Python 3.11+; validated on Linux x86_64 with Python 3.12.14. Other platforms/Python versions are not yet validated.

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install '.[netcdf]'
python -m unittest discover -v
```

The optional extra pins xarray 2025.10.1, NumPy 2.3.5, pandas 2.2.3, h5netcdf 1.6.3, and h5py 3.14.0. A different runtime is refused with exit 3. Installation obtains packages from the configured package registry; every audit runs offline. `pip install .` installs the standard-library-only manifest/matching core.

## Three real NetCDF examples

Generate tiny public fixtures into a new directory. Existing files are never overwritten:

```sh
python -m examples.netcdf_generate demo
```

### 1. Nearly equal coordinates

```sh
coordguard audit-nc demo/near-left.nc demo/near-right.nc \
  --left-variable temperature --right-variable temperature \
  --contract demo/near-contract.json --html near.html > near.json
coordguard verify-report near.json \
  --left demo/near-left.nc --right demo/near-right.nc
```

x=[0,1,2] versus x=[0,1.000000001,2] yields 2 inner-join positions, dropping one source position per side. At explicit atol=1e-8, a separate unique correspondence [0→0,1→1,2→2] is proved. It does not authorize the exact-label drops. Both commands exit 1; verification says the evidence is valid but the contract failed.

Add `--join outer` for 4 output positions and one fill per operand; `--join exact` reports the operation error. Output HTML paths must be new files.

### 2. Dimension names create a cross-product

```sh
coordguard audit-nc demo/near-left.nc demo/broadcast-right.nc \
  --left-variable temperature --right-variable temperature \
  --html broadcast.html > broadcast.json
coordguard verify-report broadcast.json \
  --left demo/near-left.nc --right demo/broadcast-right.nc
```

x=3 versus longitude=3 produces (x=3,longitude=3), or 9 structural slots. Both one-sided dimensions are flagged. Both commands exit 1.

### 3. A tolerance can be ambiguous

```sh
coordguard audit-nc demo/ambiguous-left.nc demo/ambiguous-right.nc \
  --left-variable temperature --right-variable temperature \
  --contract demo/ambiguous-contract.json --html ambiguous.html > ambiguous.json
coordguard verify-report ambiguous.json \
  --left demo/ambiguous-left.nc --right demo/ambiguous-right.nc
```

[0,0.1] versus [0.04,0.06], atol=0.1, admits two complete bijections. Both appear in complete JSON; no preferred mapping is chosen. Both commands exit 1.

For a passing CI example, audit `demo/near-left.nc` against itself with no tolerance contract. The audit and its verified replay both exit 0.

## Report contract

- `forecast_status`: complete, operation_error, unsupported, or resource_limited
- `contract_status`: pass, fail, or unverified
- Per-axis `correspondence_status`: exact, unique_within_tolerance, ambiguous, impossible, not_requested, or unsupported
- Exit 0: complete and contract satisfied; 1: known violation/operation error or invalid proof; 2: invalid input/configuration; 3: unsupported/incomplete/resource-limited

The default contract forbids label drops, fill slots, newly broadcast dimensions, duplicate labels, dropped conflicting auxiliaries, and unresolved semantics. `allow_drops`, `allow_fills`, `allow_broadcast`, and `allow_duplicates` relax only their named condition. `same_axis_declarations` can acknowledge missing spatial metadata; they never override units, role, location, calendar, or CRS conflicts. See generated contract JSON for the exact schema.

Counts are structural slots, not nonmissing observations, physical validity, or exact RAM consumption. Coordinate digests cover inspected coordinates/metadata and decoding settings, not whole-file contents or scientific payload integrity. Payload-only changes intentionally do not invalidate a coordinate report.

HTML samples long evidence with explicit truncation counts. JSON contains the complete bounded evidence. No JavaScript, CDNs, external assets, or server are required.

`verify-report` re-extracts both sources and type-strictly compares a replayed report. A separate implementation checks source coverage, join order, indexers, duplicates, drops, dimension/cardinality/impact facts, contract status, and numeric certificates. Auxiliary values and selected-field semantic annotations are checked through source-bound replay, not a second auxiliary implementation. Resource counters are diagnostics and are not independently certified. A verified failed contract retains its nonzero exit.

## Supported scope

- Root-group local regular NetCDF4/HDF5 files with genuine NetCDF dimension scales; no URL, symlink, external/soft link, hard-link alias, virtual/external storage, or inferred dimension names
- Selected ordinary one-dimensional numeric or bounded fixed-width CF character indexes decoded to Unicode; unindexed positional dimensions; scalar and 1D auxiliaries
- Binary arithmetic with explicit inner, outer, left, right, or exact join. No Dataset-wide pairing, merge, concat, in-place arithmetic, groupby, or where
- Identical duplicate indexes can execute; nonidentical duplicates predict xarray's reindexing error. Default contracts still reject duplicates
- Standard decoded datetime64 with exact s/ms/us/ns ticks and preserved raw origins/calendar, when both sides use the same decoded resolution. No forced nanosecond cast; mixed resolutions, nonstandard calendars, NaT, decode failures, and time tolerance are refused
- Different standard time-unit origins can describe identical instants. Calendar disagreement remains a blocker; omitted CF calendars are compared as effective standard calendars while the raw omission is retained

Packed coordinates, nonfinite labels, custom/curvilinear indexes, multidimensional coordinates, bounds/grid mappings, unknown/variable-length coordinate storage, unsafe mixed numeric coercions, and multidimensional auxiliary results are explicitly unsupported. All object/variable-length datasets are refused before xarray opening, including unrelated fields, because xarray may otherwise read them eagerly. Fixed-width CF character arrays can decode into bounded Unicode strings; numeric strings remain strings. Raw byte-string coordinates are explicitly unsupported, so bytes and Unicode never acquire the same label identity. Mixed signed/unsigned indexes, incompatible known label families (including empty axes), unsafe mixed auxiliary numeric comparisons/fill casts, and mixed auxiliary time resolutions are conservatively refused even where xarray might coerce them.

## Read and resource safety

Opening uses a read-only file descriptor, h5netcdf, chunks=None, cache=False, decode_cf=False and create_default_indexes=False. A native metadata preflight rejects aliases/references outside the admitted root, bounds variable-length text and attribute counts, and requires genuine dimension scales. All permitted coordinates are admitted before the first coordinate read. Decoding occurs only on a coordinate-only object. A real backend spy and native alias regression test enforce absence of scientific payload reads.

Defaults: 1,000,000 total source labels, 64 MiB source coordinate storage, 2,000,000 candidate edges, 512 MiB conservative working-allocation estimate, 64 MiB combined JSON input, 50,000,000 counted steps, and a cooperative 30-second deadline. Coordinate decompression chunks are also charged before reading. Metadata is capped at 4,096 root objects, 4,096 UTF-8 bytes per object/attribute name, 128 attributes per object, 4,096 attribute elements, 4,096 text bytes per attribute element, and 1 MiB aggregate metadata accounting including names. CLI options lower or raise the declared caps explicitly.

These are admission controls, not a hard OS memory/time sandbox. Imports and native HDF5 metadata/conversion allocations have overhead outside Python estimates. HDF5 exposes a name before its length can be checked, so an oversized initial name allocation cannot be preempted; exposed names are rejected before retaining them or inspecting their targets. Fixed-width character dimension scales are allowed, but null/scalar/non-scale dimension references and malformed DIMENSION_LIST schemas fail closed; native calls are not preemptible by the cooperative deadline. For genuinely untrusted HDF5, use a separate OS/container resource boundary. Verification charges retained report/parser memory as well as replay, so large reports may require a higher explicit working cap than their original audit. The original audit limits remain in the evidence; verification uses its explicitly permitted ceiling. Resource-limited producer reports remain incomplete and are not certified by replay.

## Standard-library core

Strict JSON manifests and APIs remain available without optional dependencies:

```sh
python -m coordguard audit examples/near-left.json examples/near-right.json
python -m coordguard match examples/ambiguous-left.json examples/ambiguous-right.json --axis x --atol 0.1 > proof.json
python -m coordguard verify proof.json --left examples/ambiguous-left.json --right examples/ambiguous-right.json --axis x
```

Standalone `verify` checks a mathematical proof and can exit 0 for valid ambiguity/impossibility evidence; it is distinct from full `verify-report`, which preserves contract failure. See [core profile](docs/CORE_PROFILE.md) and [certificate format](docs/CERTIFICATE.md). Exact float labels preserve IEEE bits; numeric certificates use exact rational predicates over represented values, not rounded subtraction or nearest-neighbor guesses.

## Validation and provenance

[Stage-two validation](docs/STAGE2_VALIDATION.md) records exact tests, independent checks, measured benchmarks, review fixes, remaining limits and distribution checks. [Stage-one validation](docs/VALIDATION.md) is historical evidence for the dependency-free mathematical core. CI definitions are included but have not been run on a remote repository. Nothing has been published.

This is operation-specific evidence, not a new matching algorithm. `xr.align(join="exact")` remains the simplest equality guard. [xarray alignment](https://docs.xarray.dev/en/v2025.10.1/generated/xarray.align.html), [automatic alignment](https://docs.xarray.dev/en/v2025.10.1/user-guide/computation.html#automatic-alignment), [nccmp](https://gitlab.com/remikz/nccmp), [cf-xarray](https://cf-xarray.readthedocs.io/), [xgcm](https://xgcm.readthedocs.io/), and [xESMF](https://xesmf.readthedocs.io/) cover adjacent or grid-aware tasks.

MIT license. No third-party source or wheels are vendored. See [dependency notices](THIRD_PARTY_NOTICES.md).
