"""Research provenance and immutable artifact management."""

from .adapters import ProjectAdapter
from .config import ProjectConfig, initialize_project, load_project
from .gitops import require_clean_source, require_committed_file
from .ids import new_id
from .records import load_record, record_path
from .service import ModelEvolution, now
from .storage import download_tree, upload_file, upload_tree

__all__ = [
    "ModelEvolution",
    "ProjectAdapter",
    "ProjectConfig",
    "download_tree",
    "initialize_project",
    "load_record",
    "load_project",
    "new_id",
    "now",
    "record_path",
    "require_clean_source",
    "require_committed_file",
    "upload_file",
    "upload_tree",
]
__version__ = "0.1.0"
