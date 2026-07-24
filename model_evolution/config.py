from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from .yamlio import load_yaml, write_yaml


@dataclass(frozen=True)
class ProjectConfig:
    root: Path
    project_id: str
    artifact_store: str
    adapter: str
    records_dir: Path
    work_dir: Path

    @property
    def config_path(self) -> Path:
        return self.root / ".model-evolution" / "project.yaml"


def load_project(start: str | Path = ".") -> ProjectConfig:
    current = Path(start).resolve()
    for root in (current, *current.parents):
        path = root / ".model-evolution" / "project.yaml"
        if path.exists():
            raw = load_yaml(path)
            if raw.get("schema_version") != 1:
                raise ValueError(f"unsupported project schema in {path}")
            store = str(raw["artifact_store"]).rstrip("/")
            parsed = urlparse(store)
            if parsed.scheme not in {"gs", "file"}:
                raise ValueError("artifact_store must use gs:// or file://")
            return ProjectConfig(
                root=root,
                project_id=str(raw["project_id"]),
                artifact_store=store,
                adapter=str(raw["adapter"]),
                records_dir=root / str(raw.get("records_dir", "model-evolution")),
                work_dir=root / str(raw.get("work_dir", ".model-evolution/work")),
            )
    raise FileNotFoundError("no .model-evolution/project.yaml found")


def initialize_project(
    root: str | Path,
    *,
    project_id: str,
    artifact_store: str,
    adapter: str,
) -> ProjectConfig:
    root_path = Path(root).resolve()
    config_path = root_path / ".model-evolution" / "project.yaml"
    if config_path.exists():
        raise FileExistsError(f"project already initialized: {config_path}")
    write_yaml(
        config_path,
        {
            "schema_version": 1,
            "project_id": project_id,
            "artifact_store": artifact_store.rstrip("/"),
            "adapter": adapter,
            "records_dir": "model-evolution",
            "work_dir": ".model-evolution/work",
        },
    )
    records_root = root_path / "model-evolution"
    for directory in (
        "hypotheses",
        "decisions",
        "experiments",
        "datasets",
        "runs",
        "modules",
        "evaluations",
    ):
        (records_root / directory).mkdir(parents=True, exist_ok=True)
    readme = records_root / "README.md"
    state = records_root / "PROJECT_STATE.md"
    readme.write_text(
        f"# {project_id} research registry\n\n"
        "Git-tracked hypotheses, experiments, datasets, runs, evaluations, modules, "
        "and decisions managed by Model Evolution.\n",
        encoding="utf-8",
    )
    state.write_text(
        "# Project state\n\n"
        "## Current recommendation\n\nNone recorded.\n\n"
        "## Active hypotheses\n\nNone recorded.\n\n"
        "## Candidate and promoted modules\n\nNone recorded.\n\n"
        "## Known failures\n\nNone recorded.\n\n"
        "## Next actions\n\nRecord the first hypothesis and experiment.\n",
        encoding="utf-8",
    )
    return load_project(root_path)
