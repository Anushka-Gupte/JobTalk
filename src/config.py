"""Settings and price-list loading."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    llm_model: str = "qwen2.5:3b"
    ollama_host: str = "http://localhost:11434"
    whisper_model: str = "base.en"
    llm_timeout_s: int = 300
    ollama_keep_alive: str = "15m"
    ollama_num_ctx: int = 2048
    ollama_num_predict: int = 512
    whisper_initial_prompt: str = (
        "Plumbing, electrical, and HVAC trade terms: P-trap, GFCI, Romex, PEX, flux, "
        "conduit, subpanel, capacitor, refrigerant R-410A, sillcock, CSST, Fernco, shutoff valve."
    )


def load_rates(path: str | Path | None) -> dict:
    if not path:
        return {}
    return json.loads(Path(path).read_text())
