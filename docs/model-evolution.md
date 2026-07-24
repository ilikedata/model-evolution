# Model Evolution

Model Evolution is the research provenance and artifact-lineage layer used by
Latent Arborist. It is incubated in this repository as the project-independent
`model_evolution` package and can later be extracted without moving the
Latent Arborist adapter into its core.

Other projects implement the `ProjectAdapter` protocol and may register it with
the `model_evolution.adapters` Python entry-point group. The core record,
storage, Git, and CLI layers do not import Latent Arborist until its configured
adapter is invoked.

Git is authoritative for lightweight intent, provenance, results, and decisions.
GCS is authoritative for immutable large artifacts. IDs combine a readable slug
with a ULID and are never reused.

## Records

The `.model-evolution/project.yaml` file selects the project, adapter, and
artifact store. Git-tracked records live under `model-evolution/`:

| Record | Format | Purpose |
| --- | --- | --- |
| Hypothesis | Markdown with YAML front matter | Research claim and expected evidence |
| Experiment | YAML | Planned comparison linked to hypotheses and config |
| Dataset | YAML | Generator/config/commit provenance and immutable GCS object tree |
| Run | YAML | One execution with pinned inputs, initialization, status, and outputs |
| Evaluation | YAML | Metrics and report against a pinned dataset |
| Module | YAML | Reusable weights, compatibility contract, and source run |
| Decision | Markdown with YAML front matter | Human-readable conclusions and approvals |

Run statuses are `planned`, `running`, `completed`, `failed`, or `interrupted`.
Failed and interrupted runs remain part of the history. Modules begin as
`candidate`; promotion requires a human instruction and produces a Markdown
decision note.

From-scratch runs explicitly have no parents:

```yaml
initialization:
  kind: from_scratch
  parents: []
```

Inherited runs pin exact module IDs. The Latent Arborist metric adapter verifies
the model dimensions, architecture contract, and tensor vocabulary before
loading inherited weights into a fresh optimizer and training schedule.

## Agent workflow

Set an agent identity and the credential path in the process environment:

```bash
export MODEL_EVOLUTION_ACTOR="agent-name"
export GOOGLE_APPLICATION_CREDENTIALS="$HOME/Downloads/latent-arborist-017d179c5642.json"
```

Never inspect, print, copy, or commit the credential file. The Python GCS client
consumes the path through Application Default Credentials.

The normal workflow is:

1. Record a hypothesis and experiment.
2. Generate/register an immutable dataset.
3. Plan and commit a run before allocating compute.
4. Claim and execute the run.
5. Record its evaluation and candidate module.
6. Promote only after explicit human approval.

Agents should also record consequential research-direction choices with
`make research-decision`. The generated Markdown keeps observations, inference,
confidence, and next action in separate sections and links the evidence records.

Use `make help` for the corresponding `research-*` targets. Examples:

```bash
make research-validate
make research-status
make research-gcs-probe
make research-backfill-codex
make research-backfill-validate
make research-backfill-upload-plan
make research-backfill-upload
make research-backfill-upload-verify

make research-dataset \
  DATASET_SLUG=metric-depth \
  DATASET_CONFIG=configs/data_depth_fixture.toml

make research-run-plan \
  RUN_SLUG=metric-depth \
  EXPERIMENT_ID=<experiment-id> \
  DATASET_ID=<dataset-id> \
  RUN_CONFIG=configs/metric_depth_fixture.toml

make research-run RUN_ID=<run-id>
make research-lineage RECORD_ID=<module-or-run-id>
```

An evidence-derived hypothesis should reference the canonical records that
support it:

```bash
make research-hypothesis \
  HYPOTHESIS_SLUG=source-conditioned-tape \
  TITLE="Source-conditioned action embeddings improve tape execution" \
  BODY_FILE=/path/to/hypothesis.md \
  REFERENCE_IDS="<run-id> <evaluation-id> <decision-id>"
```

References are validated and become part of the repository lineage graph.

Each mutation commits only its generated research records. Launches fail while
tracked source or configuration changes are uncommitted. This guarantees that
the recorded Git revision describes the code that ran.

## Immutable artifacts

Artifacts are stored below:

```text
gs://latent-arborist-models/model-evolution/latent-arborist/
  datasets/<dataset-id>/
  runs/<run-id>/
  modules/<module-name>/<module-id>/
  evaluations/<evaluation-id>/
  claims/<run-id>.json
```

Dataset directories are uploaded as object trees with a checksummed
`_index.json`. Every upload uses the GCS create-only generation precondition.
Existing objects cause a hard failure; Model Evolution exposes no overwrite or
delete operation. A one-shot claim prevents two agents from executing the same
run ID. Interrupted work is retried with a new run ID.

`make research-gcs-probe` is the opt-in live acceptance check. It creates,
reads back, and intentionally retains a uniquely named object under `probes/`.

The dataset record connects every immutable dataset to its generator entrypoint,
Git revision, config path and digest, seed, generated metadata digest, and GCS
index. Run records then pin that dataset ID, closing the provenance chain from
generator source to trained weights.

## Historical Codex session backfill

Historical reconstruction is deliberately separated from the canonical
Git-tracked registry. Run:

```bash
make research-backfill-codex
make research-backfill-validate
```

The extractor selects only sessions whose recorded working directory exactly
matches the project root. It writes an ignored local bundle under
`.model-evolution/work/backfill/codex/` containing:

- a source and policy manifest;
- normalized visible user/agent messages and tool interactions;
- local run/checkpoint, terminal TensorBoard scalar, and dataset-manifest identities;
- Git history and mentioned-path correlations;
- mechanically grounded run, module, evaluation, and dataset draft candidates;
- keyword-selected hypothesis, decision, and result review leads; and
- a human review report.

It excludes reasoning events, system/developer messages, compaction payloads,
and recently modified sessions. Secret-shaped values are redacted, large tool
payloads are truncated, and generic checkpoint discovery hashes `.pt` files.
When an adapter provides a safe historical inspector, it may additionally read
metadata using a weights-only loader; model, optimizer, RNG, and other tensor
state is omitted from the bundle. Raw transcripts remain in the local Codex
session store and are never copied to GCS.

Validation re-hashes every bundle file, original source session, discovered
checkpoint, metrics file, and dataset manifest. It also verifies evidence
uniqueness, source pointers, allowed event types, redaction safety, and recorded
Git commit availability. Replayed context copied into forked sessions remains
available as evidence but is marked and excluded from draft correlations and
review leads. Source-session validation hashes the exact captured byte prefix,
so a continuing Codex session may append new events without invalidating
historical evidence; changes within the captured prefix still fail validation.
TensorBoard event files are hashed and their final scalar point per tag is
recorded separately from best-checkpoint metrics. Use `--skip-source-digests` or
`--skip-artifact-digests` only for a fast diagnostic; reviewed backfill should
pass the full validation.

Review leads are not claims. Before creating a canonical record, reconcile its
technical fields against Git, artifact hashes, manifests, and metrics. A
backfilled record can preserve field-level evidence with optional provenance.
The generated `drafts.jsonl` keeps unknown dataset, module-initialization,
compatibility-contract, and Git fields explicitly unresolved rather than
guessing:

```yaml
provenance:
  - kind: codex_session
    locator: 019...#ev-a1b2c3
    sha256: 0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef
    claim_type: inferred
    confidence: medium
  - kind: run_metrics
    locator: runs/metric-v1/metrics.jsonl
    sha256: fedcba9876543210fedcba9876543210fedcba9876543210fedcba9876543210
    claim_type: observed
    confidence: high
```

Allowed provenance kinds are `codex_session`, `git`, `artifact`,
`dataset_manifest`, `run_metrics`, and `human_attestation`. Canonical repository
validation also rejects cyclic run/module weight inheritance.

### Uploading accepted historical artifacts

After review and full backfill validation, the historical import has three
explicit stages:

```bash
make research-backfill-upload-plan
make research-backfill-upload
make research-backfill-upload-verify
```

The plan is written under `.model-evolution/work/backfill/upload/`. It derives
its scope from dataset and run candidates in the reviewed bundle. Raw Codex
sessions, normalized evidence, Git-tracked research records, caches, and
unresolved or missing artifacts are not uploaded.

Datasets are packaged as deterministic `tar.zst` archives. This preserves every
file without creating millions of tiny GCS objects. Run files remain separate,
so checkpoints and reports can be addressed directly for module inheritance.
The import is stored below `historical/<import-id>/`; its immutable plan and
receipt are stored below `imports/<import-id>/`.

Every write uses `if_generation_match=0`. A retry skips an existing object only
when its byte size and SHA-256 metadata match the plan; any difference is an
immutable collision and stops the import. Upload verification checks every
object's generation, size, CRC32C presence, and SHA-256 metadata. There is no
overwrite or delete path.
