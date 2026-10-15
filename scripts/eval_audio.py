"""Evaluate real audio transcription (faster-whisper) and end-to-end JobTalk extraction."""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from jobtalk.config import Settings
from jobtalk.extract import extract_job
from jobtalk.models import Job
from jobtalk.pricing import apply_rates
from jobtalk.transcribe import transcribe

TRADE_KEYWORDS = [
    "p-trap",
    "gfci",
    "romex",
    "pex",
    "flux",
    "conduit",
    "subpanel",
    "capacitor",
    "refrigerant",
    "sillcock",
    "csst",
    "fernco",
    "shutoff valve",
    "breaker",
    "manometer",
    "coupling",
    "flange",
    "drain",
    "thermostat",
    "c-wire",
]


def normalize_words(text: str) -> list[str]:
    cleaned = re.sub(r"[^\w\s]", " ", text.lower())
    return [w for w in cleaned.split() if w]


def compute_wer(reference: str, hypothesis: str) -> float:
    """Compute Word Error Rate (WER) using dynamic programming Levenshtein distance."""
    r_words = normalize_words(reference)
    h_words = normalize_words(hypothesis)

    if not r_words:
        return 0.0 if not h_words else 1.0

    n = len(r_words)
    m = len(h_words)

    # dp[i][j] = distance between r_words[:i] and h_words[:j]
    dp = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n + 1):
        dp[i][0] = i
    for j in range(m + 1):
        dp[0][j] = j

    for i in range(1, n + 1):
        for j in range(1, m + 1):
            if r_words[i - 1] == h_words[j - 1]:
                dp[i][j] = dp[i - 1][j - 1]
            else:
                sub = dp[i - 1][j - 1] + 1
                ins = dp[i][j - 1] + 1
                delete = dp[i - 1][j] + 1
                dp[i][j] = min(sub, ins, delete)

    return dp[n][m] / n


def evaluate_trade_terms(
    reference: str, hypothesis: str, terms: list[str] | None = None
) -> dict[str, float]:
    """Calculate recall of trade terms found in reference vs hypothesis."""
    vocab = terms or TRADE_KEYWORDS
    ref_norm = " " + " ".join(normalize_words(reference)) + " "
    hyp_norm = " " + " ".join(normalize_words(hypothesis)) + " "

    expected_terms = [t for t in vocab if " " + " ".join(normalize_words(t)) + " " in ref_norm]
    if not expected_terms:
        return {"expected_count": 0, "found_count": 0, "recall": 100.0}

    found = [t for t in expected_terms if " " + " ".join(normalize_words(t)) + " " in hyp_norm]
    recall = (len(found) / len(expected_terms)) * 100
    return {
        "expected_count": len(expected_terms),
        "found_count": len(found),
        "recall": round(recall, 1),
    }


@dataclass
class AudioEvalResult:
    audio_file: str
    duration_s: float
    raw_wer: float
    prompted_wer: float
    raw_trade_recall: float
    prompted_trade_recall: float
    extracted_items_count: int
    expected_items_count: int


def run_audio_eval(
    audio_dir: Path,
    model: str = "qwen2.5:3b",
    whisper_model: str = "base.en",
    host: str = "http://localhost:11434",
) -> list[AudioEvalResult]:
    audio_files = sorted(
        [p for p in audio_dir.iterdir() if p.suffix.lower() in {".wav", ".m4a", ".mp3", ".ogg"}]
    )
    if not audio_files:
        print(f"\n[INFO] No audio files (.wav, .m4a) found in {audio_dir}.")
        print("Script is ready and verified. Once recordings are added, rerun this script to evaluate.")
        return []

    settings = Settings(llm_model=model, whisper_model=whisper_model, ollama_host=host)
    results: list[AudioEvalResult] = []

    print("\n=======================================================")
    print(f"Evaluating {len(audio_files)} audio recording(s)")
    print(f"Audio directory: {audio_dir}")
    print(f"Whisper Model:   {whisper_model}")
    print(f"LLM Model:       {model}")
    print("=======================================================")

    for audio_path in audio_files:
        expected_path = audio_path.with_suffix(".expected.json")
        if not expected_path.exists():
            # Try file.stem.expected.json
            alt_expected = audio_path.parent / f"{audio_path.stem}.expected.json"
            if alt_expected.exists():
                expected_path = alt_expected
            else:
                print(f"Skipping {audio_path.name}: matching .expected.json not found.")
                continue

        data = json.loads(expected_path.read_text())
        ref_transcript = data.get("transcript", "")
        rates = data.get("rates", {})
        expected_job = Job.from_dict(data.get("expected", {}))

        t0 = time.perf_counter()

        # 1. Transcribe without initial_prompt (raw baseline)
        raw_transcript = transcribe(audio_path, settings, initial_prompt="")

        # 2. Transcribe with trade-term initial_prompt
        prompted_transcript = transcribe(audio_path, settings, initial_prompt=settings.whisper_initial_prompt)

        duration_s = time.perf_counter() - t0

        raw_wer = compute_wer(ref_transcript, raw_transcript)
        prompted_wer = compute_wer(ref_transcript, prompted_transcript)

        raw_trade = evaluate_trade_terms(ref_transcript, raw_transcript)
        prompted_trade = evaluate_trade_terms(ref_transcript, prompted_transcript)

        # 3. End-to-end extraction from prompted transcript
        extracted_job = apply_rates(extract_job(prompted_transcript, rates, settings), rates)

        res = AudioEvalResult(
            audio_file=audio_path.name,
            duration_s=round(duration_s, 2),
            raw_wer=round(raw_wer * 100, 1),
            prompted_wer=round(prompted_wer * 100, 1),
            raw_trade_recall=raw_trade["recall"],
            prompted_trade_recall=prompted_trade["recall"],
            extracted_items_count=len(extracted_job.items),
            expected_items_count=len(expected_job.items),
        )
        results.append(res)

        print(f"\n[{audio_path.name}] ({duration_s:.1f}s)")
        print(f"  WER: {res.raw_wer}% (raw) -> {res.prompted_wer}% (with trade prompt)")
        print(f"  Trade Term Recall: {res.raw_trade_recall}% -> {res.prompted_trade_recall}%")
        print(f"  Extracted Line Items: {res.extracted_items_count}/{res.expected_items_count}")

    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate audio transcription and quote extraction.")
    parser.add_argument(
        "--audio-dir",
        default="tests/audio",
        help="Directory containing .wav/.m4a files and .expected.json files",
    )
    parser.add_argument("--model", default="qwen2.5:3b", help="Ollama LLM model name")
    parser.add_argument("--whisper-model", default="base.en", help="faster-whisper model")
    parser.add_argument("--host", default="http://localhost:11434", help="Ollama host URL")
    args = parser.parse_args(argv)

    audio_dir = Path(args.audio_dir)
    audio_dir.mkdir(parents=True, exist_ok=True)

    results = run_audio_eval(
        audio_dir=audio_dir,
        model=args.model,
        whisper_model=args.whisper_model,
        host=args.host,
    )

    if not results:
        return 0

    avg_raw_wer = sum(r.raw_wer for r in results) / len(results)
    avg_prompted_wer = sum(r.prompted_wer for r in results) / len(results)
    avg_trade_recall = sum(r.prompted_trade_recall for r in results) / len(results)

    print("\n---------------- AUDIO BENCHMARK SUMMARY ----------------")
    print(f"Files Evaluated:          {len(results)}")
    print(f"Average Baseline WER:     {avg_raw_wer:.1f}%")
    print(f"Average Prompted WER:     {avg_prompted_wer:.1f}%")
    print(f"Trade Term Recall:        {avg_trade_recall:.1f}%")
    print("---------------------------------------------------------\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
