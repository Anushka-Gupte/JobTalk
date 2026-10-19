"""Render a Job + Totals into a printable HTML quote."""
from __future__ import annotations

import html
from datetime import date
from pathlib import Path
from string import Template

from jobtalk.models import Job, Totals
from jobtalk.money import format_inr

_TEMPLATE = Template((Path(__file__).parent / "templates" / "quote.html").read_text(encoding="utf-8"))


def render_quote(
    job: Job,
    totals: Totals,
    business: str,
    tax_rate: float,
    quote_no: str,
    on: date | None = None,
    currency_symbol: str = "₹",
    gstin: str = "",
    gst_split: bool = False,
) -> str:
    esc = html.escape
    fmt = lambda v: format_inr(v, currency_symbol)

    # Labour label for user-facing display (internal field stays "labor")
    def kind_label(kind: str) -> str:
        return "Labour" if kind == "labor" else "Material"

    rows = []
    for it in job.items:
        price = fmt(it.unit_price) if it.unit_price is not None else '<span class="warn">needs price</span>'
        line = fmt(it.total) if it.total is not None else '<span class="warn">?</span>'
        rows.append(
            f"<tr><td>{esc(it.description)}<small>{kind_label(it.kind)}</small></td>"
            f'<td class="n">{it.qty:g}</td><td class="n">{price}</td><td class="n">{line}</td></tr>'
        )

    warning = (
        f'<p class="warn">{totals.unpriced} line(s) have no price. Edit before sending.</p>'
        if totals.unpriced
        else ""
    )

    gstin_line = f'<p>GSTIN: {esc(gstin)}</p>' if gstin else ""
    business_header = f"<h1>{esc(business)}</h1>" if business.strip() else ""

    if gst_split and (totals.cgst or totals.sgst):
        gst_rows = (
            f"CGST ({tax_rate / 2:.1%}) {fmt(totals.cgst)}<br>"
            f"SGST ({tax_rate / 2:.1%}) {fmt(totals.sgst)}<br>"
        )
        raw_gst_text = f"CGST ({tax_rate / 2:.1%}): {fmt(totals.cgst)}\\nSGST ({tax_rate / 2:.1%}): {fmt(totals.sgst)}\\n"
    elif tax_rate > 0:
        gst_rows = f"GST ({tax_rate:.0%}) {fmt(totals.tax)}<br>"
        raw_gst_text = f"GST ({tax_rate:.0%}): {fmt(totals.tax)}\\n"
    else:
        gst_rows = f"GST (0%) {fmt(0.0)}<br>"
        raw_gst_text = ""

    round_off_line = f"Round-off {fmt(totals.round_off)}<br>" if totals.round_off else ""

    return _TEMPLATE.substitute(
        business_header=business_header,
        quote_no=esc(quote_no),
        date=(on or date.today()).strftime("%d %b %Y"),
        customer=esc(job.customer),
        address=esc(job.address),
        summary=esc(job.job_summary),
        notes=esc(job.notes),
        rows="\n".join(rows),
        warning=warning,
        gstin_line=gstin_line,
        subtotal=fmt(totals.subtotal),
        gst_rows=gst_rows,
        round_off_line=round_off_line,
        total=fmt(totals.total),
        raw_gst_text=raw_gst_text,
    )
