from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Iterable


def _git(root: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=root,
        check=check,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def revision(root: Path) -> str:
    return _git(root, "rev-parse", "HEAD").stdout.strip()


def tracked_changes(root: Path, *, allowed_prefixes: Iterable[str] = ()) -> list[str]:
    prefixes = tuple(prefix.rstrip("/") + "/" for prefix in allowed_prefixes)
    result = _git(root, "status", "--porcelain=v1", "--untracked-files=all")
    changed: list[str] = []
    for line in result.stdout.splitlines():
        value = line[3:]
        if " -> " in value:
            value = value.split(" -> ", 1)[1]
        if any(value == prefix[:-1] or value.startswith(prefix) for prefix in prefixes):
            continue
        changed.append(value)
    return changed


def require_clean_source(root: Path) -> None:
    changes = tracked_changes(
        root,
        allowed_prefixes=("model-evolution", ".model-evolution", "runs", "data/generated", "data/cache"),
    )
    if changes:
        raise RuntimeError("tracked source/config changes must be committed: " + ", ".join(changes))


def require_tracked_file(root: Path, path: str | Path) -> Path:
    source = Path(path)
    if not source.is_absolute():
        source = root / source
    source = source.resolve()
    try:
        relative = source.relative_to(root.resolve())
    except ValueError as error:
        raise ValueError(f"provenance file must be inside the project repository: {source}") from error
    result = _git(root, "ls-files", "--error-unmatch", "--", str(relative), check=False)
    if result.returncode != 0:
        raise ValueError(f"provenance file must be tracked by Git: {relative}")
    return relative


def require_committed_file(root: Path, path: str | Path) -> None:
    relative = require_tracked_file(root, path)
    unstaged = _git(root, "diff", "--quiet", "HEAD", "--", str(relative), check=False)
    staged = _git(root, "diff", "--cached", "--quiet", "HEAD", "--", str(relative), check=False)
    if unstaged.returncode != 0 or staged.returncode != 0:
        raise RuntimeError(f"record must be committed before use: {relative}")


def commit_paths(root: Path, paths: Iterable[Path], message: str) -> str:
    relative = [str(path.resolve().relative_to(root.resolve())) for path in paths]
    if not relative:
        return revision(root)
    _git(root, "add", "--", *relative)
    staged = _git(root, "diff", "--cached", "--quiet", check=False)
    if staged.returncode == 0:
        return revision(root)
    _git(root, "commit", "-m", message, "--", *relative)
    return revision(root)
