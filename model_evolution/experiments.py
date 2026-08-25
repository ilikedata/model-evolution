"""Supported experiment lifecycle over schema-v2 study and run records."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .adapters import load_adapter
from .config import load_project
from .records import iter_records, load_record, record_path
from .service import ModelEvolution


def _experiment_id(value: str) -> str:
    path = Path(value)
    if not value or path.parent != Path(".") or value in {".", ".."}:
        raise ValueError("experiment_id must be a plain record ID, not a path")
    return value


def load_experiment(
    experiment_id: str,
    *,
    root: str | Path = ".",
) -> dict[str, Any]:
    """Load an experiment definition and all of its execution attempts."""
    experiment_id = _experiment_id(experiment_id)
    project = load_project(root)
    definition = load_record(project, "study", experiment_id)
    runs = [
        record
        for _, record in iter_records(project, ("run",))
        if record.get("study_id") == experiment_id
    ]
    return {
        "kind": "experiment",
        "id": experiment_id,
        "status": definition["status"],
        "definition": definition,
        "runs": runs,
    }


def plan_experiment(
    definition: str | Path,
    *,
    root: str | Path = ".",
    actor: str | None = None,
    commit: bool = True,
) -> dict[str, Any]:
    """Validate and commit a canonical experiment definition."""
    project = load_project(root)
    service = ModelEvolution(project, actor=actor, commit=commit)
    study = service.commit_study(definition)
    return load_experiment(str(study["id"]), root=project.root)


def execute_experiment(
    experiment_id: str,
    *,
    root: str | Path = ".",
    actor: str | None = None,
    commit: bool = True,
) -> dict[str, Any]:
    """Plan and execute an internal run with the project-configured adapter."""
    experiment_id = _experiment_id(experiment_id)
    if not commit:
        raise ValueError("experiment execution requires committed lifecycle records")
    project = load_project(root)
    adapter = load_adapter(project.adapter)
    service = ModelEvolution(project, actor=actor, commit=True)
    run = service.plan_run(
        experiment_id,
        study_id=experiment_id,
        adapter=project.adapter,
    )
    adapter.execute_run(service, str(run["id"]))
    return load_experiment(experiment_id, root=project.root)


def conclude_experiment(
    experiment_id: str,
    *,
    root: str | Path = ".",
    actor: str | None = None,
    commit: bool = True,
) -> dict[str, Any]:
    """Validate and commit the conclusion authored in an experiment definition."""
    experiment_id = _experiment_id(experiment_id)
    project = load_project(root)
    service = ModelEvolution(project, actor=actor, commit=commit)
    service.commit_study(
        record_path(project, "study", experiment_id),
        concluded=True,
    )
    return load_experiment(experiment_id, root=project.root)
