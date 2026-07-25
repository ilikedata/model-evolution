from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
import tempfile
import unittest

from model_evolution.config import initialize_project
from model_evolution.records import base_record, write_record
from model_evolution.storage_plan import build_storage_plan, load_storage_plan


STUDY_BODY = """# Storage fixture

## Claim

Artifacts can be planned by record identity.

## Basis

The inputs are checksummed.

## Expected evidence

The plan is deterministic.

## Falsification

The plan changes without source changes.
"""


def _file_sha(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _tree_sha(root: Path) -> str:
    rows = [
        f"{path.relative_to(root).as_posix()}:{_file_sha(path)}"
        for path in sorted(item for item in root.rglob("*") if item.is_file())
    ]
    return sha256("\n".join(rows).encode()).hexdigest()


class StoragePlanTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.project = initialize_project(
            self.root,
            project_id="storage-plan-test",
            artifact_store="gs://example/model-evolution",
            adapter="test",
        )
        data = self.root / "data/generated/tiny"
        data.mkdir(parents=True)
        (data / "dataset.json").write_text('{"records": 1}\n', encoding="utf-8")
        checkpoint = self.root / "runs/tiny/best.pt"
        checkpoint.parent.mkdir(parents=True)
        checkpoint.write_bytes(b"weights")

        study = base_record("study", "study-one", status="draft")
        study["references"] = []
        write_record(self.project, "study", study, body=STUDY_BODY)

        dataset = base_record("dataset", "dataset-one", status="registered")
        dataset.update(
            {
                "artifact": {
                    "status": "local",
                    "path": "data/generated/tiny",
                    "tree_sha256": _tree_sha(data),
                },
                "generation": {"status": "unavailable"},
            }
        )
        write_record(self.project, "dataset", dataset, body="# Tiny dataset")

        run = base_record("run", "run-one", status="completed")
        run.update(
            {
                "study_id": "study-one",
                "dataset_id": "dataset-one",
                "adapter": "test",
                "config": {"status": "unavailable"},
                "source_revision": {"status": "unavailable"},
                "initialization": {"kind": "from_scratch", "parents": []},
                "results": {"primary": {"status": "unavailable"}},
                "artifacts": [
                    {
                        "status": "local",
                        "path": "runs/tiny/best.pt",
                        "object_name": "best.pt",
                        "sha256": _file_sha(checkpoint),
                    }
                ],
                "module_ids": ["module-one"],
            }
        )
        write_record(self.project, "run", run, body="# Tiny run")

        module = base_record("module", "module-one", status="available")
        module.update(
            {
                "module_name": "tiny",
                "source_run": "run-one",
                "artifact": {
                    "status": "local",
                    "path": "runs/tiny/best.pt",
                    "sha256": _file_sha(checkpoint),
                },
                "contract": {"status": "unavailable"},
            }
        )
        write_record(self.project, "module", module, body="# Tiny module")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_plan_uses_record_ids_and_is_deterministic(self) -> None:
        first = build_storage_plan(self.project)
        second = build_storage_plan(self.project)
        self.assertEqual(first["plan_sha256"], second["plan_sha256"])
        self.assertEqual(
            {item["object_path"] for item in first["artifacts"]},
            {
                "datasets/dataset-one/tree.tar.zst",
                "runs/run-one/best.pt",
                "modules/tiny/module-one/weights.pt",
            },
        )
        _, loaded = load_storage_plan(self.project)
        self.assertEqual(first, loaded)

    def test_plan_detects_changed_artifact(self) -> None:
        build_storage_plan(self.project)
        (self.root / "runs/tiny/best.pt").write_bytes(b"changed")
        with self.assertRaisesRegex(ValueError, "digest changed"):
            build_storage_plan(self.project)

    def test_plan_is_json_and_contains_no_group_namespace(self) -> None:
        plan = build_storage_plan(self.project)
        encoded = json.dumps(plan)
        self.assertNotIn("import_id", encoded)
        self.assertTrue(all(item["record_id"] for item in plan["artifacts"]))


if __name__ == "__main__":
    unittest.main()
