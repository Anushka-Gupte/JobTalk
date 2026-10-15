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
                          pricing.py  (price list fill-in + all math, in code)
                               │
                               ▼
                          render.py + templates/quote.html ──► quote.html / PDF
```

## Design decisions

- **LLM extracts, code calculates.** Small models are unreliable at arithmetic, so
  totals and tax never come from the model.
- **No guessed prices.** Unknown prices stay `None`, are filled only from the user's
  own `rates.json`, and are flagged loudly in the quote if still missing.
- **Injectable LLM backend.** `extract_job(chat=...)` lets tests, or other runtimes
  (llama.cpp, LM Studio), replace Ollama without touching the rest.
- **Zero runtime dependencies for the core.** Only audio needs `faster-whisper`.
  Everything else is the standard library, so it installs anywhere, offline.

## Roadmap

1. Phone-friendly web UI (record, edit lines, send).
2. Fine-tuned or few-shot prompts per trade (plumbing, electrical, HVAC).
3. Direct PDF export, customer database, quote history.
