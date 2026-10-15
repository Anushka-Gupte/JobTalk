"""Transcript -> structured Job, using a local LLM served by Ollama."""
from __future__ import annotations

import json
import urllib.request
from collections.abc import Callable

from jobtalk.config import Settings
from jobtalk.models import Job

SYSTEM_PROMPT = """You turn a tradesperson's spoken job description into JSON.
Return ONLY JSON with keys: customer, address, job_summary, notes,
items (list of {description, kind: "labor"|"material", qty: number, unit_price: number or null}).
Rules: use ONLY facts in the transcript or the price list. If a price was not
spoken and is not in the price list, set unit_price to null. Never invent
prices, names or addresses; use "" if unknown. Do not compute totals."""

ChatFn = Callable[[str, str, Settings], str]


def ollama_chat(system: str, user: str, settings: Settings) -> str:
    body = json.dumps({
        "model": settings.llm_model,
        "stream": False,
        "format": "json",
        "options": {"temperature": 0},
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
    }).encode()
    req = urllib.request.Request(
        f"{settings.ollama_host}/api/chat", body, {"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=settings.llm_timeout_s) as resp:
        return json.loads(resp.read())["message"]["content"]


def extract_job(transcript: str, rates: dict, settings: Settings | None = None,
                chat: ChatFn = ollama_chat) -> Job:
    """`chat` is injectable so tests (and other backends) don't need a live LLM."""
    if settings is None:
        settings = Settings()
    user = f"Price list: {json.dumps(rates)}\n\nTranscript:\n{transcript}"
    try:
        raw = chat(SYSTEM_PROMPT, user, settings)
    except OSError as exc:
        raise RuntimeError(
            f"Could not reach Ollama at {settings.ollama_host}. Is it running?"
        ) from exc
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError("Model did not return valid JSON. Try again or use a larger model.") from exc
    return Job.from_dict(data)
