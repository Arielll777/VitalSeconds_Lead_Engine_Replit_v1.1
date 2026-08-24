"""Thin wrapper around utils.normalize for service-layer consistency."""

from __future__ import annotations

from typing import Any, Dict, Optional

from vitalseconds.utils.normalize import (
    normalize_company,
    normalize_domain,
    normalize_email,
    normalize_name,
)


class Normalizer:
    """Normalize fields for matching only. Original values are never overwritten."""

    @staticmethod
    def domain(value: Optional[str]) -> str:
        return normalize_domain(value)

    @staticmethod
    def email(value: Optional[str]) -> str:
        return normalize_email(value)

    @staticmethod
    def name(value: Optional[str]) -> str:
        return normalize_name(value)

    @staticmethod
    def company(value: Optional[str]) -> str:
        return normalize_company(value)

    @staticmethod
    def full_name(first: Optional[str], last: Optional[str]) -> str:
        return normalize_name(f"{first or ''} {last or ''}".strip())

    @classmethod
    def row_for_matching(cls, row: Dict[str, Any]) -> Dict[str, str]:
        """Return a normalized view of a raw row for matching purposes."""
        return {
            "domain": cls.domain(row.get("domain") or row.get("Domain")),
            "email": cls.email(row.get("email") or row.get("Email") or row.get("public_email")),
            "company": cls.company(row.get("company") or row.get("Company")),
            "first_name": cls.name(row.get("first_name") or row.get("First_Name")),
            "last_name": cls.name(row.get("last_name") or row.get("Last_Name")),
            "full_name": cls.full_name(
                row.get("first_name") or row.get("First_Name"),
                row.get("last_name") or row.get("Last_Name"),
            ),
            "buying_group": (row.get("buying_group") or row.get("Buying_Group") or "").strip(),
        }
