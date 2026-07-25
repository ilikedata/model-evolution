from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol

from ..service import ModelEvolution


class ProjectAdapter(Protocol):
    name: str

    def generate_dataset(
        self,
        service: ModelEvolution,
        *,
        slug: str,
        config_path: str | Path,
    ) -> dict[str, Any]: ...

    def execute_run(
        self,
        service: ModelEvolution,
        run_id: str,
        *,
        epochs_this_run: int | None = None,
    ) -> dict[str, Any]: ...

    def inspect_artifact(
        self,
        path: str | Path,
    ) -> dict[str, Any] | None: ...
