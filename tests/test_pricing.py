from jobtalk.models import Job, LineItem
from jobtalk.pricing import apply_rates, compute_totals


def test_totals_and_tax():
    # Use round_to_rupee=False to test raw paise arithmetic
    job = Job(items=[LineItem("Labor", "labor", 2, 50), LineItem("Valve", "material", 1, 20)])
    t = compute_totals(job, 0.1, round_to_rupee=False)
    assert (t.subtotal, t.tax, t.total, t.unpriced) == (120.0, 12.0, 132.0, 0)


def test_unpriced_items_are_counted_not_guessed():
    job = Job(items=[LineItem("Mystery part"), LineItem("Valve", unit_price=10)])
    t = compute_totals(job)
    assert t.subtotal == 10.0 and t.unpriced == 1


def test_rates_fill_missing_prices_only():
    job = Job(items=[
        LineItem("Shutoff valve"),
        LineItem("Labor", "labor", 2),
        LineItem("P-trap", unit_price=99),
    ])
    apply_rates(job, {"labor_hourly": 85, "items": {"shutoff valve": 22, "p-trap": 14}})
    assert [i.unit_price for i in job.items] == [22.0, 85.0, 99]


def test_gst_split_computation():
    # Subtotal 1000, 18% GST = 180. CGST = 90, SGST = 90; total is integer so round_off = 0
    job = Job(items=[LineItem("Labour", "labor", 2, 500)])
    t = compute_totals(job, 0.18, gst_split=True)
    assert t.subtotal == 1000.0
    assert t.tax == 180.0
    assert t.cgst == 90.0
    assert t.sgst == 90.0
    assert t.total == 1180.0
    assert t.round_off == 0.0


def test_round_off_odd_paise():
    # Subtotal 999.75, 0% tax → raw_total 999.75 → rounded to 1000 → round_off = 0.25
    job = Job(items=[LineItem("Tap washer", "material", 1, 999.75)])
    t = compute_totals(job, 0.0, round_to_rupee=True)
    assert t.subtotal == 999.75
    assert t.tax == 0.0
    assert t.total == 1000.0
    assert t.round_off == 0.25
    # Invariant: subtotal + tax + round_off == total
    assert round(t.subtotal + t.tax + t.round_off, 2) == t.total


def test_round_off_rounds_up():
    # Subtotal 100.60, 0% tax → raw_total 100.60 → rounded to 101 → round_off = 0.40
    job = Job(items=[LineItem("Fitting", "material", 1, 100.60)])
    t = compute_totals(job, 0.0, round_to_rupee=True)
    assert t.total == 101.0
    assert round(t.round_off, 2) == 0.40
    assert round(t.subtotal + t.tax + t.round_off, 2) == t.total


def test_gst_split_with_round_off_invariant():
    # Subtotal 333.33, 18% GST = 59.9994 → rounded to 60.0 → effective_tax depends on rounding
    # CGST = 30.0, SGST = 30.0; raw_total = 393.33 → rounded to 393 → round_off = -0.33
    job = Job(items=[LineItem("Labour", "labor", 1, 333.33)])
    t = compute_totals(job, 0.18, gst_split=True, round_to_rupee=True)
    # Invariant: subtotal + cgst + sgst + round_off == total
    assert round(t.subtotal + t.cgst + t.sgst + t.round_off, 2) == t.total
    assert t.cgst + t.sgst == t.tax


def test_round_to_rupee_false_preserves_paise():
    # round_to_rupee=False must leave totals at exact paise without rounding
    job = Job(items=[LineItem("Labour", "labor", 2.5, 90.0), LineItem("Valve", "material", 2.0, 25.0)])
    t = compute_totals(job, 0.10, round_to_rupee=False)
    assert t.subtotal == 275.0
    assert t.tax == 27.5
    assert t.total == 302.5
    assert t.round_off == 0.0


def test_property_randomized_accounting_invariants():
    """Property test across 1,000 random orders: subtotal + tax + round_off == total exactly."""
    import random

    rng = random.Random(42)  # deterministic seed

    for _ in range(1000):
        # Generate 1 to 5 random items
        num_items = rng.randint(1, 5)
        items = []
        for j in range(num_items):
            qty = round(rng.uniform(0.25, 20.0), 2)
            # unit price with odd paise: e.g. 12.37, 499.95, etc.
            price = round(rng.uniform(1.0, 5000.0), 2)
            items.append(LineItem(f"Item {j}", "material", qty, price))

        job = Job(items=items)
        tax_rate = rng.choice([0.0, 0.05, 0.12, 0.18, 0.28])
        gst_split = rng.choice([True, False])
        round_to_rupee = rng.choice([True, False])

        totals = compute_totals(job, tax_rate, gst_split=gst_split, round_to_rupee=round_to_rupee)

        # Invariant 1: subtotal + effective_tax + round_off == total (within floating representation precision)
        expected_total = round(totals.subtotal + totals.tax + totals.round_off, 2)
        assert expected_total == totals.total, (
            f"Failed invariant: {totals.subtotal} + {totals.tax} + {totals.round_off} != {totals.total}"
        )

        # Invariant 2: if gst_split, cgst + sgst == tax exactly
        if gst_split:
            assert round(totals.cgst + totals.sgst, 2) == totals.tax
            assert round(totals.subtotal + totals.cgst + totals.sgst + totals.round_off, 2) == totals.total

        # Invariant 3: if round_to_rupee is True, total has no fractional rupee (i.e. paise is 0.0)
        if round_to_rupee:
            assert totals.total == float(int(totals.total))

