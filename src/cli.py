"""Command-line entry point: audio/text -> quote.html."""
from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

from jobtalk import __version__
from jobtalk.config import Settings, load_rates
from jobtalk.demo import DEMO_JOB, DEMO_TRANSCRIPT
from jobtalk.extract import extract_job
from jobtalk.models import Job
from jobtalk.pricing import apply_rates, compute_totals
from jobtalk.render import render_quote
from jobtalk.transcribe import transcribe


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="jobtalk", description="Dictate a job, get a quote.")
    p.add_argument("input", nargs="?", help="audio file or .txt transcript")
    p.add_argument("--demo", action="store_true", help="run with a built-in sample job")
    p.add_argument("--business", default="Your Business")
    p.add_argument("--tax", type=float, default=0.0, help="tax rate, e.g. 0.08")
    p.add_argument("--rates", help="path to rates.json price list")
    p.add_argument("--model", default=Settings.llm_model, help="Ollama model name")
    p.add_argument("--host", default=Settings.ollama_host)
    p.add_argument("--out", default="output/quote.html")
    p.add_argument("--serve", action="store_true", help="start local web UI server")
    p.add_argument("--port", type=int, default=5000, help="port for web server (default 5000)")
    p.add_argument("--bind", default="0.0.0.0", help="host address to bind (default 0.0.0.0)")
    p.add_argument("--version", action="version", version=f"jobtalk {__version__}")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    settings = Settings(llm_model=args.model, ollama_host=args.host)
    rates = load_rates(args.rates)

    if args.serve:
        from jobtalk.web import run_server
        print(f"Starting JobTalk Web UI on http://{args.bind}:{args.port}")
        print("Point your browser to the URL above. For phone use, connect to the same Wi-Fi.")
        server = run_server(
            host=args.bind,
            port=args.port,
            settings=settings,
            rates=rates,
            business=args.business,
            tax_rate=args.tax,
        )
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            print("\nShutting down server.")
            server.server_close()
        return 0

    try:
        if args.demo:
            transcript, job = DEMO_TRANSCRIPT, Job.from_dict(DEMO_JOB)
        elif args.input:
            path = Path(args.input)
            transcript = path.read_text() if path.suffix == ".txt" else transcribe(path, settings)
            job = extract_job(transcript, rates, settings)
        else:
            build_parser().error("provide an input file, use --demo, or start the server with --serve")
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    job = apply_rates(job, rates)
    totals = compute_totals(job, args.tax)
    quote_no = date.today().strftime("%y%m%d") + "-1"
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render_quote(job, totals, args.business, args.tax, quote_no))

    print(f"Transcript: {transcript}")
    if totals.unpriced:
        print(f"Warning: {totals.unpriced} line(s) have no price.")
    print(f"Wrote {out} (total ${totals.total:,.2f}). Open in a browser and print to PDF.")
    return 0
