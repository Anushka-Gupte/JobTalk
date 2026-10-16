"""Measure cold and warm time to first item across 5 fixtures."""
from __future__ import annotations

import json
import statistics
import sys
import time
import urllib.request
from pathlib import Path

from jobtalk.config import Settings
from jobtalk.extract import stream_extract_job


def unload_model(settings: Settings) -> None:
    req = urllib.request.Request(
        f"{settings.ollama_host}/api/generate",
        data=json.dumps({"model": settings.llm_model, "keep_alive": 0}).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        resp.read()
    time.sleep(0.5)


def measure_stream(fixture_path: Path, settings: Settings) -> tuple[float, float, float]:
    """Returns (t_first_token, t_first_item, t_total)."""
    data = json.loads(fixture_path.read_text())
    transcript = data["transcript"]
    rates = data.get("rates", {})

    t0 = time.perf_counter()
    t_first_token: float | None = None
    t_first_item: float | None = None

    for kind, payload in stream_extract_job(transcript, rates, settings):
        now = time.perf_counter()
        if kind == "token" and t_first_token is None:
            t_first_token = now - t0
        elif kind == "item" and t_first_item is None:
            t_first_item = now - t0

    t_total = time.perf_counter() - t0
    return (t_first_token or t_total, t_first_item or t_total, t_total)


def main() -> int:
    fixtures_dir = Path(__file__).parent.parent / "tests" / "fixtures"
    fixtures = [
        fixtures_dir / "plumbing_01_water_heater.json",
        fixtures_dir / "electrical_01_subpanel.json",
        fixtures_dir / "hvac_01_ac_capacitor_tuneup.json",
        fixtures_dir / "plumbing_02_drain_and_ptrap.json",
        fixtures_dir / "electrical_02_recessed_lighting.json",
    ]

    settings = Settings(llm_model="qwen2.5:1.5b")

    print(f"Measuring time to first item on {len(fixtures)} fixtures with model {settings.llm_model}")
    print("--------------------------------------------------------------------------------")

    cold_items: list[float] = []
    warm_items: list[float] = []

    for f in fixtures:
        print(f"Fixture: {f.name}")
        # 1. Cold measurement (unload model first)
        unload_model(settings)
        t_tok_c, t_item_c, t_tot_c = measure_stream(f, settings)
        cold_items.append(t_item_c)
        print(f"  COLD: first_tok={t_tok_c:.2f}s, first_item={t_item_c:.2f}s, total={t_tot_c:.2f}s")

        # 2. Warm measurement (run immediately while model is resident)
        t_tok_w, t_item_w, t_tot_w = measure_stream(f, settings)
        warm_items.append(t_item_w)
        print(f"  WARM: first_tok={t_tok_w:.2f}s, first_item={t_item_w:.2f}s, total={t_tot_w:.2f}s")

    print("\n================ SUMMARY: TIME TO FIRST ITEM ================")
    print(f"Cold runs (n={len(cold_items)}):")
    print(f"  Range:  {min(cold_items):.2f}s - {max(cold_items):.2f}s")
    print(f"  Median: {statistics.median(cold_items):.2f}s")
    print(f"  Mean:   {statistics.mean(cold_items):.2f}s")
    print("\nWarm runs (n={len(warm_items)}):")
    print(f"  Range:  {min(warm_items):.2f}s - {max(warm_items):.2f}s")
    print(f"  Median: {statistics.median(warm_items):.2f}s")
    print(f"  Mean:   {statistics.mean(warm_items):.2f}s")
    print("==============================================================")
    return 0


if __name__ == "__main__":
    sys.exit(main())
