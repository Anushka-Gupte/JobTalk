"""Transcript -> structured Job, using a local LLM served by Ollama."""
from __future__ import annotations

import json
import urllib.request
from collections.abc import Callable

from jobtalk.config import Settings
from jobtalk.models import Job

SYSTEM_PROMPT = """You extract structured job and quote facts from a tradesperson's spoken audio transcript.
Return ONLY a valid JSON object with exact keys:
- "customer": string (customer name, or "" if unknown)
- "address": string (service address, or "" if unknown)
- "job_summary": string (brief summary of work performed)
- "notes": string (general notes, observations, or access details; NOT line items)
- "items": list of objects {description: string, kind: "labor" | "material", qty: number, unit_price: number | null}

CRITICAL RULES:
1. LINE ITEMS: Every material, replacement part, consumable, fixture, and labor effort mentioned MUST be extracted as an item in "items". Do not leave parts or hours trapped inside "notes" or "job_summary".
2. LABOR: Always include labor as an item with description "Labor" (or specific service name), kind "labor", and qty = hours in decimal.
3. MATERIALS: Every physical part or material used or supplied must be kind "material".
4. SELF-CORRECTIONS & RETRACTIONS: Spoken speech frequently contains corrections (e.g. "two hours, no, make that two and a half", "four breakers, scratch that, five"). ALWAYS honor the final corrected number or retracted statement.
5. NUMBERS IN WORDS: Convert spoken numbers to decimal values (e.g. "two and a half" -> 2.5, "three quarters of an hour" -> 0.75, "an hour and a half" -> 1.5, "forty-five" -> 45, "a couple" -> 2).
6. NEVER GUESS PRICES: If a price is explicitly stated by the speaker or matches the price list, use it. If not spoken and not in price list, unit_price MUST be null. Never invent or estimate prices. Do not calculate line totals or overall totals.

FEW-SHOT EXAMPLE:
Price list: {"labor_hourly": 90, "items": {"shutoff valve": 25}}
Transcript:
"Hey, finished at Mrs. Higgins on 12 Oak Street. Replaced the water heater with a forty-gallon Rheem unit. Labor was two hours, no, make that two and a half hours total. Installed one new shutoff valve and used some copper fittings from the van."
Response:
{
  "customer": "Mrs. Higgins",
  "address": "12 Oak Street",
  "job_summary": "Replaced water heater and installed shutoff valve",
  "notes": "Used copper fittings from van.",
  "items": [
    {"description": "40-gallon Rheem water heater", "kind": "material", "qty": 1.0, "unit_price": null},
    {"description": "Brass shutoff valve", "kind": "material", "qty": 1.0, "unit_price": 25.0},
    {"description": "Copper pipe fittings", "kind": "material", "qty": 1.0, "unit_price": null},
    {"description": "Labor", "kind": "labor", "qty": 2.5, "unit_price": 90.0}
  ]
}"""

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
