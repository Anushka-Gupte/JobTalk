"""Render a Job + Totals into a printable HTML quote."""
from __future__ import annotations

import html
from datetime import date
from pathlib import Path
from string import Template

from jobtalk.models import Job, Totals

_TEMPLATE = Template((Path(__file__).parent / "templates" / "quote.html").read_text())


def money(value: float) -> str:
    return f"${value:,.2f}"


def render_quote(job: Job, totals: Totals, business: str, tax_rate: float,
                 quote_no: str, on: date | None = None) -> str:
    esc = html.escape
    rows = []
    for it in job.items:
        price = money(it.unit_price) if it.unit_price is not None else '<span class="warn">needs price</span>'
        line = money(it.total) if it.total is not None else '<span class="warn">?</span>'
        rows.append(
            f"<tr><td>{esc(it.description)}<small>{esc(it.kind)}</small></td>"
            f'<td class="n">{it.qty:g}</td><td class="n">{price}</td><td class="n">{line}</td></tr>'
        )
    warning = (f'<p class="warn">{totals.unpriced} line(s) have no price. Edit before sending.</p>'
               if totals.unpriced else "")
    return _TEMPLATE.substitute(
        business=esc(business), quote_no=esc(quote_no),
        date=(on or date.today()).strftime("%b %d, %Y"),
        customer=esc(job.customer), address=esc(job.address),
        summary=esc(job.job_summary), notes=esc(job.notes),
        rows="\n".join(rows), warning=warning,
        subtotal=money(totals.subtotal), tax_pct=f"{tax_rate:.0%}",
        tax=money(totals.tax), total=money(totals.total),
    )
