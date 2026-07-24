from __future__ import annotations

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
    load_record,
    now,
    record_path,
    validate_repository,
    write_record,
)
from .storage import ArtifactStore, create_json, open_store, upload_file, upload_tree


def _file_reference(root: Path, path: str | Path) -> dict[str, Any]:
    relative = require_tracked_file(root, path)
    source = root / relative
    payload = source.read_bytes()
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

    def create_hypothesis(self, slug: str, title: str, body: str) -> dict[str, Any]:
        record_id = new_id(slug)
        record = base_record(
            "hypothesis",
            record_id,
            status="active",
            git_revision=self._git_revision(),
            producer=self.actor,
        )
        record["title"] = title
        path = write_record(self.project, "hypothesis", record, body=f"# {title}\n\n{body}")
        self._commit([path], f"research: add hypothesis {record_id}")
        return record

    def create_experiment(
        self,
        slug: str,
        *,
        hypothesis_ids: list[str],
        config_path: str | Path,
        objective: str,
    ) -> dict[str, Any]:
        for hypothesis_id in hypothesis_ids:
            load_record(self.project, "hypothesis", hypothesis_id)
            if self.commit:
                require_committed_file(
                    self.project.root,
                    record_path(self.project, "hypothesis", hypothesis_id),
                )
        record_id = new_id(slug)
        record = base_record(
            "experiment",
            record_id,
            status="planned",
            git_revision=self._git_revision(),
            producer=self.actor,
        )
        record.update(
            {
                "objective": objective,
                "hypothesis_ids": hypothesis_ids,
                "config": _file_reference(self.project.root, config_path),
            }
        )
        path = write_record(self.project, "experiment", record)
        self._commit([path], f"research: plan experiment {record_id}")
        return record

    def create_decision(
        self,
        slug: str,
        *,
        title: str,
        observations: list[str],
        inference: str,
        confidence: str,
        next_action: str,
        references: list[str],
    ) -> dict[str, Any]:
        if confidence not in {"low", "medium", "high"}:
            raise ValueError("decision confidence must be low, medium, or high")
        known = {str(record["id"]) for _, record in iter_records(self.project)}
        missing = sorted(set(references) - known)
        if missing:
            raise ValueError("decision references missing records: " + ", ".join(missing))
        record_id = new_id(slug)
        record = base_record(
            "decision",
            record_id,
            status="accepted",
            git_revision=self._git_revision(),
            producer=self.actor,
        )
        record.update(
            {
                "title": title,
                "decision": "research_direction",
                "confidence": confidence,
                "references": references,
            }
        )
        facts = "\n".join(f"- {observation}" for observation in observations)
        body = (
            f"# {title}\n\n"
            f"## Observations\n\n{facts}\n\n"
            f"## Inference\n\n{inference}\n\n"
            f"## Next action\n\n{next_action}"
        )
        path = write_record(self.project, "decision", record, body=body)
        self._commit([path], f"research: record decision {record_id}")
        return record

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
        record = base_record(
            "dataset",
            record_id,
            status="registered",
            git_revision=self._git_revision(),
            producer=self.actor,
        )
        record.update(
            {
                "artifact": {**artifact, "prefix": prefix},
                "generation": {
                    "entrypoint": generator,
                    "config": _file_reference(self.project.root, generator_config),
                    "seed": seed,
                    "source_git_revision": self._git_revision(),
                    "dataset_metadata_sha256": sha256(metadata_path.read_bytes()).hexdigest(),
                    "dataset_version": metadata.get("dataset_version"),
                },
            }
        )
        path = write_record(self.project, "dataset", record)
        self._commit([path], f"research: register dataset {record_id}")
        return record

    def plan_run(
        self,
        slug: str,
        *,
        experiment_id: str,
        dataset_id: str,
        config_path: str | Path,
        adapter: str,
        parent_module_ids: list[str],
    ) -> dict[str, Any]:
        require_clean_source(self.project.root)
        load_record(self.project, "experiment", experiment_id)
        load_record(self.project, "dataset", dataset_id)
        if self.commit:
            require_committed_file(
                self.project.root,
                record_path(self.project, "experiment", experiment_id),
            )
            require_committed_file(
                self.project.root,
                record_path(self.project, "dataset", dataset_id),
            )
        parents: list[dict[str, str]] = []
        for module_id in parent_module_ids:
            module = load_record(self.project, "module", module_id)
            if module["status"] != "promoted":
                raise ValueError(f"module is not promoted for reuse: {module_id}")
            if self.commit:
                require_committed_file(
                    self.project.root,
                    record_path(self.project, "module", module_id),
                )
            parents.append({"role": str(module["module_name"]), "module_id": module_id})
        record_id = new_id(slug)
        record = base_record(
            "run",
            record_id,
            status="planned",
            git_revision=self._git_revision(),
            producer=self.actor,
        )
        record.update(
            {
                "experiment_id": experiment_id,
                "dataset_id": dataset_id,
                "adapter": adapter,
                "config": _file_reference(self.project.root, config_path),
                "initialization": {
                    "kind": "inherited" if parents else "from_scratch",
                    "parents": parents,
                },
                "artifacts": [],
            }
        )
        path = write_record(self.project, "run", record)
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
                "schema_version": 1,
                "run_id": run_id,
                "agent": self.actor,
                "claimed_at": now(),
                "git_revision": self._git_revision(),
            },
        )

    def update_run(self, run: dict[str, Any], *, status: str, **updates: Any) -> Path:
        run = dict(run)
        run["status"] = status
        run["updated_at"] = now()
        run.update(updates)
        path = write_record(self.project, "run", run, replace_existing=True)
        self._commit([path], f"research: mark run {run['id']} {status}")
        return path

    def create_evaluation(
        self,
        *,
        run_id: str,
        dataset_id: str,
        metrics: dict[str, Any],
        artifact: dict[str, Any],
        split: str,
        evaluation_id: str | None = None,
    ) -> dict[str, Any]:
        load_record(self.project, "run", run_id)
        load_record(self.project, "dataset", dataset_id)
        if self.commit:
            require_committed_file(
                self.project.root,
                record_path(self.project, "run", run_id),
            )
            require_committed_file(
                self.project.root,
                record_path(self.project, "dataset", dataset_id),
            )
        record_id = evaluation_id or new_id(f"{run_id}-evaluation")
        record = base_record(
            "evaluation",
            record_id,
            status="completed",
            git_revision=self._git_revision(),
            producer=self.actor,
        )
        record.update(
            {
                "run_id": run_id,
                "dataset_id": dataset_id,
                "split": split,
                "metrics": metrics,
                "artifact": artifact,
            }
        )
        path = write_record(self.project, "evaluation", record)
        self._commit([path], f"research: record evaluation {record_id}")
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
        if self.commit:
            require_committed_file(
                self.project.root,
                record_path(self.project, "run", source_run),
            )
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
                "schema_version": 1,
                "module_id": record_id,
                "module_name": module_name,
                "source_run": source_run,
                "contract": contract,
                "weights": artifact,
            },
        )
        record = base_record(
            "module",
            record_id,
            status="candidate",
            git_revision=self._git_revision(),
            producer=self.actor,
        )
        record.update(
            {
                "module_name": module_name,
                "source_run": source_run,
                "artifact": {**artifact, "manifest_uri": manifest_uri},
                "contract": contract,
            }
        )
        path = write_record(self.project, "module", record)
        self._commit([path], f"research: publish candidate module {record_id}")
        return record

    def promote_module(
        self,
        module_id: str,
        *,
        evaluation_id: str,
        rationale: str,
        approval_context: str,
    ) -> dict[str, Any]:
        module = load_record(self.project, "module", module_id)
        if module["status"] != "candidate":
            raise ValueError(f"only candidate modules may be promoted: {module_id}")
        evaluation = load_record(self.project, "evaluation", evaluation_id)
        if self.commit:
            require_committed_file(
                self.project.root,
                record_path(self.project, "module", module_id),
            )
            require_committed_file(
                self.project.root,
                record_path(self.project, "evaluation", evaluation_id),
            )
        if evaluation["run_id"] != module["source_run"]:
            raise ValueError("evaluation does not belong to the candidate source run")
        decision_id = new_id(f"promote-{module_id}")
        decision = base_record(
            "decision",
            decision_id,
            status="accepted",
            git_revision=self._git_revision(),
            producer=self.actor,
        )
        decision.update(
            {
                "decision": "promote_module",
                "module_id": module_id,
                "evaluation_id": evaluation_id,
                "approval_context": approval_context,
            }
        )
        decision_path = write_record(
            self.project,
            "decision",
            decision,
            body=f"# Promote {module_id}\n\n{rationale}",
        )
        module = dict(module)
        module["status"] = "promoted"
        module["updated_at"] = now()
        module["promotion_decision"] = decision_id
        module_path = write_record(
            self.project,
            "module",
            module,
            replace_existing=True,
        )
        self._commit(
            [decision_path, module_path],
            f"research: promote module {module_id}",
        )
        return {"module": module, "decision": decision}

    def status(self) -> dict[str, Any]:
        counts: dict[str, dict[str, int]] = {}
        active: list[dict[str, str]] = []
        for _, record in iter_records(self.project):
            kind = str(record["kind"])
            status = str(record["status"])
            counts.setdefault(kind, {})
            counts[kind][status] = counts[kind].get(status, 0) + 1
            if status in {"active", "planned", "running", "candidate", "promoted"}:
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
            references: list[tuple[str, str]] = []
            for relation, field in (
                ("experiment", "experiment_id"),
                ("dataset", "dataset_id"),
                ("run", "run_id"),
                ("source_run", "source_run"),
                ("supersedes", "supersedes"),
            ):
                value = record.get(field)
                if isinstance(value, str):
                    references.append((relation, value))
            for value in record.get("hypothesis_ids", []):
                references.append(("hypothesis", str(value)))
            for parent in record.get("initialization", {}).get("parents", []):
                references.append((str(parent.get("role", "parent")), str(parent["module_id"])))
            for relation, target in references:
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
            "schema_version": 1,
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
