from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path, PurePosixPath
from typing import Any, Protocol
from urllib.parse import urlparse


class ArtifactCollisionError(FileExistsError):
    pass


class ArtifactStore(Protocol):
    base_uri: str

    def create_bytes(self, relative_path: str, payload: bytes, *, content_type: str | None = None) -> str: ...
    def read_bytes(self, relative_path: str) -> bytes: ...
    def exists(self, relative_path: str) -> bool: ...


def _clean_relative(path: str) -> str:
    value = str(PurePosixPath(path))
    if value.startswith("/") or value == "." or ".." in PurePosixPath(value).parts:
        raise ValueError(f"artifact path must be relative: {path}")
    return value


@dataclass
class LocalArtifactStore:
    root: Path

    @property
    def base_uri(self) -> str:
        return self.root.resolve().as_uri()

    def create_bytes(self, relative_path: str, payload: bytes, *, content_type: str | None = None) -> str:
        del content_type
        destination = self.root / _clean_relative(relative_path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        try:
            with destination.open("xb") as handle:
                handle.write(payload)
        except FileExistsError as error:
            raise ArtifactCollisionError(str(destination)) from error
        return destination.resolve().as_uri()

    def read_bytes(self, relative_path: str) -> bytes:
        return (self.root / _clean_relative(relative_path)).read_bytes()

    def exists(self, relative_path: str) -> bool:
        return (self.root / _clean_relative(relative_path)).exists()


class GCSArtifactStore:
    def __init__(self, uri: str):
        parsed = urlparse(uri)
        if parsed.scheme != "gs" or not parsed.netloc:
            raise ValueError(f"invalid GCS URI: {uri}")
        self.bucket_name = parsed.netloc
        self.prefix = parsed.path.strip("/")
        self.base_uri = uri.rstrip("/")
        try:
            from google.cloud import storage
        except ImportError as error:
            raise RuntimeError("install google-cloud-storage to use gs:// artifact stores") from error
        self._bucket = storage.Client().bucket(self.bucket_name)

    def _name(self, relative_path: str) -> str:
        relative = _clean_relative(relative_path)
        return f"{self.prefix}/{relative}" if self.prefix else relative

    def create_bytes(self, relative_path: str, payload: bytes, *, content_type: str | None = None) -> str:
        from google.api_core.exceptions import PreconditionFailed

        name = self._name(relative_path)
        try:
            self._bucket.blob(name).upload_from_string(
                payload,
                content_type=content_type,
                if_generation_match=0,
            )
        except PreconditionFailed as error:
            raise ArtifactCollisionError(f"gs://{self.bucket_name}/{name}") from error
        return f"gs://{self.bucket_name}/{name}"

    def read_bytes(self, relative_path: str) -> bytes:
        return self._bucket.blob(self._name(relative_path)).download_as_bytes()

    def exists(self, relative_path: str) -> bool:
        return self._bucket.blob(self._name(relative_path)).exists()


def open_store(uri: str) -> ArtifactStore:
    parsed = urlparse(uri)
    if parsed.scheme == "gs":
        return GCSArtifactStore(uri)
    if parsed.scheme == "file":
        return LocalArtifactStore(Path(parsed.path))
    raise ValueError(f"unsupported artifact store: {uri}")


def create_json(store: ArtifactStore, relative_path: str, value: dict[str, Any]) -> str:
    payload = (json.dumps(value, indent=2, sort_keys=True) + "\n").encode()
    return store.create_bytes(relative_path, payload, content_type="application/json")


def upload_file(store: ArtifactStore, source: str | Path, relative_path: str) -> dict[str, Any]:
    path = Path(source)
    payload = path.read_bytes()
    return {
        "path": _clean_relative(relative_path),
        "uri": store.create_bytes(relative_path, payload),
        "sha256": sha256(payload).hexdigest(),
        "size": len(payload),
    }


def upload_tree(store: ArtifactStore, source: str | Path, prefix: str) -> dict[str, Any]:
    root = Path(source)
    if not root.is_dir():
        raise NotADirectoryError(root)
    entries: list[dict[str, Any]] = []
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        local = path.relative_to(root).as_posix()
        entries.append(upload_file(store, path, f"{prefix}/{local}"))
    index = {
        "schema_version": 1,
        "prefix": _clean_relative(prefix),
        "files": entries,
        "tree_sha256": sha256(
            "\n".join(f"{entry['path']}:{entry['sha256']}" for entry in entries).encode()
        ).hexdigest(),
    }
    index_uri = create_json(store, f"{prefix}/_index.json", index)
    return {
        "uri": f"{store.base_uri}/{_clean_relative(prefix)}",
        "index_uri": index_uri,
        "tree_sha256": index["tree_sha256"],
        "files": len(entries),
        "bytes": sum(entry["size"] for entry in entries),
    }


def download_tree(store: ArtifactStore, prefix: str, destination: str | Path) -> dict[str, Any]:
    index = json.loads(store.read_bytes(f"{prefix}/_index.json"))
    root = Path(destination)
    root.mkdir(parents=True, exist_ok=True)
    verified: list[str] = []
    for entry in index["files"]:
        full_path = str(entry["path"])
        relative_prefix = _clean_relative(prefix) + "/"
        if not full_path.startswith(relative_prefix):
            raise ValueError(f"index path escapes artifact prefix: {full_path}")
        relative = full_path[len(relative_prefix):]
        payload = store.read_bytes(full_path)
        if sha256(payload).hexdigest() != entry["sha256"]:
            raise ValueError(f"artifact checksum mismatch: {full_path}")
        verified.append(f"{full_path}:{entry['sha256']}")
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)
    if sha256("\n".join(verified).encode()).hexdigest() != index["tree_sha256"]:
        raise ValueError(f"artifact tree checksum mismatch: {prefix}")
    marker = root / ".model-evolution-index.json"
    temporary = marker.with_suffix(".tmp")
    temporary.write_text(json.dumps(index, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(marker)
    return index
