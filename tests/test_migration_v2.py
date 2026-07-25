from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from model_evolution.config import load_project
from model_evolution.migration_v2 import migrate_v2
from model_evolution.records import load_record, validate_repository
from model_evolution.yamlio import load_yaml, write_markdown, write_yaml


class MigrationV2Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        records = self.root / "model-evolution"
        for directory in (
            "hypotheses",
            "experiments",
            "datasets",
            "runs",
            "modules",
            "evaluations",
            "decisions",
        ):
            (records / directory).mkdir(parents=True)
        config = self.root / ".model-evolution/project.yaml"
        config.parent.mkdir(parents=True)
        write_yaml(
            config,
            {
                "schema_version": 1,
                "project_id": "migration-test",
                "artifact_store": "file:///tmp/artifacts",
                "adapter": "test",
                "records_dir": "model-evolution",
                "work_dir": ".model-evolution/work",
            },
        )
        common = {
            "schema_version": 1,
            "created_at": "2026-01-01T00:00:00+00:00",
            "updated_at": "2026-01-01T00:00:00+00:00",
            "git_revision": "1" * 40,
            "producer": "test",
        }
        write_markdown(
            records / "hypotheses/hypothesis-one.md",
            {
                **common,
                "kind": "hypothesis",
                "id": "hypothesis-one",
                "status": "supported",
                "title": "Hypothesis one",
                "references": ["decision-one"],
            },
            "# Hypothesis one\n\nThe model should improve.",
        )
        write_yaml(
            records / "experiments/experiment-one.yaml",
            {
                **common,
                "kind": "experiment",
                "id": "experiment-one",
                "status": "completed",
                "objective": "Test improvement.",
                "hypothesis_ids": ["hypothesis-one"],
                "config": {"path": "configs/test.toml", "sha256": "2" * 64},
            },
        )
        write_yaml(
            records / "datasets/dataset-one.yaml",
            {
                **common,
                "kind": "dataset",
                "id": "dataset-one",
                "status": "registered",
                "artifact": {
                    "uri": "gs://bucket/dataset",
                    "sha256": "3" * 64,
                },
                "generation": {"entrypoint": "generator"},
            },
        )
        write_yaml(
            records / "runs/run-one.yaml",
            {
                **common,
                "kind": "run",
                "id": "run-one",
                "status": "completed",
                "experiment_id": "experiment-one",
                "dataset_id": "dataset-one",
                "adapter": "test",
                "config": {"path": "configs/test.toml", "sha256": "2" * 64},
                "initialization": {"kind": "from_scratch", "parents": []},
                "artifacts": [{"uri": "gs://bucket/run", "sha256": "4" * 64}],
            },
        )
        write_yaml(
            records / "evaluations/evaluation-one.yaml",
            {
                **common,
                "kind": "evaluation",
                "id": "evaluation-one",
                "status": "completed",
                "run_id": "run-one",
                "dataset_id": "dataset-one",
                "split": "test",
                "metrics": {"loss": 0.1},
                "artifact": {
                    "uri": "gs://bucket/evaluation",
                    "sha256": "5" * 64,
                },
            },
        )
        write_yaml(
            records / "modules/module-one.yaml",
            {
                **common,
                "kind": "module",
                "id": "module-one",
                "status": "candidate",
                "module_name": "encoder",
                "source_run": "run-one",
                "evaluation_id": "evaluation-one",
                "artifact": {
                    "uri": "gs://bucket/module",
                    "sha256": "6" * 64,
                },
                "contract": {"version": 1},
            },
        )
        write_markdown(
            records / "decisions/decision-one.md",
            {
                **common,
                "kind": "decision",
                "id": "decision-one",
                "status": "accepted",
                "decision": "research_direction",
                "references": ["run-one", "evaluation-one", "module-one"],
            },
            "# Continue\n\nUse the result.",
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_check_is_non_mutating_and_apply_is_deterministic(self) -> None:
        checked = migrate_v2(self.root)
        self.assertEqual(checked["source_records"], 7)
        self.assertEqual(checked["target_records"], 4)
        self.assertEqual(load_yaml(self.root / ".model-evolution/project.yaml")["schema_version"], 1)
        self.assertFalse((self.root / "model-evolution/studies/hypothesis-one.md").exists())

        applied = migrate_v2(self.root, apply=True)
        self.assertEqual(applied["absorbed_ids"]["experiment-one"], "hypothesis-one")
        self.assertEqual(applied["absorbed_ids"]["evaluation-one"], "run-one")
        self.assertEqual(applied["absorbed_ids"]["decision-one"], "hypothesis-one")
        project = load_project(self.root)
        self.assertTrue(validate_repository(project)["valid"])
        run = load_record(project, "run", "run-one")
        module = load_record(project, "module", "module-one")
        self.assertEqual(run["results"]["primary"]["artifact"]["uri"], "gs://bucket/evaluation")
        self.assertEqual(run["artifacts"][0]["uri"], "gs://bucket/run")
        self.assertEqual(module["artifact"]["uri"], "gs://bucket/module")
        self.assertEqual(module["status"], "available")
        self.assertEqual(migrate_v2(self.root)["status"], "already_v2")


if __name__ == "__main__":
    unittest.main()
