from __future__ import annotations

import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from model_evolution.adapters.latent_arborist import ADAPTER_NAME, execute_metric_run
from model_evolution.config import initialize_project, load_project
from model_evolution.ids import new_id, observed_id
from model_evolution.records import (
    load_document,
    load_record,
    validate_repository,
    validate_record,
    write_record,
)
from model_evolution.service import ModelEvolution
from model_evolution.storage import (
    ArtifactCollisionError,
    LocalArtifactStore,
    download_tree,
    upload_tree,
)
from model_evolution.yamlio import write_markdown


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True)


def _metric_config(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        """
dataset = "unused"
cache = "unused"
run_dir = "unused"
seed = 1729

[model]
latent_dim = 64
embedding_dim = 16
ema_decay = 0.996
max_depth = 3

[training]
batch_size = 4
epochs = 1
learning_rate = 0.001
weight_decay = 0.0001
warmup_steps = 1
gradient_clip = 1.0
early_stopping_patience = 1
num_workers = 0
shard_size = 8
""".strip()
        + "\n",
        encoding="utf-8",
    )


STUDY_BODY = """# Metric geometry

## Claim

The metric encoder will learn useful geometry.

## Basis

The generated data contains exact structural distances.

## Expected evidence

Held-out loss improves.

## Falsification

Reject if held-out loss does not improve.

## Method

Train one metric encoder against the pinned fixture.
"""


class ProjectCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        _git(self.root, "init")
        _git(self.root, "config", "user.email", "test@example.com")
        _git(self.root, "config", "user.name", "Model Evolution Test")
        (self.root / "README.md").write_text("# test\n", encoding="utf-8")
        self.config_path = self.root / "configs" / "metric.toml"
        _metric_config(self.config_path)
        _git(self.root, "add", "README.md", "configs/metric.toml")
        _git(self.root, "commit", "-m", "initial")
        self.artifacts = self.root / ".model-evolution" / "work" / "artifacts"
        initialize_project(
            self.root,
            project_id="test-project",
            artifact_store=self.artifacts.as_uri(),
            adapter="latent-arborist",
        )
        _git(self.root, "add", ".model-evolution/project.yaml", "model-evolution")
        _git(self.root, "commit", "-m", "initialize model evolution")
        self.project = load_project(self.root)
        self.service = ModelEvolution(
            self.project,
            store=LocalArtifactStore(self.artifacts),
            actor="test-agent",
            commit=False,
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def create_dataset(self) -> dict:
        source = self.root / "data" / "generated" / "fixture"
        source.mkdir(parents=True, exist_ok=True)
        (source / "dataset.json").write_text(
            json.dumps({"dataset_version": "fixture-v1"}) + "\n",
            encoding="utf-8",
        )
        (source / "sample.txt").write_text("sample\n", encoding="utf-8")
        return self.service.register_dataset(
            "fixture",
            source=source,
            generator="test generator",
            generator_config=self.config_path,
            seed=1729,
        )

    def create_study(
        self,
        dataset_id: str,
        *,
        study_id: str = "metric-study",
        inherited_modules: list[dict[str, str]] | None = None,
    ) -> dict:
        front = {
            "status": "planned",
            "references": [],
            "design": {
                "dataset_id": dataset_id,
                "config": "configs/metric.toml",
                "inherited_modules": inherited_modules or [],
            },
        }
        write_markdown(
            self.project.records_dir / "studies" / f"{study_id}.md",
            front,
            STUDY_BODY,
        )
        return load_record(self.project, "study", study_id)


class IdTests(unittest.TestCase):
    def test_id_is_readable_ulid(self) -> None:
        value = new_id("Metric Depth")
        self.assertRegex(value, r"^metric-depth-[0-9A-HJKMNP-TV-Z]{26}$")

    def test_observed_id_is_stable_and_source_specific(self) -> None:
        timestamp = "2026-07-24T00:26:03Z"
        first = observed_id("Metric Depth", timestamp, "runs/metric-depth")
        self.assertEqual(
            first,
            observed_id("Metric Depth", timestamp, "runs/metric-depth"),
        )
        self.assertNotEqual(
            first,
            observed_id("Metric Depth", timestamp, "runs/other"),
        )
        self.assertRegex(first, r"^metric-depth-[0-9A-HJKMNP-TV-Z]{26}$")


class StorageTests(unittest.TestCase):
    def test_tree_round_trip_and_collision(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            source.mkdir()
            (source / "a.txt").write_text("alpha", encoding="utf-8")
            (source / "nested").mkdir()
            (source / "nested" / "b.txt").write_text("beta", encoding="utf-8")
            store = LocalArtifactStore(root / "store")
            result = upload_tree(store, source, "datasets/example")
            self.assertEqual(result["files"], 2)
            destination = root / "download"
            download_tree(store, "datasets/example", destination)
            self.assertEqual((destination / "nested" / "b.txt").read_text(), "beta")
            with self.assertRaises(ArtifactCollisionError):
                upload_tree(store, source, "datasets/example")


class RegistryTests(ProjectCase):
    def test_component_outcomes_are_typed_and_component_scoped(self) -> None:
        dataset = self.create_dataset()
        study = self.create_study(dataset["id"], study_id="components")
        stored, body = load_document(self.project, "study", study["id"])
        stored["component_outcomes"] = [
            {
                "component": "semantic_policy",
                "outcome": "supported",
                "summary": "Held-out semantic execution passed.",
                "reusable": True,
                "metrics": {"accuracy": 0.8},
            },
            {
                "component": "legacy_exact_match",
                "outcome": "not_diagnostic",
                "summary": "The legacy target contains hidden randomness.",
                "reason": "The target magnitude is absent from the prompt.",
            },
        ]
        validate_record(stored, body=body, expected_kind="study")

        stored["component_outcomes"][0]["outcome"] = "promising"
        with self.assertRaisesRegex(ValueError, "component outcome"):
            validate_record(stored, body=body, expected_kind="study")

    def test_study_identity_is_derived_from_path_and_git_commit_is_scoped(self) -> None:
        dataset = self.create_dataset()
        study = self.create_study(dataset["id"], study_id="path-derived")
        unrelated = self.root / "notes.txt"
        unrelated.write_text("do not commit\n", encoding="utf-8")
        service = ModelEvolution(
            self.project,
            store=LocalArtifactStore(self.artifacts),
            actor="test-agent",
            commit=True,
        )
        committed = service.commit_study(
            self.project.records_dir / "studies" / "path-derived.md"
        )
        self.assertEqual(study["id"], "path-derived")
        self.assertEqual(committed["kind"], "study")
        shown = subprocess.run(
            ["git", "show", "--pretty=format:", "--name-only", "HEAD"],
            cwd=self.root,
            check=True,
            capture_output=True,
            text=True,
        )
        self.assertEqual(shown.stdout.strip(), "model-evolution/studies/path-derived.md")
        self.assertTrue(unrelated.exists())

    def test_study_requires_research_sections(self) -> None:
        write_markdown(
            self.project.records_dir / "studies" / "incomplete.md",
            {"status": "draft", "references": []},
            "# Incomplete\n\n## Claim\n\nA claim.",
        )
        with self.assertRaisesRegex(ValueError, "missing Markdown sections"):
            validate_repository(self.project)

    def test_dataset_run_lineage_and_config_pinning(self) -> None:
        dataset = self.create_dataset()
        study = self.create_study(dataset["id"])
        run = self.service.plan_run("metric", study_id=study["id"], adapter=ADAPTER_NAME)
        self.assertEqual(run["initialization"], {"kind": "from_scratch", "parents": []})
        self.assertEqual(run["config"]["path"], "configs/metric.toml")
        self.assertEqual(len(run["config"]["sha256"]), 64)
        lineage = self.service.lineage(run["id"])
        self.assertEqual(
            {record["id"] for record in lineage["records"]},
            {run["id"], dataset["id"], study["id"]},
        )

    def test_source_changes_block_run_planning(self) -> None:
        dataset = self.create_dataset()
        study = self.create_study(dataset["id"])
        (self.root / "README.md").write_text("changed\n", encoding="utf-8")
        with self.assertRaisesRegex(RuntimeError, "must be committed"):
            self.service.plan_run("blocked", study_id=study["id"], adapter=ADAPTER_NAME)

    def test_metric_adapter_embeds_results_and_publishes_available_module(self) -> None:
        dataset = self.create_dataset()
        study = self.create_study(dataset["id"])
        run = self.service.plan_run("metric", study_id=study["id"], adapter=ADAPTER_NAME)

        def fake_train(config, **kwargs):
            del kwargs
            config.run_dir.mkdir(parents=True, exist_ok=True)
            (config.run_dir / "best.pt").write_bytes(b"weights")
            (config.run_dir / "latest.pt").write_bytes(b"weights")
            (config.run_dir / "metrics.jsonl").write_text('{"loss": 1.0}\n', encoding="utf-8")
            return {"best_loss": 1.0, "epochs_completed": 1, "global_step": 1}

        def fake_evaluate(checkpoint, *, split):
            report = {
                "checkpoint": str(checkpoint),
                "split": split,
                "metrics": {"loss": 1.0},
                "quality_gate": {"passed": True},
            }
            Path(checkpoint).parent.joinpath("test-report.json").write_text(
                json.dumps(report) + "\n", encoding="utf-8"
            )
            return report

        with (
            patch(
                "model_evolution.adapters.latent_arborist._download_dataset",
                return_value=self.root / "dataset",
            ),
            patch(
                "model_evolution.adapters.latent_arborist._prepare_cache",
                return_value=self.root / "cache",
            ),
            patch("model_evolution.adapters.latent_arborist.train_metric", side_effect=fake_train),
            patch(
                "model_evolution.adapters.latent_arborist.evaluate_checkpoint",
                side_effect=fake_evaluate,
            ),
        ):
            result = execute_metric_run(self.service, run["id"])

        completed = load_record(self.project, "run", run["id"])
        module = load_record(self.project, "module", result["module_id"])
        self.assertEqual(completed["status"], "completed")
        self.assertEqual(completed["results"]["primary"]["split"], "test")
        self.assertEqual(completed["module_ids"], [module["id"]])
        self.assertEqual(module["status"], "available")
        self.assertTrue(validate_repository(self.project)["valid"])

    def test_failed_run_records_error_and_surviving_artifacts(self) -> None:
        dataset = self.create_dataset()
        study = self.create_study(dataset["id"])
        run = self.service.plan_run("failure", study_id=study["id"], adapter=ADAPTER_NAME)

        def failing_train(config, **kwargs):
            del kwargs
            config.run_dir.mkdir(parents=True, exist_ok=True)
            (config.run_dir / "metrics.jsonl").write_text('{"loss": 9.0}\n', encoding="utf-8")
            raise RuntimeError("training failed")

        with (
            patch(
                "model_evolution.adapters.latent_arborist._download_dataset",
                return_value=self.root / "dataset",
            ),
            patch(
                "model_evolution.adapters.latent_arborist._prepare_cache",
                return_value=self.root / "cache",
            ),
            patch("model_evolution.adapters.latent_arborist.train_metric", side_effect=failing_train),
        ):
            with self.assertRaisesRegex(RuntimeError, "training failed"):
                execute_metric_run(self.service, run["id"])

        failed = load_record(self.project, "run", run["id"])
        self.assertEqual(failed["status"], "failed")
        self.assertEqual(failed["error"]["type"], "RuntimeError")
        self.assertTrue(failed["artifact"]["incomplete"])

    def test_available_module_can_be_inherited_and_deprecated_module_cannot(self) -> None:
        dataset = self.create_dataset()
        first_study = self.create_study(dataset["id"], study_id="first")
        source_run = self.service.plan_run(
            "source", study_id=first_study["id"], adapter=ADAPTER_NAME
        )
        weights = self.project.work_dir / "weights.pt"
        weights.parent.mkdir(parents=True, exist_ok=True)
        weights.write_bytes(b"weights")
        module = self.service.create_module(
            slug="metric",
            module_name="metric_encoder",
            source_run=source_run["id"],
            source_weights=weights,
            contract={"architecture": "test", "version": 1},
        )
        inherited_study = self.create_study(
            dataset["id"],
            study_id="inherited",
            inherited_modules=[{"role": "metric_encoder", "module_id": module["id"]}],
        )
        inherited = self.service.plan_run(
            "inherited", study_id=inherited_study["id"], adapter=ADAPTER_NAME
        )
        self.assertEqual(inherited["initialization"]["parents"][0]["module_id"], module["id"])
        self.assertEqual(
            inherited["initialization"]["parents"][0]["sha256"],
            module["artifact"]["sha256"],
        )

        stored, body = load_document(self.project, "module", module["id"])
        stored["status"] = "deprecated"
        write_record(
            self.project,
            "module",
            stored,
            body=body,
            replace_existing=True,
        )
        with self.assertRaisesRegex(ValueError, "not available"):
            self.service.plan_run(
                "blocked", study_id=inherited_study["id"], adapter=ADAPTER_NAME
            )

    def test_later_assessment_is_independent(self) -> None:
        dataset = self.create_dataset()
        study = self.create_study(dataset["id"])
        run = self.service.plan_run("metric", study_id=study["id"], adapter=ADAPTER_NAME)
        assessment = self.service.create_assessment(
            run_id=run["id"],
            dataset_id=dataset["id"],
            evaluator={"name": "benchmark", "version": "2"},
            metrics={"accuracy": 0.8},
            artifact={"uri": "file:///report", "sha256": "a" * 64},
            purpose="Evaluate against a later protocol.",
        )
        self.assertEqual(load_record(self.project, "assessment", assessment["id"])["run_id"], run["id"])
        self.assertTrue(validate_repository(self.project)["valid"])

    def test_validation_rejects_module_inheritance_cycle(self) -> None:
        dataset = self.create_dataset()
        study = self.create_study(dataset["id"])
        run = self.service.plan_run("cycle", study_id=study["id"], adapter=ADAPTER_NAME)
        weights = self.project.work_dir / "cycle.pt"
        weights.parent.mkdir(parents=True, exist_ok=True)
        weights.write_bytes(b"weights")
        module = self.service.create_module(
            slug="cycle",
            module_name="metric_encoder",
            source_run=run["id"],
            source_weights=weights,
            contract={"architecture": "test", "version": 1},
        )
        run["initialization"] = {
            "kind": "inherited",
            "parents": [
                {
                    "role": "metric_encoder",
                    "module_id": module["id"],
                    "sha256": module["artifact"]["sha256"],
                }
            ],
        }
        _, body = load_document(self.project, "run", run["id"])
        write_record(self.project, "run", run, body=body, replace_existing=True)
        with self.assertRaisesRegex(ValueError, "inheritance cycle"):
            validate_repository(self.project)

    def test_validation_rejects_inheritance_hash_drift(self) -> None:
        dataset = self.create_dataset()
        study = self.create_study(dataset["id"])
        source_run = self.service.plan_run(
            "source", study_id=study["id"], adapter=ADAPTER_NAME
        )
        weights = self.project.work_dir / "hash-drift.pt"
        weights.parent.mkdir(parents=True, exist_ok=True)
        weights.write_bytes(b"weights")
        module = self.service.create_module(
            slug="hash-drift",
            module_name="metric_encoder",
            source_run=source_run["id"],
            source_weights=weights,
            contract={"architecture": "test", "version": 1},
        )
        inherited_study = self.create_study(
            dataset["id"],
            study_id="hash-drift",
            inherited_modules=[{"module_id": module["id"]}],
        )
        inherited = self.service.plan_run(
            "hash-drift", study_id=inherited_study["id"], adapter=ADAPTER_NAME
        )
        inherited["initialization"]["parents"][0]["sha256"] = "f" * 64
        _, body = load_document(self.project, "run", inherited["id"])
        write_record(
            self.project,
            "run",
            inherited,
            body=body,
            replace_existing=True,
        )
        with self.assertRaisesRegex(ValueError, "parent hash does not match"):
            validate_repository(self.project)

    def test_validation_rejects_wrong_reference_kind(self) -> None:
        dataset = self.create_dataset()
        self.create_study(dataset["id"], study_id="wrong-kind")
        front, body = load_document(self.project, "study", "wrong-kind")
        front["design"]["dataset_id"] = "wrong-kind"
        write_record(
            self.project,
            "study",
            front,
            body=body,
            replace_existing=True,
        )
        with self.assertRaisesRegex(ValueError, "must reference a dataset"):
            validate_repository(self.project)


if __name__ == "__main__":
    unittest.main()
