from __future__ import annotations

from hashlib import sha256
import json
import mimetypes
from pathlib import Path
import shutil
import subprocess
from typing import Any, Iterable

from .config import ProjectConfig
from .records import iter_records


PLAN_SCHEMA_VERSION = 1
DEFAULT_OUTPUT = Path(".model-evolution/work/storage")


def _json_bytes(value: dict[str, Any]) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode()


def _sha256_file(path: Path, chunk_size: int = 8 * 1024 * 1024) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def _regular_files(root: Path) -> list[Path]:
    if not root.is_dir():
        raise FileNotFoundError(root)
    symlinks = sorted(path for path in root.rglob("*") if path.is_symlink())
    if symlinks:
        raise ValueError(f"artifact tree contains a symlink: {symlinks[0]}")
    return sorted(path for path in root.rglob("*") if path.is_file())


def _tree_sha256(root: Path, files: list[Path] | None = None) -> str:
    selected = files if files is not None else _regular_files(root)
    entries = [
        f"{path.relative_to(root).as_posix()}:{_sha256_file(path)}"
        for path in selected
    ]
    return sha256("\n".join(entries).encode()).hexdigest()


def _archive_dataset(source: Path, destination: Path) -> None:
    if shutil.which("tar") is None or shutil.which("zstd") is None:
        raise RuntimeError("dataset packaging requires tar and zstd")
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


def _local_artifacts(value: Any, trail: tuple[str, ...] = ()) -> Iterable[tuple[tuple[str, ...], dict[str, Any]]]:
    if isinstance(value, dict):
        if value.get("status") == "local" and isinstance(value.get("path"), str):
            yield trail, value
            return
        for key, item in value.items():
            yield from _local_artifacts(item, (*trail, str(key)))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from _local_artifacts(item, (*trail, str(index)))


def _object_path(kind: str, record: dict[str, Any], artifact: dict[str, Any]) -> str:
    record_id = str(record["id"])
    requested = artifact.get("object_name")
    if requested:
        name = str(requested).strip("/")
    else:
        name = Path(str(artifact["path"])).name
    if kind == "dataset":
        return f"datasets/{record_id}/tree.tar.zst"
    if kind == "run":
        return f"runs/{record_id}/{name}"
    if kind == "module":
        return f"modules/{record['module_name']}/{record_id}/weights.pt"
    if kind == "assessment":
        return f"assessments/{record_id}/{name}"
    raise ValueError(f"unsupported artifact record kind: {kind}")


def build_storage_plan(
    project: ProjectConfig,
    *,
    output: str | Path | None = None,
) -> dict[str, Any]:
    output_path = (
        (project.root / Path(output)).resolve()
        if output is not None
        else project.root / DEFAULT_OUTPUT
    )
    packages = output_path / "packages"
    entries: list[dict[str, Any]] = []
    destinations: set[str] = set()

    for _, record in iter_records(project):
        kind = str(record["kind"])
        for trail, artifact in _local_artifacts(record):
            source = (project.root / str(artifact["path"])).resolve()
            try:
                relative_source = source.relative_to(project.root)
            except ValueError as error:
                raise ValueError(
                    f"local artifact must be inside the repository: {source}"
                ) from error
            destination = _object_path(kind, record, artifact)
            if destination in destinations:
                raise ValueError(f"duplicate storage destination: {destination}")
            destinations.add(destination)

            if kind == "dataset":
                files = _regular_files(source)
                expected_tree = str(artifact.get("tree_sha256", ""))
                observed_tree = _tree_sha256(source, files)
                if observed_tree != expected_tree:
                    raise ValueError(f"dataset tree digest changed: {relative_source}")
                packaged = packages / f"{record['id']}.tar.zst"
                _archive_dataset(source, packaged)
                upload_source = packaged
                logical_files = len(files)
                source_bytes = sum(path.stat().st_size for path in files)
            else:
                if not source.is_file():
                    raise FileNotFoundError(source)
                expected = str(artifact.get("sha256", ""))
                observed = _sha256_file(source)
                if observed != expected:
                    raise ValueError(f"artifact digest changed: {relative_source}")
                upload_source = source
                logical_files = 1
                source_bytes = source.stat().st_size

            entries.append(
                {
                    "record_id": str(record["id"]),
                    "record_kind": kind,
                    "role": ".".join(trail),
                    "source_path": relative_source.as_posix(),
                    "upload_path": upload_source.relative_to(project.root).as_posix(),
                    "object_path": destination,
                    "logical_files": logical_files,
                    "source_bytes": source_bytes,
                    "size": upload_source.stat().st_size,
                    "sha256": _sha256_file(upload_source),
                    "content_type": (
                        "application/zstd"
                        if kind == "dataset"
                        else mimetypes.guess_type(upload_source.name)[0]
                        or "application/octet-stream"
                    ),
                }
            )

    entries.sort(key=lambda item: item["object_path"])
    identity = {
        "schema_version": PLAN_SCHEMA_VERSION,
        "project_id": project.project_id,
        "artifact_store": project.artifact_store,
        "artifacts": entries,
    }
    plan_sha256 = sha256(_json_bytes(identity)).hexdigest()
    plan = {
        **identity,
        "kind": "artifact_storage_plan",
        "plan_sha256": plan_sha256,
        "policy": {
            "write": "create_only",
            "overwrite": False,
            "delete": False,
            "dataset_packaging": "deterministic_tar_zstd",
        },
        "summary": {
            "records": len({entry["record_id"] for entry in entries}),
            "objects": len(entries),
            "logical_files": sum(entry["logical_files"] for entry in entries),
            "source_bytes": sum(entry["source_bytes"] for entry in entries),
            "upload_bytes": sum(entry["size"] for entry in entries),
        },
    }
    output_path.mkdir(parents=True, exist_ok=True)
    plan_path = output_path / "plan.json"
    temporary = plan_path.with_suffix(".tmp")
    temporary.write_bytes(_json_bytes(plan))
    temporary.replace(plan_path)
    return plan


def load_storage_plan(
    project: ProjectConfig, plan: str | Path | None = None
) -> tuple[Path, dict[str, Any]]:
    path = (
        (project.root / Path(plan)).resolve()
        if plan is not None
        else project.root / DEFAULT_OUTPUT / "plan.json"
    )
    value = json.loads(path.read_text(encoding="utf-8"))
    if value.get("kind") != "artifact_storage_plan":
        raise ValueError(f"not an artifact storage plan: {path}")
    if value.get("artifact_store") != project.artifact_store:
        raise ValueError("storage plan artifact store differs from project configuration")
    identity = {
        key: value[key]
        for key in ("schema_version", "project_id", "artifact_store", "artifacts")
    }
    if sha256(_json_bytes(identity)).hexdigest() != value.get("plan_sha256"):
        raise ValueError("storage plan identity digest mismatch")
    return path, value
