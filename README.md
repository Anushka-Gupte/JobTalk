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
make install                 # pip install -e ".[audio,dev]"
make demo                    # no mic or LLM needed → output/quote.html
```

Real use (needs a one-time model download, then works offline):
```bash
ollama pull qwen2.5:1.5b
jobtalk job.m4a --business "Dave's Plumbing" --tax 0.08 --rates examples/rates.json
jobtalk examples/sample_transcript.txt --rates examples/rates.json
```
Open `output/quote.html` in a browser and print to PDF.

## Local Web UI (Browser & Phone)

Start the local web server:
```bash
jobtalk --serve --rates examples/rates.json --business "Dave's Plumbing" --tax 0.08
```
Then navigate to:
- **On this computer**: `http://localhost:5000`
- **On a phone or tablet (same Wi-Fi)**: `http://<laptop-ip>:5000` (e.g. `http://192.168.1.50:5000`)

### Features
- **Progressive SSE Streaming**: Spoken transcript appears immediately after Whisper finishes, followed by line items populating one by one in real-time as the local LLM generates them.
- **Editable Line Items**: Live editing of item description, kind (labor/material), quantity, and price. Includes an **"+ Add missing line"** button for missed billable items.
- **Live Recalculation**: All money math and tax computations run strictly in Python via `/api/recalculate`, never in client-side JavaScript.
- **Unpriced Line Protection**: Unpriced lines are flagged with visual badges. Quote export is blocked until prices are entered or the user explicitly checks "Accept unpriced items on quote".
- **Zero External Calls**: 100% offline, self-contained HTML/CSS/JS without external CDN fonts, scripts, or analytics.

### Mobile & Phone Browser Workaround
Modern phone browsers (iOS Safari and Android Chrome) restrict the Web Audio `MediaRecorder` API to secure contexts (`https://` or `localhost`). When accessing the web UI over local Wi-Fi HTTP (`http://192.168.x.x:5000`):
1. The browser microphone button is automatically hidden.
2. **Workaround**: Dictate your job into your phone's native **Voice Memos** (iOS) or **Voice Recorder** (Android) app.
3. Tap **Select Audio File** in JobTalk and select your recorded memo. Whisper and the local LLM process the audio completely offline.

## Project layout
```
src/           transcribe, extract, pricing, render, cli (installs as `jobtalk`)
tests/         pytest suite (no LLM or mic required)
examples/      sample rates.json and transcript
docs/          architecture and design decisions
```

## Development
```bash
make test    # pytest (runs offline unit tests)
make lint    # ruff check src tests scripts
```

### Evaluation & Model Benchmarks
Run the automated accuracy benchmark on 10 realistic tradesperson fixtures:
```bash
python scripts/eval.py --model qwen2.5:3b --verbose
```
See [docs/model-comparison.md](docs/model-comparison.md) for full benchmarks and latency analysis.

## License
MIT
