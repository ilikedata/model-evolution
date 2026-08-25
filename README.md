# Model Evolution

Model Evolution is a Git-backed research provenance and artifact-lineage tool
for machine-learning projects. It keeps research intent human-readable in
Markdown, validates references between studies, datasets, runs, modules, and
assessments, and publishes immutable artifacts without embedding a particular
training framework.

Project-specific dataset generation, training, and artifact inspection are
provided by installable adapters. The core package does not depend on PyTorch,
TensorFlow, or a model implementation.

## Installation

```bash
python -m pip install model-evolution
```

Install Google Cloud Storage support when a project uses a `gs://` artifact
store:

```bash
python -m pip install "model-evolution[gcs]"
```

Model Evolution requires Python 3.12 or newer and a Git repository.

## Quick start

Initialize a Git repository and commit its initial files before initializing
Model Evolution:

```bash
git init
git add .
git commit -m "Initial project"

model-evolution init \
  --project-id example \
  --artifact-store file:///absolute/path/to/artifacts \
  --adapter example

model-evolution validate
model-evolution status
```

The preferred lifecycle uses one experiment ID from planning through
conclusion:

```bash
model-evolution experiment plan model-evolution/studies/<experiment-id>.md
model-evolution experiment run <experiment-id>
model-evolution experiment show <experiment-id>
model-evolution experiment conclude <experiment-id>
```

The configured project adapter is used automatically. The existing `study`,
`run`, and record-oriented interfaces remain available for compatibility.

By default, lifecycle commands create narrowly scoped Git commits containing
the records they change. Pass the global `--no-commit` option before the
subcommand when a caller needs to manage commits itself.

The project configuration lives at `.model-evolution/project.yaml`. Canonical
Markdown records live under `model-evolution/`; generated working state belongs
under `.model-evolution/work/` and should be ignored by Git.

## Project adapters

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

The initial supported Python interface is exported from `model_evolution`:

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

See [the user guide](docs/model-evolution.md) and
[schema-v2 design](docs/model-evolution-v2.md) for the complete lifecycle.

## Development

```bash
make sync
make check
make build
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for the contribution and release process.

## License

Apache License 2.0. See [LICENSE](LICENSE).
