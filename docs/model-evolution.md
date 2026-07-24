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
