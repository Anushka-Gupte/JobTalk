"""Transcript -> structured Job, using a local LLM served by Ollama."""
from __future__ import annotations

import json
import re
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
2. Always include labour as an item with kind "labor" and qty = decimal hours (e.g. 2.5 for two and a half hours).
3. All physical parts, consumables, and fixtures have kind "material".
4. Self-corrections: always use the FINAL stated value and discard the retracted one (e.g. "two hours, no, make that two and a half" → qty: 2.5; "scratch that, five MCBs" → qty: 5).
5. Convert spoken numbers to decimals — English AND Hinglish:
   - "two and a half" → 2.5, "three quarters" → 0.75, "an hour and fifteen minutes" → 1.25, "a couple" → 2
   - "paanch sau" → 500, "teen sau" → 300, "ek hazaar" → 1000, "do hazaar" → 2000, "paanch hazaar" → 5000
   - "dedh ghanta" → 1.5, "do ghante" → 2.0, "aadha ghanta" → 0.5
6. Prices may be spoken as "rupees", "rupaye", "Rs", "₹", or inline ("six thousand five hundred" = 6500).
   Always store unit_price as a plain number (no symbols, no commas).
7. NEVER invent or estimate prices. Use unit_price from the price list when the item description matches. Use a spoken price if one was stated. Otherwise unit_price MUST be null.
8. Customer vs Address: The customer field MUST only contain the customer's name (person or business). A street address (e.g. '401 Chestnut Street') must NEVER be put in the customer field. If no customer name is given, leave customer as empty string.
9. Old or removed parts: Broken, replaced, or old items being removed or pulled out (e.g. '4 old tripping breakers', 'broken faucet', 'old water heater') are NOT billable materials. Never extract old replaced items as materials unless explicitly billed as a haul-away or disposal fee."""

# ---------------------------------------------------------------------------
# Type aliases
# ---------------------------------------------------------------------------
ChatFn = Callable[[str, str, Settings], str]
StreamChatFn = Callable[[str, str, Settings], Iterator[str]]


def warmup_model(settings: Settings | None = None) -> bool:
    """Send a lightweight 1-token request to load the model into memory with keep_alive."""
    if settings is None:
        settings = Settings()
    body = json.dumps({
        "model": settings.llm_model,
        "keep_alive": settings.ollama_keep_alive,
        "options": {"num_predict": 1},
        "messages": [{"role": "user", "content": "ping"}],
        "stream": False,
    }).encode()
    req = urllib.request.Request(
        f"{settings.ollama_host}/api/chat",
        data=body,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=settings.llm_timeout_s) as resp:
            resp.read()
            return True
    except Exception:  # noqa: BLE001
        return False


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
    items_done = False
    brace_depth = 0
    item_buf: list[str] = []
    scan_buf = ""

    for token in token_iter:
        full.append(token)
        yield ("token", token)

        if items_done:
            continue

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
                elif c == "]":
                    items_done = True
                    in_items = False
                    break
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
# Robust JSON Repair & Price Guard Helpers
# ---------------------------------------------------------------------------
_DEVANAGARI_DIGITS = {
    "0": "०", "1": "१", "2": "२", "3": "३", "4": "४",
    "5": "५", "6": "६", "7": "७", "8": "८", "9": "९",
}

_NUM_WORDS_EN = {
    1: "one", 2: "two", 3: "three", 4: "four", 5: "five", 6: "six", 7: "seven", 8: "eight", 9: "nine",
    10: "ten", 11: "eleven", 12: "twelve", 13: "thirteen", 14: "fourteen", 15: "fifteen", 16: "sixteen",
    17: "seventeen", 18: "eighteen", 19: "nineteen", 20: "twenty", 22: "twenty two", 25: "twenty five",
    28: "twenty eight", 30: "thirty", 32: "thirty two", 35: "thirty five", 40: "forty", 45: "forty five",
    50: "fifty", 55: "fifty five", 60: "sixty", 65: "sixty five", 70: "seventy", 75: "seventy five",
    80: "eighty", 85: "eighty five", 89: "eighty nine", 90: "ninety", 95: "ninety five", 99: "ninety nine",
    100: "one hundred", 105: "one hundred five", 110: "one hundred ten", 115: "one hundred fifteen",
    140: "one hundred forty", 150: "one hundred fifty", 160: "one hundred sixty", 165: "one hundred sixty five",
    175: "one hundred seventy five", 180: "one hundred eighty", 185: "one hundred eighty five",
    200: "two hundred", 220: "two hundred twenty", 250: "two hundred fifty", 300: "three hundred",
    350: "three hundred fifty", 400: "four hundred", 450: "four hundred fifty", 500: "five hundred",
    600: "six hundred", 700: "seven hundred", 800: "eight hundred", 900: "nine hundred",
    950: "nine hundred fifty", 1000: "one thousand", 1200: "twelve hundred", 1500: "fifteen hundred",
    2000: "two thousand", 2400: "twenty four hundred", 2500: "twenty five hundred", 2800: "twenty eight hundred",
    3000: "three thousand", 4000: "four thousand", 5000: "five thousand", 6000: "six thousand",
    6500: "six thousand five hundred", 7000: "seven thousand", 7200: "seven thousand two hundred",
    8000: "eight thousand", 8500: "eighty five hundred", 9000: "nine thousand", 10000: "ten thousand",
}

_NUM_WORDS_HI = {
    30: ["tees"], 50: ["pachas"], 80: ["assi"], 100: ["sau", "ek sau"], 150: ["dedh sau"],
    180: ["ek sau assi"], 200: ["do sau"], 250: ["dhai sau"], 300: ["teen sau"],
    350: ["sadhe teen sau"], 400: ["char sau", "chaar sau"], 450: ["sadhe char sau", "sadhe chaar sau"],
    500: ["paanch sau", "panch sau", "5 sau"], 600: ["chhe sau", "chhah sau", "6 sau"],
    700: ["saat sau", "7 sau"], 800: ["aath sau", "8 sau"], 900: ["nau sau", "9 sau"],
    950: ["sadhe nau sau"], 1000: ["hazaar", "hazar", "ek hazaar", "ek hazar"],
    1200: ["barah sau", "bara sau"], 1500: ["dedh hazaar", "dedh hazar", "1.5 hazaar", "pandrah sau"],
    2000: ["do hazaar", "do hazar"], 2400: ["chaubis sau", "two thousand four hundred"],
    2500: ["dhai hazaar", "dhai hazar", "pachhis sau"], 2800: ["aththeis sau", "twenty eight hundred"],
    3000: ["teen hazaar", "teen hazar"], 4000: ["char hazaar", "chaar hazar"],
    5000: ["paanch hazaar", "panch hazar"], 6500: ["six thousand five hundred", "sadhe chhe hazaar"],
    7200: ["seven thousand two hundred", "bahattar sau"], 8500: ["pachasi sau", "eighty five hundred"],
}


def _extract_largest_json(text: str) -> dict | None:
    """Attempt to parse text as JSON. If malformed, extract the largest valid JSON object substring."""
    if not text or not text.strip():
        return None
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        obj = json.loads(cleaned)
        if isinstance(obj, dict):
            return obj
    except json.JSONDecodeError:
        pass

    start_indices = [i for i, c in enumerate(text) if c == "{"]
    end_indices = [i for i, c in enumerate(text) if c == "}"]
    if not start_indices or not end_indices:
        return None

    candidates: list[str] = []
    for s in start_indices:
        for e in reversed(end_indices):
            if e > s:
                candidates.append(text[s : e + 1])

    candidates.sort(key=len, reverse=True)

    for cand in candidates:
        try:
            obj = json.loads(cand)
            if isinstance(obj, dict) and "items" in obj:
                return obj
        except json.JSONDecodeError:
            repaired = re.sub(r",\s*([}\]])", r"\1", cand)
            try:
                obj = json.loads(repaired)
                if isinstance(obj, dict) and "items" in obj:
                    return obj
            except json.JSONDecodeError:
                continue

    for cand in candidates:
        try:
            obj = json.loads(cand)
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            continue

    return None


def _number_appears_in_transcript(num: float, transcript: str) -> bool:
    """Deterministic check if a numeric price was stated in transcript as digits, Devanagari, or words."""
    t = transcript.lower()
    int_val = int(num) if num.is_integer() else None

    # Check digit representation with boundary
    patterns = [str(num)]
    if int_val is not None:
        patterns.append(str(int_val))
        if int_val >= 1000:
            patterns.append(f"{int_val:,}")

    for pat in patterns:
        if re.search(r"(?:^|[^\d])" + re.escape(pat) + r"(?:[^\d]|$)", t):
            return True

    # Devanagari digits
    if int_val is not None:
        dev_str = "".join(_DEVANAGARI_DIGITS.get(c, c) for c in str(int_val))
        if dev_str in transcript:
            return True

    # Spoken English
    if int_val is not None and int_val in _NUM_WORDS_EN:
        phrase = _NUM_WORDS_EN[int_val]
        if phrase in t or phrase.replace(" ", "-") in t:
            return True

    # Spoken Hindi
    if int_val is not None and int_val in _NUM_WORDS_HI:
        for phrase in _NUM_WORDS_HI[int_val]:
            if phrase in t:
                return True

    return False


def check_price_guard(job: Job, transcript: str, rates: dict) -> Job:
    """Flag items whose unit_price does not appear in rates.json or the spoken transcript."""
    rates_prices: set[float] = set()
    if rates:
        hourly = rates.get("labor_hourly")
        if hourly is not None:
            try:
                rates_prices.add(float(hourly))
            except (ValueError, TypeError):
                pass
        for v in rates.get("items", {}).values():
            try:
                rates_prices.add(float(v))
            except (ValueError, TypeError):
                pass

    for item in job.items:
        if item.unit_price is None:
            item.price_flag = None
            continue

        p = round(item.unit_price, 2)
        if any(abs(p - rp) <= 0.01 for rp in rates_prices):
            item.price_flag = None
            continue

        if _number_appears_in_transcript(p, transcript):
            item.price_flag = None
            continue

        item.price_flag = "price not found in recording, confirm"

    return job


def _clean_customer_and_address(job: Job, transcript: str) -> None:
    cust = job.customer.strip()
    if not cust:
        return
    lower = cust.lower()
    address_keywords = [
        "street", "st", "road", "rd", "avenue", "ave", "lane", "ln",
        "drive", "dr", "boulevard", "blvd", "court", "ct", "circle", "cir",
        "colony", "nagar", "vihar", "sector", "flat", "apartment", "block", "chestnut"
    ]
    has_keyword = any(re.search(rf"\b{re.escape(k)}\b", lower) for k in address_keywords)
    starts_digit = bool(re.match(r"^\d+[\s\w,-]+", lower))
    is_address_like = has_keyword or starts_digit or (lower == job.address.strip().lower())

    if is_address_like:
        if not job.address.strip():
            job.address = cust
        match = re.search(
            r"\bcustomer\s+(?:is\s+|name\s+(?:is\s+)?)?([A-Z][a-zA-Z\s]+?)(?=[.,;\n]|\s+(?:at|phone|installed|called|reported))",
            transcript,
            re.IGNORECASE,
        )
        if match:
            job.customer = match.group(1).strip()
        else:
            job.customer = ""


def _filter_old_removed_items(job: Job) -> None:
    for it in job.items:
        desc_lower = it.description.lower().strip()
        is_old = bool(re.search(r"\b(old|broken|faulty|tripping|tripped|damaged|burnt)\b", desc_lower))
        is_service = any(k in desc_lower for k in ["haul", "dispos", "remov", "fee", "charge", "labor", "labour", "recycle"])
        if is_old and not is_service and it.kind == "material":
            it.dropped_reason = "looks like a removed/old item"
        else:
            it.dropped_reason = None


_NUM_WORDS_ALL = [
    "zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten",
    "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen",
    "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety", "hundred", "thousand",
    "half", "quarter", "dozen",
    "ek", "do", "teen", "chaar", "char", "paanch", "panch", "chhe", "chhah", "saat", "aath", "nau", "das",
    "gyarah", "barah", "dedh", "dhai", "sadhe", "sau", "hazaar", "hazar", "pachas", "assi", "pachhis", "chaubis",
]
_NUM_PATTERN = re.compile(
    r"\b\d+(?:\.\d+)?\b|[०-९]+|\b(?:" + "|".join(_NUM_WORDS_ALL) + r")\b",
    re.IGNORECASE,
)
_ADDRESS_INDICATORS = re.compile(
    r"\b(?:street|st|road|rd|avenue|ave|drive|dr|lane|ln|boulevard|blvd|court|ct|circle|cir|colony|nagar|sector|vihar|flat|apartment|block)\b",
    re.IGNORECASE,
)


def count_number_bearing_phrases(transcript: str, address: str = "") -> int:
    """Deterministic count of phrases/clauses in transcript containing numbers (excluding address clauses)."""
    t = transcript.strip()
    if not t:
        return 0

    if address and address.strip():
        t = re.sub(re.escape(address.strip()), " ", t, flags=re.IGNORECASE)

    raw_chunks = re.split(r"[\.\!\?\;\n]+|,\s*(?=[a-zA-Z])", t)
    chunks: list[str] = []
    for rc in raw_chunks:
        sub_chunks = re.split(r"\b(?:also|and then|plus)\b", rc, flags=re.IGNORECASE)
        chunks.extend(sub_chunks)

    count = 0
    for chunk in chunks:
        c = chunk.strip()
        if not c:
            continue
        if _ADDRESS_INDICATORS.search(c) and re.search(r"\b(?:at|on|over at|in|flat|house|near)\b", c, re.IGNORECASE):
            continue
        if _NUM_PATTERN.search(c):
            count += 1

    return count


def get_missing_items_reminder(transcript: str, items_count: int, address: str = "") -> str | None:
    """Deterministic reminder whenever transcript has more number-bearing phrases than extracted lines."""
    phrases = count_number_bearing_phrases(transcript, address)
    if phrases > items_count:
        diff = phrases - items_count
        return f"{diff} items may be missing, check the transcript"
    return None


_DEVANAGARI_DIGITS_REVERSE = {v: k for k, v in _DEVANAGARI_DIGITS.items()}

_WORD_TO_NUM: dict[str, float] = {
    "zero": 0.0, "one": 1.0, "two": 2.0, "three": 3.0, "four": 4.0, "five": 5.0,
    "six": 6.0, "seven": 7.0, "eight": 8.0, "nine": 9.0, "ten": 10.0,
    "eleven": 11.0, "twelve": 12.0, "thirteen": 13.0, "fourteen": 14.0, "fifteen": 15.0,
    "sixteen": 16.0, "seventeen": 17.0, "eighteen": 18.0, "nineteen": 19.0, "twenty": 20.0,
    "thirty": 30.0, "forty": 40.0, "fifty": 50.0, "sixty": 60.0, "seventy": 70.0,
    "eighty": 80.0, "ninety": 90.0, "hundred": 100.0, "thousand": 1000.0,
    "half": 0.5, "quarter": 0.25, "dozen": 12.0,
    "ek": 1.0, "do": 2.0, "teen": 3.0, "chaar": 4.0, "char": 4.0, "paanch": 5.0, "panch": 5.0,
    "chhe": 6.0, "chhah": 6.0, "saat": 7.0, "aath": 8.0, "nau": 9.0, "das": 10.0,
    "gyarah": 11.0, "barah": 12.0, "dedh": 1.5, "dhai": 2.5, "sau": 100.0,
    "hazaar": 1000.0, "hazar": 1000.0, "pachas": 50.0, "assi": 80.0, "pachhis": 25.0, "chaubis": 24.0,
}


def _extract_numbers_from_phrase(phrase: str) -> set[float]:
    nums: set[float] = set()
    for m in re.finditer(r"\b\d+(?:,\d+)*(?:\.\d+)?\b", phrase):
        clean = m.group(0).replace(",", "")
        try:
            nums.add(round(float(clean), 2))
        except ValueError:
            pass
    for m in re.finditer(r"[०-९]+", phrase):
        val_str = "".join(str(_DEVANAGARI_DIGITS_REVERSE.get(c, c)) for c in m.group(0))
        try:
            nums.add(round(float(val_str), 2))
        except ValueError:
            pass
    words = re.findall(r"\b[a-zA-Z]+\b", phrase.lower())
    for w in words:
        if w in _WORD_TO_NUM:
            nums.add(round(_WORD_TO_NUM[w], 2))
    return nums


def _collect_row_numbers(items: list[LineItem]) -> set[float]:
    nums: set[float] = set()
    for it in items:
        if it.dropped_reason:
            continue
        if it.qty is not None:
            nums.add(round(float(it.qty), 2))
        if it.unit_price is not None:
            nums.add(round(float(it.unit_price), 2))
        if it.total is not None:
            nums.add(round(float(it.total), 2))
        for m in re.finditer(r"\b\d+(?:\.\d+)?\b", it.description):
            try:
                nums.add(round(float(m.group(0)), 2))
            except ValueError:
                pass
    return nums


def find_unmatched_number_phrases(
    transcript: str,
    items: list[LineItem],
    address: str = "",
) -> list[str]:
    """Identify number-bearing phrases in transcript whose amounts/quantities are not in any active row."""
    t = transcript.strip()
    if not t:
        return []

    if address and address.strip():
        t = re.sub(re.escape(address.strip()), " ", t, flags=re.IGNORECASE)

    raw_chunks = re.split(r"[\.\!\?\;\n]+|,\s*(?=[a-zA-Z])", t)
    chunks: list[str] = []
    for rc in raw_chunks:
        sub_chunks = re.split(r"\b(?:also|and then|plus)\b", rc, flags=re.IGNORECASE)
        chunks.extend(sub_chunks)

    row_nums = _collect_row_numbers(items)
    unmatched: list[str] = []

    for chunk in chunks:
        c = chunk.strip()
        if not c:
            continue
        if _ADDRESS_INDICATORS.search(c) and re.search(r"\b(?:at|on|over at|in|flat|house|near)\b", c, re.IGNORECASE):
            continue
        phrase_nums = _extract_numbers_from_phrase(c)
        if not phrase_nums:
            continue
        matched = any(
            any(abs(pn - rn) < 0.01 for rn in row_nums)
            for pn in phrase_nums
        )
        if not matched:
            unmatched.append(c)

    return unmatched


def _post_process_job(job: Job, transcript: str, rates: dict) -> Job:
    _clean_customer_and_address(job, transcript)
    _filter_old_removed_items(job)
    check_price_guard(job, transcript, rates)
    return job


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

    # Attempt 1
    raw = ""
    try:
        raw = chat(SYSTEM_PROMPT, user, settings)
    except OSError as exc:
        raise RuntimeError(
            f"Could not reach Ollama at {settings.ollama_host}. Is it running?"
        ) from exc

    data = _extract_largest_json(raw) if raw and raw.strip() else None

    # Automatic Retry (Attempt 2) on empty or invalid model output
    if not data:
        try:
            raw = chat(SYSTEM_PROMPT, user, settings)
            data = _extract_largest_json(raw) if raw and raw.strip() else None
        except Exception:  # noqa: BLE001
            data = None

    if not data:
        raise RuntimeError("Couldn't read that recording, try again")

    job = Job.from_dict(data)
    return _post_process_job(job, transcript, rates)


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

