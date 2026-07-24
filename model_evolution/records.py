from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
import re

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
PROVENANCE_KINDS = {
    "codex_session",
    "git",
    "artifact",
    "dataset_manifest",
    "run_metrics",
    "human_attestation",
}
PROVENANCE_CONFIDENCE = {"low", "medium", "high"}
CLAIM_TYPES = {"observed", "inferred"}


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
    _validate_provenance(record)
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


def _validate_provenance(record: dict[str, Any]) -> None:
    provenance = record.get("provenance")
    if provenance is None:
        return
    if not isinstance(provenance, list):
        raise ValueError("record provenance must be a list")
    for index, source in enumerate(provenance):
        if not isinstance(source, dict):
            raise ValueError(f"record provenance item {index} must be a mapping")
        missing = {"kind", "locator", "claim_type", "confidence"} - set(source)
        if missing:
            raise ValueError(
                f"record provenance item {index} missing: {', '.join(sorted(missing))}"
            )
        if source["kind"] not in PROVENANCE_KINDS:
            raise ValueError(
                f"record provenance item {index} has unknown kind: {source['kind']}"
            )
        if not isinstance(source["locator"], str) or not source["locator"]:
            raise ValueError(f"record provenance item {index} locator must be non-empty")
        if source["claim_type"] not in CLAIM_TYPES:
            raise ValueError(
                f"record provenance item {index} claim_type must be observed or inferred"
            )
        if source["confidence"] not in PROVENANCE_CONFIDENCE:
            raise ValueError(
                f"record provenance item {index} confidence must be low, medium, or high"
            )
        digest = source.get("sha256")
        if digest is not None and (
            not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest)
        ):
            raise ValueError(
                f"record provenance item {index} sha256 must be a lowercase SHA-256 digest"
            )


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
    _validate_inheritance_graph(records)
    return {"valid": True, "records": len(records), "paths": paths}


def _validate_inheritance_graph(records: dict[str, dict[str, Any]]) -> None:
    edges: dict[str, list[str]] = {}
    for record_id, record in records.items():
        dependencies: list[str] = []
        if record.get("kind") == "run":
            initialization = record.get("initialization", {})
            for parent in (
                initialization.get("parents", [])
                if isinstance(initialization, dict)
                else []
            ):
                if isinstance(parent, dict) and isinstance(parent.get("module_id"), str):
                    module_id = parent["module_id"]
                    if records[module_id].get("kind") != "module":
                        raise ValueError(
                            f"{record_id} parent is not a module record: {module_id}"
                        )
                    dependencies.append(module_id)
        elif record.get("kind") == "module":
            source_run = record.get("source_run")
            if isinstance(source_run, str):
                if records[source_run].get("kind") != "run":
                    raise ValueError(
                        f"{record_id} source_run is not a run record: {source_run}"
                    )
                dependencies.append(source_run)
        edges[record_id] = dependencies

    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(record_id: str, trail: list[str]) -> None:
        if record_id in visiting:
            start = trail.index(record_id)
            cycle = trail[start:]
            raise ValueError("module inheritance cycle: " + " -> ".join(cycle))
        if record_id in visited:
            return
        visiting.add(record_id)
        for dependency in edges.get(record_id, []):
            visit(dependency, [*trail, dependency])
        visiting.remove(record_id)
        visited.add(record_id)

    for record_id in edges:
        visit(record_id, [record_id])
