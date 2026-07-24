from __future__ import annotations

import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from model_evolution.adapters.latent_arborist import ADAPTER_NAME, execute_metric_run
from model_evolution.config import initialize_project, load_project
from model_evolution.ids import new_id
from model_evolution.records import load_record, validate_repository
from model_evolution.service import ModelEvolution
from model_evolution.storage import (
    ArtifactCollisionError,
    LocalArtifactStore,
    download_tree,
    upload_tree,
)


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
        source.mkdir(parents=True)
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

    def create_experiment(self) -> tuple[dict, dict]:
        hypothesis = self.service.create_hypothesis("metric", "Metric geometry", "Test geometry.")
        experiment = self.service.create_experiment(
            "metric",
            hypothesis_ids=[hypothesis["id"]],
            config_path=self.config_path,
            objective="Train the metric encoder.",
        )
        return hypothesis, experiment


class IdTests(unittest.TestCase):
    def test_id_is_readable_ulid(self) -> None:
        value = new_id("Metric Depth")
        self.assertRegex(value, r"^metric-depth-[0-9A-HJKMNP-TV-Z]{26}$")


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
    def test_generated_commit_contains_only_its_record(self) -> None:
        unrelated = self.root / "notes.txt"
        unrelated.write_text("do not commit\n", encoding="utf-8")
        service = ModelEvolution(
            self.project,
            store=LocalArtifactStore(self.artifacts),
            actor="test-agent",
            commit=True,
        )
        hypothesis = service.create_hypothesis("scope", "Commit scope", "Only commit this record.")
        result = subprocess.run(
            ["git", "show", "--pretty=format:", "--name-only", "HEAD"],
            cwd=self.root,
            check=True,
            capture_output=True,
            text=True,
        )
        self.assertEqual(
            result.stdout.strip(),
            f"model-evolution/hypotheses/{hypothesis['id']}.md",
        )
        self.assertTrue(unrelated.exists())

    def test_dataset_run_lineage_and_validation(self) -> None:
        dataset = self.create_dataset()
        hypothesis, experiment = self.create_experiment()
        run = self.service.plan_run(
            "metric",
            experiment_id=experiment["id"],
            dataset_id=dataset["id"],
            config_path=self.config_path,
            adapter=ADAPTER_NAME,
            parent_module_ids=[],
        )
        self.assertEqual(run["initialization"], {"kind": "from_scratch", "parents": []})
        result = validate_repository(self.project)
        self.assertTrue(result["valid"])
        lineage = self.service.lineage(run["id"])
        ids = {record["id"] for record in lineage["records"]}
        self.assertEqual(ids, {run["id"], dataset["id"], experiment["id"], hypothesis["id"]})

    def test_storage_probe_is_unique_and_retained(self) -> None:
        first = self.service.probe_storage()
        second = self.service.probe_storage()
        self.assertNotEqual(first["id"], second["id"])
        self.assertTrue(first["uri"].endswith(f"probes/{first['id']}.json"))
        self.assertTrue((self.artifacts / "probes" / f"{first['id']}.json").exists())

    def test_decision_separates_observation_inference_and_next_action(self) -> None:
        hypothesis = self.service.create_hypothesis("metric", "Metric geometry", "Test geometry.")
        decision = self.service.create_decision(
            "continue-metric",
            title="Continue metric training",
            observations=["Validation loss improved.", "Embeddings remained non-collapsed."],
            inference="The representation is learning useful geometry.",
            confidence="medium",
            next_action="Run the held-out evaluation.",
            references=[hypothesis["id"]],
        )
        path = self.project.records_dir / "decisions" / f"{decision['id']}.md"
        text = path.read_text(encoding="utf-8")
        self.assertIn("## Observations", text)
        self.assertIn("## Inference", text)
        self.assertIn("## Next action", text)
        self.assertTrue(validate_repository(self.project)["valid"])

    def test_source_changes_block_run_planning(self) -> None:
        dataset = self.create_dataset()
        _, experiment = self.create_experiment()
        tracked = self.root / "README.md"
        tracked.write_text("changed\n", encoding="utf-8")
        with self.assertRaisesRegex(RuntimeError, "must be committed"):
            self.service.plan_run(
                "blocked",
                experiment_id=experiment["id"],
                dataset_id=dataset["id"],
                config_path=self.config_path,
                adapter=ADAPTER_NAME,
                parent_module_ids=[],
            )

    def test_metric_adapter_records_complete_vertical_slice(self) -> None:
        dataset = self.create_dataset()
        _, experiment = self.create_experiment()
        run = self.service.plan_run(
            "metric",
            experiment_id=experiment["id"],
            dataset_id=dataset["id"],
            config_path=self.config_path,
            adapter=ADAPTER_NAME,
            parent_module_ids=[],
        )

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
                json.dumps(report) + "\n",
                encoding="utf-8",
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

        self.assertEqual(result["status"], "completed")
        completed = load_record(self.project, "run", run["id"])
        self.assertEqual(completed["status"], "completed")
        evaluation = load_record(self.project, "evaluation", result["evaluation_id"])
        module = load_record(self.project, "module", result["module_id"])
        self.assertEqual(evaluation["run_id"], run["id"])
        self.assertEqual(module["status"], "candidate")
        self.assertEqual(module["source_run"], run["id"])
        self.assertTrue(validate_repository(self.project)["valid"])

    def test_failed_run_records_error_and_surviving_artifacts(self) -> None:
        dataset = self.create_dataset()
        _, experiment = self.create_experiment()
        run = self.service.plan_run(
            "metric-failure",
            experiment_id=experiment["id"],
            dataset_id=dataset["id"],
            config_path=self.config_path,
            adapter=ADAPTER_NAME,
            parent_module_ids=[],
        )

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
        self.assertIsNone(failed["artifact_error"])

    def test_promotion_is_backed_by_decision_note(self) -> None:
        dataset = self.create_dataset()
        _, experiment = self.create_experiment()
        run = self.service.plan_run(
            "metric",
            experiment_id=experiment["id"],
            dataset_id=dataset["id"],
            config_path=self.config_path,
            adapter=ADAPTER_NAME,
            parent_module_ids=[],
        )
        weights = self.project.work_dir / "weights.pt"
        weights.parent.mkdir(parents=True, exist_ok=True)
        weights.write_bytes(b"weights")
        module = self.service.create_module(
            slug="metric",
            module_name="metric_encoder",
            source_run=run["id"],
            source_weights=weights,
            contract={"architecture": "test", "version": 1},
        )
        with self.assertRaisesRegex(ValueError, "not promoted"):
            self.service.plan_run(
                "metric-candidate",
                experiment_id=experiment["id"],
                dataset_id=dataset["id"],
                config_path=self.config_path,
                adapter=ADAPTER_NAME,
                parent_module_ids=[module["id"]],
            )
        report = self.project.work_dir / "report.json"
        report.write_text("{}\n", encoding="utf-8")
        artifact = {"uri": "file:///report", "sha256": "0", "path": "report"}
        evaluation = self.service.create_evaluation(
            run_id=run["id"],
            dataset_id=dataset["id"],
            metrics={"quality_gate": {"passed": True}},
            artifact=artifact,
            split="test",
        )
        promoted = self.service.promote_module(
            module["id"],
            evaluation_id=evaluation["id"],
            rationale="The quality gate passed.",
            approval_context="Human explicitly requested promotion in the current conversation.",
        )
        self.assertEqual(promoted["module"]["status"], "promoted")
        decision_path = (
            self.project.records_dir
            / "decisions"
            / f"{promoted['decision']['id']}.md"
        )
        self.assertIn("The quality gate passed.", decision_path.read_text(encoding="utf-8"))
        inherited = self.service.plan_run(
            "metric-inherited",
            experiment_id=experiment["id"],
            dataset_id=dataset["id"],
            config_path=self.config_path,
            adapter=ADAPTER_NAME,
            parent_module_ids=[module["id"]],
        )
        self.assertEqual(inherited["initialization"]["kind"], "inherited")
        self.assertEqual(
            inherited["initialization"]["parents"],
            [{"role": "metric_encoder", "module_id": module["id"]}],
        )
        self.assertTrue(validate_repository(self.project)["valid"])


if __name__ == "__main__":
    unittest.main()
