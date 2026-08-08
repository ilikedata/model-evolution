from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
import sys
from typing import Any, Callable

from tqdm import tqdm

from .config import ProjectConfig
from .gitops import commit_paths
from .records import load_document, validate_repository, write_record
from .storage import ArtifactCollisionError, ArtifactStore, open_store
from .storage_plan import DEFAULT_OUTPUT, _json_bytes, _sha256_file, build_storage_plan


RECEIPT_SCHEMA_VERSION = 1


def _artifact_at(record: dict[str, Any], trail: list[str]) -> dict[str, Any]:
    value: Any = record
    for segment in trail:
        value = value[int(segment)] if isinstance(value, list) else value[segment]
    if not isinstance(value, dict):
        raise ValueError(f"artifact path is not a mapping: {'.'.join(trail)}")
    return value


def _verify_local(project: ProjectConfig, artifact: dict[str, Any]) -> Path:
    source = (project.root / str(artifact["upload_path"])).resolve()
    try:
        source.relative_to(project.root)
    except ValueError as error:
        raise ValueError(f"upload path escapes project root: {source}") from error
    if not source.is_file():
        raise FileNotFoundError(source)
    if source.stat().st_size != artifact["size"]:
        raise ValueError(f"upload size changed: {artifact['upload_path']}")
    if _sha256_file(source) != artifact["sha256"]:
        raise ValueError(f"upload digest changed: {artifact['upload_path']}")
    return source


def _verify_remote(
    store: ArtifactStore,
    artifact: dict[str, Any],
    remote: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if remote is None:
        remote = store.stat(str(artifact["object_path"]))
    if remote is None:
        raise ValueError(f"uploaded object is missing: {artifact['object_path']}")
    if remote.get("size") != artifact["size"]:
        raise ValueError(f"remote size mismatch: {artifact['object_path']}")
    if remote.get("sha256") != artifact["sha256"]:
        raise ValueError(f"remote SHA-256 mismatch: {artifact['object_path']}")
    return remote


def _publish_artifact(
    project: ProjectConfig,
    store: ArtifactStore,
    plan: dict[str, Any],
    artifact: dict[str, Any],
    progress_callback: Callable[[int], None] | None = None,
) -> dict[str, Any]:
    remote = store.stat(str(artifact["object_path"]))
    if remote is not None:
        verified = _verify_remote(store, artifact, remote)
        return {
            "record_id": artifact["record_id"],
            "record_kind": artifact["record_kind"],
            "artifact_path": artifact["artifact_path"],
            "artifact_paths": artifact.get(
                "artifact_paths", [artifact["artifact_path"]]
            ),
            "object_path": artifact["object_path"],
            "uri": verified["uri"],
            "size": verified["size"],
            "sha256": verified["sha256"],
            "generation": verified.get("generation"),
            "disposition": "existing",
        }

    source = _verify_local(project, artifact)
    metadata = {
        "model-evolution-sha256": str(artifact["sha256"]),
        "model-evolution-plan": str(plan["plan_sha256"]),
        "model-evolution-record": str(artifact["record_id"]),
        "model-evolution-role": str(artifact["role"]),
    }
    disposition = "created"
    try:
        remote = store.create_file(
            str(artifact["object_path"]),
            source,
            content_type=str(artifact["content_type"]),
            metadata=metadata,
            progress_callback=progress_callback,
        )
    except ArtifactCollisionError:
        disposition = "existing"
        remote = store.stat(str(artifact["object_path"]))
    remote = _verify_remote(store, artifact, remote)
    return {
        "record_id": artifact["record_id"],
        "record_kind": artifact["record_kind"],
        "artifact_path": artifact["artifact_path"],
        "artifact_paths": artifact.get(
            "artifact_paths", [artifact["artifact_path"]]
        ),
        "object_path": artifact["object_path"],
        "uri": remote["uri"],
        "size": remote["size"],
        "sha256": remote["sha256"],
        "generation": remote.get("generation"),
        "disposition": disposition,
    }


def _update_records(
    project: ProjectConfig,
    plan: dict[str, Any],
    published: list[dict[str, Any]],
) -> list[Path]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for item in published:
        grouped[(str(item["record_kind"]), str(item["record_id"]))].append(item)

    changed: list[Path] = []
    for (kind, record_id), items in sorted(grouped.items()):
        record, body = load_document(project, kind, record_id)
        record_changed = False
        for item in items:
            published_fields = {
                "status": "available",
                "uri": item["uri"],
                "storage": {
                    "object_path": item["object_path"],
                    "object_sha256": item["sha256"],
                    "size": item["size"],
                    "generation": item["generation"],
                    "plan_sha256": plan["plan_sha256"],
                },
            }
            artifact_paths = item.get(
                "artifact_paths", [item["artifact_path"]]
            )
            for artifact_path in artifact_paths:
                artifact = _artifact_at(record, list(artifact_path))
                if any(
                    artifact.get(key) != value
                    for key, value in published_fields.items()
                ):
                    artifact.update(published_fields)
                    record_changed = True
        if record_changed:
            changed.append(
                write_record(
                    project,
                    kind,
                    record,
                    body=body,
                    replace_existing=True,
                )
            )
    return changed


def publish_storage(
    project: ProjectConfig,
    *,
    output: str | Path | None = None,
    store: ArtifactStore | None = None,
    commit: bool = True,
    progress: bool = False,
    rebuild: bool = False,
) -> dict[str, Any]:
    validate_repository(project)
    plan = build_storage_plan(
        project,
        output=output,
        progress=progress,
        rebuild=rebuild,
    )
    artifact_store = store or open_store(project.artifact_store)
    published: list[dict[str, Any]] = []
    upload_progress = tqdm(
        total=plan["summary"]["upload_bytes"],
        desc="publish artifacts",
        unit="B",
        unit_scale=True,
        disable=not progress or not sys.stderr.isatty(),
    )
    try:
        for artifact in plan["artifacts"]:
            transferred = 0

            def update_transferred(size: int) -> None:
                nonlocal transferred
                applied = min(size, int(artifact["size"]) - transferred)
                if applied > 0:
                    upload_progress.update(applied)
                    transferred += applied

            result = _publish_artifact(
                project,
                artifact_store,
                plan,
                artifact,
                progress_callback=update_transferred,
            )
            published.append(result)
            update_transferred(int(artifact["size"]) - transferred)
            upload_progress.set_postfix(
                created=sum(item["disposition"] == "created" for item in published),
                existing=sum(item["disposition"] == "existing" for item in published),
                refresh=False,
            )
    finally:
        upload_progress.close()

    # Newly created objects get a separate readback before records change.
    created_paths = {
        item["object_path"]
        for item in published
        if item["disposition"] == "created"
    }
    for artifact in tqdm(
        [
            artifact
            for artifact in plan["artifacts"]
            if artifact["object_path"] in created_paths
        ],
        desc="verify new artifacts",
        unit="object",
        disable=not progress or not sys.stderr.isatty(),
    ):
        _verify_remote(artifact_store, artifact)

    changed = _update_records(project, plan, published)
    validation = validate_repository(project)
    if commit:
        commit_paths(
            project.root,
            changed,
            f"research: publish {len(published)} immutable artifacts",
        )

    output_path = (
        (project.root / Path(output)).resolve()
        if output is not None
        else project.root / DEFAULT_OUTPUT
    )
    receipt = {
        "schema_version": RECEIPT_SCHEMA_VERSION,
        "kind": "artifact_storage_receipt",
        "project_id": project.project_id,
        "artifact_store": project.artifact_store,
        "plan_sha256": plan["plan_sha256"],
        "published_at": datetime.now(timezone.utc).isoformat(),
        "status": "verified",
        "summary": {
            **plan["summary"],
            "created": sum(item["disposition"] == "created" for item in published),
            "existing": sum(item["disposition"] == "existing" for item in published),
            "updated_records": len(changed),
            "validated_records": validation["records"],
        },
        "artifacts": published,
    }
    receipt_path = output_path / "receipt.json"
    temporary = receipt_path.with_suffix(".tmp")
    temporary.write_bytes(_json_bytes(receipt))
    temporary.replace(receipt_path)
    return receipt
