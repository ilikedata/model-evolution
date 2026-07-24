from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any

from .backfill import extract_codex_sessions, validate_backfill_bundle
from .config import initialize_project, load_project
from .gitops import commit_paths
from .historical_upload import (
    build_historical_upload_plan,
    upload_historical_plan,
    verify_historical_upload,
)
from .ids import new_id
from .service import ModelEvolution
from .storage import upload_file
from .yamlio import load_yaml

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

    hypothesis = commands.add_parser("hypothesis")
    hypothesis_commands = hypothesis.add_subparsers(dest="hypothesis_command", required=True)
    hypothesis_create = hypothesis_commands.add_parser("create")
    hypothesis_create.add_argument("--slug", required=True)
    hypothesis_create.add_argument("--title", required=True)
    hypothesis_create.add_argument("--body", default="")
    hypothesis_create.add_argument("--body-file")
    hypothesis_create.add_argument(
        "--reference",
        action="append",
        default=[],
        help="existing canonical record used as evidence",
    )

    experiment = commands.add_parser("experiment")
    experiment_commands = experiment.add_subparsers(dest="experiment_command", required=True)
    experiment_create = experiment_commands.add_parser("create")
    experiment_create.add_argument("--slug", required=True)
    experiment_create.add_argument("--hypothesis", action="append", required=True)
    experiment_create.add_argument("--config", required=True)
    experiment_create.add_argument("--objective", required=True)

    decision = commands.add_parser("decision")
    decision_commands = decision.add_subparsers(dest="decision_command", required=True)
    decision_create = decision_commands.add_parser("create")
    decision_create.add_argument("--slug", required=True)
    decision_create.add_argument("--title", required=True)
    decision_create.add_argument("--observation", action="append", required=True)
    decision_create.add_argument("--inference", required=True)
    decision_create.add_argument("--confidence", choices=("low", "medium", "high"), required=True)
    decision_create.add_argument("--next-action", required=True)
    decision_create.add_argument("--reference", action="append", default=[])

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
    run_plan.add_argument("--experiment", required=True)
    run_plan.add_argument("--dataset", required=True)
    run_plan.add_argument("--config", required=True)
    run_plan.add_argument("--adapter", default=LATENT_ARBORIST_METRIC_ADAPTER)
    run_plan.add_argument("--parent-module", action="append", default=[])
    run_execute = run_commands.add_parser("execute")
    run_execute.add_argument("run_id")
    run_execute.add_argument("--epochs-this-run", type=int)

    evaluation = commands.add_parser("evaluation")
    evaluation_commands = evaluation.add_subparsers(dest="evaluation_command", required=True)
    evaluation_record = evaluation_commands.add_parser("record")
    evaluation_record.add_argument("--run", required=True)
    evaluation_record.add_argument("--dataset", required=True)
    evaluation_record.add_argument("--split", default="test")
    evaluation_record.add_argument("--report", required=True)

    module = commands.add_parser("module")
    module_commands = module.add_subparsers(dest="module_command", required=True)
    module_publish = module_commands.add_parser("publish")
    module_publish.add_argument("--slug", required=True)
    module_publish.add_argument("--name", required=True)
    module_publish.add_argument("--run", required=True)
    module_publish.add_argument("--weights", required=True)
    module_publish.add_argument("--contract", required=True)
    module_promote = module_commands.add_parser("promote")
    module_promote.add_argument("module_id")
    module_promote.add_argument("--evaluation", required=True)
    module_promote.add_argument("--rationale", required=True)
    module_promote.add_argument("--approval-context", required=True)

    commands.add_parser("status", help="summarize current project state")
    lineage = commands.add_parser("lineage", help="show dependency ancestry")
    lineage.add_argument("record_id")
    backfill = commands.add_parser("backfill", help="reconstruct historical research evidence")
    backfill_commands = backfill.add_subparsers(dest="backfill_command", required=True)
    codex = backfill_commands.add_parser(
        "codex-sessions",
        help="extract a local, reviewable evidence bundle from Codex sessions",
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
    backfill_validate = backfill_commands.add_parser(
        "validate",
        help="validate evidence, source-session, Git, and artifact identities",
    )
    backfill_validate.add_argument("--bundle", help="bundle directory")
    backfill_validate.add_argument(
        "--skip-source-digests",
        action="store_true",
        help="do not re-hash original Codex session files",
    )
    backfill_validate.add_argument(
        "--skip-artifact-digests",
        action="store_true",
        help="do not re-hash local run and dataset artifacts",
    )
    backfill_plan = backfill_commands.add_parser(
        "upload-plan",
        help="package accepted historical artifacts and write a deterministic GCS plan",
    )
    backfill_plan.add_argument("--bundle", help="validated backfill bundle directory")
    backfill_plan.add_argument("--output", help="upload work directory")
    backfill_upload = backfill_commands.add_parser(
        "upload",
        help="execute a historical plan with create-only GCS writes",
    )
    backfill_upload.add_argument("--plan", help="upload plan JSON")
    backfill_verify = backfill_commands.add_parser(
        "upload-verify",
        help="verify every planned historical object in GCS",
    )
    backfill_verify.add_argument("--plan", help="upload plan JSON")
    storage = commands.add_parser("storage")
    storage_commands = storage.add_subparsers(dest="storage_command", required=True)
    storage_commands.add_parser("probe", help="create and retain a unique readback probe")
    return parser


def _emit(result: dict[str, Any], *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(result, indent=2, sort_keys=True))
        return
    if "id" in result:
        print(f"{result.get('kind', 'record')} {result['id']}: {result.get('status', 'created')}")
    elif "run_id" in result:
        print(f"run {result['run_id']}: {result['status']}")
    elif result.get("kind") == "historical_upload_plan":
        summary = result["summary"]
        print(
            f"planned {result['import_id']}: {summary['logical_files']} logical files, "
            f"{summary['gcs_objects']} GCS objects, {summary['artifact_bytes']} upload bytes"
        )
    elif result.get("kind") == "historical_upload_verification":
        print(
            f"valid {result['import_id']}: {result['objects']} GCS objects, "
            f"{result['logical_files']} logical files"
        )
    elif result.get("valid"):
        if "records" in result:
            print(f"valid: {result['records']} records")
        else:
            print(
                f"valid: {result.get('sessions', 0)} sessions, "
                f"{result.get('evidence', 0)} evidence events, "
                f"{result.get('artifacts', 0)} artifacts, "
                f"{result.get('drafts', 0)} drafts"
            )
    else:
        print(json.dumps(result, indent=2, sort_keys=True))


def _body(args: argparse.Namespace) -> str:
    if args.body_file:
        return Path(args.body_file).read_text(encoding="utf-8")
    return args.body


def _service(args: argparse.Namespace) -> ModelEvolution:
    return ModelEvolution(
        load_project(args.root),
        actor=args.actor,
        commit=not args.no_commit,
    )


def _run(args: argparse.Namespace) -> dict[str, Any]:
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
    if args.command == "hypothesis":
        return service.create_hypothesis(
            args.slug,
            args.title,
            _body(args),
            references=args.reference,
        )
    if args.command == "experiment":
        return service.create_experiment(
            args.slug,
            hypothesis_ids=args.hypothesis,
            config_path=args.config,
            objective=args.objective,
        )
    if args.command == "decision":
        return service.create_decision(
            args.slug,
            title=args.title,
            observations=args.observation,
            inference=args.inference,
            confidence=args.confidence,
            next_action=args.next_action,
            references=args.reference,
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
                experiment_id=args.experiment,
                dataset_id=args.dataset,
                config_path=args.config,
                adapter=args.adapter,
                parent_module_ids=args.parent_module,
            )
        if args.no_commit:
            raise ValueError("run execution requires committed lifecycle records")
        from .adapters import load_adapter

        adapter = load_adapter(service.project.adapter)
        return adapter.execute_run(service, args.run_id, epochs_this_run=args.epochs_this_run)
    if args.command == "evaluation":
        report_path = Path(args.report)
        report = json.loads(report_path.read_text(encoding="utf-8"))
        evaluation_id = new_id(f"{args.run}-{args.split}")
        artifact = upload_file(
            service.store,
            report_path,
            f"evaluations/{evaluation_id}/report.json",
        )
        return service.create_evaluation(
            run_id=args.run,
            dataset_id=args.dataset,
            metrics=report,
            artifact=artifact,
            split=args.split,
            evaluation_id=evaluation_id,
        )
    if args.command == "module":
        if args.module_command == "publish":
            return service.create_module(
                slug=args.slug,
                module_name=args.name,
                source_run=args.run,
                source_weights=args.weights,
                contract=load_yaml(args.contract),
            )
        return service.promote_module(
            args.module_id,
            evaluation_id=args.evaluation,
            rationale=args.rationale,
            approval_context=args.approval_context,
        )
    if args.command == "status":
        return service.status()
    if args.command == "backfill":
        if args.backfill_command == "codex-sessions":
            if args.active_window_seconds < 0:
                raise ValueError("--active-window-seconds cannot be negative")
            return extract_codex_sessions(
                service.project,
                sessions_root=args.sessions_root,
                output=args.output,
                active_window_seconds=args.active_window_seconds,
            )
        if args.backfill_command == "validate":
            return validate_backfill_bundle(
                service.project,
                bundle=args.bundle,
                verify_sources=not args.skip_source_digests,
                verify_artifacts=not args.skip_artifact_digests,
            )
        if args.backfill_command == "upload-plan":
            plan = build_historical_upload_plan(
                service.project, bundle=args.bundle, output=args.output
            )
            return {
                "kind": plan["kind"],
                "import_id": plan["import_id"],
                "summary": plan["summary"],
            }
        if args.backfill_command == "upload":
            return upload_historical_plan(service.project, plan=args.plan)
        return verify_historical_upload(service.project, plan=args.plan)
    if args.command == "storage":
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
