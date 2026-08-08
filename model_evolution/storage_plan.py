from __future__ import annotations

from hashlib import sha256
import json
import mimetypes
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Any, Iterable

from tqdm import tqdm

from .config import ProjectConfig
from .records import iter_records


PLAN_SCHEMA_VERSION = 1
CACHE_SCHEMA_VERSION = 1
DATASET_PACKAGING_VERSION = "tar-posix-zstd3-v1"
DEFAULT_OUTPUT = Path(".model-evolution/work/storage")


def _json_bytes(value: dict[str, Any]) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode()


def _sha256_file(
    path: Path,
    chunk_size: int = 8 * 1024 * 1024,
    *,
    progress: bool = False,
) -> str:
    digest = sha256()
    bar = tqdm(
        total=path.stat().st_size,
        desc=f"hash {path.name}",
        unit="B",
        unit_scale=True,
        leave=False,
        disable=not progress or not sys.stderr.isatty(),
    )
    try:
        with path.open("rb") as handle:
            while chunk := handle.read(chunk_size):
                digest.update(chunk)
                bar.update(len(chunk))
    finally:
        bar.close()
    return digest.hexdigest()


def _stat_identity(path: Path) -> dict[str, int]:
    stat = path.stat()
    return {
        "size": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
        "device": stat.st_dev,
        "inode": stat.st_ino,
    }


def _load_cache(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {
            "schema_version": CACHE_SCHEMA_VERSION,
            "dataset_packaging_version": DATASET_PACKAGING_VERSION,
            "artifacts": {},
        }
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        value = {}
    if (
        value.get("schema_version") != CACHE_SCHEMA_VERSION
        or value.get("dataset_packaging_version") != DATASET_PACKAGING_VERSION
        or not isinstance(value.get("artifacts"), dict)
    ):
        return {
            "schema_version": CACHE_SCHEMA_VERSION,
            "dataset_packaging_version": DATASET_PACKAGING_VERSION,
            "artifacts": {},
        }
    return value


def _save_cache(path: Path, cache: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_bytes(_json_bytes(cache))
    temporary.replace(path)


def _cache_key(kind: str, source_path: str, digest: str) -> str:
    return sha256(
        f"{kind}\0{source_path}\0{digest}\0{DATASET_PACKAGING_VERSION}".encode()
    ).hexdigest()


def _valid_cached_upload(entry: Any, path: Path) -> bool:
    return (
        isinstance(entry, dict)
        and path.is_file()
        and entry.get("upload_stat") == _stat_identity(path)
        and isinstance(entry.get("sha256"), str)
        and isinstance(entry.get("size"), int)
    )


def _regular_files(root: Path, *, progress: bool = False) -> list[Path]:
    if not root.is_dir():
        raise FileNotFoundError(root)
    files: list[Path] = []
    paths = tqdm(
        root.rglob("*"),
        desc=f"scan {root.name}",
        unit="entry",
        leave=False,
        disable=not progress or not sys.stderr.isatty(),
    )
    for path in paths:
        if path.is_symlink():
            raise ValueError(f"artifact tree contains a symlink: {path}")
        if path.is_file():
            files.append(path)
    return sorted(files)


def _tree_sha256(
    root: Path,
    files: list[Path] | None = None,
    *,
    progress: bool = False,
) -> str:
    selected = files if files is not None else _regular_files(root, progress=progress)
    entries = [
        f"{path.relative_to(root).as_posix()}:{_sha256_file(path)}"
        for path in tqdm(
            selected,
            desc=f"hash {root.name}",
            unit="file",
            leave=False,
            disable=not progress or not sys.stderr.isatty(),
        )
    ]
    return sha256("\n".join(entries).encode()).hexdigest()


def _archive_dataset(
    source: Path,
    destination: Path,
    *,
    progress: bool = False,
) -> None:
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
    compressed = subprocess.Popen(
        ["zstd", "-T0", "-3", "-q", "-o", str(temporary)],
        stdin=subprocess.PIPE,
    )
    assert compressed.stdin is not None
    archive_progress = tqdm(
        desc=f"pack {source.name}",
        unit="B",
        unit_scale=True,
        leave=False,
        disable=not progress or not sys.stderr.isatty(),
    )
    try:
        while chunk := tar.stdout.read(8 * 1024 * 1024):
            compressed.stdin.write(chunk)
            archive_progress.update(len(chunk))
    finally:
        archive_progress.close()
        tar.stdout.close()
        compressed.stdin.close()
    tar_status = tar.wait()
    compressed_status = compressed.wait()
    if tar_status or compressed_status:
        temporary.unlink(missing_ok=True)
        raise RuntimeError(
            f"dataset archive failed for {source} "
            f"(tar={tar_status}, zstd={compressed_status})"
        )
    temporary.replace(destination)


def _local_artifacts(value: Any, trail: tuple[str, ...] = ()) -> Iterable[tuple[tuple[str, ...], dict[str, Any]]]:
    if isinstance(value, dict):
        if (
            value.get("status") in {"local", "available"}
            and isinstance(value.get("path"), str)
        ):
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
    progress: bool = False,
    rebuild: bool = False,
) -> dict[str, Any]:
    output_path = (
        (project.root / Path(output)).resolve()
        if output is not None
        else project.root / DEFAULT_OUTPUT
    )
    cache_path = output_path / "cache" / "index.json"
    cache = _load_cache(cache_path)
    cached_artifacts = cache["artifacts"]
    dataset_packages = output_path / "cache" / "datasets"
    previous_artifacts: dict[tuple[str, str], dict[str, Any]] = {}
    previous_plan_path = output_path / "plan.json"
    if not rebuild and previous_plan_path.exists():
        try:
            _, previous_plan = load_storage_plan(project, previous_plan_path)
        except (KeyError, OSError, ValueError, json.JSONDecodeError):
            previous_plan = {}
        previous_artifacts = {
            (str(item.get("record_id")), str(item.get("object_path"))): item
            for item in previous_plan.get("artifacts", [])
            if isinstance(item, dict)
    }
    entries: list[dict[str, Any]] = []
    destinations: dict[str, dict[str, Any]] = {}
    cache_hits = 0
    cache_misses = 0

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
            expected_source_digest = str(
                artifact.get("tree_sha256" if kind == "dataset" else "sha256", "")
            )
            existing = destinations.get(destination)
            if existing is not None:
                same_artifact = (
                    existing["record_id"] == str(record["id"])
                    and existing["record_kind"] == kind
                    and existing["source_path"] == relative_source.as_posix()
                    and existing["source_digest"] == expected_source_digest
                )
                if not same_artifact:
                    raise ValueError(
                        f"conflicting storage destination: {destination}"
                    )
                existing["artifact_paths"].append(list(trail))
                continue

            if kind == "dataset":
                expected_tree = str(artifact.get("tree_sha256", ""))
                key = _cache_key(kind, relative_source.as_posix(), expected_tree)
                packaged = dataset_packages / f"{expected_tree}.tar.zst"
                cached = cached_artifacts.get(key)
                previous = previous_artifacts.get(
                    (str(record["id"]), destination)
                )
                if (
                    not rebuild
                    and not _valid_cached_upload(cached, packaged)
                    and isinstance(previous, dict)
                    and previous.get("source_path") == relative_source.as_posix()
                ):
                    previous_upload = (
                        project.root / str(previous.get("upload_path", ""))
                    ).resolve()
                    try:
                        previous_upload.relative_to(project.root)
                    except ValueError:
                        previous_upload = Path()
                    if (
                        previous_upload.is_file()
                        and previous_upload.stat().st_size == previous.get("size")
                        and _sha256_file(previous_upload, progress=progress)
                        == previous.get("sha256")
                    ):
                        packaged.parent.mkdir(parents=True, exist_ok=True)
                        if previous_upload != packaged:
                            previous_upload.replace(packaged)
                        cached = {
                            "kind": kind,
                            "source_path": relative_source.as_posix(),
                            "source_digest": expected_tree,
                            "upload_path": packaged.relative_to(
                                project.root
                            ).as_posix(),
                            "logical_files": int(previous["logical_files"]),
                            "source_bytes": int(previous["source_bytes"]),
                            "size": packaged.stat().st_size,
                            "sha256": str(previous["sha256"]),
                            "upload_stat": _stat_identity(packaged),
                        }
                        cached_artifacts[key] = cached
                        _save_cache(cache_path, cache)
                if rebuild or not _valid_cached_upload(cached, packaged):
                    cache_misses += 1
                    files = _regular_files(source, progress=progress)
                    observed_tree = _tree_sha256(source, files, progress=progress)
                    if observed_tree != expected_tree:
                        raise ValueError(
                            f"dataset tree digest changed: {relative_source}"
                        )
                    _archive_dataset(source, packaged, progress=progress)
                    cached = {
                        "kind": kind,
                        "source_path": relative_source.as_posix(),
                        "source_digest": expected_tree,
                        "upload_path": packaged.relative_to(project.root).as_posix(),
                        "logical_files": len(files),
                        "source_bytes": sum(path.stat().st_size for path in files),
                        "size": packaged.stat().st_size,
                        "sha256": _sha256_file(packaged, progress=progress),
                        "upload_stat": _stat_identity(packaged),
                    }
                    cached_artifacts[key] = cached
                    _save_cache(cache_path, cache)
                else:
                    cache_hits += 1
                upload_source = packaged
                logical_files = int(cached["logical_files"])
                source_bytes = int(cached["source_bytes"])
                upload_size = int(cached["size"])
                upload_sha256 = str(cached["sha256"])
                source_digest = expected_tree
            else:
                if not source.is_file():
                    raise FileNotFoundError(source)
                expected = str(artifact.get("sha256", ""))
                key = _cache_key("file", relative_source.as_posix(), expected)
                cached = cached_artifacts.get(key)
                source_stat = _stat_identity(source)
                if (
                    rebuild
                    or not isinstance(cached, dict)
                    or cached.get("source_stat") != source_stat
                    or cached.get("sha256") != expected
                ):
                    cache_misses += 1
                    observed = _sha256_file(source, progress=progress)
                    if observed != expected:
                        raise ValueError(
                            f"artifact digest changed: {relative_source}"
                        )
                    cached = {
                        "kind": kind,
                        "source_path": relative_source.as_posix(),
                        "source_digest": expected,
                        "upload_path": relative_source.as_posix(),
                        "logical_files": 1,
                        "source_bytes": source.stat().st_size,
                        "size": source.stat().st_size,
                        "sha256": observed,
                        "source_stat": source_stat,
                        "upload_stat": source_stat,
                    }
                    cached_artifacts[key] = cached
                    _save_cache(cache_path, cache)
                else:
                    cache_hits += 1
                upload_source = source
                logical_files = 1
                source_bytes = source.stat().st_size
                upload_size = int(cached["size"])
                upload_sha256 = str(cached["sha256"])
                source_digest = expected

            entry = {
                "record_id": str(record["id"]),
                "record_kind": kind,
                "role": ".".join(trail),
                "artifact_path": list(trail),
                "artifact_paths": [list(trail)],
                "source_path": relative_source.as_posix(),
                "source_digest": source_digest,
                "upload_path": upload_source.relative_to(project.root).as_posix(),
                "object_path": destination,
                "logical_files": logical_files,
                "source_bytes": source_bytes,
                "size": upload_size,
                "sha256": upload_sha256,
                "content_type": (
                    "application/zstd"
                    if kind == "dataset"
                    else mimetypes.guess_type(upload_source.name)[0]
                    or "application/octet-stream"
                ),
            }
            entries.append(entry)
            destinations[destination] = entry

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
            "dataset_packaging": DATASET_PACKAGING_VERSION,
            "cache": "content_addressed",
        },
        "summary": {
            "records": len({entry["record_id"] for entry in entries}),
            "objects": len(entries),
            "logical_files": sum(entry["logical_files"] for entry in entries),
            "source_bytes": sum(entry["source_bytes"] for entry in entries),
            "upload_bytes": sum(entry["size"] for entry in entries),
            "cache_hits": cache_hits,
            "cache_misses": cache_misses,
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
