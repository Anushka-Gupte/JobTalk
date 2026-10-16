"""Transcript -> structured Job, using a local LLM served by Ollama."""
from __future__ import annotations

import json
import urllib.request
from collections.abc import Callable, Iterator

from jobtalk.config import Settings
from jobtalk.models import Job, LineItem

# ---------------------------------------------------------------------------
# Output schema for Ollama constrained decoding (Ollama 0.5+ / format: {...})
# ---------------------------------------------------------------------------
FORMAT_SCHEMA = {
    "type": "object",
    "required": ["customer", "address", "job_summary", "notes", "items"],
    "properties": {
        "customer": {"type": "string"},
        "address": {"type": "string"},
        "job_summary": {"type": "string"},
        "notes": {"type": "string"},
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["description", "kind", "qty", "unit_price"],
                "properties": {
                    "description": {"type": "string"},
                    "kind": {"type": "string", "enum": ["labor", "material"]},
                    "qty": {"type": "number"},
                    "unit_price": {"anyOf": [{"type": "number"}, {"type": "null"}]},
                },
            },
        },
    },
}

# Shortened system prompt: schema enforces structure, so we omit the few-shot example.
SYSTEM_PROMPT = """Extract billing facts from a tradesperson's spoken job description.
Return a JSON object with keys: customer, address, job_summary, notes, items.

CRITICAL RULES:
1. Extract EVERY material, part, fixture, and labor effort as a separate item. Never put billable items inside notes or job_summary.
2. Always include labor as an item with kind "labor" and qty = decimal hours (e.g. 2.5 for two and a half hours).
3. All physical parts, consumables, and fixtures have kind "material".
4. Self-corrections: always use the FINAL stated value and discard the retracted one (e.g. "two hours, no, make that two and a half" → qty: 2.5; "scratch that, five breakers" → qty: 5).
5. Convert spoken numbers to decimals (e.g. "two and a half" → 2.5, "three quarters" → 0.75, "an hour and fifteen minutes" → 1.25, "a couple" → 2).
6. NEVER invent or estimate prices. Use unit_price from the price list when the item description matches. Use a spoken price if one was stated. Otherwise unit_price MUST be null."""

# ---------------------------------------------------------------------------
# Type aliases
# ---------------------------------------------------------------------------
ChatFn = Callable[[str, str, Settings], str]
StreamChatFn = Callable[[str, str, Settings], Iterator[str]]


# ---------------------------------------------------------------------------
# Non-streaming Ollama client (with schema format)
# ---------------------------------------------------------------------------
def ollama_chat(system: str, user: str, settings: Settings) -> str:
    body = json.dumps({
        "model": settings.llm_model,
        "stream": False,
        "format": FORMAT_SCHEMA,
        "keep_alive": settings.ollama_keep_alive,
        "options": {
            "temperature": 0,
            "num_ctx": settings.ollama_num_ctx,
            "num_predict": settings.ollama_num_predict,
        },
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
    }).encode()
    req = urllib.request.Request(
        f"{settings.ollama_host}/api/chat", body, {"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=settings.llm_timeout_s) as resp:
        data = json.loads(resp.read())
        return data["message"]["content"]


# ---------------------------------------------------------------------------
# Streaming Ollama client
# ---------------------------------------------------------------------------
def ollama_stream_chat(system: str, user: str, settings: Settings) -> Iterator[str]:
    """Yield raw token strings as the model generates them."""
    body = json.dumps({
        "model": settings.llm_model,
        "stream": True,
        "format": FORMAT_SCHEMA,
        "keep_alive": settings.ollama_keep_alive,
        "options": {
            "temperature": 0,
            "num_ctx": settings.ollama_num_ctx,
            "num_predict": settings.ollama_num_predict,
        },
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
    }).encode()
    req = urllib.request.Request(
        f"{settings.ollama_host}/api/chat", body, {"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=settings.llm_timeout_s) as resp:
        for raw_line in resp:
            line = raw_line.strip()
            if not line:
                continue
            try:
                chunk = json.loads(line)
            except json.JSONDecodeError:
                continue
            content = chunk.get("message", {}).get("content", "")
            if content:
                yield content
            if chunk.get("done"):
                break


# ---------------------------------------------------------------------------
# Streaming item parser: state machine for progressive item detection
# ---------------------------------------------------------------------------
def _iter_events(token_iter: Iterator[str]) -> Iterator[tuple[str, object]]:
    """
    Drive token stream and yield events:
      ("token",   chunk_str)        - every raw token (for first-byte detection)
      ("item",    LineItem)         - each complete item object as it is parsed
      ("done",    full_json_str)    - once stream is finished
    """
    full: list[str] = []
    in_items = False
    brace_depth = 0
    item_buf: list[str] = []
    scan_buf = ""

    for token in token_iter:
        full.append(token)
        yield ("token", token)

        scan_buf += token

        # Detect transition into the "items": [ ... ] section
        if not in_items:
            marker = scan_buf.find('"items"')
            if marker != -1:
                bracket = scan_buf.find("[", marker)
                if bracket != -1:
                    in_items = True
                    scan_buf = scan_buf[bracket + 1:]  # keep only chars after '['
                else:
                    # Keep a small trailing window in case '[' arrives next token
                    scan_buf = scan_buf[marker:]
            else:
                # Keep a small trailing window to detect the marker across token boundaries
                scan_buf = scan_buf[-20:]
            continue  # nothing to scan yet

        # ----------------------------------------------------------------
        # We are inside the items array: scan character by character
        # ----------------------------------------------------------------
        for c in scan_buf:
            if brace_depth == 0:
                if c == "{":
                    brace_depth = 1
                    item_buf = ["{"]
                # else ignore (commas, whitespace, closing ']', etc.)
            else:
                item_buf.append(c)
                if c == "{":
                    brace_depth += 1
                elif c == "}":
                    brace_depth -= 1
                    if brace_depth == 0:
                        raw_item = json.loads("".join(item_buf))
                        kind = (
                            "labor"
                            if str(raw_item.get("kind", "")).lower() == "labor"
                            else "material"
                        )
                        try:
                            up = raw_item.get("unit_price")
                            item = LineItem(
                                description=str(raw_item.get("description", "")).strip(),
                                kind=kind,
                                qty=float(raw_item.get("qty") or 1.0),
                                unit_price=float(up) if up is not None else None,
                            )
                            yield ("item", item)
                        except (ValueError, TypeError):
                            pass
                        item_buf = []

        scan_buf = ""  # consumed all characters

    yield ("done", "".join(full))


# ---------------------------------------------------------------------------
# Public extraction functions
# ---------------------------------------------------------------------------
def extract_job(
    transcript: str,
    rates: dict,
    settings: Settings | None = None,
    chat: ChatFn = ollama_chat,
) -> Job:
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
        raise RuntimeError(
            "Model did not return valid JSON. Try again or use a larger model."
        ) from exc
    return Job.from_dict(data)


def stream_extract_job(
    transcript: str,
    rates: dict,
    settings: Settings | None = None,
    stream_chat: StreamChatFn = ollama_stream_chat,
) -> Iterator[tuple[str, object]]:
    """
    Streaming variant. Yields the same events as `_iter_events`:
      ("token",  chunk_str)   - raw token, enables first-byte UI feedback
      ("item",   LineItem)    - each complete line item as it is parsed
      ("done",   full_json)   - full JSON string; caller can build the Job

    Usage::

        for kind, payload in stream_extract_job(transcript, rates):
            if kind == "item":
                print(payload)          # LineItem available immediately
            elif kind == "done":
                job = Job.from_dict(json.loads(payload))
    """
    if settings is None:
        settings = Settings()
    user = f"Price list: {json.dumps(rates)}\n\nTranscript:\n{transcript}"
    try:
        token_iter = stream_chat(SYSTEM_PROMPT, user, settings)
        yield from _iter_events(token_iter)
    except OSError as exc:
        raise RuntimeError(
            f"Could not reach Ollama at {settings.ollama_host}. Is it running?"
        ) from exc
