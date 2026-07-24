from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import subprocess
import time
from typing import Any, Iterable

from .config import ProjectConfig
from .gitops import revision
from .yamlio import load_yaml, write_yaml


BUNDLE_SCHEMA_VERSION = 1
ALLOWED_EVIDENCE_KINDS = {
    "user_message",
    "agent_message",
    "tool_call",
    "tool_output",
}
_ARTIFACT_NAMES = {"best.pt", "latest.pt", "student.pt"}
_PATH_PATTERN = re.compile(
    r"(?<![A-Za-z0-9_.-])"
    r"((?:runs|data/generated|configs)/[A-Za-z0-9_.@%+=:,/~*-]+)"
)
_LEAD_PATTERNS = {
    "hypothesis": re.compile(
        r"\b(hypothes|expect|predict|assum|wonder|idea|goal|aim|should|could|might)\w*",
        re.IGNORECASE,
    ),
    "decision": re.compile(
        r"\b(agreed|decid|choose|chosen|switch|instead|keep|drop|remove|change|let'?s)\w*",
        re.IGNORECASE,
    ),
    "result": re.compile(
        r"\b(loss|accuracy|reward|metric|evaluation|overfit|checkpoint|passed|failed|"
        r"improv|regress|collapse|epoch)\w*",
        re.IGNORECASE,
    ),
}
_PRIVATE_KEY_PATTERN = re.compile(
    r"-----BEGIN [^-]*PRIVATE KEY-----.*?-----END [^-]*PRIVATE KEY-----",
    re.DOTALL,
)
_JSON_SECRET_PATTERN = re.compile(
    r'(?i)(["\']?(?:private_key|client_secret|access_token|refresh_token|'
    r'api_key|password)["\']?\s*[:=]\s*)(["\'])(.*?)(\2)',
    re.DOTALL,
)
_BEARER_PATTERN = re.compile(r"(?i)\b(Bearer\s+)[A-Za-z0-9._~+/\-=]+")
_TOKEN_PATTERN = re.compile(
    r"\b(?:sk-[A-Za-z0-9_-]{16,}|gh[opsu]_[A-Za-z0-9]{20,}|"
    r"AIza[A-Za-z0-9_-]{20,})\b"
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _digest_bytes(payload: bytes) -> str:
    return sha256(payload).hexdigest()


def _digest_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _digest_file_prefix(path: Path, size: int) -> str:
    digest = sha256()
    remaining = size
    with path.open("rb") as source:
        while remaining:
            chunk = source.read(min(1024 * 1024, remaining))
            if not chunk:
                raise ValueError(f"source file is shorter than its captured snapshot: {path}")
            digest.update(chunk)
            remaining -= len(chunk)
    return digest.hexdigest()


def _json_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def redact_secrets(text: str) -> tuple[str, int]:
    count = 0

    def replace_private_key(match: re.Match[str]) -> str:
        nonlocal count
        count += 1
        return "[REDACTED PRIVATE KEY]"

    def replace_json_secret(match: re.Match[str]) -> str:
        nonlocal count
        count += 1
        return f"{match.group(1)}{match.group(2)}[REDACTED]{match.group(4)}"

    def replace_bearer(match: re.Match[str]) -> str:
        nonlocal count
        count += 1
        return f"{match.group(1)}[REDACTED]"

    def replace_token(_match: re.Match[str]) -> str:
        nonlocal count
        count += 1
        return "[REDACTED TOKEN]"

    text = _PRIVATE_KEY_PATTERN.sub(replace_private_key, text)
    text = _JSON_SECRET_PATTERN.sub(replace_json_secret, text)
    text = _BEARER_PATTERN.sub(replace_bearer, text)
    text = _TOKEN_PATTERN.sub(replace_token, text)
    return text, count


def _truncate(text: str, limit: int) -> tuple[str, bool]:
    if len(text) <= limit:
        return text, False
    omitted = len(text) - limit
    return f"{text[:limit]}\n[TRUNCATED {omitted} CHARACTERS]", True


def _write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> str:
    text = "".join(
        json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
        for row in rows
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)
    return _digest_bytes(text.encode("utf-8"))


def _read_jsonl(path: Path) -> Iterable[tuple[int, dict[str, Any]]]:
    with path.open(encoding="utf-8") as source:
        for line_number, line in enumerate(source, 1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"{path}:{line_number} must contain a JSON object")
            yield line_number, value


def _git_history(root: Path) -> list[dict[str, str]]:
    result = subprocess.run(
        ["git", "log", "--all", "--format=%H%x00%cI%x00%D%x00%s"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    commits: list[dict[str, str]] = []
    for line in result.stdout.splitlines():
        commit_hash, committed_at, refs, subject = line.split("\0", 3)
        commits.append(
            {
                "commit": commit_hash,
                "committed_at": committed_at,
                "refs": refs,
                "subject": subject,
            }
        )
    return commits


def _session_meta(rows: list[tuple[int, dict[str, Any]]]) -> dict[str, Any] | None:
    for _line_number, row in rows:
        if row.get("type") == "session_meta" and isinstance(row.get("payload"), dict):
            return row["payload"]
    return None


def _session_matches_repository(path: Path, repository: Path) -> bool:
    try:
        with path.open(encoding="utf-8", errors="replace") as source:
            for line in source:
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if row.get("type") != "session_meta":
                    continue
                payload = row.get("payload")
                if not isinstance(payload, dict):
                    return False
                cwd = payload.get("cwd")
                return (
                    isinstance(cwd, str)
                    and bool(cwd)
                    and Path(cwd).resolve() == repository.resolve()
                )
    except OSError:
        return False
    return False


def _evidence_row(
    *,
    session_id: str,
    session_sha256: str,
    source_path: str,
    line_number: int,
    timestamp: str | None,
    kind: str,
    content: str,
    max_chars: int,
    attributes: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], int]:
    redacted, redactions = redact_secrets(content)
    redacted, truncated = _truncate(redacted, max_chars)
    identity = f"{session_id}\0{line_number}\0{kind}".encode()
    row: dict[str, Any] = {
        "evidence_id": f"ev-{sha256(identity).hexdigest()[:24]}",
        "kind": kind,
        "session_id": session_id,
        "timestamp": timestamp,
        "source": {
            "path": source_path,
            "line": line_number,
            "session_sha256": session_sha256,
        },
        "content_sha256": _digest_bytes(content.encode("utf-8", errors="replace")),
        "content": redacted,
        "redactions": redactions,
        "truncated": truncated,
    }
    if attributes:
        row["attributes"] = attributes
    return row, redactions


def _extract_session(
    path: Path,
    *,
    sessions_root: Path,
    repository: Path,
    max_message_chars: int,
    max_tool_chars: int,
    source_size: int | None = None,
) -> tuple[dict[str, Any] | None, list[dict[str, Any]], int]:
    stat_before = path.stat()
    if source_size is None:
        payload = path.read_bytes()
    else:
        with path.open("rb") as source:
            payload = source.read(source_size)
        if len(payload) != source_size:
            raise ValueError(f"source session is shorter than its captured snapshot: {path}")
    stat_after = path.stat()
    if source_size is None and (
        stat_before.st_size != stat_after.st_size
        or stat_before.st_mtime_ns != stat_after.st_mtime_ns
    ):
        raise RuntimeError(f"session changed while being read: {path}")
    session_sha256 = _digest_bytes(payload)
    rows: list[tuple[int, dict[str, Any]]] = []
    for line_number, line in enumerate(payload.splitlines(), 1):
        try:
            value = json.loads(line)
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
        if isinstance(value, dict):
            rows.append((line_number, value))
    meta = _session_meta(rows)
    cwd = meta.get("cwd") if meta else None
    if not isinstance(cwd, str) or not cwd or Path(cwd).resolve() != repository.resolve():
        return None, [], 0

    session_id = str(meta.get("id") or path.stem)
    relative_path = str(path.relative_to(sessions_root))
    evidence: list[dict[str, Any]] = []
    redaction_count = 0
    kinds: Counter[str] = Counter()
    timestamps: list[str] = []
    for line_number, row in rows:
        timestamp = row.get("timestamp")
        if isinstance(timestamp, str):
            timestamps.append(timestamp)
        row_type = row.get("type")
        item = row.get("payload")
        if not isinstance(item, dict):
            continue
        evidence_kind: str | None = None
        content: str | None = None
        attributes: dict[str, Any] = {}
        if row_type == "event_msg" and item.get("type") in {
            "user_message",
            "agent_message",
        }:
            evidence_kind = str(item["type"])
            content = _json_text(item.get("message", ""))
            attributes["speaker"] = "user" if evidence_kind == "user_message" else "agent"
            limit = max_message_chars
        elif row_type == "response_item" and item.get("type") in {
            "function_call",
            "custom_tool_call",
        }:
            evidence_kind = "tool_call"
            content = _json_text(item.get("arguments", item.get("input", "")))
            attributes.update(
                {
                    "tool": str(item.get("name", "unknown")),
                    "call_id": str(item.get("call_id", "")),
                }
            )
            limit = max_tool_chars
        elif row_type == "response_item" and item.get("type") in {
            "function_call_output",
            "custom_tool_call_output",
        }:
            evidence_kind = "tool_output"
            content = _json_text(item.get("output", ""))
            attributes["call_id"] = str(item.get("call_id", ""))
            limit = max_tool_chars
        else:
            continue
        evidence_row, redactions = _evidence_row(
            session_id=session_id,
            session_sha256=session_sha256,
            source_path=relative_path,
            line_number=line_number,
            timestamp=timestamp if isinstance(timestamp, str) else None,
            kind=evidence_kind,
            content=content,
            max_chars=limit,
            attributes=attributes,
        )
        evidence.append(evidence_row)
        redaction_count += redactions
        kinds[evidence_kind] += 1

    git = meta.get("git") if isinstance(meta.get("git"), dict) else {}
    session = {
        "session_id": session_id,
        "started_at": str(meta.get("timestamp", timestamps[0] if timestamps else "")),
        "last_event_at": timestamps[-1] if timestamps else str(meta.get("timestamp", "")),
        "source_path": relative_path,
        "source_sha256": session_sha256,
        "source_size": len(payload),
        "cli_version": str(meta.get("cli_version", "")),
        "git": {
            "commit": git.get("commit_hash"),
            "branch": git.get("branch"),
            "repository_url": git.get("repository_url"),
        },
        "evidence_counts": dict(sorted(kinds.items())),
        "redactions": redaction_count,
    }
    return session, evidence, redaction_count


def _safe_json_metadata(path: Path) -> dict[str, Any] | None:
    if path.stat().st_size > 2 * 1024 * 1024:
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(value, dict):
        return {"value_type": type(value).__name__}
    redacted, _ = redact_secrets(json.dumps(value, ensure_ascii=False, sort_keys=True))
    return json.loads(redacted)


def _safe_jsonl_metadata(path: Path) -> dict[str, Any]:
    records = 0
    last: dict[str, Any] | None = None
    try:
        with path.open(encoding="utf-8") as source:
            for line in source:
                try:
                    value = json.loads(line)
                except json.JSONDecodeError:
                    continue
                records += 1
                if isinstance(value, dict):
                    last = value
    except UnicodeDecodeError:
        return {"records": records}
    result: dict[str, Any] = {"records": records}
    if last is not None:
        redacted, _ = redact_secrets(json.dumps(last, ensure_ascii=False, sort_keys=True))
        result["last_record"] = json.loads(redacted)
    return result


def _artifact_paths(project: ProjectConfig) -> Iterable[tuple[str, Path]]:
    runs = project.root / "runs"
    if runs.exists():
        for path in runs.rglob("*"):
            if not path.is_file():
                continue
            if (
                path.name in _ARTIFACT_NAMES
                or path.suffix == ".pt"
                or path.suffix in {".json", ".jsonl"}
                or path.name.endswith(".pt.meta")
                or path.name.startswith("events.out.tfevents.")
            ):
                yield "run_artifact", path
    generated = project.root / "data" / "generated"
    if generated.exists():
        for path in generated.rglob("dataset.json"):
            if path.is_file():
                yield "dataset_manifest", path


def _inventory_artifacts(project: ProjectConfig) -> list[dict[str, Any]]:
    inspector = None
    try:
        from .adapters import load_adapter

        adapter = load_adapter(project.adapter)
        inspector = getattr(adapter, "inspect_historical_artifact", None)
    except (ImportError, ValueError):
        inspector = None
    artifacts: list[dict[str, Any]] = []
    for kind, path in sorted(_artifact_paths(project), key=lambda item: str(item[1])):
        stat = path.stat()
        record: dict[str, Any] = {
            "kind": kind,
            "path": str(path.relative_to(project.root)),
            "size": stat.st_size,
            "modified_at": datetime.fromtimestamp(
                stat.st_mtime, tz=timezone.utc
            ).isoformat(timespec="seconds"),
            "sha256": _digest_file(path),
        }
        if path.suffix == ".json":
            metadata = _safe_json_metadata(path)
            if metadata is not None:
                record["observed_metadata"] = metadata
        elif path.suffix == ".jsonl":
            record["observed_metadata"] = _safe_jsonl_metadata(path)
        elif path.suffix == ".pt" and inspector is not None:
            checkpoint_metadata = inspector(path)
            if checkpoint_metadata is not None:
                record["observed_metadata"] = checkpoint_metadata
        elif path.name.startswith("events.out.tfevents.") and inspector is not None:
            event_metadata = inspector(path)
            if event_metadata is not None:
                record["observed_metadata"] = event_metadata
        artifacts.append(record)
    return artifacts


def _nearest_preceding_commit(
    commits: list[dict[str, str]], timestamp: datetime
) -> dict[str, str] | None:
    candidates: list[tuple[datetime, dict[str, str]]] = []
    for commit in commits:
        committed_at = _parse_timestamp(commit.get("committed_at"))
        if committed_at is not None and committed_at <= timestamp:
            candidates.append((committed_at, commit))
    if not candidates:
        return None
    return max(candidates, key=lambda item: item[0])[1]


def _walk_metadata(value: Any) -> Iterable[tuple[str, Any, dict[str, Any]]]:
    if isinstance(value, dict):
        for key, item in value.items():
            yield str(key), item, value
            yield from _walk_metadata(item)
    elif isinstance(value, list):
        for item in value:
            yield from _walk_metadata(item)


def _metadata_digests(value: Any) -> set[str]:
    return {
        item
        for _key, item, _parent in _walk_metadata(value)
        if isinstance(item, str) and re.fullmatch(r"[0-9a-f]{64}", item)
    }


def _dataset_identity_digests(value: Any) -> set[str]:
    identities: set[str] = set()
    for key, item, _parent in _walk_metadata(value):
        if "dataset" not in key and "manifest" not in key:
            continue
        if isinstance(item, str) and re.fullmatch(r"[0-9a-f]{64}", item):
            identities.add(item)
        else:
            identities.update(_metadata_digests(item))
    return identities


def _checkpoint_dependencies(
    metadata: Any,
    *,
    artifact_by_path: dict[str, dict[str, Any]],
    current_run: str,
) -> list[dict[str, Any]]:
    dependencies: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for key, value, parent in _walk_metadata(metadata):
        if not key.endswith("_checkpoint") or not isinstance(value, str):
            continue
        match = re.fullmatch(r"runs/([^/]+)/.+\.pt", value)
        if not match or match.group(1) == current_run:
            continue
        role = key.removesuffix("_checkpoint")
        identity = (role, value)
        if identity in seen:
            continue
        seen.add(identity)
        recorded_digest = None
        for digest_key in (
            f"{key}_sha256",
            f"{role}_sha256",
        ):
            candidate = parent.get(digest_key)
            if isinstance(candidate, str):
                recorded_digest = candidate
                break
        artifact = artifact_by_path.get(value)
        observed_digest = artifact.get("sha256") if artifact else None
        if recorded_digest and observed_digest and recorded_digest != observed_digest:
            status = "conflict"
        elif artifact is not None:
            status = "observed"
        else:
            status = "unresolved"
        dependencies.append(
            {
                "role": role,
                "module_candidate": f"historical-module:{match.group(1)}",
                "path": value,
                "recorded_sha256": recorded_digest,
                "observed_sha256": observed_digest,
                "status": status,
            }
        )
    return dependencies


def _metric_summaries(metadata: Any) -> dict[str, Any]:
    summaries: dict[str, Any] = {}
    for key, value, _parent in _walk_metadata(metadata):
        if key in {
            "metrics",
            "last_metrics",
            "best_loss",
            "best_reward",
            "best_relative_progress",
        }:
            summaries.setdefault(key, value)
    return summaries


def _terminal_tensorboard_summary(
    artifacts: list[dict[str, Any]],
) -> dict[str, Any] | None:
    selected: dict[str, dict[str, Any]] = {}
    source_paths: list[str] = []
    for artifact in artifacts:
        metadata = artifact.get("observed_metadata")
        if not isinstance(metadata, dict) or metadata.get("inspection") != "tensorboard_scalars":
            continue
        source_paths.append(str(artifact["path"]))
        scalars = metadata.get("scalars")
        if not isinstance(scalars, dict):
            continue
        for tag, point in scalars.items():
            if not isinstance(point, dict):
                continue
            existing = selected.get(str(tag))
            identity = (str(point.get("wall_time", "")), int(point.get("step", -1)))
            existing_identity = (
                (str(existing.get("wall_time", "")), int(existing.get("step", -1)))
                if existing is not None
                else ("", -1)
            )
            if identity > existing_identity:
                selected[str(tag)] = point
    if not selected:
        return None
    return {
        "last_step": max(int(point.get("step", -1)) for point in selected.values()),
        "last_wall_time": max(str(point.get("wall_time", "")) for point in selected.values()),
        "metrics": {
            tag: {
                "step": int(point["step"]),
                "value": float(point["value"]),
            }
            for tag, point in sorted(selected.items())
        },
        "source_paths": sorted(source_paths),
    }


def _draft_candidates(
    project: ProjectConfig,
    *,
    artifacts: list[dict[str, Any]],
    mentions: list[dict[str, Any]],
    commits: list[dict[str, str]],
) -> list[dict[str, Any]]:
    drafts: list[dict[str, Any]] = []
    artifact_by_path = {str(item["path"]): item for item in artifacts}
    dataset_by_digest: dict[str, set[str]] = {}
    for item in artifacts:
        if item["kind"] != "dataset_manifest":
            continue
        dataset_name = Path(str(item["path"])).parent.name
        for digest in {
            str(item["sha256"]),
            *_metadata_digests(item.get("observed_metadata")),
        }:
            dataset_by_digest.setdefault(digest, set()).add(dataset_name)
    run_root = project.root / "runs"
    run_names = (
        sorted(path.name for path in run_root.iterdir() if path.is_dir())
        if run_root.is_dir()
        else []
    )
    for run_name in run_names:
        prefix = f"runs/{run_name}/"
        run_artifacts = [
            item
            for item in artifacts
            if str(item["path"]).startswith(prefix)
        ]
        run_mentions = [
            item
            for item in mentions
            if str(item["path"]) == f"runs/{run_name}"
            or str(item["path"]).startswith(prefix)
        ]
        evidence_ids = sorted(
            {str(item["evidence_id"]) for item in run_mentions}
        )
        modified = [
            timestamp
            for item in run_artifacts
            if (timestamp := _parse_timestamp(item.get("modified_at"))) is not None
        ]
        nearest = _nearest_preceding_commit(commits, min(modified)) if modified else None
        config = artifact_by_path.get(f"{prefix}config.json")
        checkpoints = [
            item for item in run_artifacts if str(item["path"]).endswith(".pt")
        ]
        preferred_checkpoint = next(
            (
                item
                for name in ("best.pt", "student.pt", "latest.pt")
                for item in checkpoints
                if Path(str(item["path"])).name == name
            ),
            checkpoints[0] if checkpoints else None,
        )
        checkpoint_metadata = (
            preferred_checkpoint.get("observed_metadata")
            if preferred_checkpoint is not None
            else None
        )
        dependencies = _checkpoint_dependencies(
            checkpoint_metadata,
            artifact_by_path=artifact_by_path,
            current_run=run_name,
        )
        dataset_candidates: set[str] = set()
        dataset_identities = _dataset_identity_digests(checkpoint_metadata)
        for digest in dataset_identities:
            dataset_candidates.update(dataset_by_digest.get(digest, set()))
        unresolved_dataset_digests = sorted(
            digest for digest in dataset_identities if digest not in dataset_by_digest
        )
        for _key, value, _parent in _walk_metadata(checkpoint_metadata):
            if not isinstance(value, str):
                continue
            match = re.fullmatch(r"data/generated/([^/]+)", value.rstrip("/"))
            if match:
                dataset_candidates.add(match.group(1))
        reports = [
            item
            for item in run_artifacts
            if str(item["path"]).endswith("-report.json")
            or str(item["path"]).endswith("metrics.jsonl")
        ]
        terminal_summary = _terminal_tensorboard_summary(run_artifacts)
        provenance = [
            {
                "kind": "artifact",
                "locator": str(item["path"]),
                "sha256": str(item["sha256"]),
                "claim_type": "observed",
                "confidence": "high",
            }
            for item in run_artifacts
        ]
        provenance.extend(
            {
                "kind": "codex_session",
                "locator": evidence_id,
                "claim_type": "observed",
                "confidence": "medium",
            }
            for evidence_id in evidence_ids
        )
        run_candidate_id = f"historical-run:{run_name}"
        drafts.append(
            {
                "candidate_id": run_candidate_id,
                "kind": "run_candidate",
                "status": "needs_review",
                "name": run_name,
                "observed_status": "has_checkpoint" if checkpoints else "artifacts_only",
                "first_artifact_at": min(item["modified_at"] for item in run_artifacts)
                if run_artifacts
                else None,
                "last_artifact_at": max(item["modified_at"] for item in run_artifacts)
                if run_artifacts
                else None,
                "config": {
                    "status": (
                        "observed"
                        if config
                        else "checkpoint_observed"
                        if checkpoint_metadata
                        else "unresolved"
                    ),
                    "path": config["path"] if config else None,
                    "sha256": config["sha256"] if config else None,
                },
                "dataset": {
                    "status": (
                        "observed"
                        if len(dataset_candidates) == 1
                        and not unresolved_dataset_digests
                        else "ambiguous"
                        if dataset_candidates
                        else "missing_historical_version"
                        if unresolved_dataset_digests
                        else "unresolved"
                    ),
                    "candidates": [
                        f"historical-dataset:{name}"
                        for name in sorted(dataset_candidates)
                    ],
                    "recorded_digests": sorted(dataset_identities),
                    "unresolved_digests": unresolved_dataset_digests,
                },
                "initialization": {
                    "status": (
                        "observed"
                        if dependencies and all(
                            item["status"] == "observed" for item in dependencies
                        )
                        else "conflict"
                        if any(item["status"] == "conflict" for item in dependencies)
                        else "inferred_from_scratch"
                        if checkpoint_metadata
                        else "unresolved"
                    ),
                    "kind": "inherited" if dependencies else "from_scratch",
                    "parents": dependencies,
                },
                "git_revision": {
                    "status": "inferred" if nearest else "unresolved",
                    "candidate": nearest["commit"] if nearest else None,
                    "method": "closest commit preceding first artifact",
                    "confidence": "low" if nearest else None,
                },
                "checkpoint_paths": [item["path"] for item in checkpoints],
                "evaluation_paths": [item["path"] for item in reports],
                "terminal_metrics": (
                    {"status": "observed", **terminal_summary}
                    if terminal_summary is not None
                    else {"status": "unavailable"}
                ),
                "evidence_ids": evidence_ids,
                "provenance": provenance,
            }
        )
        if preferred_checkpoint is not None:
            drafts.append(
                {
                    "candidate_id": f"historical-module:{run_name}",
                    "kind": "module_candidate",
                    "status": "needs_review",
                    "name": run_name,
                    "source_run_candidate": run_candidate_id,
                    "module_name": None,
                    "artifact": {
                        "path": preferred_checkpoint["path"],
                        "sha256": preferred_checkpoint["sha256"],
                    },
                    "contract": {"status": "unresolved"},
                    "provenance": [
                        {
                            "kind": "artifact",
                            "locator": preferred_checkpoint["path"],
                            "sha256": preferred_checkpoint["sha256"],
                            "claim_type": "observed",
                            "confidence": "high",
                        }
                    ],
                }
            )
            metric_summaries = _metric_summaries(checkpoint_metadata)
            if metric_summaries and not reports:
                drafts.append(
                    {
                        "candidate_id": f"historical-evaluation:{run_name}:checkpoint",
                        "kind": "evaluation_candidate",
                        "status": "needs_review",
                        "run_candidate": run_candidate_id,
                        "artifact": {
                            "path": preferred_checkpoint["path"],
                            "sha256": preferred_checkpoint["sha256"],
                        },
                        "observed_metadata": metric_summaries,
                        "provenance": [
                            {
                                "kind": "artifact",
                                "locator": preferred_checkpoint["path"],
                                "sha256": preferred_checkpoint["sha256"],
                                "claim_type": "observed",
                                "confidence": "high",
                            }
                        ],
                    }
                )
        for report in reports:
            drafts.append(
                {
                    "candidate_id": (
                        f"historical-evaluation:{run_name}:"
                        f"{Path(str(report['path'])).name}"
                    ),
                    "kind": "evaluation_candidate",
                    "status": "needs_review",
                    "run_candidate": run_candidate_id,
                    "artifact": {
                        "path": report["path"],
                        "sha256": report["sha256"],
                    },
                    "observed_metadata": report.get("observed_metadata"),
                    "provenance": [
                        {
                            "kind": "run_metrics",
                            "locator": report["path"],
                            "sha256": report["sha256"],
                            "claim_type": "observed",
                            "confidence": "high",
                        }
                    ],
                }
            )
        if terminal_summary is not None:
            terminal_artifacts = [
                artifact_by_path[path]
                for path in terminal_summary["source_paths"]
                if path in artifact_by_path
            ]
            drafts.append(
                {
                    "candidate_id": f"historical-evaluation:{run_name}:terminal-tensorboard",
                    "kind": "evaluation_candidate",
                    "status": "needs_review",
                    "run_candidate": run_candidate_id,
                    "artifact": {
                        "paths": terminal_summary["source_paths"],
                        "sha256": [item["sha256"] for item in terminal_artifacts],
                    },
                    "observed_metadata": terminal_summary,
                    "provenance": [
                        {
                            "kind": "run_metrics",
                            "locator": item["path"],
                            "sha256": item["sha256"],
                            "claim_type": "observed",
                            "confidence": "high",
                        }
                        for item in terminal_artifacts
                    ],
                }
            )

    for artifact in artifacts:
        if artifact["kind"] != "dataset_manifest":
            continue
        dataset_name = Path(str(artifact["path"])).parent.name
        drafts.append(
            {
                "candidate_id": f"historical-dataset:{dataset_name}",
                "kind": "dataset_candidate",
                "status": "needs_review",
                "name": dataset_name,
                "manifest": {
                    "path": artifact["path"],
                    "sha256": artifact["sha256"],
                },
                "observed_metadata": artifact.get("observed_metadata"),
                "generator": {"status": "unresolved"},
                "artifact_store": {"status": "not_uploaded"},
                "provenance": [
                    {
                        "kind": "dataset_manifest",
                        "locator": artifact["path"],
                        "sha256": artifact["sha256"],
                        "claim_type": "observed",
                        "confidence": "high",
                    }
                ],
            }
        )
    drafts.sort(key=lambda value: (value["kind"], value["candidate_id"]))
    return drafts


def _path_mentions(evidence: list[dict[str, Any]], project: ProjectConfig) -> list[dict[str, Any]]:
    mentions: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for row in evidence:
        if row.get("replayed_context"):
            continue
        for match in _PATH_PATTERN.finditer(str(row["content"])):
            value = match.group(1).rstrip(".,:;)]}'\"")
            while value.endswith("*"):
                value = value[:-1]
            key = (str(row["evidence_id"]), value)
            if not value or key in seen:
                continue
            seen.add(key)
            mentions.append(
                {
                    "evidence_id": row["evidence_id"],
                    "session_id": row["session_id"],
                    "path": value,
                    "exists": (project.root / value).exists(),
                }
            )
    return mentions


def _review_leads(evidence: list[dict[str, Any]]) -> list[dict[str, Any]]:
    leads: list[dict[str, Any]] = []
    for row in evidence:
        if row.get("replayed_context"):
            continue
        if row["kind"] not in {"user_message", "agent_message", "tool_output"}:
            continue
        text = str(row["content"])
        for category, pattern in _LEAD_PATTERNS.items():
            if not pattern.search(text):
                continue
            excerpt = re.sub(r"\s+", " ", text).strip()
            excerpt, _ = _truncate(excerpt, 500)
            leads.append(
                {
                    "kind": f"{category}_lead",
                    "status": "unreviewed",
                    "confidence": "lead_only",
                    "evidence_id": row["evidence_id"],
                    "session_id": row["session_id"],
                    "timestamp": row.get("timestamp"),
                    "speaker": row.get("attributes", {}).get("speaker", row["kind"]),
                    "excerpt": excerpt,
                }
            )
    return leads


def _parse_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _mark_replayed_context(
    sessions: list[dict[str, Any]],
    evidence: list[dict[str, Any]],
    *,
    replay_window_seconds: float = 5.0,
    minimum_replay_events: int = 10,
) -> int:
    starts = {
        str(session["session_id"]): _parse_timestamp(session.get("started_at"))
        for session in sessions
    }
    early_by_session: dict[str, list[dict[str, Any]]] = {}
    for row in evidence:
        start = starts.get(str(row["session_id"]))
        timestamp = _parse_timestamp(row.get("timestamp"))
        if start is None or timestamp is None:
            continue
        elapsed = (timestamp - start).total_seconds()
        if 0 <= elapsed <= replay_window_seconds:
            early_by_session.setdefault(str(row["session_id"]), []).append(row)

    prior_by_content: dict[tuple[str, str], str] = {}
    replayed = 0
    early_ids = {
        session_id: {str(row["evidence_id"]) for row in rows}
        for session_id, rows in early_by_session.items()
        if len(rows) >= minimum_replay_events
    }
    for row in evidence:
        key = (str(row["kind"]), str(row["content_sha256"]))
        is_replayed = str(row["evidence_id"]) in early_ids.get(
            str(row["session_id"]), set()
        )
        if is_replayed:
            row["replayed_context"] = True
            if key in prior_by_content:
                row["duplicate_of"] = prior_by_content[key]
            replayed += 1
        else:
            prior_by_content.setdefault(key, str(row["evidence_id"]))
    for session in sessions:
        session["replayed_context_events"] = sum(
            1
            for row in evidence
            if row["session_id"] == session["session_id"] and row.get("replayed_context")
        )
    return replayed


def _review_markdown(
    *,
    sessions: list[dict[str, Any]],
    evidence: list[dict[str, Any]],
    artifacts: list[dict[str, Any]],
    mentions: list[dict[str, Any]],
    leads: list[dict[str, Any]],
    drafts: list[dict[str, Any]],
    excluded_active: list[str],
    git_commits: set[str],
) -> str:
    evidence_counts = Counter(str(row["kind"]) for row in evidence)
    replayed = sum(1 for row in evidence if row.get("replayed_context"))
    artifact_counts = Counter(str(row["kind"]) for row in artifacts)
    lead_counts = Counter(str(row["kind"]) for row in leads)
    draft_counts = Counter(str(row["kind"]) for row in drafts)
    missing_commits = [
        session for session in sessions if session["git"]["commit"] not in git_commits
    ]
    unresolved_mentions = [mention for mention in mentions if not mention["exists"]]
    lines = [
        "# Codex history backfill review",
        "",
        "This is a local, generated review workspace. It is evidence for drafting "
        "canonical records, not canonical research history.",
        "",
        "## Extraction policy",
        "",
        "- Exact repository working-directory matches only.",
        "- Visible user messages, visible agent messages, and tool calls/results only.",
        "- Reasoning, system/developer instructions, and compaction payloads are excluded.",
        "- Secret-shaped values are redacted and large payloads are truncated.",
        "- Raw transcripts remain in the local Codex session store.",
        "",
        "## Inventory",
        "",
        f"- Sessions: {len(sessions)}",
        f"- Evidence events: {len(evidence)}",
        f"- Primary evidence events: {len(evidence) - replayed}",
        f"- Replayed/forked context events: {replayed}",
        f"- User messages: {evidence_counts['user_message']}",
        f"- Agent messages: {evidence_counts['agent_message']}",
        f"- Tool calls/results: {evidence_counts['tool_call'] + evidence_counts['tool_output']}",
        f"- Run artifacts: {artifact_counts['run_artifact']}",
        f"- Dataset manifests: {artifact_counts['dataset_manifest']}",
        f"- Run candidates: {draft_counts['run_candidate']}",
        f"- Module candidates: {draft_counts['module_candidate']}",
        f"- Evaluation candidates: {draft_counts['evaluation_candidate']}",
        f"- Dataset candidates: {draft_counts['dataset_candidate']}",
        f"- Path mentions: {len(mentions)} ({len(unresolved_mentions)} currently unresolved)",
        f"- Excluded active sessions: {len(excluded_active)}",
        "",
        "## Review leads",
        "",
        f"- Hypothesis leads: {lead_counts['hypothesis_lead']}",
        f"- Decision leads: {lead_counts['decision_lead']}",
        f"- Result leads: {lead_counts['result_lead']}",
        "",
        "Leads are keyword-selected evidence, not accepted claims. Review "
        "`review-leads.jsonl` and reconcile each retained claim against artifacts and Git.",
        "Mechanically grounded draft records are in `drafts.jsonl`; unresolved "
        "dataset, initialization, contract, and Git fields remain explicit.",
        "",
        "## Session epochs",
        "",
        "| Started | Session | Commit | Evidence |",
        "| --- | --- | --- | ---: |",
    ]
    for session in sessions:
        counts = sum(int(value) for value in session["evidence_counts"].values())
        commit = session["git"]["commit"] or "unrecorded"
        lines.append(
            f"| {session['started_at']} | `{session['session_id']}` | `{commit}` | {counts} |"
        )
    lines.extend(
        [
            "",
            "## Validation queue",
            "",
            f"- Missing recorded Git commits: {len(missing_commits)}",
            f"- Unresolved mentioned paths: {len(unresolved_mentions)}",
            "- Checkpoints are hashed generically; adapter metadata inspection is weights-only.",
            "- GCS existence/checksum reconciliation remains a separate, opt-in read operation.",
            "",
            "## Graduation rule",
            "",
            "Promote a lead into the Git registry only after its factual fields are "
            "corroborated. Preserve the evidence ID, session ID, source digest, "
            "confidence, and whether the claim is observed or inferred.",
            "",
        ]
    )
    return "\n".join(lines)


def extract_codex_sessions(
    project: ProjectConfig,
    *,
    sessions_root: str | Path,
    output: str | Path | None = None,
    active_window_seconds: int = 300,
    max_message_chars: int = 20_000,
    max_tool_chars: int = 12_000,
) -> dict[str, Any]:
    source_root = Path(sessions_root).expanduser().resolve()
    if not source_root.is_dir():
        raise FileNotFoundError(f"Codex sessions directory not found: {source_root}")
    output_root = (
        Path(output).expanduser().resolve()
        if output is not None
        else project.work_dir / "backfill" / "codex"
    )
    output_root.mkdir(parents=True, exist_ok=True)

    sessions: list[dict[str, Any]] = []
    evidence: list[dict[str, Any]] = []
    excluded_active: list[str] = []
    redactions = 0
    cutoff = time.time() - active_window_seconds
    for path in sorted(source_root.rglob("*.jsonl")):
        if not _session_matches_repository(path, project.root):
            continue
        if active_window_seconds > 0 and path.stat().st_mtime > cutoff:
            excluded_active.append(str(path.relative_to(source_root)))
            continue
        session, session_evidence, session_redactions = _extract_session(
            path,
            sessions_root=source_root,
            repository=project.root,
            max_message_chars=max_message_chars,
            max_tool_chars=max_tool_chars,
        )
        if session is None:
            continue
        sessions.append(session)
        evidence.extend(session_evidence)
        redactions += session_redactions
    sessions.sort(key=lambda value: (value["started_at"], value["session_id"]))
    evidence.sort(
        key=lambda value: (
            value.get("timestamp") or "",
            value["session_id"],
            int(value["source"]["line"]),
        )
    )
    replayed_context = _mark_replayed_context(sessions, evidence)

    artifacts = _inventory_artifacts(project)
    mentions = _path_mentions(evidence, project)
    leads = _review_leads(evidence)
    commits = _git_history(project.root)
    drafts = _draft_candidates(
        project,
        artifacts=artifacts,
        mentions=mentions,
        commits=commits,
    )
    git_commit_ids = {item["commit"] for item in commits}

    evidence_path = output_root / "evidence.jsonl"
    artifacts_path = output_root / "artifacts.jsonl"
    mentions_path = output_root / "path-mentions.jsonl"
    leads_path = output_root / "review-leads.jsonl"
    commits_path = output_root / "git-commits.jsonl"
    drafts_path = output_root / "drafts.jsonl"
    digests = {
        "evidence.jsonl": _write_jsonl(evidence_path, evidence),
        "artifacts.jsonl": _write_jsonl(artifacts_path, artifacts),
        "path-mentions.jsonl": _write_jsonl(mentions_path, mentions),
        "review-leads.jsonl": _write_jsonl(leads_path, leads),
        "git-commits.jsonl": _write_jsonl(commits_path, commits),
        "drafts.jsonl": _write_jsonl(drafts_path, drafts),
    }
    review = _review_markdown(
        sessions=sessions,
        evidence=evidence,
        artifacts=artifacts,
        mentions=mentions,
        leads=leads,
        drafts=drafts,
        excluded_active=excluded_active,
        git_commits=git_commit_ids,
    )
    review_path = output_root / "REVIEW.md"
    temporary_review = review_path.with_suffix(".md.tmp")
    temporary_review.write_text(review, encoding="utf-8")
    temporary_review.replace(review_path)
    digests["REVIEW.md"] = _digest_bytes(review.encode("utf-8"))

    source_fingerprint = _digest_bytes(
        "\n".join(
            f"{session['session_id']}:{session['source_sha256']}" for session in sessions
        ).encode()
    )
    manifest = {
        "schema_version": BUNDLE_SCHEMA_VERSION,
        "kind": "codex_session_backfill_bundle",
        "generated_at": _utc_now(),
        "project": {
            "id": project.project_id,
            "repository": str(project.root),
            "git_revision": revision(project.root),
        },
        "source": {
            "kind": "codex_sessions",
            "root": str(source_root),
            "exact_cwd": str(project.root),
            "source_fingerprint": source_fingerprint,
            "active_window_seconds": active_window_seconds,
            "excluded_active": excluded_active,
        },
        "policy": {
            "included": sorted(ALLOWED_EVIDENCE_KINDS),
            "excluded": [
                "reasoning",
                "agent_reasoning",
                "system_messages",
                "developer_messages",
                "compaction_payloads",
            ],
            "secret_redaction": True,
            "max_message_chars": max_message_chars,
            "max_tool_chars": max_tool_chars,
        },
        "counts": {
            "sessions": len(sessions),
            "evidence": len(evidence),
            "primary_evidence": len(evidence) - replayed_context,
            "replayed_context": replayed_context,
            "artifacts": len(artifacts),
            "path_mentions": len(mentions),
            "review_leads": len(leads),
            "drafts": len(drafts),
            "redactions": redactions,
        },
        "sessions": sessions,
        "files": {
            name: {"sha256": digest}
            for name, digest in sorted(digests.items())
        },
    }
    write_yaml(output_root / "manifest.yaml", manifest)
    return {
        "kind": "backfill_bundle",
        "status": "extracted",
        "path": str(output_root),
        **manifest["counts"],
        "excluded_active": len(excluded_active),
        "source_fingerprint": source_fingerprint,
    }


def _assert_sha256(value: Any, context: str) -> None:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
        raise ValueError(f"{context} must be a lowercase SHA-256 digest")


def validate_backfill_bundle(
    project: ProjectConfig,
    *,
    bundle: str | Path | None = None,
    verify_sources: bool = True,
    verify_artifacts: bool = True,
) -> dict[str, Any]:
    root = (
        Path(bundle).expanduser().resolve()
        if bundle is not None
        else project.work_dir / "backfill" / "codex"
    )
    manifest = load_yaml(root / "manifest.yaml")
    if manifest.get("schema_version") != BUNDLE_SCHEMA_VERSION:
        raise ValueError("unsupported backfill bundle schema")
    if manifest.get("kind") != "codex_session_backfill_bundle":
        raise ValueError("not a Codex session backfill bundle")
    if Path(str(manifest["project"]["repository"])).resolve() != project.root.resolve():
        raise ValueError("backfill bundle belongs to a different repository")

    for name, expected in manifest.get("files", {}).items():
        path = root / name
        if not path.is_file():
            raise FileNotFoundError(f"backfill bundle file missing: {path}")
        _assert_sha256(expected.get("sha256"), f"{name} digest")
        observed = _digest_file(path)
        if observed != expected["sha256"]:
            raise ValueError(f"backfill bundle file digest mismatch: {name}")

    sessions = {
        str(session["session_id"]): session for session in manifest.get("sessions", [])
    }
    if len(sessions) != len(manifest.get("sessions", [])):
        raise ValueError("duplicate session IDs in backfill manifest")
    evidence_ids: set[str] = set()
    evidence_by_id: dict[str, dict[str, Any]] = {}
    evidence_count = 0
    for line_number, row in _read_jsonl(root / "evidence.jsonl"):
        evidence_count += 1
        evidence_id = str(row.get("evidence_id", ""))
        if not evidence_id or evidence_id in evidence_ids:
            raise ValueError(f"invalid or duplicate evidence ID at line {line_number}")
        evidence_ids.add(evidence_id)
        evidence_by_id[evidence_id] = row
        if row.get("kind") not in ALLOWED_EVIDENCE_KINDS:
            raise ValueError(f"forbidden evidence kind at line {line_number}: {row.get('kind')}")
        session_id = str(row.get("session_id", ""))
        if session_id not in sessions:
            raise ValueError(f"unknown session at evidence line {line_number}: {session_id}")
        _assert_sha256(row.get("content_sha256"), f"evidence line {line_number} content digest")
        if _PRIVATE_KEY_PATTERN.search(str(row.get("content", ""))):
            raise ValueError(f"unredacted private key at evidence line {line_number}")
        if _TOKEN_PATTERN.search(str(row.get("content", ""))):
            raise ValueError(f"unredacted token-shaped value at evidence line {line_number}")
        source = row.get("source")
        if not isinstance(source, dict):
            raise ValueError(f"missing evidence source at line {line_number}")
        if source.get("path") != sessions[session_id]["source_path"]:
            raise ValueError(f"session path disagreement at evidence line {line_number}")
        if not isinstance(source.get("line"), int) or source["line"] < 1:
            raise ValueError(f"invalid source line at evidence line {line_number}")
        if source.get("session_sha256") != sessions[session_id]["source_sha256"]:
            raise ValueError(f"session digest disagreement at evidence line {line_number}")
        identity = f"{session_id}\0{source['line']}\0{row['kind']}".encode()
        expected_id = f"ev-{sha256(identity).hexdigest()[:24]}"
        if evidence_id != expected_id:
            raise ValueError(f"evidence ID/source disagreement at line {line_number}")
    if evidence_count != manifest["counts"]["evidence"]:
        raise ValueError("evidence count does not match manifest")

    source_root = Path(str(manifest["source"]["root"]))
    if verify_sources:
        expected_sessions: list[dict[str, Any]] = []
        expected_evidence_rows: list[dict[str, Any]] = []
        for session in sessions.values():
            path = source_root / str(session["source_path"])
            if not path.is_file():
                raise FileNotFoundError(f"source session missing: {path}")
            captured_size = int(session["source_size"])
            if _digest_file_prefix(path, captured_size) != session["source_sha256"]:
                raise ValueError(f"captured source session prefix changed: {path}")
            expected_session, expected_evidence, _ = _extract_session(
                path,
                sessions_root=source_root,
                repository=project.root,
                max_message_chars=int(manifest["policy"]["max_message_chars"]),
                max_tool_chars=int(manifest["policy"]["max_tool_chars"]),
                source_size=captured_size,
            )
            if expected_session is None or expected_session["session_id"] != session["session_id"]:
                raise ValueError(f"source session metadata changed: {path}")
            expected_sessions.append(expected_session)
            expected_evidence_rows.extend(expected_evidence)
        expected_sessions.sort(
            key=lambda value: (value["started_at"], value["session_id"])
        )
        expected_evidence_rows.sort(
            key=lambda value: (
                value.get("timestamp") or "",
                value["session_id"],
                int(value["source"]["line"]),
            )
        )
        _mark_replayed_context(expected_sessions, expected_evidence_rows)
        expected_evidence_ids = {
            str(row["evidence_id"]) for row in expected_evidence_rows
        }
        for expected in expected_evidence_rows:
            evidence_id = str(expected["evidence_id"])
            observed = evidence_by_id.get(evidence_id)
            if observed is None:
                raise ValueError(f"source evidence missing from bundle: {evidence_id}")
            for field in (
                "kind",
                "source",
                "content_sha256",
                "content",
                "redactions",
                "truncated",
                "replayed_context",
                "duplicate_of",
            ):
                if observed.get(field) != expected.get(field):
                    raise ValueError(
                        f"source evidence disagreement for {evidence_id}: {field}"
                    )
        if expected_evidence_ids != evidence_ids:
            unexpected = sorted(evidence_ids - expected_evidence_ids)
            raise ValueError(
                "bundle contains evidence absent from source sessions: "
                + ", ".join(unexpected[:5])
            )

    artifact_count = 0
    artifact_by_path: dict[str, dict[str, Any]] = {}
    if verify_artifacts:
        for line_number, artifact in _read_jsonl(root / "artifacts.jsonl"):
            artifact_count += 1
            artifact_by_path[str(artifact.get("path", ""))] = artifact
            path = project.root / str(artifact.get("path", ""))
            if not path.is_file():
                raise FileNotFoundError(f"artifact missing: {path}")
            _assert_sha256(artifact.get("sha256"), f"artifact line {line_number} digest")
            if _digest_file(path) != artifact["sha256"]:
                raise ValueError(f"artifact changed: {path}")
    else:
        for _line_number, artifact in _read_jsonl(root / "artifacts.jsonl"):
            artifact_count += 1
            artifact_by_path[str(artifact.get("path", ""))] = artifact
    if artifact_count != manifest["counts"]["artifacts"]:
        raise ValueError("artifact count does not match manifest")

    draft_ids: set[str] = set()
    drafts: list[dict[str, Any]] = []
    for line_number, draft in _read_jsonl(root / "drafts.jsonl"):
        candidate_id = str(draft.get("candidate_id", ""))
        if not candidate_id or candidate_id in draft_ids:
            raise ValueError(f"invalid or duplicate draft ID at line {line_number}")
        draft_ids.add(candidate_id)
        drafts.append(draft)
        if draft.get("status") != "needs_review":
            raise ValueError(f"draft is not review-gated at line {line_number}")
        for evidence_id in draft.get("evidence_ids", []):
            if evidence_id not in evidence_ids:
                raise ValueError(
                    f"draft references unknown evidence at line {line_number}: {evidence_id}"
                )
        for source in draft.get("provenance", []):
            if not isinstance(source, dict) or not isinstance(source.get("locator"), str):
                raise ValueError(f"invalid draft provenance at line {line_number}")
            locator = source["locator"]
            digest = source.get("sha256")
            if digest is not None:
                _assert_sha256(digest, f"draft line {line_number} provenance digest")
            if source.get("kind") in {"artifact", "dataset_manifest", "run_metrics"}:
                artifact = artifact_by_path.get(locator)
                if artifact is None:
                    raise ValueError(
                        f"draft references unknown artifact at line {line_number}: {locator}"
                    )
                if digest != artifact["sha256"]:
                    raise ValueError(
                        f"draft artifact digest disagreement at line {line_number}: {locator}"
                    )
    for draft in drafts:
        if draft["kind"] == "run_candidate":
            for dataset_candidate in draft.get("dataset", {}).get("candidates", []):
                if dataset_candidate not in draft_ids:
                    raise ValueError(
                        f"{draft['candidate_id']} references unknown dataset candidate"
                    )
            for parent in draft.get("initialization", {}).get("parents", []):
                if parent.get("module_candidate") not in draft_ids:
                    raise ValueError(
                        f"{draft['candidate_id']} references unknown module candidate"
                    )
        if draft["kind"] == "module_candidate":
            if draft.get("source_run_candidate") not in draft_ids:
                raise ValueError(
                    f"{draft['candidate_id']} references unknown run candidate"
                )
        if draft["kind"] == "evaluation_candidate":
            if draft.get("run_candidate") not in draft_ids:
                raise ValueError(
                    f"{draft['candidate_id']} references unknown run candidate"
                )
    if len(drafts) != manifest["counts"]["drafts"]:
        raise ValueError("draft count does not match manifest")

    commits = {item["commit"] for _, item in _read_jsonl(root / "git-commits.jsonl")}
    unknown_commits = sorted(
        {
            str(session["git"]["commit"])
            for session in sessions.values()
            if session["git"]["commit"] and session["git"]["commit"] not in commits
        }
    )
    curated_references = 0
    curated_review = project.records_dir / "BACKFILL_REVIEW.md"
    if curated_review.is_file():
        text = curated_review.read_text(encoding="utf-8")
        for session_id, evidence_id in re.findall(
            r"([0-9a-f-]{36})#(ev-[0-9a-f]{24})",
            text,
        ):
            curated_references += 1
            evidence_row = evidence_by_id.get(evidence_id)
            if evidence_row is None:
                raise ValueError(
                    f"curated backfill review references unknown evidence: {evidence_id}"
                )
            if evidence_row["session_id"] != session_id:
                raise ValueError(
                    f"curated backfill review session/evidence disagreement: {evidence_id}"
                )
    return {
        "valid": True,
        "kind": "backfill_bundle",
        "path": str(root),
        "sessions": len(sessions),
        "evidence": evidence_count,
        "artifacts": artifact_count,
        "drafts": len(drafts),
        "curated_references": curated_references,
        "unknown_session_commits": unknown_commits,
        "source_fingerprint": manifest["source"]["source_fingerprint"],
    }
