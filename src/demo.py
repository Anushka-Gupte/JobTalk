"""Canned job so the pipeline can be demoed with no mic and no LLM."""

DEMO_TRANSCRIPT = (
    "Replaced the water heater at Mrs. Patel's on 12 Oak Street. Forty gallon Rheem unit, "
    "two and a half hours labor, new shutoff valve, a few copper fittings, and hauled away "
    "the old tank."
)

DEMO_JOB = {
    "customer": "Mrs. Patel",
    "address": "12 Oak Street",
    "job_summary": "Replaced 40-gallon water heater",
    "items": [
        {"description": "Rheem 40-gallon water heater", "kind": "material", "qty": 1, "unit_price": 640},
        {"description": "Labor", "kind": "labor", "qty": 2.5, "unit_price": 85},
        {"description": "Shutoff valve", "kind": "material", "qty": 1, "unit_price": 22},
        {"description": "Copper fittings (assorted)", "kind": "material", "qty": 1, "unit_price": None},
        {"description": "Haul-away of old tank", "kind": "labor", "qty": 1, "unit_price": 45},
    ],
}
