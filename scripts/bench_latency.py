"""Benchmark streaming latency and token generation for JobTalk extraction."""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from jobtalk.config import Settings
from jobtalk.extract import extract_job, stream_extract_job
from jobtalk.models import LineItem


def bench_fixture(
    fixture_path: Path,
    settings: Settings,
    compare_non_streaming: bool = True,
) -> dict:
    data = json.loads(fixture_path.read_text())
    transcript = data["transcript"]
    rates = data.get("rates", {})

    # Warm-up / streaming extraction
    t0 = time.perf_counter()
    t_first_token: float | None = None
    t_first_item: float | None = None
    token_count = 0
    items: list[LineItem] = []
    final_json = ""

    for kind, payload in stream_extract_job(transcript, rates, settings):
        now = time.perf_counter()
        if kind == "token":
            token_count += 1
            if t_first_token is None:
                t_first_token = now - t0
        elif kind == "item":
            if t_first_item is None:
                t_first_item = now - t0
            items.append(payload)  # type: ignore[arg-type]
        elif kind == "done":
            final_json = str(payload)

    t_total = time.perf_counter() - t0

    result = {
        "fixture": fixture_path.name,
        "model": settings.llm_model,
        "t_first_token_s": round(t_first_token or 0.0, 3),
        "t_first_item_s": round(t_first_item or 0.0, 3),
        "t_total_streaming_s": round(t_total, 3),
        "token_chunks": token_count,
        "items_count": len(items),
        "output_chars": len(final_json),
    }

    if compare_non_streaming:
        t_ns_start = time.perf_counter()
        extract_job(transcript, rates, settings)
        t_ns_total = time.perf_counter() - t_ns_start
        result["t_total_non_streaming_s"] = round(t_ns_total, 3)

    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Benchmark extraction latency and streaming.")
    parser.add_argument("--model", default="qwen2.5:1.5b", help="Ollama model name")
    parser.add_argument("--host", default="http://localhost:11434", help="Ollama host URL")
    parser.add_argument(
        "--fixture",
        default=str(Path(__file__).parent.parent / "tests" / "fixtures" / "plumbing_01_water_heater.json"),
        help="Path to a fixture file",
    )
    parser.add_argument("--skip-non-streaming", action="store_true", help="Skip non-streaming comparison")
    args = parser.parse_args(argv)

    fixture_file = Path(args.fixture)
    if not fixture_file.exists():
        print(f"Fixture file not found: {fixture_file}")
        return 1

    settings = Settings(llm_model=args.model, ollama_host=args.host)
    print(f"Benchmarking model {args.model} on {fixture_file.name} ...")
    res = bench_fixture(fixture_file, settings, compare_non_streaming=not args.skip_non_streaming)

    print("\n---------------- STREAMING LATENCY RESULTS ----------------")
    print(f"Fixture:               {res['fixture']}")
    print(f"Model:                 {res['model']}")
    print(f"Time to First Token:   {res['t_first_token_s']:.3f} s")
    print(f"Time to First Item:    {res['t_first_item_s']:.3f} s")
    print(f"Total Streaming Time:  {res['t_total_streaming_s']:.3f} s")
    if "t_total_non_streaming_s" in res:
        print(f"Non-Streaming Time:    {res['t_total_non_streaming_s']:.3f} s")
    print(f"Token Chunks Yielded:  {res['token_chunks']}")
    print(f"Extracted Items:       {res['items_count']}")
    print(f"Total Output Chars:    {res['output_chars']}")
    print("-----------------------------------------------------------\n")

    return 0


if __name__ == "__main__":
    sys.exit(main())
