"""Helpers for rendering the public OFAC SDN tracker."""

from __future__ import annotations


def is_sdn_designation_row(row) -> bool:
    """Return True only for actual SDN-list diff rows.

    General-license notices are stored under the broader OFAC source bucket so
    they can feed briefings, but they are not SDN designations and should not
    appear in the public SDN tracker table or counts.
    """
    article_type = (getattr(row, "article_type", "") or "").strip().lower()
    if not article_type.startswith("sdn "):
        return False

    meta = getattr(row, "extra_metadata", None) or {}
    return bool(meta.get("uid") and meta.get("name") and meta.get("program"))
