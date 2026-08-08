from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from model_evolution.config import initialize_project
from model_evolution.records import (
    base_record,
    load_document,
    load_record,
    write_record,
)
from model_evolution.storage import GCSArtifactStore, LocalArtifactStore
from model_evolution.storage_plan import build_storage_plan, load_storage_plan
from model_evolution.storage_publish import publish_storage


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
        with (
            patch(
                "model_evolution.storage_plan._regular_files",
                side_effect=AssertionError("dataset scan should be cached"),
            ),
            patch(
                "model_evolution.storage_plan._archive_dataset",
                side_effect=AssertionError("dataset archive should be cached"),
            ),
            patch(
                "model_evolution.storage_plan._sha256_file",
                side_effect=AssertionError("file hashes should be cached"),
            ),
        ):
            second = build_storage_plan(self.project)
        self.assertEqual(first["plan_sha256"], second["plan_sha256"])
        self.assertEqual(second["summary"]["cache_misses"], 0)
        self.assertEqual(second["summary"]["cache_hits"], 3)
        self.assertEqual(
            {item["object_path"] for item in first["artifacts"]},
            {
                "datasets/dataset-one/tree.tar.zst",
                "runs/run-one/best.pt",
                "modules/tiny/module-one/weights.pt",
            },
        )
        _, loaded = load_storage_plan(self.project)
        self.assertEqual(second, loaded)

    def test_identical_artifact_aliases_share_one_object_and_all_update(self) -> None:
        run, body = load_document(self.project, "run", "run-one")
        run["results"]["primary"] = {
            "status": "observed",
            "artifact": {
                "status": "local",
                "path": "runs/tiny/best.pt",
                "sha256": _file_sha(self.root / "runs/tiny/best.pt"),
            },
        }
        write_record(
            self.project,
            "run",
            run,
            body=body,
            replace_existing=True,
        )

        plan = build_storage_plan(self.project)
        run_entry = next(
            item for item in plan["artifacts"]
            if item["object_path"] == "runs/run-one/best.pt"
        )
        self.assertEqual(
            run_entry["artifact_paths"],
            [["results", "primary", "artifact"], ["artifacts", "0"]],
        )
        self.assertEqual(plan["summary"]["objects"], 3)

        publish_storage(
            self.project,
            store=LocalArtifactStore(self.root / ".remote"),
            commit=False,
        )
        updated = load_record(self.project, "run", "run-one")
        self.assertEqual(
            updated["results"]["primary"]["artifact"]["status"], "available"
        )
        self.assertEqual(updated["artifacts"][0]["status"], "available")
        self.assertEqual(
            updated["results"]["primary"]["artifact"]["uri"],
            updated["artifacts"][0]["uri"],
        )

    def test_conflicting_artifact_aliases_still_fail(self) -> None:
        other = self.root / "runs/tiny/other.pt"
        other.write_bytes(b"other")
        run, body = load_document(self.project, "run", "run-one")
        run["results"]["primary"] = {
            "status": "observed",
            "artifact": {
                "status": "local",
                "path": "runs/tiny/other.pt",
                "object_name": "best.pt",
                "sha256": _file_sha(other),
            },
        }
        write_record(
            self.project,
            "run",
            run,
            body=body,
            replace_existing=True,
        )

        with self.assertRaisesRegex(ValueError, "conflicting storage destination"):
            build_storage_plan(self.project)

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

    def test_publish_is_create_only_verified_and_idempotent(self) -> None:
        remote = LocalArtifactStore(self.root / ".remote")
        first = publish_storage(self.project, store=remote, commit=False)
        self.assertEqual(first["status"], "verified")
        self.assertEqual(first["summary"]["created"], 3)
        self.assertEqual(first["summary"]["existing"], 0)
        self.assertEqual(first["summary"]["updated_records"], 3)

        dataset = load_record(self.project, "dataset", "dataset-one")
        self.assertEqual(dataset["artifact"]["status"], "available")
        self.assertTrue(dataset["artifact"]["uri"].endswith("tree.tar.zst"))
        self.assertEqual(
            dataset["artifact"]["storage"]["object_sha256"],
            first["artifacts"][0]["sha256"],
        )
        self.assertTrue(
            (self.project.work_dir / "storage" / "receipt.json").is_file()
        )

        with (
            patch.object(remote, "create_file", wraps=remote.create_file) as create,
            patch(
                "model_evolution.storage_publish._verify_local",
                side_effect=AssertionError(
                    "verified remote objects should not read local files"
                ),
            ),
        ):
            second = publish_storage(self.project, store=remote, commit=False)
        create.assert_not_called()
        self.assertEqual(second["plan_sha256"], first["plan_sha256"])
        self.assertEqual(second["summary"]["created"], 0)
        self.assertEqual(second["summary"]["existing"], 3)
        self.assertEqual(second["summary"]["updated_records"], 0)

    def test_publish_rejects_a_conflicting_remote_without_updating_records(self) -> None:
        remote = LocalArtifactStore(self.root / ".remote")
        remote.create_bytes(
            "datasets/dataset-one/tree.tar.zst",
            b"not the planned archive",
        )
        with self.assertRaisesRegex(ValueError, "remote (size|SHA-256) mismatch"):
            publish_storage(self.project, store=remote, commit=False)
        dataset = load_record(self.project, "dataset", "dataset-one")
        self.assertEqual(dataset["artifact"]["status"], "local")
        self.assertNotIn("uri", dataset["artifact"])

    def test_changed_cached_dataset_package_is_rebuilt(self) -> None:
        first = build_storage_plan(self.project)
        dataset = next(
            item
            for item in first["artifacts"]
            if item["record_kind"] == "dataset"
        )
        package = self.root / dataset["upload_path"]
        package.write_bytes(b"corrupt cache")

        rebuilt = build_storage_plan(self.project)
        self.assertEqual(rebuilt["plan_sha256"], first["plan_sha256"])
        self.assertGreaterEqual(rebuilt["summary"]["cache_misses"], 1)
        self.assertEqual(_file_sha(package), dataset["sha256"])

    def test_gcs_file_creation_always_uses_create_only_precondition(self) -> None:
        source = self.root / "artifact.bin"
        source.write_bytes(b"immutable")
        blob = MagicMock()
        blob.size = len(b"immutable")
        blob.generation = 42
        blob.metadata = {"model-evolution-sha256": _file_sha(source)}
        bucket = MagicMock()
        bucket.blob.return_value = blob
        client = MagicMock()
        client.bucket.return_value = bucket

        with patch("google.cloud.storage.Client", return_value=client):
            store = GCSArtifactStore("gs://example/model-evolution")
            result = store.create_file(
                "runs/run-one/artifact.bin",
                source,
                content_type="application/octet-stream",
                metadata={"model-evolution-sha256": _file_sha(source)},
            )

        blob.upload_from_file.assert_called_once()
        upload_call = blob.upload_from_file.call_args
        self.assertEqual(
            upload_call.kwargs,
            {
                "size": source.stat().st_size,
                "content_type": "application/octet-stream",
                "if_generation_match": 0,
                "checksum": "auto",
            },
        )
        self.assertEqual(
            blob.metadata,
            {"model-evolution-sha256": _file_sha(source)},
        )
        self.assertEqual(result["generation"], "42")

    def test_file_creation_reports_transferred_bytes(self) -> None:
        source = self.root / "progress.bin"
        source.write_bytes(b"x" * (9 * 1024 * 1024))
        transferred: list[int] = []
        remote = LocalArtifactStore(self.root / ".remote")
        remote.create_file(
            "runs/run-one/progress.bin",
            source,
            progress_callback=transferred.append,
        )
        self.assertEqual(sum(transferred), source.stat().st_size)
        self.assertGreater(len(transferred), 1)

if __name__ == "__main__":
    unittest.main()
