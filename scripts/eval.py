"""Evaluation script for tradesperson transcript extraction accuracy and latency."""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from jobtalk.config import Settings
from jobtalk.extract import extract_job
from jobtalk.models import Job, LineItem
from jobtalk.pricing import apply_rates


def _normalize_text(s: str) -> str:
    return re.sub(r"[^\w\s]", "", s.lower()).strip()


def _token_overlap(a: str, b: str) -> float:
    tokens_a = set(_normalize_text(a).split())
    tokens_b = set(_normalize_text(b).split())
    if not tokens_a or not tokens_b:
        return 0.0
    return len(tokens_a & tokens_b) / max(len(tokens_a), len(tokens_b))


@dataclass
class CaseResult:
    case_id: str
    trade: str
    duration_s: float
    customer_match: bool
    address_match: bool
    summary_present: bool
    expected_items_count: int
    extracted_items_count: int
    matched_items_count: int
    qty_matches: int
    price_matches: int
    kind_matches: int
    errors: list[str] = field(default_factory=list)


def evaluate_fixture(
    fixture_path: Path, settings: Settings, verbose: bool = False
) -> CaseResult:
    data = json.loads(fixture_path.read_text())
    case_id = data["id"]
    trade = data.get("trade", "unknown")
    transcript = data["transcript"]
    rates = data.get("rates", {})
    expected_dict = data["expected"]
    expected_job = Job.from_dict(expected_dict)

    t0 = time.perf_counter()
    errors: list[str] = []
    try:
        extracted_job = extract_job(transcript, rates, settings)
        extracted_job = apply_rates(extracted_job, rates)
    except Exception as exc:  # noqa: BLE001
        duration_s = round(time.perf_counter() - t0, 3)
        return CaseResult(
            case_id=case_id,
            trade=trade,
            duration_s=duration_s,
            customer_match=False,
            address_match=False,
            summary_present=False,
            expected_items_count=len(expected_job.items),
            extracted_items_count=0,
            matched_items_count=0,
            qty_matches=0,
            price_matches=0,
            kind_matches=0,
            errors=[f"Extraction exception: {exc}"],
        )

    duration_s = round(time.perf_counter() - t0, 3)

    # Customer matching: case-insensitive normalized substring or match
    norm_exp_cust = _normalize_text(expected_job.customer)
    norm_ext_cust = _normalize_text(extracted_job.customer)
    customer_match = (
        norm_exp_cust in norm_ext_cust
        or norm_ext_cust in norm_exp_cust
        or _token_overlap(norm_exp_cust, norm_ext_cust) >= 0.5
    )
    if not customer_match:
        errors.append(f"Customer mismatch: expected '{expected_job.customer}', got '{extracted_job.customer}'")

    # Address matching
    norm_exp_addr = _normalize_text(expected_job.address)
    norm_ext_addr = _normalize_text(extracted_job.address)
    address_match = (
        norm_exp_addr in norm_ext_addr
        or norm_ext_addr in norm_exp_addr
        or _token_overlap(norm_exp_addr, norm_ext_addr) >= 0.5
    )
    if not address_match:
        errors.append(f"Address mismatch: expected '{expected_job.address}', got '{extracted_job.address}'")

    summary_present = bool(extracted_job.job_summary.strip())

    # Line item alignment
    unmatched_extracted = list(extracted_job.items)
    matched_pairs: list[tuple[LineItem, LineItem]] = []

    for exp_item in expected_job.items:
        best_idx = -1
        best_score = 0.0
        for i, ext_item in enumerate(unmatched_extracted):
            # Check kind compatibility and token overlap
            score = _token_overlap(exp_item.description, ext_item.description)
            if exp_item.kind.lower() == ext_item.kind.lower():
                score += 0.2
            if score > best_score:
                best_score = score
                best_idx = i

        if best_idx >= 0 and best_score >= 0.25:
            matched_pairs.append((exp_item, unmatched_extracted.pop(best_idx)))
        else:
            errors.append(f"Missing expected item: '{exp_item.description}' ({exp_item.kind})")

    for leftover in unmatched_extracted:
        errors.append(f"Extra extracted item: '{leftover.description}' ({leftover.kind})")

    qty_matches = 0
    price_matches = 0
    kind_matches = 0

    for exp_item, ext_item in matched_pairs:
        if exp_item.kind.lower() == ext_item.kind.lower():
            kind_matches += 1
        else:
            errors.append(
                f"Kind mismatch on '{exp_item.description}': expected '{exp_item.kind}', got '{ext_item.kind}'"
            )

        if abs(exp_item.qty - ext_item.qty) <= 0.05:
            qty_matches += 1
        else:
            errors.append(
                f"Qty mismatch on '{exp_item.description}': expected {exp_item.qty}, got {ext_item.qty}"
            )

        # Price matching: both None or numerical match within 0.01
        exp_p = exp_item.unit_price
        ext_p = ext_item.unit_price
        if (exp_p is None and ext_p is None) or (
            exp_p is not None and ext_p is not None and abs(exp_p - ext_p) <= 0.01
        ):
            price_matches += 1
        else:
            errors.append(
                f"Price mismatch on '{exp_item.description}': expected {exp_p}, got {ext_p}"
            )

    if verbose and errors:
        print(f"\n[{case_id}] ({trade}) Errors:")
        for err in errors:
            print(f"  - {err}")

    return CaseResult(
        case_id=case_id,
        trade=trade,
        duration_s=duration_s,
        customer_match=customer_match,
        address_match=address_match,
        summary_present=summary_present,
        expected_items_count=len(expected_job.items),
        extracted_items_count=len(extracted_job.items),
        matched_items_count=len(matched_pairs),
        qty_matches=qty_matches,
        price_matches=price_matches,
        kind_matches=kind_matches,
        errors=errors,
    )


def run_eval(
    model: str,
    host: str,
    fixtures_dir: Path,
    verbose: bool = False,
    timeout_s: int = 180,
) -> dict:
    settings = Settings(llm_model=model, ollama_host=host, llm_timeout_s=timeout_s)
    files = sorted(fixtures_dir.glob("*.json"))
    if not files:
        print(f"No fixtures found in {fixtures_dir}")
        return {}

    print("\n=======================================================")
    print(f"Evaluating model: {model} on {len(files)} fixtures")
    print(f"Host: {host}")
    print("=======================================================")

    results: list[CaseResult] = []
    for f in files:
        print(f"Evaluating {f.name} ... ", end="", flush=True)
        res = evaluate_fixture(f, settings, verbose=verbose)
        status = "OK" if not res.errors else f"{len(res.errors)} issues"
        print(f"{status} ({res.duration_s:.2f}s)")
        results.append(res)

    total_fixtures = len(results)
    total_time = sum(r.duration_s for r in results)
    avg_latency = total_time / total_fixtures if total_fixtures else 0

    customer_acc = sum(1 for r in results if r.customer_match) / total_fixtures * 100
    address_acc = sum(1 for r in results if r.address_match) / total_fixtures * 100

    total_exp_items = sum(r.expected_items_count for r in results)
    total_ext_items = sum(r.extracted_items_count for r in results)
    total_matched_items = sum(r.matched_items_count for r in results)

    item_recall = (total_matched_items / total_exp_items * 100) if total_exp_items else 0
    item_precision = (total_matched_items / total_ext_items * 100) if total_ext_items else 0

    total_qty_matches = sum(r.qty_matches for r in results)
    qty_acc = (total_qty_matches / total_matched_items * 100) if total_matched_items else 0

    total_price_matches = sum(r.price_matches for r in results)
    price_acc = (total_price_matches / total_matched_items * 100) if total_matched_items else 0

    total_kind_matches = sum(r.kind_matches for r in results)
    kind_acc = (total_kind_matches / total_matched_items * 100) if total_matched_items else 0

    # Composite accuracy
    composite_acc = (customer_acc + address_acc + item_recall + qty_acc + price_acc) / 5

    summary = {
        "model": model,
        "fixtures_count": total_fixtures,
        "total_time_s": round(total_time, 2),
        "avg_latency_s": round(avg_latency, 2),
        "customer_accuracy": round(customer_acc, 1),
        "address_accuracy": round(address_acc, 1),
        "item_recall": round(item_recall, 1),
        "item_precision": round(item_precision, 1),
        "kind_accuracy": round(kind_acc, 1),
        "qty_accuracy": round(qty_acc, 1),
        "price_accuracy": round(price_acc, 1),
        "composite_accuracy": round(composite_acc, 1),
    }

    print("\n---------------- RESULTS SUMMARY ----------------")
    print(f"Model:                {model}")
    print(f"Average Latency:      {avg_latency:.2f} s/quote (Total: {total_time:.1f} s)")
    print(f"Customer Accuracy:    {customer_acc:.1f}%")
    print(f"Address Accuracy:     {address_acc:.1f}%")
    print(f"Item Recall:          {item_recall:.1f}% ({total_matched_items}/{total_exp_items})")
    print(f"Item Precision:       {item_precision:.1f}% ({total_matched_items}/{total_ext_items})")
    print(f"Item Kind Accuracy:   {kind_acc:.1f}%")
    print(f"Quantity Accuracy:    {qty_acc:.1f}% ({total_qty_matches}/{total_matched_items})")
    print(f"Price Accuracy:       {price_acc:.1f}% ({total_price_matches}/{total_matched_items})")
    print(f"Composite Accuracy:   {composite_acc:.1f}%")
    print("-------------------------------------------------\n")

    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate JobTalk extraction accuracy.")
    parser.add_argument("--model", default="qwen2.5:3b", help="Ollama model name")
    parser.add_argument("--host", default="http://localhost:11434", help="Ollama host URL")
    parser.add_argument(
        "--fixtures",
        default=str(Path(__file__).parent.parent / "tests" / "fixtures"),
        help="Path to directory containing JSON fixtures",
    )
    parser.add_argument("--verbose", "-v", action="store_true", help="Print detailed error diffs")
    parser.add_argument("--json", action="store_true", help="Output summary as JSON")
    args = parser.parse_args(argv)

    summary = run_eval(
        model=args.model,
        host=args.host,
        fixtures_dir=Path(args.fixtures),
        verbose=args.verbose,
    )
    if args.json:
        print(json.dumps(summary, indent=2))
    return 0 if summary.get("composite_accuracy", 0) >= 60 else 1


if __name__ == "__main__":
    sys.exit(main())
