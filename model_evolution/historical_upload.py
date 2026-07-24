from __future__ import annotations

from hashlib import sha256
import json
import mimetypes
from pathlib import Path
import shutil
import subprocess
from typing import Any, Iterable
from urllib.parse import urlparse

from .config import ProjectConfig


PLAN_SCHEMA_VERSION = 1
DEFAULT_BUNDLE = Path(".model-evolution/work/backfill/codex")
DEFAULT_OUTPUT = Path(".model-evolution/work/backfill/upload")


class HistoricalUploadCollisionError(FileExistsError):
    pass


def _json_bytes(value: dict[str, Any]) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode()


def _sha256_file(path: Path, chunk_size: int = 8 * 1024 * 1024) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def _read_jsonl(path: Path) -> Iterable[dict[str, Any]]:
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                yield json.loads(line)


def _candidate_names(bundle: Path, kind: str) -> list[str]:
    names = []
    for row in _read_jsonl(bundle / "drafts.jsonl"):
        if row.get("kind") != kind:
            continue
        name = row.get("name")
        if not isinstance(name, str) or not name:
            raise ValueError(f"{kind} is missing its name")
        names.append(name)
    if len(names) != len(set(names)):
        raise ValueError(f"duplicate {kind} names in {bundle / 'drafts.jsonl'}")
    return sorted(names)


def _regular_files(root: Path) -> list[Path]:
    if not root.is_dir():
        raise FileNotFoundError(root)
    symlinks = sorted(path for path in root.rglob("*") if path.is_symlink())
    if symlinks:
        raise ValueError(f"historical artifact tree contains a symlink: {symlinks[0]}")
    return sorted(path for path in root.rglob("*") if path.is_file())


def _archive_dataset(source: Path, destination: Path) -> None:
    if shutil.which("tar") is None or shutil.which("zstd") is None:
        raise RuntimeError("historical dataset packaging requires tar and zstd")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    temporary.unlink(missing_ok=True)
    tar = subprocess.Popen(
        [
            "tar",
            "--sort=name",
            "--format=posix",
            "--mtime=@0",
            "--owner=0",
            "--group=0",
            "--numeric-owner",
            "--pax-option=delete=atime,delete=ctime",
            "-C",
            str(source.parent),
            "-cf",
            "-",
            source.name,
        ],
        stdout=subprocess.PIPE,
    )
    assert tar.stdout is not None
    compressed = subprocess.run(
        ["zstd", "-T0", "-3", "-q", "-o", str(temporary)],
        stdin=tar.stdout,
        check=False,
    )
    tar.stdout.close()
    tar_status = tar.wait()
    if tar_status or compressed.returncode:
        temporary.unlink(missing_ok=True)
        raise RuntimeError(
            f"dataset archive failed for {source} "
            f"(tar={tar_status}, zstd={compressed.returncode})"
        )
    temporary.replace(destination)


def build_historical_upload_plan(
    project: ProjectConfig,
    *,
    bundle: str | Path | None = None,
    output: str | Path | None = None,
) -> dict[str, Any]:
    bundle_path = project.root / (Path(bundle) if bundle else DEFAULT_BUNDLE)
    output_path = project.root / (Path(output) if output else DEFAULT_OUTPUT)
    package_path = output_path / "packages"
    package_path.mkdir(parents=True, exist_ok=True)

    datasets = _candidate_names(bundle_path, "dataset_candidate")
    runs = _candidate_names(bundle_path, "run_candidate")
    artifacts: list[dict[str, Any]] = []
    logical_files = 0
    source_bytes = 0

    for name in datasets:
        source = project.root / "data" / "generated" / name
        files = _regular_files(source)
        file_bytes = sum(path.stat().st_size for path in files)
        archive = package_path / f"dataset-{name}.tar.zst"
        _archive_dataset(source, archive)
        logical_files += len(files)
        source_bytes += file_bytes
        artifacts.append(
            {
                "category": "dataset_archive",
                "name": name,
                "local_path": archive.relative_to(project.root).as_posix(),
                "source_path": source.relative_to(project.root).as_posix(),
                "logical_files": len(files),
                "source_bytes": file_bytes,
                "size": archive.stat().st_size,
                "sha256": _sha256_file(archive),
                "content_type": "application/zstd",
            }
        )

    for name in runs:
        source = project.root / "runs" / name
        files = _regular_files(source)
        logical_files += len(files)
        source_bytes += sum(path.stat().st_size for path in files)
        for path in files:
            relative = path.relative_to(source).as_posix()
            artifacts.append(
                {
                    "category": "run_artifact",
                    "name": name,
                    "local_path": path.relative_to(project.root).as_posix(),
                    "source_path": path.relative_to(project.root).as_posix(),
                    "relative_path": relative,
                    "logical_files": 1,
                    "source_bytes": path.stat().st_size,
                    "size": path.stat().st_size,
                    "sha256": _sha256_file(path),
                    "content_type": mimetypes.guess_type(path.name)[0]
                    or "application/octet-stream",
                }
            )

    identity = {
        "schema_version": PLAN_SCHEMA_VERSION,
        "project_id": project.project_id,
        "backfill_manifest_sha256": _sha256_file(bundle_path / "manifest.yaml"),
        "artifacts": [
            {
                key: entry[key]
                for key in (
                    "category",
                    "name",
                    "source_path",
                    "relative_path",
                    "logical_files",
                    "source_bytes",
                    "size",
                    "sha256",
                )
                if key in entry
            }
            for entry in artifacts
        ],
    }
    identity_sha256 = sha256(_json_bytes(identity)).hexdigest()
    import_id = f"historical-{identity_sha256[:20]}"
    for entry in artifacts:
        if entry["category"] == "dataset_archive":
            entry["object_path"] = (
                f"historical/{import_id}/datasets/{entry['name']}/tree.tar.zst"
            )
        else:
            entry["object_path"] = (
                f"historical/{import_id}/runs/{entry['name']}/{entry['relative_path']}"
            )
    plan = {
        "schema_version": PLAN_SCHEMA_VERSION,
        "kind": "historical_upload_plan",
        "project_id": project.project_id,
        "artifact_store": project.artifact_store,
        "import_id": import_id,
        "identity_sha256": identity_sha256,
        "backfill_bundle": bundle_path.relative_to(project.root).as_posix(),
        "backfill_manifest_sha256": identity["backfill_manifest_sha256"],
        "policy": {
            "write": "create_only",
            "overwrite": False,
            "delete": False,
            "raw_codex_evidence_uploaded": False,
            "dataset_packaging": "deterministic_tar_zstd",
        },
        "summary": {
            "datasets": len(datasets),
            "runs": len(runs),
            "logical_files": logical_files,
            "source_bytes": source_bytes,
            "gcs_objects": len(artifacts) + 2,
            "artifact_bytes": sum(entry["size"] for entry in artifacts),
        },
        "artifacts": artifacts,
    }
    output_path.mkdir(parents=True, exist_ok=True)
    plan_path = output_path / "plan.json"
    temporary = plan_path.with_suffix(".tmp")
    temporary.write_bytes(_json_bytes(plan))
    temporary.replace(plan_path)
    return plan


def load_historical_upload_plan(
    project: ProjectConfig, plan: str | Path | None = None
) -> tuple[Path, dict[str, Any]]:
    path = project.root / (Path(plan) if plan else DEFAULT_OUTPUT / "plan.json")
    value = json.loads(path.read_text(encoding="utf-8"))
    if value.get("kind") != "historical_upload_plan":
        raise ValueError(f"not a historical upload plan: {path}")
    if value.get("artifact_store") != project.artifact_store:
        raise ValueError("upload plan artifact store differs from project configuration")
    return path, value


class _GCSHistoricalImporter:
    def __init__(self, uri: str):
        parsed = urlparse(uri)
        if parsed.scheme != "gs" or not parsed.netloc:
            raise ValueError(f"historical upload requires a GCS store: {uri}")
        from google.cloud import storage

        self.bucket_name = parsed.netloc
        self.prefix = parsed.path.strip("/")
        self.bucket = storage.Client().bucket(self.bucket_name)

    def _name(self, relative: str) -> str:
        return f"{self.prefix}/{relative}" if self.prefix else relative

    def _matching(
        self, relative: str, expected_sha256: str, expected_size: int
    ) -> dict[str, Any] | None:
        blob = self.bucket.get_blob(self._name(relative))
        if blob is None:
            return None
        metadata = blob.metadata or {}
        if metadata.get("sha256") != expected_sha256 or blob.size != expected_size:
            raise HistoricalUploadCollisionError(
                f"immutable object collision: gs://{self.bucket_name}/{blob.name}"
            )
        return {
            "object_path": relative,
            "uri": f"gs://{self.bucket_name}/{blob.name}",
            "generation": int(blob.generation),
            "size": int(blob.size),
            "crc32c": blob.crc32c,
            "sha256": expected_sha256,
        }

    def create_file(
        self, relative: str, source: Path, entry: dict[str, Any]
    ) -> tuple[str, dict[str, Any]]:
        existing = self._matching(relative, entry["sha256"], entry["size"])
        if existing:
            return "skipped", existing
        from google.api_core.exceptions import PreconditionFailed

        blob = self.bucket.blob(self._name(relative))
        blob.metadata = {
            "sha256": entry["sha256"],
            "historical_import_id": entry["import_id"],
            "source_path": entry["source_path"],
            "category": entry["category"],
        }
        try:
            blob.upload_from_filename(
                str(source),
                content_type=entry["content_type"],
                if_generation_match=0,
                checksum="auto",
                timeout=600,
            )
        except PreconditionFailed:
            matched = self._matching(relative, entry["sha256"], entry["size"])
            if matched is None:
                raise
            return "skipped", matched
        blob.reload()
        return "uploaded", {
            "object_path": relative,
            "uri": f"gs://{self.bucket_name}/{blob.name}",
            "generation": int(blob.generation),
            "size": int(blob.size),
            "crc32c": blob.crc32c,
            "sha256": entry["sha256"],
        }

    def create_bytes(
        self, relative: str, payload: bytes, *, kind: str, import_id: str
    ) -> tuple[str, dict[str, Any]]:
        expected = sha256(payload).hexdigest()
        existing = self._matching(relative, expected, len(payload))
        if existing:
            return "skipped", existing
        from google.api_core.exceptions import PreconditionFailed

        blob = self.bucket.blob(self._name(relative))
        blob.metadata = {
            "sha256": expected,
            "historical_import_id": import_id,
            "category": kind,
        }
        try:
            blob.upload_from_string(
                payload,
                content_type="application/json",
                if_generation_match=0,
                checksum="auto",
                timeout=600,
            )
        except PreconditionFailed:
            matched = self._matching(relative, expected, len(payload))
            if matched is None:
                raise
            return "skipped", matched
        blob.reload()
        return "uploaded", {
            "object_path": relative,
            "uri": f"gs://{self.bucket_name}/{blob.name}",
            "generation": int(blob.generation),
            "size": int(blob.size),
            "crc32c": blob.crc32c,
            "sha256": expected,
        }

    def verify(
        self, relative: str, expected_sha256: str, expected_size: int
    ) -> dict[str, Any]:
        result = self._matching(relative, expected_sha256, expected_size)
        if result is None:
            raise FileNotFoundError(
                f"missing GCS object: gs://{self.bucket_name}/{self._name(relative)}"
            )
        if not result["generation"] or not result["crc32c"]:
            raise ValueError(f"incomplete GCS integrity metadata: {result['uri']}")
        return result


def upload_historical_plan(
    project: ProjectConfig, *, plan: str | Path | None = None
) -> dict[str, Any]:
    plan_path, value = load_historical_upload_plan(project, plan)
    importer = _GCSHistoricalImporter(project.artifact_store)
    import_id = value["import_id"]
    results = []
    uploaded = skipped = 0
    for raw in value["artifacts"]:
        entry = dict(raw, import_id=import_id)
        source = project.root / entry["local_path"]
        if source.stat().st_size != entry["size"] or _sha256_file(source) != entry["sha256"]:
            raise ValueError(f"planned artifact changed: {entry['local_path']}")
        status, result = importer.create_file(entry["object_path"], source, entry)
        uploaded += status == "uploaded"
        skipped += status == "skipped"
        results.append(result)

    plan_payload = plan_path.read_bytes()
    plan_object = f"imports/{import_id}/plan.json"
    status, plan_result = importer.create_bytes(
        plan_object, plan_payload, kind="historical_upload_plan", import_id=import_id
    )
    uploaded += status == "uploaded"
    skipped += status == "skipped"
    receipt = {
        "schema_version": PLAN_SCHEMA_VERSION,
        "kind": "historical_upload_receipt",
        "import_id": import_id,
        "plan_object": plan_result,
        "objects": results,
    }
    receipt_payload = _json_bytes(receipt)
    receipt_object = f"imports/{import_id}/receipt.json"
    status, receipt_result = importer.create_bytes(
        receipt_object,
        receipt_payload,
        kind="historical_upload_receipt",
        import_id=import_id,
    )
    uploaded += status == "uploaded"
    skipped += status == "skipped"
    local_receipt = plan_path.parent / "receipt.json"
    local_receipt.write_bytes(receipt_payload)
    return {
        "kind": "historical_upload",
        "import_id": import_id,
        "uploaded": uploaded,
        "skipped": skipped,
        "objects": len(results) + 2,
        "bytes": value["summary"]["artifact_bytes"],
        "receipt_uri": receipt_result["uri"],
    }


def verify_historical_upload(
    project: ProjectConfig, *, plan: str | Path | None = None
) -> dict[str, Any]:
    plan_path, value = load_historical_upload_plan(project, plan)
    importer = _GCSHistoricalImporter(project.artifact_store)
    verified = 0
    for entry in value["artifacts"]:
        importer.verify(entry["object_path"], entry["sha256"], entry["size"])
        verified += 1
    import_id = value["import_id"]
    plan_payload = plan_path.read_bytes()
    importer.verify(
        f"imports/{import_id}/plan.json",
        sha256(plan_payload).hexdigest(),
        len(plan_payload),
    )
    receipt_path = plan_path.parent / "receipt.json"
    receipt_payload = receipt_path.read_bytes()
    importer.verify(
        f"imports/{import_id}/receipt.json",
        sha256(receipt_payload).hexdigest(),
        len(receipt_payload),
    )
    return {
        "valid": True,
        "kind": "historical_upload_verification",
        "import_id": import_id,
        "objects": verified + 2,
        "logical_files": value["summary"]["logical_files"],
        "source_bytes": value["summary"]["source_bytes"],
    }
