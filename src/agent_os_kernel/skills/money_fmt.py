"""One pure money formatter shared by skill stubs. Not a display service."""
from __future__ import annotations

from typing import Any


def format_gmv(value: Any) -> str:
    """Money-like sample GMV: 2 decimal places, then drop a long float tail."""
    if isinstance(value, bool):
        return str(value)
    if value is None:
        return "—"
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return text
        try:
            number = float(text)
        except ValueError:
            return text
    elif isinstance(value, (int, float)):
        number = float(value)
    else:
        return str(value)
    if number != number or number in (float("inf"), float("-inf")):
        return str(value).strip() if isinstance(value, str) else str(value)
    rounded = round(number, 2)
    if rounded == int(rounded):
        return str(int(rounded))
    return f"{rounded:.2f}".rstrip("0").rstrip(".")
