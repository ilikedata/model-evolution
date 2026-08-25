from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import model_evolution
from model_evolution.cli import main
from model_evolution.config import initialize_project, load_project
from model_evolution.records import load_document
from model_evolution.service import ModelEvolution
from model_evolution.storage import LocalArtifactStore
from model_evolution.yamlio import write_markdown


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True)


class _ConfiguredAdapter:
    name = "example"

    def execute_run(
        self,
        service: ModelEvolution,
        run_id: str,
        *,
        epochs_this_run: int | None = None,
    ) -> dict:
        run = model_evolution.load_record(service.project, "run", run_id)
        service.update_run(
            run,
            status="completed",
            results={"score": 1.0},
            artifacts=[],
            module_ids=[],
        )
        return model_evolution.load_record(service.project, "run", run_id)


class ExperimentLifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        _git(self.root, "init")
        _git(self.root, "config", "user.email", "test@example.com")
        _git(self.root, "config", "user.name", "Model Evolution Test")
        (self.root / "README.md").write_text("# test\n", encoding="utf-8")
        config_path = self.root / "configs" / "experiment.toml"
        config_path.parent.mkdir(parents=True)
        config_path.write_text("seed = 1729\n", encoding="utf-8")
        _git(self.root, "add", "README.md", "configs/experiment.toml")
        _git(self.root, "commit", "-m", "initial")

        self.artifacts = self.root / ".model-evolution" / "work" / "artifacts"
        initialize_project(
            self.root,
            project_id="test-project",
            artifact_store=self.artifacts.as_uri(),
            adapter="example",
        )
        _git(self.root, "add", ".model-evolution/project.yaml", "model-evolution")
        _git(self.root, "commit", "-m", "initialize model evolution")
        self.project = load_project(self.root)
        service = ModelEvolution(
            self.project,
            store=LocalArtifactStore(self.artifacts),
            actor="test-agent",
            commit=False,
        )
        source = self.root / "data" / "fixture"
        source.mkdir(parents=True)
        (source / "dataset.json").write_text(
            json.dumps({"dataset_version": "fixture-v1"}) + "\n",
            encoding="utf-8",
        )
        _git(self.root, "add", "data/fixture/dataset.json")
        _git(self.root, "commit", "-m", "add fixture source")
        dataset = service.register_dataset(
            "fixture",
            source=source,
            generator="fixture generator",
            generator_config=config_path,
            seed=1729,
        )
        self.experiment_id = "metric-geometry"
        self.definition_path = (
            self.project.records_dir / "studies" / f"{self.experiment_id}.md"
        )
        write_markdown(
            self.definition_path,
            {
                "status": "planned",
                "references": [],
                "design": {
                    "dataset_id": dataset["id"],
                    "config": "configs/experiment.toml",
                    "inherited_modules": [],
                },
            },
            """# Metric geometry

## Claim

The encoder learns useful geometry.

## Basis

The fixture has exact distances.

## Expected evidence

The score reaches one.

## Falsification

Reject if the score is below one.

## Method

Execute the configured adapter.
""",
        )
        _git(self.root, "add", "model-evolution/datasets")
        _git(self.root, "commit", "-m", "register fixture")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_supported_functions_drive_one_experiment_lifecycle(self) -> None:
        planned = model_evolution.plan_experiment(self.definition_path, root=self.root)
        self.assertEqual(planned["kind"], "experiment")
        self.assertEqual(planned["id"], self.experiment_id)
        self.assertEqual(planned["status"], "planned")
        self.assertEqual(planned["definition"]["kind"], "study")
        self.assertEqual(planned["runs"], [])

        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            exit_code = main(
                [
                    "--root",
                    str(self.root),
                    "--json",
                    "experiment",
                    "show",
                    self.experiment_id,
                ]
            )
        self.assertEqual(exit_code, 0)
        self.assertEqual(json.loads(output.getvalue()), planned)

        configured_names: list[str] = []

        def load_configured_adapter(name: str) -> _ConfiguredAdapter:
            configured_names.append(name)
            return _ConfiguredAdapter()

        with patch(
            "model_evolution.experiments.load_adapter",
            side_effect=load_configured_adapter,
        ):
            executed = model_evolution.execute_experiment(
                self.experiment_id,
                root=self.root,
                actor="test-agent",
            )

        self.assertEqual(configured_names, ["example"])
        self.assertEqual(executed["id"], self.experiment_id)
        self.assertEqual(len(executed["runs"]), 1)
        self.assertEqual(executed["runs"][0]["status"], "completed")
        self.assertEqual(executed["runs"][0]["adapter"], "example")
        self.assertEqual(
            model_evolution.load_experiment(self.experiment_id, root=self.root),
            executed,
        )

        definition, body = load_document(self.project, "study", self.experiment_id)
        definition["status"] = "concluded"
        definition["conclusion"] = {
            "outcome": "supported",
            "confidence": "high",
            "evidence": [executed["runs"][0]["id"]],
        }
        write_markdown(
            self.definition_path,
            {key: value for key, value in definition.items() if key not in {"kind", "id"}},
            body
            + """

## Observations

The configured adapter completed successfully.

## Conclusion

The evidence supports the claim.

## Next action

Retain the result.
""",
        )
        concluded = model_evolution.conclude_experiment(self.experiment_id, root=self.root)
        self.assertEqual(concluded["status"], "concluded")
        self.assertEqual(concluded["runs"], executed["runs"])

    def test_execution_requires_committed_lifecycle_records(self) -> None:
        model_evolution.plan_experiment(self.definition_path, root=self.root, commit=False)

        with self.assertRaisesRegex(ValueError, "committed lifecycle records"):
            model_evolution.execute_experiment(
                self.experiment_id,
                root=self.root,
                commit=False,
            )

    def test_adapter_configuration_failure_does_not_create_a_run(self) -> None:
        model_evolution.plan_experiment(self.definition_path, root=self.root)

        with patch(
            "model_evolution.experiments.load_adapter",
            side_effect=ValueError("unknown Model Evolution adapter: example"),
        ):
            with self.assertRaisesRegex(ValueError, "unknown Model Evolution adapter"):
                model_evolution.execute_experiment(self.experiment_id, root=self.root)

        experiment = model_evolution.load_experiment(self.experiment_id, root=self.root)
        self.assertEqual(experiment["runs"], [])

    def test_experiment_id_cannot_escape_the_records_directory(self) -> None:
        with self.assertRaisesRegex(ValueError, "plain record ID"):
            model_evolution.load_experiment("../outside", root=self.root)


if __name__ == "__main__":
    unittest.main()
