"""
How a starter-product run (create_seed_item) is summed up for the screen.

Kept free of Frappe imports so it can be tested on its own.
"""

from __future__ import annotations

import re


def seed_result_status(created: int, failed: int, skipped: int, stock_needed: bool, stock_created: bool) -> str:
    """
    "success": every product was saved (or already existed) and, when
    quantities were given, the opening stock was recorded.
    "partial_success": some things worked and some did not.
    "failed": nothing was saved.

    Earlier, a run with no quantities was never a "success", because no stock
    entry is needed for it, so a run that saved every product was reported as
    a failure.
    """
    stock_ok = stock_created or not stock_needed
    if failed == 0 and stock_ok and (created or skipped):
        return "success"
    if created or (stock_needed and stock_created):
        return "partial_success"
    return "failed"


_MANDATORY = re.compile(r"^\s*\[[^\]]*\]:\s*(.+)$")
_TAGS = re.compile(r"<[^>]+>")


def readable_seed_error(error) -> str:
    """One short sentence explaining why a product could not be saved."""
    text = _TAGS.sub("", str(error or "")).strip()
    match = _MANDATORY.match(text)
    if match:
        fields = [f.strip().replace("custom_", "").replace("_", " ") for f in match.group(1).split(",") if f.strip()]
        return "Missing required details: " + ", ".join(fields) + "."
    if not text:
        return "Could not be saved."
    return text if len(text) <= 240 else text[:237].rstrip() + "..."
