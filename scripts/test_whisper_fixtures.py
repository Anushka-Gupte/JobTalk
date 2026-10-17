"""Test qwen2.5:1.5b on Whisper-style fixtures with and without number normalizer."""
from __future__ import annotations

import json
import re
from pathlib import Path

from jobtalk.config import Settings
from jobtalk.extract import extract_job
from jobtalk.models import Job
from jobtalk.pricing import apply_rates

# Devanagari digit mapping
DEVANAGARI_DIGITS = {
    '०': '0', '१': '1', '२': '2', '३': '3', '४': '4',
    '५': '5', '६': '6', '७': '7', '८': '8', '९': '9'
}

def normalize_numbers(text: str) -> str:
    """Deterministic pre-LLM normalizer for Devanagari numerals and common Hinglish number variants."""
    # 1. Translate Devanagari digits to ASCII digits
    res = text
    for dev, asc in DEVANAGARI_DIGITS.items():
        res = res.replace(dev, asc)

    # 2. Inconsistent romanization of multipliers:
    # 'paanch sau', 'panch sau', '5 sau' -> 500
    # Common words: ek=1, do=2, teen=3, char/chaar=4, panch/paanch=5, chhe=6, saat=7, aath=8, nau=9, das=10
    word_map = {
        r'\b(?:paanch|panch)\s+sau\b': '500',
        r'\b(?:chaar|char)\s+sau\b': '400',
        r'\bteen\s+sau\b': '300',
        r'\bdo\s+sau\b': '200',
        r'\bek\s+sau\b': '100',
        r'(\d+)\s+sau\b': lambda m: str(int(m.group(1)) * 100),
        r'\b(?:paanch|panch)\s+haza?ar\b': '5000',
        r'\b(?:chaar|char)\s+haza?ar\b': '4000',
        r'\bteen\s+haza?ar\b': '3000',
        r'\bdo\s+haza?ar\b': '2000',
        r'\bek\s+haza?ar\b': '1000',
        r'(\d+)\s+haza?ar\b': lambda m: str(int(m.group(1)) * 1000),
    }

    for pat, rep in word_map.items():
        res = re.sub(pat, rep, res, flags=re.IGNORECASE)

    return res


def test_fixture(path: Path, settings: Settings, apply_norm: bool = False):
    data = json.loads(path.read_text())
    transcript = data["transcript"]
    rates = data.get("rates", {})
    expected = Job.from_dict(data["expected"])

    if apply_norm:
        transcript = normalize_numbers(transcript)

    print(f"\n--- Testing {path.name} (Normalized: {apply_norm}) ---")
    print(f"Transcript: {transcript}")
    try:
        extracted = extract_job(transcript, rates, settings)
        extracted = apply_rates(extracted, rates)
    except Exception as exc:  # noqa: BLE001
        print(f"Extraction error: {exc}")
        return

    print("Expected items:")
    for item in expected.items:
        print(f"  - '{item.description}' ({item.kind}): qty={item.qty}, price={item.unit_price}")
    print("Extracted items:")
    for item in extracted.items:
        print(f"  - '{item.description}' ({item.kind}): qty={item.qty}, price={item.unit_price}")


def main():
    settings = Settings(llm_model="qwen2.5:1.5b", ollama_host="http://localhost:11434")
    fixtures = [
        Path("tests/fixtures/whisper_01_devanagari.json"),
        Path("tests/fixtures/whisper_02_inconsistent_romanization.json"),
        Path("tests/fixtures/whisper_03_mixed_script.json"),
    ]

    print("=================== RAW (WITHOUT NORMALIZER) ===================")
    for f in fixtures:
        test_fixture(f, settings, apply_norm=False)

    print("\n\n=================== WITH NUMBER NORMALIZER ===================")
    for f in fixtures:
        test_fixture(f, settings, apply_norm=True)


if __name__ == "__main__":
    main()
