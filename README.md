# Model Evolution

Model Evolution keeps the reasoning behind a machine-learning model connected
to the exact data, code, runs, and artifacts that produced it.

You—or your coding agent—write the hypothesis, method, expected evidence, and
falsification condition in Markdown. Your existing project code executes the
experiment through a small adapter. Model Evolution records each attempt,
validates its lineage, publishes immutable artifacts, and keeps the conclusion
beside the original intent in Git.

It is not a training framework or experiment dashboard. It adds a durable,
inspectable research record around the tools you already use, without depending
on PyTorch, TensorFlow, or a particular model implementation.

## Why Model Evolution

Model development rarely leaves behind one coherent account of what happened.
The hypothesis may live in a conversation, the configuration in Git, metrics in
a report, and weights in object storage. Even when all of those pieces survive,
their relationships and the reasoning behind the final model are easy to lose.

Model Evolution gives an experiment one stable identity from plan to
conclusion:

```text
plan a claim -> execute one or more runs -> inspect evidence -> conclude
     Git              project adapter           records          Git
                              |
                              v
                     immutable artifacts
```

Git is authoritative for human-readable intent, reasoning, summaries, and
lineage. The configured artifact store is authoritative for immutable datasets,
weights, complete metrics, and reports. Checksums and typed references bind the
two together.

This makes it possible for a person or agent returning later to answer:

- What claim was this model intended to test?
- Which exact data, configuration, source revision, and parent modules did it
  use?
- How many execution attempts were made, and what happened in each?
- Which evidence supported or rejected the claim?
- Which outputs are safe to reuse in the next experiment?

## How an experiment evolves

An experiment has one public ID throughout its lifecycle. Its **study** record
is the canonical definition and eventual conclusion; **run** records capture
individual execution attempts. Datasets, modules, and assessments provide the
typed lineage around them.

| Record | What it captures |
| --- | --- |
| Study | Claim, method, expected evidence, falsification, and conclusion |
| Dataset | Generator provenance and an immutable dataset artifact |
| Run | Pinned inputs, primary results, artifacts, and anomalies for one attempt |
| Module | Reusable weights and their compatibility contract |
| Assessment | Later, additional, or independently versioned evaluation |

The records are Markdown with machine-validated YAML front matter. Record kind
comes from the directory, and record ID comes from the filename:

```text
model-evolution/
├── studies/
├── datasets/
├── runs/
├── modules/
└── assessments/
```

Agents can help author and review experiment plans, inspect recorded evidence,
and write conclusions. The repository-provided agent workflow keeps execution
as an explicit owner action, so training, evaluation, dataset generation, and
their costs are never triggered merely by asking an agent to manage the
research record.

## Installation

Model Evolution requires Python 3.12 or newer and a Git repository.

```bash
python -m pip install model-evolution
```

Install Google Cloud Storage support when a project uses a `gs://` artifact
store:

```bash
python -m pip install "model-evolution[gcs]"
```

## Quick start

Initialize and commit the repository before initializing Model Evolution:

```bash
git init
git add .
git commit -m "Initial project"

model-evolution init \
  --project-id example \
  --artifact-store file:///absolute/path/to/artifacts \
  --adapter example
```

The adapter name identifies project code that performs dataset generation,
training, and artifact inspection. You register that adapter as described in
[Connecting project code](#connecting-project-code); `example` is a placeholder,
not a built-in trainer.

You or your agent then create a study under
`model-evolution/studies/<experiment-id>.md`. A planned study describes the
claim before execution:

```markdown
---
status: planned
design:
  dataset_id: <dataset-id>
  config: configs/example.toml
  inherited_modules: []
---

# Test a smaller learning rate

## Claim

Reducing the learning rate will improve held-out accuracy.

## Basis

The baseline run showed unstable validation loss near convergence.

## Expected evidence

Held-out accuracy exceeds the baseline by at least one percentage point.

## Falsification

Reject the claim if the improvement is smaller than one percentage point.

## Method

Repeat the baseline configuration with only the learning rate changed.
```

Use the same experiment ID through planning, execution, inspection, and
conclusion:

```bash
model-evolution experiment plan model-evolution/studies/<experiment-id>.md
model-evolution experiment run <experiment-id>
model-evolution experiment show <experiment-id>
model-evolution experiment conclude <experiment-id>
```

The configured project adapter is selected automatically. By default, lifecycle
commands create narrowly scoped Git commits containing the records they change.
Pass the global `--no-commit` option before the subcommand when another caller
needs to manage commits itself. Experiment execution always requires committed
lifecycle records.

The project configuration lives at `.model-evolution/project.yaml`. Generated
working state belongs under `.model-evolution/work/` and should be ignored by
Git. Use these commands to check the registry at any time:

```bash
model-evolution validate
model-evolution status
```

## Connecting project code

An adapter connects the generic lifecycle to a project's dataset generator,
trainer, and artifact formats:

```python
from pathlib import Path
from typing import Any

from model_evolution import ModelEvolution


class ExampleAdapter:
    name = "example"

    def generate_dataset(
        self,
        service: ModelEvolution,
        *,
        slug: str,
        config_path: str | Path,
    ) -> dict[str, Any]:
        ...

    def execute_run(
        self,
        service: ModelEvolution,
        run_id: str,
        *,
        epochs_this_run: int | None = None,
    ) -> dict[str, Any]:
        ...

    def inspect_artifact(self, path: str | Path) -> dict[str, Any] | None:
        ...
```

Register it from the consuming project's `pyproject.toml`:

```toml
[project.entry-points."model_evolution.adapters"]
example = "example_project.research:ExampleAdapter"
```

The entry-point name must match the adapter's `name` attribute. Model Evolution
loads adapters only when an adapter-backed command needs one.

## Supported interface

The supported Python interface is exported from `model_evolution`:

- `ModelEvolution`
- `ProjectAdapter`
- `ProjectConfig`
- `conclude_experiment`
- `download_tree`
- `execute_experiment`
- `initialize_project`
- `load_experiment`
- `load_record`
- `load_project`
- `new_id`
- `now`
- `plan_experiment`
- `record_path`
- `require_clean_source`
- `require_committed_file`
- `upload_file`
- `upload_tree`

The CLI, schema-v2 record layout, and adapter entry-point group
`model_evolution.adapters` are also compatibility surfaces. Other module paths
remain provisional during the `0.x` series. Projects should import supported
functions from `model_evolution`, not their implementation modules.

The existing `study`, `run`, and record-oriented interfaces remain available
for compatibility. See [the user guide](docs/model-evolution.md) and
[schema-v2 design](docs/model-evolution-v2.md) for the complete lifecycle and
record formats.

## Agent-assisted workflow

This repository includes the standalone
[`manage-model-evolution-experiments`](skills/manage-model-evolution-experiments/SKILL.md)
skill for coding agents working in projects that use Model Evolution. It has two
separate modes:

- plan one focused experiment and hand its exact execution command to the
  repository owner; or
- conclude an experiment from evidence the owner has already produced.

The skill never executes experiments, training, evaluation, or dataset
generation. It is maintained in this repository but installed separately from
the Python package. See the
[experiment skill guide](docs/experiment-skill.md) for installation, usage,
execution handoff, and stop conditions.

## Development

```bash
make sync
make check
make build
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for the contribution and release process.

## License

Apache License 2.0. See [LICENSE](LICENSE).
