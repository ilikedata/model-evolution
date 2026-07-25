from __future__ import annotations

import os
import re
import time
from datetime import datetime
from hashlib import sha256

_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_SLUG_PATTERN = re.compile(r"[^a-z0-9]+")


def slugify(value: str) -> str:
    slug = _SLUG_PATTERN.sub("-", value.strip().lower()).strip("-")
    if not slug:
        raise ValueError("identifier slug must contain a letter or number")
    return slug


def _encode(value: int, length: int) -> str:
    result = ["0"] * length
    for index in range(length - 1, -1, -1):
        result[index] = _ALPHABET[value & 31]
        value >>= 5
    return "".join(result)


def ulid() -> str:
    timestamp_ms = int(time.time_ns() // 1_000_000)
    randomness = int.from_bytes(os.urandom(10), "big")
    return _encode(timestamp_ms, 10) + _encode(randomness, 16)


def new_id(slug: str) -> str:
    return f"{slugify(slug)}-{ulid()}"


def observed_id(slug: str, observed_at: str, source_identity: str) -> str:
    """Return a stable ULID-shaped ID anchored to an observed timestamp."""
    timestamp = datetime.fromisoformat(observed_at.replace("Z", "+00:00"))
    timestamp_ms = int(timestamp.timestamp() * 1000)
    randomness = int.from_bytes(
        sha256(f"{slugify(slug)}:{observed_at}:{source_identity}".encode()).digest()[:10],
        "big",
    )
    return (
        f"{slugify(slug)}-"
        f"{_encode(timestamp_ms, 10)}{_encode(randomness, 16)}"
    )
