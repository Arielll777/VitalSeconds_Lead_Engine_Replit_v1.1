"""Normalization helpers — used only for matching; original values are preserved."""

from __future__ import annotations

import re
from typing import Optional


def normalize_domain(raw: Optional[str]) -> str:
    if not raw:
        return ""
    d = str(raw).strip().lower()
    d = re.sub(r"^https?://", "", d)
    d = d.split("/")[0].split("?")[0]
    if d.startswith("www."):
        d = d[4:]
    return d.strip().rstrip(".")


def normalize_email(raw: Optional[str]) -> str:
    if not raw:
        return ""
    e = str(raw).strip().lower()
    e = e.replace(" ", "")
    return e


def normalize_name(raw: Optional[str]) -> str:
    if not raw:
        return ""
    return re.sub(r"\s+", " ", str(raw).strip().lower())


def normalize_company(raw: Optional[str]) -> str:
    if not raw:
        return ""
    name = re.sub(r"\s+", " ", str(raw).strip().lower())
    for suffix in (
        " llc",
        " l.l.c.",
        " inc",
        " inc.",
        " corp",
        " corp.",
        " ltd",
        " ltd.",
        " co",
        " co.",
    ):
        if name.endswith(suffix):
            name = name[: -len(suffix)].strip()
    return name


def build_flast(first_name: str, last_name: str) -> str:
    """Pass 1: first initial + last name (no dot). Example: jsmith"""
    f = (first_name or "").strip().lower()
    l = (last_name or "").strip().lower()
    if not f or not l:
        return ""
    l_clean = re.sub(r"[^a-z0-9]", "", l)
    return f[0] + l_clean if l_clean else ""


def build_first(first_name: str) -> str:
    """Pass 2: first name only. Example: john"""
    f = (first_name or "").strip().lower()
    return re.sub(r"[^a-z0-9]", "", f)


def build_first_last(first_name: str, last_name: str) -> str:
    """Pass 3: first.last. Example: john.smith"""
    f = build_first(first_name)
    l = re.sub(r"[^a-z0-9]", "", (last_name or "").strip().lower())
    if not f or not l:
        return ""
    return f"{f}.{l}"
