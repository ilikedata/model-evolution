from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Any

from .config import ProjectConfig
from .yamlio import load_markdown, load_yaml, write_markdown, write_yaml


LEGACY_KINDS = {
    "hypothesis": ("hypotheses", ".md"),
    "experiment": ("experiments", ".yaml"),
    "dataset": ("datasets", ".yaml"),
    "run": ("runs", ".yaml"),
    "module": ("modules", ".yaml"),
    "evaluation": ("evaluations", ".yaml"),
    "decision": ("decisions", ".md"),
}


def _legacy_records(root: Path, records_dir: Path) -> dict[str, dict[str, Any]]:
    records: dict[str, dict[str, Any]] = {}
    for kind, (directory, suffix) in LEGACY_KINDS.items():
        for path in sorted((records_dir / directory).glob(f"*{suffix}")):
            if suffix == ".md":
                value, body = load_markdown(path)
                value["_body"] = body
            else:
                value = load_yaml(path)
            value["_path"] = path
            value["_legacy_kind"] = kind
            records[str(value["id"])] = value
    return records


def _clean(value: dict[str, Any], *drop: str) -> dict[str, Any]:
    ignored = {
        "schema_version",
        "kind",
        "id",
        "created_at",
        "updated_at",
        "git_revision",
        "producer",
        "_body",
        "_path",
        "_legacy_kind",
        *drop,
    }
    return {key: item for key, item in value.items() if key not in ignored}


def _title(record: dict[str, Any]) -> str:
    return str(record.get("title") or record["id"].rsplit("-", 1)[0]).replace("-", " ")


def _study_body(
    hypothesis: dict[str, Any],
    experiment: dict[str, Any] | None,
    evidence: list[str],
) -> str:
    title = _title(hypothesis)
    original = str(hypothesis.get("_body", "")).strip()
    if all(f"## {heading}" in original for heading in ("Claim", "Basis", "Expected evidence", "Falsification")):
        body = original
    else:
        claim = original
        if claim.startswith("# "):
            claim = "\n".join(claim.splitlines()[1:]).strip()
        body = (
            f"# {title}\n\n"
            f"## Claim\n\n{claim or 'Historical claim reconstructed from the v1 registry.'}\n\n"
            "## Basis\n\nSee the migrated provenance and evidence references.\n\n"
            "## Expected evidence\n\nThe experiment should produce measurable improvement "
            "against its recorded objective.\n\n"
            "## Falsification\n\nReject the claim if the recorded objective is not met."
        )
    if experiment and "## Method" not in body:
        body += f"\n\n## Method\n\n{experiment.get('objective', 'Method unavailable.')}"
    if experiment and hypothesis.get("status") in {"supported", "rejected"}:
        outcome = "supported" if hypothesis["status"] == "supported" else "rejected"
        body += (
            "\n\n## Observations\n\nSee the embedded results in the migrated run records."
            f"\n\n## Conclusion\n\nThe prior evidence marked this claim {outcome}."
            "\n\n## Next action\n\nContinue from the recorded project state."
        )
    if evidence:
        body += "\n\n## Migrated evidence\n\n" + "\n".join(f"- `{value}`" for value in evidence)
    return body


def _project(root: Path, raw: dict[str, Any]) -> ProjectConfig:
    return ProjectConfig(
        root=root,
        project_id=str(raw["project_id"]),
        artifact_store=str(raw["artifact_store"]).rstrip("/"),
        adapter=str(raw["adapter"]),
        records_dir=root / str(raw.get("records_dir", "model-evolution")),
        work_dir=root / str(raw.get("work_dir", ".model-evolution/work")),
    )


def migrate_v2(start: str | Path = ".", *, apply: bool = False) -> dict[str, Any]:
    root = Path(start).resolve()
    config_path = root / ".model-evolution" / "project.yaml"
    raw = load_yaml(config_path)
    if raw.get("schema_version") == 2:
        return {"kind": "migration_v2", "status": "already_v2", "changes": 0}
    if raw.get("schema_version") != 1:
        raise ValueError(f"unsupported project schema in {config_path}")
    project = _project(root, raw)
    records = _legacy_records(root, project.records_dir)
    if not records:
        raise ValueError("schema-v1 project has no records to migrate")

    experiments = [value for value in records.values() if value["_legacy_kind"] == "experiment"]
    runs = [value for value in records.values() if value["_legacy_kind"] == "run"]
    evaluations = [value for value in records.values() if value["_legacy_kind"] == "evaluation"]
    modules = [value for value in records.values() if value["_legacy_kind"] == "module"]
    by_experiment: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_run_evaluation: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_run_module: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for run in runs:
        by_experiment[str(run["experiment_id"])].append(run)
    for evaluation in evaluations:
        by_run_evaluation[str(evaluation["run_id"])].append(evaluation)
    for module in modules:
        by_run_module[str(module["source_run"])].append(module)

    experiment_for_hypothesis: dict[str, dict[str, Any]] = {}
    for experiment in experiments:
        for hypothesis_id in experiment.get("hypothesis_ids", []):
            experiment_for_hypothesis[str(hypothesis_id)] = experiment
    absorbed: dict[str, str] = {}
    evaluation_to_run = {
        str(evaluation["id"]): str(evaluation["run_id"])
        for evaluation in evaluations
    }
    documents: list[tuple[Path, dict[str, Any], str]] = []

    for hypothesis in (
        value for value in records.values() if value["_legacy_kind"] == "hypothesis"
    ):
        study_id = str(hypothesis["id"])
        experiment = experiment_for_hypothesis.get(study_id)
        evidence = []
        references = []
        for value in hypothesis.get("references", []):
            target = records.get(str(value))
            if target and target["_legacy_kind"] == "evaluation":
                value = str(target["run_id"])
            elif target and target["_legacy_kind"] == "decision":
                absorbed[str(target["id"])] = study_id
                references.extend(
                    evaluation_to_run.get(str(item), str(item))
                    for item in target.get("references", [])
                )
                continue
            references.append(str(value))
        if experiment:
            absorbed[str(experiment["id"])] = study_id
            evidence = [str(run["id"]) for run in by_experiment[str(experiment["id"])]]
        references = list(
            dict.fromkeys(
                evaluation_to_run.get(str(value), str(value))
                for value in [*references, *evidence]
            )
        )
        concluded = bool(
            experiment and hypothesis.get("status") in {"supported", "rejected"}
        )
        status = "concluded" if concluded else "draft"
        front = {"status": status, "references": references}
        if experiment:
            linked_runs = by_experiment[str(experiment["id"])]
            if linked_runs:
                source = linked_runs[0]
                front["design"] = {
                    "dataset_id": source["dataset_id"],
                    "config": source.get("config", {}).get("path"),
                }
        if concluded:
            front["conclusion"] = {
                "outcome": (
                    "supported" if hypothesis["status"] == "supported" else "rejected"
                ),
                "confidence": "medium",
                "evidence": evidence,
            }
        front.update(
            {
                key: value
                for key, value in _clean(hypothesis, "title", "references", "status").items()
                if key == "provenance"
            }
        )
        documents.append(
            (
                project.records_dir / "studies" / f"{study_id}.md",
                front,
                _study_body(hypothesis, experiment, evidence),
            )
        )

    for dataset in (
        value for value in records.values() if value["_legacy_kind"] == "dataset"
    ):
        record_id = str(dataset["id"])
        documents.append(
            (
                project.records_dir / "datasets" / f"{record_id}.md",
                _clean(dataset),
                (
                    f"# {_title(dataset)}\n\n"
                    "## Purpose\n\nMigrated immutable prior dataset.\n\n"
                    "## Generation notes\n\nGeneration provenance is retained in front matter.\n\n"
                    "## Limitations\n\nSee unresolved fields and provenance where present."
                ),
            )
        )

    for run in runs:
        record_id = str(run["id"])
        experiment_id = str(run["experiment_id"])
        hypothesis_ids = records[experiment_id].get("hypothesis_ids", [])
        if not hypothesis_ids:
            raise ValueError(f"experiment has no hypothesis: {experiment_id}")
        front = _clean(run, "experiment_id", "evaluation_id")
        front["study_id"] = str(hypothesis_ids[0])
        front["source_revision"] = str(run["git_revision"])
        linked_evaluations = by_run_evaluation.get(record_id, [])
        if linked_evaluations:
            primary = linked_evaluations[0]
            absorbed[str(primary["id"])] = record_id
            front["results"] = {
                "primary": {
                    "dataset_id": primary["dataset_id"],
                    "split": primary.get("split"),
                    "metrics": primary["metrics"],
                    "artifact": primary.get("artifact"),
                    "legacy_evaluation_id": primary["id"],
                }
            }
        else:
            front.setdefault("results", {"primary": {"status": "unavailable"}})
        front.setdefault("artifacts", front.pop("artifact", []))
        front["module_ids"] = [str(value["id"]) for value in by_run_module.get(record_id, [])]
        documents.append(
            (
                project.records_dir / "runs" / f"{record_id}.md",
                front,
                (
                    f"# {_title(run)}\n\n"
                    "## Execution plan\n\nMigrated execution.\n\n"
                    "## Execution notes\n\nSee structured results and provenance.\n\n"
                    "## Observations\n\nPrimary evaluation is embedded in front matter.\n\n"
                    "## Anomalies\n\nSee provenance gaps where recorded."
                ),
            )
        )

    for module in modules:
        record_id = str(module["id"])
        front = _clean(module, "evaluation_id", "promotion_decision")
        front["status"] = "deprecated" if module["status"] == "deprecated" else "available"
        documents.append(
            (
                project.records_dir / "modules" / f"{record_id}.md",
                front,
                (
                    f"# {_title(module)}\n\n"
                    "## Intended use\n\nReusable output of the source run.\n\n"
                    "## Training evidence\n\nSee the source run's embedded results.\n\n"
                    "## Limitations\n\nSee the compatibility contract and provenance."
                ),
            )
        )

    for evaluation in evaluations:
        run_evaluations = by_run_evaluation[str(evaluation["run_id"])]
        if evaluation is run_evaluations[0]:
            continue
        record_id = str(evaluation["id"])
        documents.append(
            (
                project.records_dir / "assessments" / f"{record_id}.md",
                {
                    **_clean(evaluation, "split"),
                    "evaluator": {"name": "legacy", "version": "v1"},
                },
                (
                    f"# Assessment of {evaluation['run_id']}\n\n"
                    "## Purpose\n\nMigrated additional evaluation.\n\n"
                    "## Observations\n\nSee structured metrics."
                ),
            )
        )

    targets = [str(path.relative_to(root)) for path, _, _ in documents]
    result = {
        "kind": "migration_v2",
        "status": "ready" if not apply else "migrated",
        "source_records": len(records),
        "target_records": len(documents),
        "absorbed_ids": dict(sorted(absorbed.items())),
        "targets": sorted(targets),
    }
    if not apply:
        return result

    for path, front, body in documents:
        write_markdown(path, front, body)
    mapping = [
        "# Model Evolution v2 migration",
        "",
        "The following schema-v1 IDs were absorbed into schema-v2 records:",
        "",
        *(
            f"- `{source}` → `{target}`"
            for source, target in sorted(absorbed.items())
        ),
        "",
        "Git history remains authoritative for the original record contents.",
    ]
    (project.records_dir / "MIGRATION_V2.md").write_text(
        "\n".join(mapping) + "\n", encoding="utf-8"
    )
    for value in records.values():
        Path(value["_path"]).unlink()
    raw["schema_version"] = 2
    write_yaml(config_path, raw)
    return result
