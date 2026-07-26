from __future__ import annotations

from dataclasses import asdict, replace
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import subprocess
import sys
import tomllib
from typing import Any

import torch
from torch.torch_version import TorchVersion

from latent_arborist.training.cache import prepare_metric_cache, validate_cache
from latent_arborist.training.config import MetricConfig, load_metric_config
from latent_arborist.training.engine import evaluate_checkpoint, train_metric
from latent_arborist.training.tensor import vocabulary_checksum

from ..gitops import require_clean_source, require_committed_file
from ..records import load_record, record_path
from ..service import ModelEvolution, now
from ..storage import download_tree, upload_file, upload_tree

ADAPTER_NAME = "latent-arborist.metric"
MODULE_NAME = "metric_encoder"
ARCHITECTURE = "latent_arborist.training.MomentumMetricModel"
CONTRACT_VERSION = 1


class LatentArboristAdapter:
    name = "latent-arborist"

    def generate_dataset(
        self,
        service: ModelEvolution,
        *,
        slug: str,
        config_path: str | Path,
    ) -> dict[str, Any]:
        return generate_and_register_dataset(service, slug=slug, config_path=config_path)

    def execute_run(
        self,
        service: ModelEvolution,
        run_id: str,
        *,
        epochs_this_run: int | None = None,
    ) -> dict[str, Any]:
        return execute_metric_run(service, run_id, epochs_this_run=epochs_this_run)

    def inspect_artifact(
        self,
        path: str | Path,
    ) -> dict[str, Any] | None:
        artifact_path = Path(path)
        if artifact_path.suffix == ".pt":
            return inspect_checkpoint_metadata(artifact_path)
        if artifact_path.name.startswith("events.out.tfevents."):
            return inspect_tensorboard_metadata(artifact_path)
        return None


_CHECKPOINT_METADATA_KEYS = {
    "atomic_preflight",
    "baseline_metrics",
    "batch_size",
    "best_loss",
    "best_relative_progress",
    "cache_identity",
    "checkpoint_selection",
    "config",
    "config_sha256",
    "cuda",
    "dataset",
    "dataset_manifest_sha256",
    "dataset_sha256",
    "distilled_text_checkpoint",
    "distilled_text_checkpoint_sha256",
    "entropy_weight",
    "epoch",
    "frozen_modules",
    "global_step",
    "last_metrics",
    "latent_dim",
    "learning_rate",
    "loss",
    "max_tape_length",
    "metadata",
    "metric_checkpoint",
    "metric_checkpoint_sha256",
    "metric_sha256",
    "metrics",
    "next_epoch",
    "num_workers",
    "objective",
    "oracle_cache_dir",
    "oracle_delta_scale",
    "oracle_record_limit",
    "oracle_train_tape",
    "oracle_validate_on_train",
    "oracle_weight_decay",
    "overfit",
    "prompt_space_checkpoint",
    "prompt_space_checkpoint_sha256",
    "prompt_space_metrics",
    "stage",
    "stale_epochs",
    "student_config",
    "supervised_checkpoint",
    "supervised_sha256",
    "tape_checkpoint",
    "tape_checkpoint_sha256",
    "tape_metrics",
    "tape_sha256",
    "task_embedding_contract",
    "target_iterations",
    "teacher_dim",
    "teacher_model",
    "temperature",
    "termination",
    "text_encoder_backend",
    "text_encoder_config",
    "text_lr",
    "train_pairs",
    "trained_modules",
    "tree_lr",
    "val_pairs",
    "vocabulary_sha256",
    "warm_start_checkpoint",
    "warm_start_sha256",
    "source_checkpoint",
    "source_checkpoint_sha256",
    "source_revision",
}


def _safe_checkpoint_value(value: Any, *, depth: int = 0) -> Any:
    if depth > 8:
        return "<maximum depth>"
    if isinstance(value, dict):
        return {
            str(key): _safe_checkpoint_value(item, depth=depth + 1)
            for key, item in value.items()
            if str(key) in _CHECKPOINT_METADATA_KEYS or depth > 0
            if not isinstance(item, torch.Tensor)
        }
    if isinstance(value, (list, tuple)):
        if len(value) > 100:
            return {"type": type(value).__name__, "length": len(value)}
        return [_safe_checkpoint_value(item, depth=depth + 1) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return {"type": type(value).__name__}


def inspect_checkpoint_metadata(path: str | Path) -> dict[str, Any] | None:
    checkpoint_path = Path(path)
    if checkpoint_path.suffix != ".pt":
        return None
    try:
        with torch.serialization.safe_globals([TorchVersion]):
            checkpoint = torch.load(
                checkpoint_path,
                map_location="cpu",
                weights_only=True,
            )
    except Exception as error:
        return {
            "inspection": "unavailable",
            "error": f"{type(error).__name__}: {error}",
        }
    if not isinstance(checkpoint, dict):
        return {
            "inspection": "weights_only",
            "checkpoint_type": type(checkpoint).__name__,
        }
    return {
        "inspection": "weights_only",
        "metadata": _safe_checkpoint_value(checkpoint),
    }


def inspect_tensorboard_metadata(path: str | Path) -> dict[str, Any]:
    from tensorboard.backend.event_processing.event_accumulator import EventAccumulator

    event_path = Path(path)
    try:
        accumulator = EventAccumulator(
            str(event_path),
            size_guidance={"scalars": 0},
        )
        accumulator.Reload()
    except Exception as error:
        return {
            "inspection": "unavailable",
            "error": f"{type(error).__name__}: {error}",
        }
    scalars: dict[str, dict[str, Any]] = {}
    for tag in accumulator.Tags().get("scalars", []):
        values = accumulator.Scalars(tag)
        if not values:
            continue
        last = values[-1]
        scalars[str(tag)] = {
            "points": len(values),
            "step": int(last.step),
            "value": float(last.value),
            "wall_time": datetime.fromtimestamp(
                float(last.wall_time),
                tz=timezone.utc,
            ).isoformat(timespec="seconds"),
        }
    return {
        "inspection": "tensorboard_scalars",
        "scalars": scalars,
    }


def generate_and_register_dataset(
    service: ModelEvolution,
    *,
    slug: str,
    config_path: str | Path,
) -> dict[str, Any]:
    require_clean_source(service.project.root)
    config_reference = Path(config_path)
    if not config_reference.is_absolute():
        config_reference = service.project.root / config_reference
    with config_reference.open("rb") as handle:
        raw = tomllib.load(handle)
    staging = service.project.work_dir / "generated" / new_id(slug)
    subprocess.run(
        [
            sys.executable,
            "-m",
            "latent_arborist.data",
            "generate",
            "--config",
            str(config_reference),
            "--output",
            str(staging),
        ],
        cwd=service.project.root,
        check=True,
    )
    return service.register_dataset(
        slug,
        source=staging,
        generator="python -m latent_arborist.data generate",
        generator_config=config_reference,
        seed=int(raw["master_seed"]),
    )


def _metric_contract(config: MetricConfig) -> dict[str, Any]:
    return {
        "architecture": ARCHITECTURE,
        "version": CONTRACT_VERSION,
        "model": asdict(config.model),
        "vocabulary_sha256": vocabulary_checksum(),
    }


def _download_dataset(service: ModelEvolution, dataset: dict[str, Any]) -> Path:
    destination = service.project.work_dir / "cache" / "datasets" / str(dataset["id"])
    marker = destination / ".model-evolution-index.json"
    if marker.exists():
        index = json.loads(marker.read_text(encoding="utf-8"))
        if index["tree_sha256"] != dataset["artifact"]["tree_sha256"]:
            raise ValueError(f"cached dataset checksum mismatch: {dataset['id']}")
    else:
        download_tree(service.store, str(dataset["artifact"]["prefix"]), destination)
    return destination


def _prepare_cache(service: ModelEvolution, dataset_path: Path, dataset_id: str, shard_size: int) -> Path:
    cache = service.project.work_dir / "cache" / "metric" / dataset_id
    if (cache / "cache.json").exists():
        validate_cache(cache, dataset_path)
    else:
        prepare_metric_cache(dataset_path, cache, shard_size=shard_size)
    return cache


def _inherited_checkpoint(
    service: ModelEvolution,
    run: dict[str, Any],
    config: MetricConfig,
) -> Path | None:
    parents = run["initialization"]["parents"]
    if not parents:
        return None
    if len(parents) != 1 or parents[0]["role"] != MODULE_NAME:
        raise ValueError("latent-arborist metric runs accept one metric_encoder parent")
    module = load_record(service.project, "module", str(parents[0]["module_id"]))
    expected = _metric_contract(config)
    if module["contract"] != expected:
        raise ValueError(f"incompatible inherited module contract: {module['id']}")
    artifact = module["artifact"]
    payload = service.store.read_bytes(str(artifact["path"]))
    if sha256(payload).hexdigest() != artifact["sha256"]:
        raise ValueError(f"inherited module checksum mismatch: {module['id']}")
    destination = service.project.work_dir / "cache" / "modules" / str(module["id"]) / "weights.pt"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(payload)
    return destination


def execute_metric_run(
    service: ModelEvolution,
    run_id: str,
    *,
    epochs_this_run: int | None = None,
) -> dict[str, Any]:
    require_clean_source(service.project.root)
    run = load_record(service.project, "run", run_id)
    if run["adapter"] != ADAPTER_NAME:
        raise ValueError(f"run adapter must be {ADAPTER_NAME}")
    if run["status"] != "planned":
        raise ValueError(f"run must be planned: {run_id}")
    if service.commit:
        require_committed_file(
            service.project.root,
            record_path(service.project, "run", run_id),
        )
    claim_uri = service.claim_run(run_id)
    service.update_run(run, status="running", claim_uri=claim_uri, started_at=now())
    run = load_record(service.project, "run", run_id)
    run_dir = service.project.root / "runs" / "model-evolution" / run_id
    run_artifact: dict[str, Any] | None = None
    try:
        dataset = load_record(service.project, "dataset", str(run["dataset_id"]))
        dataset_path = _download_dataset(service, dataset)
        config_path = service.project.root / str(run["config"]["path"])
        source_config = load_metric_config(config_path)
        cache = _prepare_cache(
            service,
            dataset_path,
            str(dataset["id"]),
            source_config.training.shard_size,
        )
        config = replace(source_config, dataset=dataset_path, cache=cache, run_dir=run_dir)
        inherited = _inherited_checkpoint(service, run, config)
        training_result = train_metric(
            config,
            initialize_from=inherited,
            epochs_this_run=epochs_this_run,
        )
        evaluation_result = evaluate_checkpoint(run_dir / "best.pt", split="test")
        run_artifact = upload_tree(service.store, run_dir, f"runs/{run_id}")

        result_artifact = upload_file(
            service.store,
            run_dir / "test-report.json",
            f"runs/{run_id}/primary-result.json",
        )
        module = service.create_module(
            slug=f"{MODULE_NAME}-{run_id}",
            module_name=MODULE_NAME,
            source_run=run_id,
            source_weights=run_dir / "best.pt",
            contract=_metric_contract(config),
        )
        completed = load_record(service.project, "run", run_id)
        service.update_run(
            completed,
            status="completed",
            body=(
                f"# {run_id}\n\n"
                "## Execution plan\n\nExecuted the pinned study design.\n\n"
                "## Execution notes\n\nTraining and the primary test evaluation completed "
                "successfully.\n\n"
                "## Observations\n\nSee the primary structured results and immutable "
                "report.\n\n"
                "## Anomalies\n\nNone recorded."
            ),
            training_result=training_result,
            artifacts={
                "run": {**run_artifact, "prefix": f"runs/{run_id}"},
                "primary_result": result_artifact,
            },
            results={
                "primary": {
                    "dataset_id": str(dataset["id"]),
                    "split": "test",
                    "evaluator": {
                        "adapter": ADAPTER_NAME,
                        "source_revision": run["source_revision"],
                    },
                    "metrics": evaluation_result,
                    "artifact": result_artifact,
                },
                "training": training_result,
            },
            module_ids=[module["id"]],
            ended_at=now(),
        )
        return {
            "run_id": run_id,
            "status": "completed",
            "module_id": module["id"],
        }
    except KeyboardInterrupt as error:
        interrupted = load_record(service.project, "run", run_id)
        artifact, artifact_error = _capture_failed_artifacts(
            service,
            run_id,
            run_dir,
            run_artifact,
        )
        service.update_run(
            interrupted,
            status="interrupted",
            error={"type": type(error).__name__, "message": "run interrupted"},
            artifact=artifact,
            artifact_error=artifact_error,
            ended_at=now(),
        )
        raise
    except Exception as error:
        failed = load_record(service.project, "run", run_id)
        artifact, artifact_error = _capture_failed_artifacts(
            service,
            run_id,
            run_dir,
            run_artifact,
        )
        service.update_run(
            failed,
            status="failed",
            error={"type": type(error).__name__, "message": str(error)},
            artifact=artifact,
            artifact_error=artifact_error,
            ended_at=now(),
        )
        raise


def _capture_failed_artifacts(
    service: ModelEvolution,
    run_id: str,
    run_dir: Path,
    existing: dict[str, Any] | None,
) -> tuple[dict[str, Any] | None, str | None]:
    if existing is not None:
        return {**existing, "prefix": f"runs/{run_id}"}, None
    if not run_dir.is_dir() or not any(path.is_file() for path in run_dir.rglob("*")):
        return None, None
    try:
        artifact = upload_tree(service.store, run_dir, f"runs/{run_id}")
        return {**artifact, "prefix": f"runs/{run_id}", "incomplete": True}, None
    except Exception as error:
        return (
            {"prefix": f"runs/{run_id}", "incomplete": True},
            f"{type(error).__name__}: {error}",
        )
