# Architecture

```
audio ──► transcribe.py ──► transcript
                                │
                                ▼
                           extract.py  (local LLM via Ollama → JSON)
                                │
                                ▼
                           models.Job.from_dict  (tolerant validation)
                                │
                                ▼
                           pricing.py  (price list fill-in + GST & all math, in code)
                                │
                                ▼
                           money.py  (Indian digit grouping format ₹1,23,456.00)
                                │
                                ▼
                           render.py + quote.html ──► quote.html / Web UI / PDF
```

## Design decisions

- **LLM extracts, code calculates.** Small models are unreliable at arithmetic, so
  totals, GST, and line totals never come from the model.
- **Single money formatting module (`src/money.py`).** The UI, CLI, and backend share one
  source of truth for formatting with Indian numbering logic (`₹1,23,456.00`). JavaScript
  never calculates or formats money.
- **GST in `pricing.py`.** Standard 18% GST with optional 50/50 CGST and SGST split
  computed strictly in Python.
- **No guessed prices.** Unknown prices stay `None`, are filled only from the user's
  own `rates.json`, and are flagged loudly in the quote if still missing.
- **Injectable LLM backend.** `extract_job(chat=...)` and `stream_extract_job(stream_chat=...)`
  let tests replace Ollama without touching the rest.
- **Zero runtime dependencies for the core.** Only audio needs `faster-whisper` and the web UI
  uses optional `fastapi` + `uvicorn`. Everything else is standard library.
