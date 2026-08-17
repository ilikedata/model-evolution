from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import getpass
import json
import os
from pathlib import Path
from typing import Any

from .config import ProjectConfig
from .gitops import (
    commit_paths,
    require_clean_source,
    require_committed_file,
    require_tracked_file,
    revision,
)
from .ids import new_id
from .records import (
    base_record,
    iter_records,
    load_document,
    load_record,
    record_path,
    validate_repository,
    write_record,
)
from .storage import ArtifactStore, create_json, open_store, upload_file, upload_tree


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _file_reference(root: Path, path: str | Path) -> dict[str, Any]:
    relative = require_tracked_file(root, path)
    payload = (root / relative).read_bytes()
    return {"path": str(relative), "sha256": sha256(payload).hexdigest()}


class ModelEvolution:
    def __init__(
        self,
        project: ProjectConfig,
        *,
        store: ArtifactStore | None = None,
        actor: str | None = None,
        commit: bool = True,
    ):
        self.project = project
        self._store = store
        self.actor = actor or os.environ.get("MODEL_EVOLUTION_ACTOR") or f"user:{getpass.getuser()}"
        self.commit = commit

    @property
    def store(self) -> ArtifactStore:
        if self._store is None:
            self._store = open_store(self.project.artifact_store)
        return self._store

    def _git_revision(self) -> str:
        return revision(self.project.root)

    def _commit(self, paths: list[Path], message: str) -> None:
        if self.commit:
            commit_paths(self.project.root, paths, message)

    def commit_study(self, path: str | Path, *, concluded: bool = False) -> dict[str, Any]:
        source = Path(path)
        if not source.is_absolute():
            source = self.project.root / source
        source = source.resolve()
        studies = (self.project.records_dir / "studies").resolve()
        if source.parent != studies or source.suffix != ".md":
            raise ValueError("study must be a Markdown file directly under model-evolution/studies")
        study, _ = load_document(self.project, "study", source.stem)
        expected = "concluded" if concluded else "planned"
        if study["status"] != expected:
            raise ValueError(f"study status must be {expected}: {study['id']}")
        if not concluded:
            design = study["design"]
            require_tracked_file(self.project.root, str(design["config"]))
        validate_repository(self.project)
        self._commit(
            [source],
            (
                f"research: conclude study {study['id']}"
                if concluded
                else f"research: plan study {study['id']}"
            ),
        )
        return study

    def register_dataset(
        self,
        slug: str,
        *,
        source: str | Path,
        generator: str,
        generator_config: str | Path,
        seed: int,
    ) -> dict[str, Any]:
        require_clean_source(self.project.root)
        record_id = new_id(slug)
        source_path = Path(source).resolve()
        metadata_path = source_path / "dataset.json"
        if not metadata_path.exists():
            raise FileNotFoundError(f"dataset metadata not found: {metadata_path}")
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        prefix = f"datasets/{record_id}"
        artifact = upload_tree(self.store, source_path, prefix)
        record = base_record("dataset", record_id, status="registered")
        record.update(
            {
                "artifact": {**artifact, "prefix": prefix},
                "generation": {
                    "entrypoint": generator,
                    "config": _file_reference(self.project.root, generator_config),
                    "seed": seed,
                    "source_revision": self._git_revision(),
                    "dataset_metadata_sha256": sha256(metadata_path.read_bytes()).hexdigest(),
                    "dataset_version": metadata.get("dataset_version"),
                },
            }
        )
        body = (
            f"# {slug}\n\n"
            "## Purpose\n\nGenerated dataset registered for immutable reuse.\n\n"
            "## Generation notes\n\nSee the pinned generator configuration in front matter.\n\n"
            "## Limitations\n\nNone recorded."
        )
        path = write_record(self.project, "dataset", record, body=body)
        self._commit([path], f"research: register dataset {record_id}")
        return record

    def plan_run(
        self,
        slug: str,
        *,
        study_id: str,
        adapter: str,
    ) -> dict[str, Any]:
        require_clean_source(self.project.root)
        study = load_record(self.project, "study", study_id)
        if study["status"] not in {"planned", "active"}:
            raise ValueError(f"study must be planned or active: {study_id}")
        if self.commit:
            require_committed_file(self.project.root, record_path(self.project, "study", study_id))
        design = study["design"]
        if not isinstance(design, dict):
            raise ValueError("study design must be a mapping")
        if design.get("experiment_mode") in {"proof", "scaled"} and not design[
            "preflight"
        ]["tiny_overfit"]["passed"]:
            raise ValueError("training experiment preflight must pass before planning")
        dataset_id = str(design.get("dataset_id", ""))
        config_path = design.get("config")
        if not dataset_id or not config_path:
            raise ValueError("study design must specify dataset_id and config")
        load_record(self.project, "dataset", dataset_id)
        if self.commit:
            require_committed_file(
                self.project.root, record_path(self.project, "dataset", dataset_id)
            )
        parents: list[dict[str, str]] = []
        for declared in design.get("inherited_modules", []):
            if not isinstance(declared, dict):
                raise ValueError("inherited_modules items must be mappings")
            module_id = str(declared.get("module_id", ""))
            module = load_record(self.project, "module", module_id)
            if module["status"] != "available":
                raise ValueError(f"module is not available for reuse: {module_id}")
            if self.commit:
                require_committed_file(
                    self.project.root, record_path(self.project, "module", module_id)
                )
            parents.append(
                {
                    "role": str(declared.get("role") or module["module_name"]),
                    "module_id": module_id,
                    "sha256": str(module["artifact"]["sha256"]),
                }
            )
        record_id = new_id(slug)
        record = base_record("run", record_id, status="planned")
        record.update(
            {
                "study_id": study_id,
                "dataset_id": dataset_id,
                "adapter": adapter,
                "config": _file_reference(self.project.root, str(config_path)),
                "source_revision": self._git_revision(),
                "initialization": {
                    "kind": "inherited" if parents else "from_scratch",
                    "parents": parents,
                },
            }
        )
        baseline = design.get("baseline_run_id")
        if baseline:
            load_record(self.project, "run", str(baseline))
            record["baseline_run_id"] = str(baseline)
        body = (
            f"# {slug}\n\n"
            "## Execution plan\n\nExecute the pinned study design.\n\n"
            "## Execution notes\n\nPending.\n\n"
            "## Observations\n\nPending.\n\n"
            "## Anomalies\n\nNone recorded."
        )
        path = write_record(self.project, "run", record, body=body)
        self._commit([path], f"research: plan run {record_id}")
        return record

    def claim_run(self, run_id: str) -> str:
        run = load_record(self.project, "run", run_id)
        if run["status"] != "planned":
            raise ValueError(f"run must be planned before claim: {run_id}")
        return create_json(
            self.store,
            f"claims/{run_id}.json",
            {
                "schema_version": 2,
                "run_id": run_id,
                "agent": self.actor,
                "claimed_at": now(),
                "source_revision": run["source_revision"],
            },
        )

    def update_run(
        self,
        run: dict[str, Any],
        *,
        status: str,
        body: str | None = None,
        **updates: Any,
    ) -> Path:
        current, current_body = load_document(self.project, "run", str(run["id"]))
        value = dict(current)
        value["status"] = status
        value.update(updates)
        path = write_record(
            self.project,
            "run",
            value,
            body=body or current_body,
            replace_existing=True,
        )
        self._commit([path], f"research: mark run {value['id']} {status}")
        return path

    def create_assessment(
        self,
        *,
        assessment_id: str | None = None,
        run_id: str,
        dataset_id: str,
        evaluator: dict[str, Any],
        metrics: dict[str, Any],
        artifact: dict[str, Any],
        purpose: str,
    ) -> dict[str, Any]:
        load_record(self.project, "run", run_id)
        load_record(self.project, "dataset", dataset_id)
        record_id = assessment_id or new_id(f"{run_id}-assessment")
        record = base_record("assessment", record_id, status="completed")
        record.update(
            {
                "run_id": run_id,
                "dataset_id": dataset_id,
                "evaluator": evaluator,
                "metrics": metrics,
                "artifact": artifact,
            }
        )
        body = (
            f"# Assessment of {run_id}\n\n"
            f"## Purpose\n\n{purpose}\n\n"
            "## Observations\n\nSee the structured metrics and immutable report."
        )
        path = write_record(self.project, "assessment", record, body=body)
        self._commit([path], f"research: assess run {run_id}")
        return record

    def create_module(
        self,
        *,
        slug: str,
        module_name: str,
        source_run: str,
        source_weights: str | Path,
        contract: dict[str, Any],
    ) -> dict[str, Any]:
        load_record(self.project, "run", source_run)
        record_id = new_id(slug)
        artifact = upload_file(
            self.store,
            source_weights,
            f"modules/{module_name}/{record_id}/weights.pt",
        )
        manifest_uri = create_json(
            self.store,
            f"modules/{module_name}/{record_id}/manifest.json",
            {
                "schema_version": 2,
                "module_id": record_id,
                "module_name": module_name,
                "source_run": source_run,
                "contract": contract,
                "weights": artifact,
            },
        )
        record = base_record("module", record_id, status="available")
        record.update(
            {
                "module_name": module_name,
                "source_run": source_run,
                "artifact": {**artifact, "manifest_uri": manifest_uri},
                "contract": contract,
            }
        )
        body = (
            f"# {module_name}\n\n"
            "## Intended use\n\nReusable output of the source training run.\n\n"
            "## Training evidence\n\nSee the source run's embedded primary results.\n\n"
            "## Limitations\n\nNone recorded."
        )
        path = write_record(self.project, "module", record, body=body)
        self._commit([path], f"research: publish module {record_id}")
        return record

    def status(self) -> dict[str, Any]:
        counts: dict[str, dict[str, int]] = {}
        active: list[dict[str, str]] = []
        for _, record in iter_records(self.project):
            kind = str(record["kind"])
            status = str(record["status"])
            counts.setdefault(kind, {})
            counts[kind][status] = counts[kind].get(status, 0) + 1
            if status in {"draft", "planned", "active", "running", "available"}:
                active.append({"id": str(record["id"]), "kind": kind, "status": status})
        return {"project_id": self.project.project_id, "counts": counts, "active": active}

    def lineage(self, record_id: str) -> dict[str, Any]:
        all_records = {str(record["id"]): record for _, record in iter_records(self.project)}
        if record_id not in all_records:
            raise FileNotFoundError(f"record not found: {record_id}")
        edges: list[dict[str, str]] = []
        visited: set[str] = set()

        def visit(current_id: str) -> None:
            if current_id in visited:
                return
            visited.add(current_id)
            record = all_records[current_id]
            refs: list[tuple[str, str]] = []
            for relation, field in (
                ("study", "study_id"),
                ("dataset", "dataset_id"),
                ("run", "run_id"),
                ("source_run", "source_run"),
                ("baseline", "baseline_run_id"),
            ):
                value = record.get(field)
                if isinstance(value, str):
                    refs.append((relation, value))
            refs.extend(("evidence", str(value)) for value in record.get("references", []))
            refs.extend(("module", str(value)) for value in record.get("module_ids", []))
            conclusion = record.get("conclusion", {})
            if isinstance(conclusion, dict):
                refs.extend(("evidence", str(value)) for value in conclusion.get("evidence", []))
            initialization = record.get("initialization", {})
            if isinstance(initialization, dict):
                for parent in initialization.get("parents", []):
                    if isinstance(parent, dict) and isinstance(parent.get("module_id"), str):
                        refs.append((str(parent.get("role", "parent")), parent["module_id"]))
            design = record.get("design", {})
            if isinstance(design, dict):
                for relation, field in (
                    ("dataset", "dataset_id"),
                    ("baseline", "baseline_run_id"),
                ):
                    value = design.get(field)
                    if isinstance(value, str):
                        refs.append((relation, value))
                for parent in design.get("inherited_modules", []):
                    if isinstance(parent, dict) and isinstance(
                        parent.get("module_id"), str
                    ):
                        refs.append(
                            (str(parent.get("role", "module")), parent["module_id"])
                        )
            for relation, target in refs:
                edges.append({"from": current_id, "relation": relation, "to": target})
                if target in all_records:
                    visit(target)

        visit(record_id)
        return {
            "root": record_id,
            "records": [all_records[value] for value in sorted(visited)],
            "edges": edges,
        }

    def validate(self) -> dict[str, Any]:
        return validate_repository(self.project)

    def probe_storage(self) -> dict[str, Any]:
        probe_id = new_id("permission-probe")
        relative_path = f"probes/{probe_id}.json"
        payload = {
            "schema_version": 2,
            "probe_id": probe_id,
            "project_id": self.project.project_id,
            "created_at": now(),
            "producer": self.actor,
        }
        uri = create_json(self.store, relative_path, payload)
        observed = json.loads(self.store.read_bytes(relative_path))
        if observed != payload:
            raise ValueError(f"storage probe readback mismatch: {uri}")
        return {"id": probe_id, "kind": "storage_probe", "status": "retained", "uri": uri}
