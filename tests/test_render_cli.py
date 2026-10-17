from jobtalk.cli import main
from jobtalk.models import Job, LineItem
from jobtalk.pricing import compute_totals
from jobtalk.render import render_quote


def test_render_escapes_html_and_flags_unpriced():
    job = Job(customer="<script>x</script>", items=[LineItem("Part", unit_price=150.0)])
    totals = compute_totals(job, 0.18)
    out = render_quote(job, totals, "Biz", 0.18, "1")
    assert "<script>" not in out
    assert "₹150.00" in out
    assert "GST (18%)" in out


def test_render_with_gst_split_and_gstin():
    job = Job(customer="Ravi", items=[LineItem("Labour", "labor", 2, 500)])
    totals = compute_totals(job, 0.18, gst_split=True)
    out = render_quote(
        job,
        totals,
        "Super Services",
        0.18,
        "101",
        gstin="07AAAAA0000A1Z5",
        gst_split=True,
    )
    assert "GSTIN: 07AAAAA0000A1Z5" in out
    assert "CGST (9.0%) ₹90.00" in out
    assert "SGST (9.0%) ₹90.00" in out
    assert "₹1,180.00" in out


def test_demo_cli_writes_quote(tmp_path, capsys):
    out = tmp_path / "q.html"
    assert main(["--demo", "--tax", "0.18", "--out", str(out)]) == 0
    content = out.read_text(encoding="utf-8")
    assert "Mr. Sharma" in content
    assert "₹" in content
    assert "Warning" in capsys.readouterr().out  # PVC pipe unpriced
