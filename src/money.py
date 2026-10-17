"""Indian Rupee formatting — the single source of truth for all money display.

Usage::

    from jobtalk.money import format_inr
    format_inr(1234567.5)   # '₹12,34,567.50'
    format_inr(999)         # '₹999.00'
    format_inr(-150.5)      # '-₹150.50'
    format_inr(0)           # '₹0.00'

Rules (Indian numbering system):
  - Last three digits form the first group from the right.
  - Every subsequent group going left is two digits.
  - Examples: 1,23,456  /  12,34,567  /  9,99,99,999
"""
from __future__ import annotations


def format_inr(value: float | None, symbol: str = "₹") -> str:
    """Format *value* as an Indian-locale currency string.

    Works correctly for zero, values under 1 000, and negative numbers.
    No external dependencies.
    """
    if value is None:
        return "—"

    negative = value < 0
    abs_val = abs(value)

    # Split into integer and decimal parts
    int_part = int(abs_val)
    dec_part = round(abs_val - int_part, 2)
    # Avoid floating-point drift (e.g. 0.10000000001)
    cents = round(dec_part * 100)
    decimal_str = f"{cents:02d}"

    # Indian grouping: last 3 digits, then groups of 2
    int_str = str(int_part)
    if len(int_str) <= 3:
        grouped = int_str
    else:
        # first (rightmost) group of 3
        grouped = int_str[-3:]
        remaining = int_str[:-3]
        parts: list[str] = []
        while remaining:
            parts.append(remaining[-2:])
            remaining = remaining[:-2]
        grouped = ",".join(reversed(parts)) + "," + grouped

    formatted = f"{symbol}{grouped}.{decimal_str}"
    return f"-{formatted}" if negative else formatted
