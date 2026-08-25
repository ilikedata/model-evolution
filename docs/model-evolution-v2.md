# Model Evolution v2

## Summary

Model Evolution v2 uses five Markdown-first concepts: study, dataset, run,
module, and assessment. Studies combine claim and experimental design; runs
embed their primary evaluation; every successful training run publishes
reusable module outputs. There are no decision, promotion, or approval records.

Studies and runs can also record typed component outcomes when a combined
claim contains independently supported, rejected, inconclusive, or
non-diagnostic parts. This prevents an overall gate from hiding reusable
capability while preserving the original study conclusion.

## Evidence-native materialization

Work completed before the registry existed is represented with exactly the same
record types and directories as new work. Stable IDs are derived from observed
timestamps and source identities. Codex sessions, Git, manifests, checkpoints,
reports, and TensorBoard files are evidence sources only.

Unavailable facts remain explicit:

- missing scalar values use `{status: unavailable}`;
- missing dataset or module dependencies receive ordinary unavailable records;
- recoverable repository artifacts use checksummed local references; and
- no current artifact may substitute for a missing recorded digest.

A complete registry contains research-intent studies, every discovered dataset
and completed run, a module for every successful checkpoint, embedded run
results, and explicit unavailable records for lost data and weights.

## Artifact lifecycle

Local references use repository-relative paths and checksums. Storage planning
packages datasets deterministically and derives every object destination from
its record ID. Planning is local-only. Later publication will convert local
references into immutable GCS references with generation and CRC metadata.

## Required guarantees

- Record kind and ID are path-derived.
- References are typed and complete.
- Inherited module hashes match their module artifacts.
- Deprecated or unavailable modules cannot initialize new runs.
- Source revisions and configurations are never inferred as exact values.
- Dataset tree and file checksums detect mutation before storage.
- Normal storage paths contain no grouped-import namespace.
