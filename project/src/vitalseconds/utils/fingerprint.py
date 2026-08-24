"""Deterministic fingerprint helpers."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Iterable


def make_fingerprint(*parts: Any) -> str:
    payload = json.dumps(
        [str(p).strip().lower() if p is not None else "" for p in parts],
        sort_keys=False,
        ensure_ascii=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def make_header_fingerprint(headers: Iterable[str]) -> str:
    normalized = sorted(h.strip().lower() for h in headers if h and str(h).strip())
    return make_fingerprint(*normalized)


def make_file_sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def make_verification_fingerprint(
    normalized_email: str = "",
    neverbounce_status: str = "",
    batch_id: str = "",
    source_identifier: str = "",
    *,
    source_file_sha256: str = "",
    source_row_number: int = 0,
    source_type: str = "NEVERBOUNCE",
    verifier: str = "NeverBounce",
) -> str:
    """Exact same source file+row = same key. Later legitimate re-verify = new key."""
    file_sha = source_file_sha256 or source_identifier
    return make_fingerprint(
        file_sha,
        source_row_number,
        source_type,
        verifier,
        batch_id,
        normalized_email,
        (neverbounce_status or "").strip().lower(),
    )
