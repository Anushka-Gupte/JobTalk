"""Detailed per-fixture evaluation breakdown runner."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from jobtalk.config import Settings
from jobtalk.extract import extract_job
from jobtalk.models import Job
from jobtalk.pricing import apply_rates

from scripts.eval import evaluate_fixture


def eval_detailed(fixtures: list[Path], settings: Settings, label: str):
    print("\n=======================================================")
    print(f"EVALUATION BREAKDOWN: {label} ({len(fixtures)} fixtures)")
    print(f"Model: {settings.llm_model}")
    print("=======================================================")

    results = []
    for f in fixtures:
        data = json.loads(f.read_text())
        expected_job = Job.from_dict(data["expected"])
        transcript = data["transcript"]
        rates = data.get("rates", {})

        # Run extraction
        try:
            extracted_job = extract_job(transcript, rates, settings)
            extracted_job = apply_rates(extracted_job, rates)
        except Exception as exc:  # noqa: BLE001
            print(f"\n[FAILED] {f.name}: Exception {exc}")
            continue

        res = evaluate_fixture(f, settings, verbose=False)
        results.append(res)

        # Compute per-fixture composite
        c_acc = 100.0 if res.customer_match else 0.0
        a_acc = 100.0 if res.address_match else 0.0
        rec = (res.matched_items_count / res.expected_items_count * 100.0) if res.expected_items_count else 100.0
        q_acc = (res.qty_matches / res.matched_items_count * 100.0) if res.matched_items_count else 100.0
        p_acc = (res.price_matches / res.matched_items_count * 100.0) if res.matched_items_count else 100.0
        composite = (c_acc + a_acc + rec + q_acc + p_acc) / 5.0

        print(f"\n--- {f.name} (id: {res.case_id}) ---")
        print(f"  Composite Score: {composite:.1f}% (time: {res.duration_s:.2f}s)")
        print(f"  Customer Match: {res.customer_match} | Address Match: {res.address_match}")
        print(f"  Items: {res.matched_items_count}/{res.expected_items_count} matched | Qty: {res.qty_matches}/{res.matched_items_count} | Price: {res.price_matches}/{res.matched_items_count} | Kind: {res.kind_matches}/{res.matched_items_count}")

        if res.errors:
            print("  FAILURES / MISMATCHES:")
            for err in res.errors:
                print(f"    * {err}")
            print("  EXPECTED ITEMS:")
            for item in expected_job.items:
                print(f"    - '{item.description}' ({item.kind}): qty={item.qty}, price={item.unit_price}")
            print("  EXTRACTED ITEMS:")
            for item in extracted_job.items:
                print(f"    - '{item.description}' ({item.kind}): qty={item.qty}, price={item.unit_price}")

    # Summary for group
    tot = len(results)
    if tot > 0:
        c_tot = sum(1 for r in results if r.customer_match) / tot * 100
        a_tot = sum(1 for r in results if r.address_match) / tot * 100
        exp_tot = sum(r.expected_items_count for r in results)
        mat_tot = sum(r.matched_items_count for r in results)
        rec_tot = (mat_tot / exp_tot * 100) if exp_tot else 0
        ext_tot = sum(r.extracted_items_count for r in results)
        prec_tot = (mat_tot / ext_tot * 100) if ext_tot else 0
        q_tot = (sum(r.qty_matches for r in results) / mat_tot * 100) if mat_tot else 0
        p_tot = (sum(r.price_matches for r in results) / mat_tot * 100) if mat_tot else 0
        comp_tot = (c_tot + a_tot + rec_tot + q_tot + p_tot) / 5
        print(f"\n>> GROUP SUMMARY ({label}):")
        print(f"  Customer: {c_tot:.1f}% | Address: {a_tot:.1f}% | Recall: {rec_tot:.1f}% | Precision: {prec_tot:.1f}%")
        print(f"  Qty: {q_tot:.1f}% | Price: {p_tot:.1f}% | Composite: {comp_tot:.1f}%")
        return comp_tot
    return 0.0


def main():
    settings = Settings(llm_model="qwen2.5:1.5b", ollama_host="http://localhost:11434", llm_timeout_s=180)
    fixtures_dir = Path("tests/fixtures")
    train_files = sorted(f for f in fixtures_dir.glob("*.json") if f.is_file())
    holdout_dir = fixtures_dir / "holdout"
    all_holdouts = sorted(holdout_dir.glob("*.json"))
    old_holdouts = [f for f in all_holdouts if int(f.name.split("_")[1]) <= 8]
    new_holdouts = [f for f in all_holdouts if int(f.name.split("_")[1]) > 8]

    eval_detailed(train_files, settings, "TRAIN (All 15)")
    eval_detailed(old_holdouts, settings, "OLD HOLDOUT (8 fixtures: 01-08)")
    eval_detailed(new_holdouts, settings, "NEW HOLDOUT (3 fixtures: 09-11)")
    eval_detailed(all_holdouts, settings, "ALL HOLDOUT (11 fixtures: 01-11)")


if __name__ == "__main__":
    main()
