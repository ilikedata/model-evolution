from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any

from .evidence import extract_codex_sessions, validate_evidence_bundle
from .config import initialize_project, load_project
from .gitops import commit_paths
from .ids import new_id
from .migration_v2 import migrate_v2
from .service import ModelEvolution
from .storage import upload_file
from .storage_plan import build_storage_plan
from .storage_publish import publish_storage

LATENT_ARBORIST_METRIC_ADAPTER = "latent-arborist.metric"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="model-evolution")
    parser.add_argument("--root", default=".", help="project directory")
    parser.add_argument("--actor", help="agent or human identity recorded in manifests")
    parser.add_argument("--no-commit", action="store_true", help="write records without committing them")
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    commands = parser.add_subparsers(dest="command", required=True)

    init = commands.add_parser("init", help="initialize a project")
    init.add_argument("--project-id", required=True)
    init.add_argument("--artifact-store", required=True)
    init.add_argument("--adapter", required=True)

    commands.add_parser("validate", help="validate all project records and references")

    study = commands.add_parser("study", help="validate and commit canonical study documents")
    study_commands = study.add_subparsers(dest="study_command", required=True)
    study_plan = study_commands.add_parser("plan")
    study_plan.add_argument("path")
    study_conclude = study_commands.add_parser("conclude")
    study_conclude.add_argument("path")

    dataset = commands.add_parser("dataset")
    dataset_commands = dataset.add_subparsers(dest="dataset_command", required=True)
    dataset_register = dataset_commands.add_parser("register")
    dataset_register.add_argument("--slug", required=True)
    dataset_register.add_argument("--source", required=True)
    dataset_register.add_argument("--generator", required=True)
    dataset_register.add_argument("--generator-config", required=True)
    dataset_register.add_argument("--seed", required=True, type=int)
    dataset_generate = dataset_commands.add_parser("generate")
    dataset_generate.add_argument("--slug", required=True)
    dataset_generate.add_argument("--config", required=True)

    run = commands.add_parser("run")
    run_commands = run.add_subparsers(dest="run_command", required=True)
    run_plan = run_commands.add_parser("plan")
    run_plan.add_argument("--slug", required=True)
    run_plan.add_argument("--study", required=True)
    run_plan.add_argument("--adapter", default=LATENT_ARBORIST_METRIC_ADAPTER)
    run_execute = run_commands.add_parser("execute")
    run_execute.add_argument("run_id")
    run_execute.add_argument("--epochs-this-run", type=int)

    assessment = commands.add_parser("assessment")
    assessment_commands = assessment.add_subparsers(
        dest="assessment_command", required=True
    )
    assessment_record = assessment_commands.add_parser("record")
    assessment_record.add_argument("--run", required=True)
    assessment_record.add_argument("--dataset", required=True)
    assessment_record.add_argument("--report", required=True)
    assessment_record.add_argument("--evaluator", required=True)
    assessment_record.add_argument("--evaluator-version", required=True)
    assessment_record.add_argument("--purpose", required=True)

    migration = commands.add_parser("migrate-v2", help="convert a schema-v1 registry")
    migration.add_argument(
        "--apply", action="store_true", help="write the conversion; default is check-only"
    )

    commands.add_parser("status", help="summarize current project state")
    lineage = commands.add_parser("lineage", help="show dependency ancestry")
    lineage.add_argument("record_id")
    evidence = commands.add_parser("evidence", help="collect normalized research evidence")
    evidence_commands = evidence.add_subparsers(dest="evidence_command", required=True)
    codex = evidence_commands.add_parser(
        "codex-sessions",
        help="extract normalized evidence from exact-repository Codex sessions",
    )
    codex.add_argument(
        "--sessions-root",
        default=str(Path.home() / ".codex" / "sessions"),
        help="Codex sessions directory (default: ~/.codex/sessions)",
    )
    codex.add_argument("--output", help="bundle output directory")
    codex.add_argument(
        "--active-window-seconds",
        type=int,
        default=300,
        help="exclude session files modified within this many seconds",
    )
    evidence_validate = evidence_commands.add_parser(
        "validate",
        help="validate evidence, source-session, Git, and artifact identities",
    )
    evidence_validate.add_argument("--bundle", help="bundle directory")
    evidence_validate.add_argument(
        "--skip-source-digests",
        action="store_true",
        help="do not re-hash original Codex session files",
    )
    evidence_validate.add_argument(
        "--skip-artifact-digests",
        action="store_true",
        help="do not re-hash local run and dataset artifacts",
    )
    storage = commands.add_parser("storage")
    storage_commands = storage.add_subparsers(dest="storage_command", required=True)
    storage_commands.add_parser("probe", help="create and retain a unique readback probe")
    storage_plan = storage_commands.add_parser(
        "plan", help="plan normal-path storage for local record artifacts"
    )
    storage_plan.add_argument("--output", help="storage work directory")
    storage_plan.add_argument(
        "--rebuild",
        action="store_true",
        help="ignore local storage caches and fully hash/package artifacts",
    )
    storage_publish = storage_commands.add_parser(
        "publish",
        help="validate, plan, create, verify, and record immutable artifacts",
    )
    storage_publish.add_argument("--output", help="storage work directory")
    storage_publish.add_argument(
        "--rebuild",
        action="store_true",
        help="ignore local storage caches and fully hash/package artifacts",
    )
    return parser


def _emit(result: dict[str, Any], *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(result, indent=2, sort_keys=True))
        return
    if "id" in result:
        print(f"{result.get('kind', 'record')} {result['id']}: {result.get('status', 'created')}")
    elif "run_id" in result:
        print(f"run {result['run_id']}: {result['status']}")
    elif result.get("kind") == "artifact_storage_plan":
        summary = result["summary"]
        print(
            f"planned {result['plan_sha256'][:12]}: {summary['logical_files']} logical "
            f"files, {summary['objects']} objects, {summary['upload_bytes']} upload bytes, "
            f"{summary.get('cache_hits', 0)} cached"
        )
    elif result.get("kind") == "artifact_storage_receipt":
        summary = result["summary"]
        print(
            f"published {result['plan_sha256'][:12]}: {summary['created']} created, "
            f"{summary['existing']} already verified, "
            f"{summary['updated_records']} records updated, "
            f"{summary.get('cache_hits', 0)} cached"
        )
    elif result.get("valid"):
        if "records" in result:
            print(f"valid: {result['records']} records")
        else:
            print(
                f"valid: {result.get('sessions', 0)} sessions, "
                f"{result.get('evidence', 0)} evidence events, "
                f"{result.get('artifacts', 0)} artifacts, "
                f"{result.get('observations', 0)} observations"
            )
    else:
        print(json.dumps(result, indent=2, sort_keys=True))


def _service(args: argparse.Namespace) -> ModelEvolution:
    return ModelEvolution(
        load_project(args.root),
        actor=args.actor,
        commit=not args.no_commit,
    )


def _run(args: argparse.Namespace) -> dict[str, Any]:
    if args.command == "migrate-v2":
        return migrate_v2(args.root, apply=args.apply)
    if args.command == "init":
        project = initialize_project(
            args.root,
            project_id=args.project_id,
            artifact_store=args.artifact_store,
            adapter=args.adapter,
        )
        if not args.no_commit:
            commit_paths(
                project.root,
                [
                    project.config_path,
                    project.records_dir / "README.md",
                    project.records_dir / "PROJECT_STATE.md",
                ],
                f"research: initialize Model Evolution for {project.project_id}",
            )
        return {"id": project.project_id, "kind": "project", "status": "initialized"}

    service = _service(args)
    if args.command == "validate":
        return service.validate()
    if args.command == "study":
        return service.commit_study(
            args.path, concluded=args.study_command == "conclude"
        )
    if args.command == "dataset":
        if args.dataset_command == "generate":
            from .adapters import load_adapter

            adapter = load_adapter(service.project.adapter)
            return adapter.generate_dataset(service, slug=args.slug, config_path=args.config)
        return service.register_dataset(
            args.slug,
            source=args.source,
            generator=args.generator,
            generator_config=args.generator_config,
            seed=args.seed,
        )
    if args.command == "run":
        if args.run_command == "plan":
            return service.plan_run(
                args.slug,
                study_id=args.study,
                adapter=args.adapter,
            )
        if args.no_commit:
            raise ValueError("run execution requires committed lifecycle records")
        from .adapters import load_adapter

        adapter = load_adapter(service.project.adapter)
        return adapter.execute_run(service, args.run_id, epochs_this_run=args.epochs_this_run)
    if args.command == "assessment":
        report_path = Path(args.report)
        report = json.loads(report_path.read_text(encoding="utf-8"))
        assessment_id = new_id(f"{args.run}-assessment")
        artifact = upload_file(
            service.store,
            report_path,
            f"assessments/{assessment_id}/{report_path.name}",
        )
        return service.create_assessment(
            assessment_id=assessment_id,
            run_id=args.run,
            dataset_id=args.dataset,
            evaluator={"name": args.evaluator, "version": args.evaluator_version},
            metrics=report,
            artifact=artifact,
            purpose=args.purpose,
        )
    if args.command == "status":
        return service.status()
    if args.command == "evidence":
        if args.evidence_command == "codex-sessions":
            if args.active_window_seconds < 0:
                raise ValueError("--active-window-seconds cannot be negative")
            return extract_codex_sessions(
                service.project,
                sessions_root=args.sessions_root,
                output=args.output,
                active_window_seconds=args.active_window_seconds,
            )
        return validate_evidence_bundle(
            service.project,
            bundle=args.bundle,
            verify_sources=not args.skip_source_digests,
            verify_artifacts=not args.skip_artifact_digests,
        )
    if args.command == "storage":
        if args.storage_command == "plan":
            plan = build_storage_plan(
                service.project,
                output=args.output,
                progress=not args.json,
                rebuild=args.rebuild,
            )
            return {
                "kind": plan["kind"],
                "plan_sha256": plan["plan_sha256"],
                "summary": plan["summary"],
            }
        if args.storage_command == "publish":
            return publish_storage(
                service.project,
                output=args.output,
                commit=not args.no_commit,
                progress=not args.json,
                rebuild=args.rebuild,
            )
        return service.probe_storage()
    return service.lineage(args.record_id)


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        result = _run(args)
    except Exception as error:
        if args.json:
            print(json.dumps({"error": type(error).__name__, "message": str(error)}, sort_keys=True))
        else:
            print(f"error: {error}", file=sys.stderr)
        return 1
    _emit(result, as_json=args.json)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
