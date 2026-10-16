# JobTalk

**Dictate a job, get a quote.** A plumber or electrician describes the work out loud on-site;
JobTalk turns it into a formatted, itemized quote. It runs fully offline on open models
(Whisper for speech, a small local LLM for extraction).

## Why open models
- **Offline:** works in basements and rural sites with no signal.
- **Private:** customer names, addresses and prices never leave the device.
- **Cheap:** no per-quote API fees, so a flat low price stays profitable.
- **Customizable:** your own price list, swappable models, fine-tunable per trade.

## Quick start

```bash
pip install -e ".[web,audio]"   # one-time setup
python run.py                   # checks Ollama, starts server, opens browser
```

Open **http://localhost:5000** — or on a phone on the same Wi-Fi, `http://<laptop-ip>:5000`.

> The first run downloads `qwen2.5:1.5b` (~1 GB) if it isn't already pulled.
> All settings (business name, tax rate, model, port) live in [`config.yaml`](config.yaml).

### CLI (no browser needed)

```bash
ollama pull qwen2.5:1.5b
jobtalk job.m4a --rates examples/rates.json
jobtalk examples/sample_transcript.txt --rates examples/rates.json
```

Open `output/quote.html` in a browser and print to PDF.

### LAN / phone access

```bash
python run.py --lan    # binds 0.0.0.0 — prints your LAN IP
```

> **No authentication is added.** Use only on a trusted network.

## Web UI features

- **SSE Streaming** — transcript appears immediately after Whisper; line items arrive one by one.
- **Editable table** — change description, kind, qty, price inline. **"+ Add missing line"** for items the model missed.
- **Python-only math** — all totals and tax computed server-side via `/api/recalculate`.
- **Unpriced guard** — highlighted rows block export until priced or explicitly accepted.
- **Fully offline** — no CDN fonts, no analytics, no external network calls.

### Phone browser note

`MediaRecorder` requires `https://` or `localhost`. Over plain HTTP on Wi-Fi the microphone
button is hidden automatically. **Workaround**: record in your phone's Voice Memos / Voice
Recorder app, then tap **Select Audio File** to upload.

## Project layout
```
backend/       FastAPI app (routes, SSE, export)
frontend/      Static HTML/CSS/JS — no build step
src/           Core pipeline: transcribe, extract, pricing, render, cli (installs as `jobtalk`)
tests/         pytest suite (no LLM or mic required)
examples/      Sample rates.json and transcript
docs/          Architecture and model benchmarks
config.yaml    Business settings (name, tax, model, port)
run.py         One-command launcher
```

## Development
```bash
python -m pytest --timeout=60   # full offline suite
ruff check src tests scripts backend run.py
```

### Evaluation & Model Benchmarks
Run the automated accuracy benchmark on 10 realistic tradesperson fixtures:
```bash
python scripts/eval.py --model qwen2.5:3b --verbose
```
See [docs/model-comparison.md](docs/model-comparison.md) for full benchmarks and latency analysis.

## License
MIT
