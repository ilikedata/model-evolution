from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable
import re

from .config import ProjectConfig
from .yamlio import load_markdown, write_markdown


RECORD_KINDS = ("study", "dataset", "run", "module", "assessment")
DIRECTORIES = {
    "study": "studies",
    "dataset": "datasets",
    "run": "runs",
    "module": "modules",
    "assessment": "assessments",
}
STATUSES = {
    "study": {"draft", "planned", "active", "concluded", "cancelled"},
    "dataset": {"registered", "unavailable"},
    "run": {"planned", "running", "completed", "failed", "interrupted"},
    "module": {"available", "deprecated", "unavailable"},
    "assessment": {"completed", "failed"},
}
STUDY_OUTCOMES = {"supported", "rejected", "inconclusive"}
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
LEGACY_DIRECTORIES = ("hypotheses", "experiments", "evaluations", "decisions")


def record_path(project: ProjectConfig, kind: str, record_id: str) -> Path:
    if kind not in DIRECTORIES:
        raise ValueError(f"unknown record kind: {kind}")
    return project.records_dir / DIRECTORIES[kind] / f"{record_id}.md"


def base_record(kind: str, record_id: str, *, status: str) -> dict[str, Any]:
    return {"kind": kind, "id": record_id, "status": status}


def _stored_front_matter(record: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in record.items() if key not in {"kind", "id"}}


def load_document(
    project: ProjectConfig, kind: str, record_id: str
) -> tuple[dict[str, Any], str]:
    path = record_path(project, kind, record_id)
    front_matter, body = load_markdown(path)
    record = {"kind": kind, "id": record_id, **front_matter}
    validate_record(record, body=body, expected_kind=kind)
    return record, body


def load_record(project: ProjectConfig, kind: str, record_id: str) -> dict[str, Any]:
    record, _ = load_document(project, kind, record_id)
    return record


def write_record(
    project: ProjectConfig,
    kind: str,
    record: dict[str, Any],
    *,
    body: str | None = None,
    replace_existing: bool = False,
) -> Path:
    validate_record(record, body=body or "", expected_kind=kind)
    path = record_path(project, kind, str(record["id"]))
    if path.exists() and not replace_existing:
        raise FileExistsError(path)
    write_markdown(path, _stored_front_matter(record), body or f"# {record['id']}")
    return path


def _headings(body: str) -> set[str]:
    return {
        match.group(1).strip()
        for match in re.finditer(r"^##[ \t]+(.+?)[ \t]*$", body, flags=re.MULTILINE)
    }


def _title(body: str) -> str | None:
    match = re.search(r"^#[ \t]+(.+?)[ \t]*$", body, flags=re.MULTILINE)
    return match.group(1).strip() if match else None


def validate_record(
    record: dict[str, Any],
    *,
    body: str = "",
    expected_kind: str | None = None,
) -> None:
    kind = str(record.get("kind", ""))
    if expected_kind and kind != expected_kind:
        raise ValueError(f"expected {expected_kind} record, found {kind}")
    if kind not in STATUSES:
        raise ValueError(f"unknown record kind: {kind}")
    if not isinstance(record.get("id"), str) or not record["id"]:
        raise ValueError("record id must be a non-empty path-derived string")
    if record.get("status") not in STATUSES[kind]:
        raise ValueError(f"invalid {kind} status: {record.get('status')}")
    if not _title(body):
        raise ValueError(f"{kind} record requires a level-one Markdown title")
    _validate_provenance(record)
    if kind == "study":
        _validate_study(record, body)
    elif kind == "dataset":
        _require(record, "artifact", "generation")
        _validate_artifact(record["artifact"], label="dataset artifact")
    elif kind == "run":
        _validate_run(record)
    elif kind == "module":
        _require(record, "module_name", "artifact", "contract", "source_run")
        _validate_artifact(record["artifact"], label="module artifact")
    elif kind == "assessment":
        _require(record, "run_id", "dataset_id", "evaluator", "metrics", "artifact")
        _validate_artifact(record["artifact"], label="assessment artifact")


def _validate_study(record: dict[str, Any], body: str) -> None:
    required = {"Claim", "Basis", "Expected evidence", "Falsification"}
    if record["status"] in {"planned", "active"}:
        required.add("Method")
        _require(record, "design")
        design = record["design"]
        if not isinstance(design, dict):
            raise ValueError("study design must be a mapping")
        if not isinstance(design.get("dataset_id"), str) or not design["dataset_id"]:
            raise ValueError("study design requires dataset_id")
        if not isinstance(design.get("config"), str) or not design["config"]:
            raise ValueError("study design requires config")
        inherited = design.get("inherited_modules", [])
        if not isinstance(inherited, list):
            raise ValueError("study inherited_modules must be a list")
        for parent in inherited:
            if not isinstance(parent, dict) or not isinstance(
                parent.get("module_id"), str
            ):
                raise ValueError("study inherited module requires module_id")
    if record["status"] == "concluded":
        required.update({"Observations", "Conclusion", "Next action"})
        _require(record, "conclusion")
        conclusion = record["conclusion"]
        if not isinstance(conclusion, dict):
            raise ValueError("study conclusion must be a mapping")
        if conclusion.get("outcome") not in STUDY_OUTCOMES:
            raise ValueError("study conclusion outcome must be supported, rejected, or inconclusive")
        if conclusion.get("confidence") not in PROVENANCE_CONFIDENCE:
            raise ValueError("study conclusion confidence must be low, medium, or high")
        if not isinstance(conclusion.get("evidence"), list) or not conclusion["evidence"]:
            raise ValueError("concluded study requires non-empty conclusion evidence")
    missing = sorted(required - _headings(body))
    if missing:
        raise ValueError("study missing Markdown sections: " + ", ".join(missing))


def _validate_run(record: dict[str, Any]) -> None:
    _require(
        record,
        "study_id",
        "dataset_id",
        "adapter",
        "config",
        "source_revision",
        "initialization",
    )
    config = record["config"]
    config_unavailable = _unavailable(config)
    if not config_unavailable and (
        not isinstance(config, dict)
        or not config.get("path")
        or not _digest(config.get("sha256"))
    ):
        raise ValueError("run config must pin path and SHA-256")
    source_revision = record.get("source_revision")
    if not _unavailable(source_revision) and not _digest(source_revision, length=40):
        raise ValueError("run source_revision must be a lowercase Git SHA-1 or unavailable")
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
    for parent in parents:
        if not isinstance(parent, dict) or not parent.get("module_id"):
            raise ValueError("run parent must pin a module_id")
        if not _unavailable(parent) and not _digest(parent.get("sha256")):
            raise ValueError("run parent must pin the module SHA-256")
    if record["status"] == "completed":
        _require(record, "results", "artifacts", "module_ids")


def _digest(value: Any, *, length: int = 64) -> bool:
    return isinstance(value, str) and bool(re.fullmatch(rf"[0-9a-f]{{{length}}}", value))


def _validate_artifact(value: Any, *, label: str) -> None:
    if _unavailable(value):
        return
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a mapping")
    status = value.get("status")
    if status == "local":
        if not isinstance(value.get("path"), str) or not value["path"]:
            raise ValueError(f"{label} local reference requires a path")
    elif not isinstance(value.get("uri"), str):
        raise ValueError(f"{label} available reference requires a URI")
    digest = value.get("sha256", value.get("tree_sha256"))
    if not _digest(digest):
        raise ValueError(f"{label} must pin a SHA-256 or tree SHA-256")


def _unavailable(value: Any) -> bool:
    return isinstance(value, dict) and value.get("status") == "unavailable"


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
        if digest is not None and not _digest(digest):
            raise ValueError(
                f"record provenance item {index} sha256 must be a lowercase SHA-256 digest"
            )


def iter_records(
    project: ProjectConfig, kinds: Iterable[str] | None = None
) -> Iterable[tuple[Path, dict[str, Any]]]:
    selected = tuple(kinds or RECORD_KINDS)
    for kind in selected:
        directory = project.records_dir / DIRECTORIES[kind]
        if not directory.exists():
            continue
        legacy_yaml = sorted(directory.glob("*.yaml"))
        if legacy_yaml:
            raise ValueError(
                f"schema-v1 record requires migration: {legacy_yaml[0].relative_to(project.root)}"
            )
        for path in sorted(directory.glob("*.md")):
            record, body = load_markdown(path)
            value = {"kind": kind, "id": path.stem, **record}
            validate_record(value, body=body, expected_kind=kind)
            yield path, value


def _references(record: dict[str, Any]) -> list[str]:
    values: list[str] = []
    for field in ("study_id", "dataset_id", "run_id", "source_run", "supersedes"):
        value = record.get(field)
        if isinstance(value, str):
            values.append(value)
    values.extend(str(value) for value in record.get("references", []))
    values.extend(str(value) for value in record.get("module_ids", []))
    conclusion = record.get("conclusion", {})
    if isinstance(conclusion, dict):
        values.extend(str(value) for value in conclusion.get("evidence", []))
    design = record.get("design", {})
    if isinstance(design, dict):
        for field in ("dataset_id", "baseline_run_id"):
            value = design.get(field)
            if isinstance(value, str):
                values.append(value)
        for parent in design.get("inherited_modules", []):
            if isinstance(parent, dict) and isinstance(parent.get("module_id"), str):
                values.append(parent["module_id"])
    initialization = record.get("initialization", {})
    if isinstance(initialization, dict):
        for parent in initialization.get("parents", []):
            if isinstance(parent, dict) and isinstance(parent.get("module_id"), str):
                values.append(parent["module_id"])
    return values


def validate_repository(project: ProjectConfig) -> dict[str, Any]:
    for directory in LEGACY_DIRECTORIES:
        legacy = project.records_dir / directory
        if legacy.exists() and any(path.is_file() and path.name != ".gitkeep" for path in legacy.iterdir()):
            raise ValueError(f"schema-v1 records require migration: {legacy.relative_to(project.root)}")
    records: dict[str, dict[str, Any]] = {}
    paths: dict[str, str] = {}
    for path, record in iter_records(project):
        record_id = str(record["id"])
        if record_id in records:
            raise ValueError(f"duplicate record id: {record_id}")
        records[record_id] = record
        paths[record_id] = str(path.relative_to(project.root))
    for record in records.values():
        missing = sorted(reference for reference in _references(record) if reference not in records)
        if missing:
            raise ValueError(f"{record['id']} references missing records: {', '.join(missing)}")
        _validate_reference_types(record, records)
    _validate_inheritance_graph(records)
    return {"valid": True, "records": len(records), "paths": paths}


def _require_kind(
    record: dict[str, Any],
    records: dict[str, dict[str, Any]],
    field: str,
    expected_kind: str,
) -> None:
    target = record.get(field)
    if isinstance(target, str) and records[target]["kind"] != expected_kind:
        raise ValueError(
            f"{record['id']} {field} must reference a {expected_kind}: {target}"
        )


def _validate_reference_types(
    record: dict[str, Any], records: dict[str, dict[str, Any]]
) -> None:
    kind = record["kind"]
    if kind == "study":
        design = record.get("design", {})
        if isinstance(design, dict):
            for field, expected in (
                ("dataset_id", "dataset"),
                ("baseline_run_id", "run"),
            ):
                target = design.get(field)
                if isinstance(target, str) and records[target]["kind"] != expected:
                    raise ValueError(
                        f"{record['id']} design {field} must reference a {expected}: "
                        f"{target}"
                    )
            for parent in design.get("inherited_modules", []):
                if not isinstance(parent, dict):
                    continue
                module_id = parent.get("module_id")
                if isinstance(module_id, str):
                    module = records[module_id]
                    if module["kind"] != "module":
                        raise ValueError(
                            f"{record['id']} inherited module is not a module: {module_id}"
                        )
                    if record["status"] in {"planned", "active"} and module[
                        "status"
                    ] != "available":
                        raise ValueError(
                            f"{record['id']} cannot plan with deprecated module: {module_id}"
                        )
    elif kind == "run":
        _require_kind(record, records, "study_id", "study")
        _require_kind(record, records, "dataset_id", "dataset")
        _require_kind(record, records, "baseline_run_id", "run")
        for module_id in record.get("module_ids", []):
            if records[str(module_id)]["kind"] != "module":
                raise ValueError(
                    f"{record['id']} module_ids entry is not a module: {module_id}"
                )
    elif kind == "module":
        _require_kind(record, records, "source_run", "run")
    elif kind == "assessment":
        _require_kind(record, records, "run_id", "run")
        _require_kind(record, records, "dataset_id", "dataset")


def _validate_inheritance_graph(records: dict[str, dict[str, Any]]) -> None:
    edges: dict[str, list[str]] = {}
    for record_id, record in records.items():
        dependencies: list[str] = []
        if record["kind"] == "run":
            for parent in record["initialization"]["parents"]:
                if "module_id" not in parent:
                    continue
                module_id = str(parent["module_id"])
                if records[module_id]["kind"] != "module":
                    raise ValueError(f"{record_id} parent is not a module record: {module_id}")
                module = records[module_id]
                if module["status"] == "unavailable":
                    if not _unavailable(parent):
                        raise ValueError(
                            f"{record_id} unavailable parent must be marked unavailable: "
                            f"{module_id}"
                        )
                elif parent.get("sha256") != module["artifact"].get("sha256"):
                    raise ValueError(
                        f"{record_id} parent hash does not match module artifact: "
                        f"{module_id}"
                    )
                if record["status"] in {"planned", "running"} and module[
                    "status"
                ] != "available":
                    raise ValueError(
                        f"{record_id} cannot initialize from deprecated module: {module_id}"
                    )
                dependencies.append(module_id)
        elif record["kind"] == "module":
            source_run = str(record["source_run"])
            if records[source_run]["kind"] != "run":
                raise ValueError(f"{record_id} source_run is not a run record: {source_run}")
            dependencies.append(source_run)
        edges[record_id] = dependencies

    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(record_id: str, trail: list[str]) -> None:
        if record_id in visiting:
            start = trail.index(record_id)
            raise ValueError("module inheritance cycle: " + " -> ".join(trail[start:]))
        if record_id in visited:
            return
        visiting.add(record_id)
        for dependency in edges.get(record_id, []):
            visit(dependency, [*trail, dependency])
        visiting.remove(record_id)
        visited.add(record_id)

    for record_id in edges:
        visit(record_id, [record_id])
