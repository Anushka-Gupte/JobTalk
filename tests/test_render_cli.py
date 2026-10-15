from jobtalk.cli import main
from jobtalk.models import Job, LineItem
from jobtalk.pricing import compute_totals
from jobtalk.render import render_quote


def test_render_escapes_html_and_flags_unpriced():
    job = Job(customer="<script>x</script>", items=[LineItem("Part")])
    out = render_quote(job, compute_totals(job), "Biz", 0.0, "1")
    assert "<script>" not in out and "needs price" in out


def test_demo_cli_writes_quote(tmp_path, capsys):
    out = tmp_path / "q.html"
    assert main(["--demo", "--tax", "0.08", "--out", str(out)]) == 0
    assert "Mrs. Patel" in out.read_text()
    assert "Warning" in capsys.readouterr().out  # copper fittings unpriced
