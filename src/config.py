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


def load_rates(path: str | Path | None) -> dict:
    if not path:
        return {}
    return json.loads(Path(path).read_text())
