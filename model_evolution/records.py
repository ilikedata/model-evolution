from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .config import ProjectConfig
from .yamlio import load_markdown, load_yaml, write_markdown, write_yaml

SCHEMA_VERSION = 1
YAML_KINDS = ("experiment", "dataset", "run", "module", "evaluation")
MARKDOWN_KINDS = ("hypothesis", "decision")
DIRECTORIES = {
    "hypothesis": "hypotheses",
    "decision": "decisions",
    "experiment": "experiments",
    "dataset": "datasets",
    "run": "runs",
    "module": "modules",
    "evaluation": "evaluations",
}
STATUSES = {
    "hypothesis": {"draft", "active", "supported", "rejected", "superseded"},
    "experiment": {"planned", "active", "completed", "cancelled"},
    "dataset": {"registered"},
    "run": {"planned", "running", "completed", "failed", "interrupted"},
    "module": {"candidate", "promoted", "deprecated"},
    "evaluation": {"completed", "failed"},
    "decision": {"accepted", "rejected", "superseded"},
}


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def record_path(project: ProjectConfig, kind: str, record_id: str) -> Path:
    if kind not in DIRECTORIES:
        raise ValueError(f"unknown record kind: {kind}")
    extension = ".md" if kind in MARKDOWN_KINDS else ".yaml"
    return project.records_dir / DIRECTORIES[kind] / f"{record_id}{extension}"


def base_record(
    kind: str,
    record_id: str,
    *,
    status: str,
    git_revision: str,
    producer: str,
    supersedes: str | None = None,
) -> dict[str, Any]:
    record: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "kind": kind,
        "id": record_id,
        "status": status,
        "created_at": now(),
        "updated_at": now(),
        "git_revision": git_revision,
        "producer": producer,
    }
    if supersedes:
        record["supersedes"] = supersedes
    return record


def load_record(project: ProjectConfig, kind: str, record_id: str) -> dict[str, Any]:
    path = record_path(project, kind, record_id)
    if kind in MARKDOWN_KINDS:
        front_matter, _ = load_markdown(path)
        return front_matter
    return load_yaml(path)


def write_record(
    project: ProjectConfig,
    kind: str,
    record: dict[str, Any],
    *,
    body: str | None = None,
    replace_existing: bool = False,
) -> Path:
    validate_record(record, expected_kind=kind)
    path = record_path(project, kind, str(record["id"]))
    if path.exists() and not replace_existing:
        raise FileExistsError(path)
    if kind in MARKDOWN_KINDS:
        write_markdown(path, record, body or f"# {record['id']}")
    else:
        write_yaml(path, record)
    return path


def validate_record(record: dict[str, Any], *, expected_kind: str | None = None) -> None:
    required = {
        "schema_version",
        "kind",
        "id",
        "status",
        "created_at",
        "updated_at",
        "git_revision",
        "producer",
    }
    missing = sorted(required - set(record))
    if missing:
        raise ValueError(f"record missing required fields: {', '.join(missing)}")
    if record["schema_version"] != SCHEMA_VERSION:
        raise ValueError(f"unsupported record schema: {record['schema_version']}")
    kind = str(record["kind"])
    if expected_kind and kind != expected_kind:
        raise ValueError(f"expected {expected_kind} record, found {kind}")
    if kind not in STATUSES:
        raise ValueError(f"unknown record kind: {kind}")
    if record["status"] not in STATUSES[kind]:
        raise ValueError(f"invalid {kind} status: {record['status']}")
    if not isinstance(record["id"], str) or not record["id"]:
        raise ValueError("record id must be a non-empty string")
    if kind == "run":
        _validate_run(record)
    elif kind == "dataset":
        _require(record, "artifact", "generation")
    elif kind == "module":
        _require(record, "module_name", "artifact", "contract", "source_run")
    elif kind == "evaluation":
        _require(record, "run_id", "dataset_id", "metrics")
    elif kind == "experiment":
        _require(record, "hypothesis_ids", "config")
    elif kind == "hypothesis":
        _require(record, "title")
    elif kind == "decision":
        _require(record, "decision")


def _require(record: dict[str, Any], *keys: str) -> None:
    missing = [key for key in keys if key not in record]
    if missing:
        raise ValueError(f"{record['kind']} record missing: {', '.join(missing)}")


def _validate_run(record: dict[str, Any]) -> None:
    _require(record, "experiment_id", "dataset_id", "adapter", "config", "initialization")
    initialization = record["initialization"]
    if not isinstance(initialization, dict):
        raise ValueError("run initialization must be a mapping")
    kind = initialization.get("kind")
    parents = initialization.get("parents")
    if kind not in {"from_scratch", "inherited"}:
        raise ValueError("run initialization kind must be from_scratch or inherited")
    if not isinstance(parents, list):
        raise ValueError("run initialization parents must be a list")
    if kind == "from_scratch" and parents:
        raise ValueError("from_scratch initialization cannot have parents")
    if kind == "inherited" and not parents:
        raise ValueError("inherited initialization requires at least one parent")


def iter_records(project: ProjectConfig, kinds: Iterable[str] | None = None) -> Iterable[tuple[Path, dict[str, Any]]]:
    for kind in kinds or (*YAML_KINDS, *MARKDOWN_KINDS):
        directory = project.records_dir / DIRECTORIES[kind]
        pattern = "*.md" if kind in MARKDOWN_KINDS else "*.yaml"
        if not directory.exists():
            continue
        for path in sorted(directory.glob(pattern)):
            if kind in MARKDOWN_KINDS:
                record, _ = load_markdown(path)
            else:
                record = load_yaml(path)
            yield path, record


def validate_repository(project: ProjectConfig) -> dict[str, Any]:
    records: dict[str, dict[str, Any]] = {}
    paths: dict[str, str] = {}
    for path, record in iter_records(project):
        validate_record(record)
        record_id = str(record["id"])
        if record_id in records:
            raise ValueError(f"duplicate record id: {record_id}")
        records[record_id] = record
        paths[record_id] = str(path.relative_to(project.root))
    for record in records.values():
        references: list[str] = []
        for field in ("experiment_id", "dataset_id", "run_id", "source_run", "supersedes"):
            value = record.get(field)
            if isinstance(value, str):
                references.append(value)
        for field in ("evaluation_id", "promotion_decision", "module_id"):
            value = record.get(field)
            if isinstance(value, str):
                references.append(value)
        references.extend(str(value) for value in record.get("module_ids", []))
        references.extend(str(value) for value in record.get("hypothesis_ids", []))
        references.extend(str(value) for value in record.get("references", []))
        initialization = record.get("initialization", {})
        for parent in initialization.get("parents", []) if isinstance(initialization, dict) else []:
            if isinstance(parent, dict) and isinstance(parent.get("module_id"), str):
                references.append(parent["module_id"])
        missing = sorted(reference for reference in references if reference not in records)
        if missing:
            raise ValueError(f"{record['id']} references missing records: {', '.join(missing)}")
    return {"valid": True, "records": len(records), "paths": paths}
