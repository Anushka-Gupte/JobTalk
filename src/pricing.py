"""Pricing logic. All money math lives here, in code, never in the LLM."""
from __future__ import annotations

from jobtalk.models import Job, Totals


def apply_rates(job: Job, rates: dict) -> Job:
    """Fill missing prices from the tradesperson's own price list."""
    named = {k.lower(): float(v) for k, v in rates.get("items", {}).items()}
    hourly = rates.get("labor_hourly")
    for item in job.items:
        if item.unit_price is not None:
            continue
        desc = item.description.lower()
        match = next((v for k, v in named.items() if k in desc), None)
        if match is not None:
            item.unit_price = match
        elif item.kind == "labor" and hourly is not None and "labor" in desc:
            item.unit_price = float(hourly)
    return job


def compute_totals(job: Job, tax_rate: float = 0.0) -> Totals:
    priced = [i.total for i in job.items if i.total is not None]
    subtotal = round(sum(priced), 2)
    tax = round(subtotal * tax_rate, 2)
    unpriced = sum(1 for i in job.items if i.unit_price is None)
    return Totals(subtotal, tax, round(subtotal + tax, 2), unpriced)
