# Model Evolution

Model Evolution is the Markdown-first research provenance and artifact-lineage
layer used by Latent Arborist. Git is authoritative for research intent,
reasoning, summaries, and lineage. GCS is authoritative for immutable datasets,
weights, complete metrics, and reports after publication.

## Records

The `.model-evolution/project.yaml` file selects the project, adapter, and
artifact store. Record kind comes from the directory and record ID comes from
the Markdown filename:

| Directory | Purpose |
| --- | --- |
| `studies/` | Claim, method, expected evidence, conclusion, and next action |
| `datasets/` | Generator provenance and versioned dataset artifact |
| `runs/` | Pinned execution inputs, primary results, artifacts, and anomalies |
| `modules/` | Reusable weights and compatibility contract |
| `assessments/` | Later, additional, or independently versioned evaluation |

IDs normally use a readable slug plus ULID. When contemporaneous work is
materialized from corroborated evidence, the ULID timestamp is derived from the
earliest observed event and its random component from the source identity. This
makes repeated materialization stable without rewriting Git history.

YAML front matter contains machine-validated state and references. Markdown
contains the explanation. Git supplies authorship and change history.

## Study authoring

Create the canonical file directly under `model-evolution/studies/`. Its
filename is its stable ID:

```markdown
---
status: planned
references:
  - <prior-run-id>
design:
  dataset_id: <dataset-id>
  config: configs/example.toml
  baseline_run_id: <optional-run-id>
  inherited_modules:
    - role: metric_encoder
      module_id: <module-id>
---

# Study title

## Claim

The claim being tested.

## Basis

Why the claim is plausible and which prior evidence supports it.

## Expected evidence

The measurable success criterion.

## Falsification

The result that rejects the claim.

## Method

The controlled comparison to execute.
```

Validate and commit the plan:

```bash
make research-plan STUDY=model-evolution/studies/<study-id>.md
```

Study states are `draft`, `planned`, `active`, `concluded`, and `cancelled`.
Concluded studies add structured outcome, confidence, and evidence in front
matter plus `Observations`, `Conclusion`, and `Next action` sections:

```bash
make research-conclude STUDY=model-evolution/studies/<study-id>.md
```

## Component outcomes

Studies and runs may decompose a combined result into machine-readable
component outcomes. Use this when an overall gate combines independently
useful capabilities and a single pass/fail label would hide what should be
retained or changed:

```yaml
component_outcomes:
- component: semantic_transform_policy
  outcome: supported
  summary: Held-out semantic execution passed every policy gate.
  reusable: true
  metrics:
    closed_loop_semantic_accuracy: 0.8046875
- component: transition_oracle
  outcome: rejected
  summary: The predicted local target moved away from the observed checkpoint.
  reusable: false
  metrics:
    goal_relative_progress: -0.6991605
- component: exact_legacy_svg_match
  outcome: not_diagnostic
  summary: The legacy target contains an unexpressed random magnitude.
  reason: Exact equality cannot measure qualitative relative execution.
```

Allowed outcomes are `supported`, `rejected`, `inconclusive`, and
`not_diagnostic`. Each entry requires a stable component name and concise
summary. `reusable` and `metrics` are optional. A `not_diagnostic` result must
explain why the measurement cannot test the component.

Component outcomes do not override the study's overall conclusion. They make
partial success explicit and identify which checkpoint components may be
legitimate parents for later studies. Prefer these evidence-bound terms over
the subjective label `promising`.

## Runs, modules, and assessments

Plan and execute a run from the study's design:

```bash
make research-run-plan RUN_SLUG=<slug> STUDY_ID=<study-id>
make research-run RUN_ID=<run-id>
```

The run snapshots the tracked config digest, source revision, dataset,
baseline, and inherited module IDs and hashes. From-scratch initialization has
no parents. Successful execution embeds its primary result and publishes every
adapter-declared module as `available`. There is no promotion or approval gate.

Modules may be `available`, `deprecated`, or `unavailable`. Deprecated modules
remain in lineage but cannot initialize new work. Unavailable module records
preserve dependencies whose exact artifact cannot be recovered.

Use an assessment only when evaluation is later or independently versioned:

```bash
make research-assess \
  RUN_ID=<run-id> \
  DATASET_ID=<dataset-id> \
  REPORT=<report.json> \
  EVALUATOR=<name> \
  EVALUATOR_VERSION=<version> \
  PURPOSE="<reason>"
```

## Availability

Canonical records distinguish three artifact states:

```yaml
# Present in this repository workspace
status: local
path: runs/example/best.pt
sha256: <digest>

# Published immutable object
status: available
uri: gs://bucket/prefix/runs/<run-id>/best.pt
sha256: <digest>
generation: <generation>
crc32c: <checksum>

# Exact value or artifact cannot be obtained
status: unavailable
```

Missing scalar facts use `{status: unavailable}`. Missing dependency artifacts
receive normal dataset or module records with status `unavailable`, preserving
a navigable graph without inventing replacements.

## Codex evidence

Codex sessions are one evidence source, not a record type. Collect and validate
only sessions whose working directory exactly matches this repository:

```bash
make research-evidence-codex
make research-evidence-validate
```

Normalized evidence lives under `.model-evolution/work/evidence/codex`. The
collector includes visible user/agent messages and tool calls/results; excludes
reasoning, system/developer messages, compaction payloads, and raw transcript
copies; redacts secret-shaped values; and hashes the captured source prefix.
Agents use the evidence to author ordinary studies, datasets, runs, modules,
and assessments.

## Artifact publishing

The routine interface is one command:

```bash
make research-publish
```

It validates the registry, builds the deterministic storage plan, packages
datasets, performs create-only uploads, verifies every remote size and SHA-256,
writes a receipt, adds verified GCS identities to the canonical records, and
commits only the changed record documents. Interactive runs show TQDM progress
for dataset scanning, hashing and packaging, bytes transferred, and remote
object verification.

The plan and receipt live under `.model-evolution/work/storage`. Dataset trees
become deterministic `tar.zst` archives. Other files remain individually
addressable. Destinations are derived directly from record IDs:

```text
datasets/<dataset-id>/tree.tar.zst
runs/<run-id>/<artifact>
modules/<module-name>/<module-id>/weights.pt
assessments/<assessment-id>/<artifact>
```

Every GCS creation uses an object-generation precondition that forbids
overwriting. On a retry, an existing object is accepted only when its stored
SHA-256 and size match the plan. No publish path deletes an object.

Verified dataset packages and file hashes are cached by canonical digest under
`.model-evolution/work/storage/cache`. A repeated publish reuses the packages,
skips unchanged file hashing, and checks GCS metadata before reading any local
upload bytes. Cache entries invalidate independently when a source file,
package, expected digest, or packaging version changes.

`make research-storage-plan` remains available as a non-publishing diagnostic.
Use `STORAGE_REBUILD=1 make research-publish` only for a deliberate full
rehash and repackaging audit.

## Authentication and routine commands

Set actor identity and the credential path in the process environment:

```bash
export MODEL_EVOLUTION_ACTOR="agent-name"
export GOOGLE_APPLICATION_CREDENTIALS="$HOME/Downloads/latent-arborist-017d179c5642.json"
```

Never inspect, print, copy, or commit the credential file.

```bash
make research-validate
make research-status
make research-lineage RECORD_ID=<record-id>
make research-publish
```

Launches reject uncommitted source or configuration changes. Each lifecycle
mutation commits only its generated research documents.
