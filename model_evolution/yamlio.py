from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


def load_yaml(path: str | Path) -> dict[str, Any]:
    value = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a YAML mapping")
    return value


def dump_yaml(value: dict[str, Any]) -> str:
    return yaml.safe_dump(value, sort_keys=False, allow_unicode=True)


def write_yaml(path: str | Path, value: dict[str, Any]) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    temporary.write_text(dump_yaml(value), encoding="utf-8")
    temporary.replace(destination)


def load_markdown(path: str | Path) -> tuple[dict[str, Any], str]:
    text = Path(path).read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        raise ValueError(f"{path} must start with YAML front matter")
    try:
        header, body = text[4:].split("\n---\n", 1)
    except ValueError as error:
        raise ValueError(f"{path} has unterminated YAML front matter") from error
    value = yaml.safe_load(header)
    if not isinstance(value, dict):
        raise ValueError(f"{path} front matter must contain a YAML mapping")
    return value, body


def write_markdown(path: str | Path, front_matter: dict[str, Any], body: str) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    text = f"---\n{dump_yaml(front_matter)}---\n\n{body.strip()}\n"
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(destination)
