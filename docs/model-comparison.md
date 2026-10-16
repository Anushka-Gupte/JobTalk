# Model Comparison: On-Device LLMs for Voice-to-Quote Extraction

## Overview
To provide a reliable, 100% offline experience for tradespeople without requiring a high-end cloud GPU or paid API key, JobTalk runs local LLMs via Ollama.

We benchmarked model performance across **18 realistic tradesperson transcripts** covering plumbing, electrical, and HVAC tasks:
- **Train split**: 10 fixtures (`tests/fixtures/`) used during prompt design.
- **Holdout split**: 8 unseen fixtures (`tests/fixtures/holdout/`) written independently without inspecting the prompt.

The test set incorporates messy spoken speech: fillers ("uh", "um"), spontaneous self-corrections ("two hours, no, make that two and a half"), numbers spoken as words ("two and a half", "three quarters of an hour"), and unpriced items.

Testing was conducted using `scripts/eval.py` on standard CPU hardware with Ollama 0.40.4.

---

## Benchmark Results Matrix

| Metric | `qwen2.5:1.5b` | `qwen2.5:3b` | `llama3.2:3b` | `mistral:latest` (7.2B, 600s timeout) | `mistral:latest` (180s timeout)* |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Parameters** | 1.5B | 3.1B | 3.2B | 7.2B | 7.2B |
| **Disk Size** | **986 MB** | 1.9 GB | 2.0 GB | 4.4 GB | 4.4 GB |
| **Peak RAM** | **~2.2 GB** | ~3.2 GB | ~3.4 GB | ~5.8 GB | ~5.8 GB |
| **Cold-Start Latency** | **40.98 s** | 87.17 s | 94.82 s | 476.23 s (~8 min) | > 180 s (aborted) |
| **Warm Avg Latency / Quote** | **24.69 s** | 37.61 s | 38.41 s | 150.87 s (~2.5 min) | 167.22 s (aborted) |
| **Train Accuracy (10)** | 93.4% | 93.3% | **95.1%** | 94.7% | 56.5%* |
| **Holdout Accuracy (8)** | 91.5% | 91.5% | **92.2%** | — | — |
| **Generalization Gap** | **+1.9%** | **+1.8%** | +2.9% | — | — |
| **Composite Accuracy** | 92.5% | 92.4% | **93.7%** | 94.7% (train only) | 56.5%* (timeout-affected) |

*\*Earlier numbers for `mistral:latest` were artificially lowered by 180s timeouts on 6 out of 10 fixtures during CPU inference. When re-run with a 600s timeout, `mistral:latest` achieved 94.7% accuracy, but required ~183s per quote on CPU and nearly 8 minutes on cold-start.*

---

## Detailed Findings

### 1. Latency & Target Under 15s
- **Latency Optimizations Applied**:
  - `keep_alive = "15m"`: Keeps the model weights resident in system RAM between dictations, avoiding repeated cold-load overhead.
  - `num_ctx = 2048` (reduced from 4096+): Lowers KV cache allocation and CPU attention computation time.
  - `num_predict = 512`: Clamps maximum generated tokens to prevent runaway loops.
- **Warm Latency Results**:
  - `qwen2.5:1.5b` achieves an average warm latency of **24.69 s/quote**, with individual short jobs finishing in **17–19 s**.
  - `llama3.2:3b` and `qwen2.5:3b` deliver ~**37–38 s/quote**.
  - On pure CPU inference on portable quad-core laptops, a sustained sub-15s average requires either GPU/NPU hardware offload or 4-bit lightweight quantized weights with trimmed prompt tokens. For tradespeople dictating in a van, 20–25s is practical and offline.

### 2. Generalization & Holdout Split Evaluation
- Evaluating on the 8 held-out fixtures (`tests/fixtures/holdout/`) confirmed that neither `qwen2.5` nor `llama3.2` overfitted to the training fixtures:
  - `qwen2.5:1.5b`: 93.4% Train vs 91.5% Holdout (**+1.9% gap**).
  - `qwen2.5:3b`: 93.3% Train vs 91.5% Holdout (**+1.8% gap**).
  - `llama3.2:3b`: 95.1% Train vs 92.2% Holdout (**+2.9% gap**).

### 3. Speech Recognition & Trade Vocabulary (`scripts/eval_audio.py`)
- We implemented a dedicated evaluation script for speech-to-text (`faster-whisper`) that computes Word Error Rate (Levenshtein distance) and trade term recall across trade-specific terms (`P-trap`, `GFCI`, `Romex`, `PEX`, `flux`, `capacitor`, `refrigerant`, `sillcock`, `CSST`, `Fernco`).
- We configured a Whisper `initial_prompt` containing these trade terms in `Settings.whisper_initial_prompt` and supported custom prompts in `jobtalk.transcribe.transcribe()`.
- The evaluation script is verified and ready to run against `.wav` or `.m4a` recordings supplied in `tests/audio/`.

### 4. Streaming Inference & JSON Schema Constrained Output (Phase 2a)
- **JSON Schema Output (`format: FORMAT_SCHEMA`)**:
  - Leverages Ollama's schema-constrained grammar sampling to guarantee structurally valid JSON adhering to `{customer, address, job_summary, notes, items}`.
  - Eliminated the need for verbose few-shot formatting tokens in `SYSTEM_PROMPT`.
  - Re-evaluating on all 18 fixtures confirmed accuracy is preserved: **93.1% Train / 92.1% Holdout** composite accuracy on `qwen2.5:1.5b`.
- **Perceived Latency via Streaming (`stream: true`)**:
  - Without streaming, the user faces a 20–25s blocking pause before seeing any output.
  - With `stream_extract_job`, token events begin arriving early, and complete line items (`("item", LineItem)`) are parsed and emitted via progressive JSON object scanning.
  - **Benchmark on `plumbing_01_water_heater.json` (`qwen2.5:1.5b`)**:
    - **Time to First Token**: ~2–15s (depending on warm KV cache).
    - **Time to First Line Item**: ~18–24s (line item populates in UI while remaining items continue streaming).
    - **Token Chunks Yielded**: ~195 chunks.
    - **Total Extraction Time**: ~25–30s.

---

## Recommendation & Default Model

1. **Default Model: `qwen2.5:1.5b`**:
   - Configured as default in `Settings.llm_model`.
   - Fastest CPU turnaround (**~24.7s warm latency**), smallest disk footprint (**986 MB**), lowest memory usage (**~2.2 GB RAM**), with **92.1% holdout accuracy**.
2. **High-Accuracy Option: `qwen2.5:3b` / `llama3.2:3b`**:
   - Stronger extraction of complex multi-part corrections at ~38s latency.

