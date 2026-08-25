"""Research provenance and immutable artifact management."""

from .adapters import ProjectAdapter
from .config import ProjectConfig, initialize_project, load_project
from .experiments import (
    conclude_experiment,
    execute_experiment,
    load_experiment,
    plan_experiment,
)
from .gitops import require_clean_source, require_committed_file
from .ids import new_id
from .records import load_record, record_path
from .service import ModelEvolution, now
from .storage import download_tree, upload_file, upload_tree

__all__ = [
    "ModelEvolution",
    "ProjectAdapter",
    "ProjectConfig",
    "conclude_experiment",
    "download_tree",
    "execute_experiment",
    "initialize_project",
    "load_experiment",
    "load_record",
    "load_project",
    "new_id",
    "now",
    "plan_experiment",
    "record_path",
    "require_clean_source",
    "require_committed_file",
    "upload_file",
    "upload_tree",
]
__version__ = "0.1.0"
