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
ollama pull qwen2.5:3b
jobtalk job.m4a --business "Dave's Plumbing" --tax 0.08 --rates examples/rates.json
jobtalk examples/sample_transcript.txt --rates examples/rates.json
```
Open `output/quote.html` in a browser and print to PDF.

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
