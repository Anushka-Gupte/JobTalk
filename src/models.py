"""Plain data types shared across the pipeline."""
from __future__ import annotations

from dataclasses import dataclass, field


def _num(value, default=None):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


@dataclass
class LineItem:
    description: str
    kind: str = "material"  # "labor" | "material"
    qty: float = 1.0
    unit_price: float | None = None

    @property
    def total(self) -> float | None:
        return None if self.unit_price is None else self.qty * self.unit_price


@dataclass
class Job:
    customer: str = ""
    address: str = ""
    job_summary: str = ""
    notes: str = ""
    items: list[LineItem] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict) -> "Job":
        """Tolerant parser: small models return messy JSON, so never trust it."""
        items = []
        for raw in data.get("items") or []:
            if not isinstance(raw, dict) or not raw.get("description"):
                continue
            kind = "labor" if str(raw.get("kind", "")).lower() == "labor" else "material"
            items.append(LineItem(
                description=str(raw["description"]).strip(),
                kind=kind,
                qty=_num(raw.get("qty"), 1.0),
                unit_price=_num(raw.get("unit_price")),
            ))
        s = lambda k: str(data.get(k) or "").strip()  # noqa: E731
        return cls(s("customer"), s("address"), s("job_summary"), s("notes"), items)


@dataclass
class Totals:
    subtotal: float
    tax: float
    total: float
    unpriced: int
