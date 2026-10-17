"""Canned job so the pipeline can be demoed with no mic and no LLM."""

DEMO_TRANSCRIPT = (
    "Okay, just finished at Sharma ji's place at 14 Nehru Colony. Replaced the old geyser "
    "with a new 25-litre Racold unit — that's 8,500 rupees for the geyser. "
    "Labour was about three hours. Put in a new ISI-mark brass stopcock, and ran some "
    "PVC pipe to the new location. Old geyser haul-away is 300 rupees."
)

DEMO_JOB = {
    "customer": "Mr. Sharma",
    "address": "14 Nehru Colony",
    "job_summary": "Replaced 25-litre Racold geyser",
    "notes": "Old unit hauled away. ISI-mark fittings used.",
    "items": [
        {"description": "Racold 25-litre geyser", "kind": "material", "qty": 1, "unit_price": 8500},
        {"description": "Labour", "kind": "labor", "qty": 3.0, "unit_price": 500},
        {"description": "ISI-mark brass stopcock", "kind": "material", "qty": 1, "unit_price": 180},
        {"description": "PVC pipe (per foot)", "kind": "material", "qty": 6, "unit_price": None},
        {"description": "Haul-away of old geyser", "kind": "labor", "qty": 1, "unit_price": 300},
    ],
}
