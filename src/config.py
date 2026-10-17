"""Settings and price-list loading."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    llm_model: str = "qwen2.5:1.5b"   # fast & accurate; use qwen2.5:3b for higher accuracy
    ollama_host: str = "http://localhost:11434"
    whisper_model: str = "base.en"
    whisper_language: str = "en"       # "en" for base.en; "hi" or "" for multilingual model
    llm_timeout_s: int = 300
    ollama_keep_alive: str = "15m"
    ollama_num_ctx: int = 2048
    ollama_num_predict: int = 512
    whisper_initial_prompt: str = (
        "Plumbing, electrical, and trade terms in India: MCB, ELCB, geyser, inverter, "
        "submersible pump, PVC pipe, copper wiring, tap washer, cistern, conduit, "
        "switchboard, earthing, RCCB, Havells, Finolex, Polycab, ISI mark."
    )

    # Currency
    currency_code: str = "INR"
    currency_symbol: str = "₹"

    # GST settings
    gst_rate: float = 0.0      # default 0% (opt-in; set to 0.18 for 18% GST)
    gst_split: bool = False    # True → show CGST + SGST as two equal halves
    gstin: str = ""            # Business GSTIN for quote header, e.g. "22AAAAA0000A1Z5"


def load_rates(path: str | Path | None) -> dict:
    if not path:
        return {}
    return json.loads(Path(path).read_text(encoding="utf-8"))
