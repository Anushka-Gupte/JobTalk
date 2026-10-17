"""Pricing logic. All money math lives here, in code, never in the LLM."""
from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

from jobtalk.models import Job, Totals

_TWO_PLACES = Decimal("0.01")
_ONE_RUPEE = Decimal(1)


def _to_decimal(val: float | str | None) -> Decimal:
    if val is None:
        return Decimal("0.00")
    return Decimal(str(val))


def _quantize_money(d: Decimal) -> Decimal:
    return d.quantize(_TWO_PLACES, rounding=ROUND_HALF_UP)


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


def compute_totals(
    job: Job,
    tax_rate: float = 0.0,
    *,
    gst_split: bool = False,
    round_to_rupee: bool = True,
) -> Totals:
    # 1. Compute subtotal using Decimal (excluding dropped items)
    priced_items = [
        _quantize_money(_to_decimal(i.qty) * _to_decimal(i.unit_price))
        for i in job.items
        if not i.dropped_reason and i.unit_price is not None and i.qty is not None
    ]
    subtotal_dec = sum(priced_items, Decimal("0.00"))
    unpriced = sum(1 for i in job.items if not i.dropped_reason and i.unit_price is None)

    # 2. Compute tax
    tax_rate_dec = _to_decimal(tax_rate)
    tax_dec = _quantize_money(subtotal_dec * tax_rate_dec)

    if gst_split:
        cgst_dec = (tax_dec / Decimal(2)).quantize(_TWO_PLACES, rounding=ROUND_HALF_UP)
        sgst_dec = tax_dec - cgst_dec
        effective_tax_dec = cgst_dec + sgst_dec
    else:
        cgst_dec = Decimal("0.00")
        sgst_dec = Decimal("0.00")
        effective_tax_dec = tax_dec

    raw_total_dec = subtotal_dec + effective_tax_dec

    if round_to_rupee:
        rounded_total_dec = raw_total_dec.quantize(_ONE_RUPEE, rounding=ROUND_HALF_UP).quantize(_TWO_PLACES)
        round_off_dec = rounded_total_dec - raw_total_dec
        total_dec = rounded_total_dec
    else:
        round_off_dec = Decimal("0.00")
        total_dec = raw_total_dec

    return Totals(
        subtotal=float(subtotal_dec),
        tax=float(effective_tax_dec),
        total=float(total_dec),
        unpriced=unpriced,
        cgst=float(cgst_dec),
        sgst=float(sgst_dec),
        round_off=float(round_off_dec),
    )

