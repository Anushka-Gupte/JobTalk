from jobtalk.models import Job, LineItem
from jobtalk.pricing import apply_rates, compute_totals


def test_totals_and_tax():
    job = Job(items=[LineItem("Labor", "labor", 2, 50), LineItem("Valve", "material", 1, 20)])
    t = compute_totals(job, 0.1)
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
