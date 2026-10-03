# Historical manifest profile

This file specifies the unchanged stage-one manifest behavior. The separate NetCDF profile, including release-tested ordering/duplicates/dtype behavior, is documented in README.md and STAGE2_VALIDATION.md.

# Stage-one profile and resource contract

## Input schema

All JSON objects reject missing/unknown keys and duplicate keys. Models are immutable. Schema version is integer 1 (boolean true is not 1). The source fixtures show the complete required shape.

ArraySnapshot fields: schema_version, name, axes, decoding_profile, semantic_blockers. `axes` is an ordered list, with at most 32 uniquely named axes per source. `semantic_blockers` is a list of at most 64 bounded strings. The only modeled decoding profile is typed-manifest-v1. An extractor must declare any unmodeled auxiliary coordinate, bounds, grid mapping, or otherwise unchecked semantics here; this stage cannot inspect them independently.

Axis fields: name, size, indexed, index_kind, labels, metadata. Size is integer 0..2^63-1. Unindexed axes have labels=null; indexed axes contain exactly size typed labels and at most 1,000,000 labels. ordinary1d is modeled; custom/curvilinear are representable but unsupported.

Labels contain exactly kind,value; value is always a string. Kinds: int (canonical decimal, at most 256 significant bits), float32 (8 lowercase hexadecimal IEEE bits), float64 (16 hexadecimal bits), str (UTF-8), null (literal text null), datetime (opaque text, unsupported for operations). Float payload bits, signed zero, nulls and nonfinite values are preserved, not normalized. Nonfinite/null labels cannot be exact forecast inputs in this profile. Datetime text is deliberately not interpreted as an instant.

Metadata has exactly units, calendar, role, location, crs, time_encoding, each bounded string or null. Strings/names/metadata are at most 4096 UTF-8 bytes with no unpaired surrogate. Raw numeric time ticks must be declared with time_encoding, role=time, calendar, or `units` containing ` since `; any such indication blocks exact forecasting. Callers are responsible for honest snapshots. A bare numeric axis without metadata is not magically inferred to be time or geographic.

Contract fields: schema_version, allow_drops, allow_fills, allow_broadcast, allow_duplicates, tolerances (dimension-to-typed-number object), same_axis_declarations (dimension names). Unknown dimensions and invalid tolerance values are configuration errors. No switch can declare unsupported forecast semantics verified.

## Exact joins

Labels compare by exact rational value for finite numeric types or by exact Unicode string. +0 and -0 compare equal while their manifest bits/digests remain different. Pure integer joins never cast to float. Within one index or two shared indexes, any participating float plus an integer not exactly representable in binary64 is unsupported. Numeric/string family mismatches and mixed string/numeric axes are unsupported.

Already-identical indexes, including duplicates, stay positional. The default contract rejects duplicate labels independently of whether the operation forecasts successfully. Every differing duplicate-index case is unsupported in stage one. For exact join, differing indexes are rejected before that rule. This deliberately does not infer real xarray duplicate reindex behavior without differential tests.

Equal-size unindexed dimensions, or one indexed and one unindexed dimension of equal size, align positionally. Conflicting sizes are operation_error. A missing dimension broadcasts by name; matching lengths or coordinate values do not imply a rename.

Inner intersection retains left order. Outer union sorts labels except when the indexes are already identical. Left/right retain the selected side's order. For common numeric labels, inner/outer preserve the left typed representation; right preserves the right representation. These are declared core semantics pending runtime differential validation.

## Admission and accounting

Default Limits:

| Limit | Default | Meaning |
|---|---:|---|
| max_labels | 1,000,000 | Total inspected labels from both operand occurrences |
| max_edges | 2,000,000 | Cumulative complete candidate edges across requested axis matches |
| max_coordinate_bytes | 67,108,864 | Logical raw label storage; floats 4/8 bytes, integers max(8, ceil(bits/8)), text UTF-8 bytes |
| max_working_bytes | 536,870,912 | Conservative cumulative reservation estimate |
| max_input_bytes | 67,108,864 | Combined serialized JSON inputs to a CLI invocation |
| max_steps | 50,000,000 | Explicitly counted graph, verification and parser work |
| deadline_seconds | 30 | Cooperative monotonic deadline |

The estimate charges a 1 MiB base; each source label 2048 bytes plus 16 times its logical bytes; each inspected axis 64 KiB plus 32 times name/metadata UTF-8 bytes; snapshot names/profiles/blockers get the same text charge; each candidate edge 128 bytes; additional verifier structures, serialized proofs, metadata and snapshots get explicit reservations. CLI decoding reserves 16 times file bytes before reading, then a pre-parse lexical scan additionally charges every object (512 bytes), array (256 bytes), string token (128 bytes) and other scalar token (128 bytes), before calling the JSON decoder. It shares that same budget/deadline with the operation. Reservations are cumulative, not released, so the estimate deliberately errs toward refusal. The verifier charges more for independent proof reconstruction and can refuse a proof a less expensive solve admitted; increase explicit caps only after considering available resources.

Manifest inputs are existing regular local files, never URLs, symlinks, devices, pipes or pickle. JSON is capped before parsing; nested depth is at most 32, each object at most 64 keys, and integer tokens at most 2048 characters. No remote references are followed. Direct API callers already own their input memory: their preexisting allocations are outside this tool's control, but inspected/derived work is still charged.

A sorted exact-rational window enumerates candidates without a dense matrix. Every candidate is checked against the exact predicate before insertion. Allocation caps apply incrementally. The deterministic matching solver uses iterative shortest augmenting paths (worst-case O(VE)); max_steps and deadline bound adversarial cases. The verifier independently uses sliding sorted rational windows and validates all graph edges against proof claims. Neither samples.

The deadline is cooperative: Python sorting, encoding, individual bounded-integer operations, OS file reads and allocation calls cannot be preempted. Checkpoints occur around those operations and during explicit loops. This is not an OS CPU/RSS sandbox; run untrusted bulk workloads with your own process/container limits too. Output reports may retain checked per-axis facts on exhaustion, but output dimensions/counts become null and the contract stays unverified.

## Semantic statements

Generic missing units remain unknown and do not prevent a purely structural exact forecast. Known units/location/calendar/role disagreements block contract certification. CRS mismatch prevents a complete stage-one forecast. For requested spatial tolerance (explicit spatial role), missing units/location require an explicit same-axis declaration; conflicting units/location/CRS remain blocked even with a declaration. Equality of metadata still does not establish scientific meaning.

Low-level correspond/verify APIs prove only the numeric relation and intentionally do not enforce semantic metadata agreement; the audit is the interface that applies semantic gates. No command applies a mapping or repairs data.
