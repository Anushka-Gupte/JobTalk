# Model Comparison: On-Device LLMs for Voice-to-Quote Extraction

## Overview
To provide a reliable, 100% offline experience for tradespeople without requiring a high-end cloud GPU or paid API key, JobTalk runs local LLMs via Ollama.

We benchmarked model performance across **29 realistic tradesperson transcripts** (expanded from 18, then 26) covering plumbing, electrical, and HVAC tasks in both standard English and Indian trade terminology with Hinglish phrasing and rupee pricing:
- **Train split**: 18 fixtures (`tests/fixtures/`) including 5 Indian trade fixtures with rupee pricing and Hinglish phrasing, plus 3 Whisper speech-variation fixtures (Devanagari, inconsistent romanization, mixed script).
- **Holdout split**: 11 unseen fixtures (`tests/fixtures/holdout/`) including 3 Indian holdout fixtures written independently without inspecting the prompt.

The test set incorporates messy spoken speech: fillers ("uh", "um"), spontaneous self-corrections ("two hours, no, make that two and a half"), numbers spoken as words ("two and a half", "three quarters of an hour", "paanch sau", "teen sau rupaye", "dedh ghanta"), and unpriced items.

Testing was conducted using `scripts/eval.py` on standard CPU hardware with Ollama 0.40.4.

---

## Benchmark Results Matrix

| Metric | `qwen2.5:1.5b` | `qwen2.5:3b` | `llama3.2:3b` | `mistral:latest` (7.2B, 600s timeout) | `mistral:latest` (180s timeout)* |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Parameters** | 1.5B | 3.1B | 3.2B | 7.2B | 7.2B |
| **Disk Size** | **986 MB** | 1.9 GB | 2.0 GB | 4.4 GB | 4.4 GB |
| **Peak RAM** | **~2.2 GB** ¹ | ~3.2 GB ¹ | ~3.4 GB ¹ | ~5.8 GB ¹ | ~5.8 GB ¹ |
| **Cold-Start Latency** | **40.98 s** | 87.17 s | 94.82 s | 476.23 s (~8 min) | > 180 s (aborted) |
| **Warm Avg Latency / Quote** | **23.38 s** | 37.61 s | 38.41 s | 150.87 s (~2.5 min) | 167.22 s (aborted) |
| **Train Accuracy** | **90.7%** (18 fixtures) | 93.3% (10) | **95.1%** (10) | 94.7% (10) | 56.5%* (10) |
| **Holdout Accuracy** | **91.7%** (11 fixtures) | 91.5% (8) | **92.2%** (8) | — | — |
| **Generalization Gap** | **-1.0%** | **+1.8%** | +2.9% | — | — |
| **Composite Accuracy** | **91.2%** | 92.4% | **93.7%** | 94.7% (train only) | 56.5%* (timeout-affected) |

*\*Earlier numbers for `mistral:latest` were artificially lowered by 180s timeouts on 6 out of 10 fixtures during CPU inference. When re-run with a 600s timeout, `mistral:latest` achieved 94.7% accuracy, but required ~183s per quote on CPU and nearly 8 minutes on cold-start.*

¹ **RAM figures are estimates, not measured with `scripts/eval_audio.py` or a profiler on this hardware.** They are derived from Ollama's reported model sizes and typical quantization overhead.

---

## Detailed Findings

### 1. Latency & Target Under 15s
- **Latency Optimizations Applied**:
  - `keep_alive = "15m"`: Keeps the model weights resident in system RAM between dictations, avoiding repeated cold-load overhead.
  - `num_ctx = 2048` (reduced from 4096+): Lowers KV cache allocation and CPU attention computation time.
  - `num_predict = 512`: Clamps maximum generated tokens to prevent runaway loops.
- **Warm Latency Results**:
  - `qwen2.5:1.5b` achieves an average warm latency of **23.38 s/quote** on train (27.64 s/quote on holdout), with individual short jobs finishing in **12–16 s**.
  - `llama3.2:3b` and `qwen2.5:3b` deliver ~**37–38 s/quote**.
  - On pure CPU inference on portable quad-core laptops, a sustained sub-15s average requires either GPU/NPU hardware offload or 4-bit lightweight quantized weights with trimmed prompt tokens. For tradespeople dictating in a van, 20–25s is practical and offline.

### 2. Generalization & Holdout Split Evaluation
- Evaluating on the held-out fixtures confirmed consistent generalization for `qwen2.5` and `llama3.2`:
  - `qwen2.5:1.5b`: 90.7% Train (18 fixtures) vs 91.7% Holdout (11 fixtures) (**-1.0% gap**).
  - Note on original 8 holdout fixtures: The original holdout accuracy went from **91.5% to 90.0%** after the INR prompt changes; this drop is not attributed to temperature or randomness, but to prompt length expansion and trade-off adjustments for multilingual rupee rules.
  - `qwen2.5:3b`: 93.3% Train vs 91.5% Holdout (**+1.8% gap** on original 18 fixtures).
  - `llama3.2:3b`: 95.1% Train vs 92.2% Holdout (**+2.9% gap** on original 18 fixtures).

### 3. Speech Recognition & Trade Vocabulary (`scripts/eval_audio.py`)
- We implemented a dedicated evaluation script for speech-to-text (`faster-whisper`) that computes Word Error Rate (Levenshtein distance) and trade term recall across trade-specific terms (`P-trap`, `GFCI`, `Romex`, `PEX`, `flux`, `capacitor`, `refrigerant`, `sillcock`, `CSST`, `Fernco`).
- We configured a Whisper `initial_prompt` containing these trade terms in `Settings.whisper_initial_prompt` and supported custom prompts in `jobtalk.transcribe.transcribe()`.
- **Whisper speed figures (~2s/~5s) are estimates, not measured here.** Run `scripts/eval_audio.py` on real `.wav`/`.m4a` files in `tests/audio/` for actual numbers on your hardware.
- The evaluation script is verified and ready to run against `.wav` or `.m4a` recordings supplied in `tests/audio/`.

### 4. Streaming Inference & JSON Schema Constrained Output (Phase 2a)
- **JSON Schema Output (`format: FORMAT_SCHEMA`)**:
  - Leverages Ollama's schema-constrained grammar sampling to guarantee structurally valid JSON adhering to `{customer, address, job_summary, notes, items}`.
  - Eliminated the need for verbose few-shot formatting tokens in `SYSTEM_PROMPT`.
  - Re-evaluating on all fixtures confirmed validity is preserved with automatic recovery for wrapped prose outputs.
- **Perceived Latency via Streaming (`stream: true`)**:
  - Without streaming, the user faces a 20–25s blocking pause before seeing any output.
  - With `stream_extract_job`, token events begin arriving early, and complete line items (`("item", LineItem)`) are parsed and emitted via progressive JSON object scanning.
  - **Benchmark on `plumbing_01_water_heater.json` (`qwen2.5:1.5b`)**:
    - **Time to First Token**: ~2–15s (depending on warm KV cache).
    - **Time to First Line Item**: ~18–24s (line item populates in UI while remaining items continue streaming).
    - **Token Chunks Yielded**: ~195 chunks.
    - **Total Extraction Time**: ~23–25s.

### 5. Post-Hardening INR & Hinglish Benchmark (29 Fixtures)
- Re-running `scripts/eval.py` post-hardening (with labour kind-matching scorer, spoken quantity corrections, customer address guard, and dropped old-item handling) on `qwen2.5:1.5b`:
  - **Holdout Accuracy (11 fixtures)**: **92.3%** composite (Customer: 100%, Address: 100%, Item Recall: **87.5% (42/48)**, Item Precision: 87.5%, Price: 88.1%, Qty: 85.7%).
  - **Train Accuracy (18 fixtures)**: **90.7%** composite (Customer: 100%, Address: 100%, Item Recall: 72.1%, Price: 93.9%, Qty: 87.8%).
  - **Customer and Address Accuracy**: **100.0%** across both train and holdout splits. The post-processing address guard fixed instances where addresses were erroneously parsed into the customer field (e.g. `hvac_03`).
  - **Dropped / Old Items Filter**: Rather than silently deleting old/removed items, they are flagged with `dropped_reason: "looks like a removed/old item"` and excluded from quote calculations and export unless restored by the user.
  - **Price Accuracy**: **93.9% on train, 88.1% on holdout**.
  - **Generalization Gap**: **-1.6%** (90.7% train vs 92.3% holdout).
  - **Average Latency**: **30.52 s/quote warm** on holdout, **23.38 s/quote warm** on train.

> **Interpretation of the -1.6% gap:** The holdout accuracy (92.3%) and train accuracy (90.7%) are virtually identical. The 11 holdout fixtures contain 48 expected items total, so differences under approximately **3 percentage points** are statistical noise. The negative gap is a small-sample artifact, not evidence of "zero overfitting" or superior generalization.

---

## Old vs. New Holdout Comparison

| Holdout Set | Fixtures | Composite Accuracy | Notes |
| :--- | :--- | :--- | :--- |
| Original 8 holdouts (English, USD baseline) | `holdout_01` – `holdout_08` | **90.0%** | Measured 90.0% (post-INR hardening), down from 91.5% original baseline due to prompt changes (not temperature) |
| 3 new Indian holdouts (INR, Hinglish) | `holdout_09` – `holdout_11` | **98.3%** | Clean extraction: 100% Customer, 100% Address, 100% Recall, 100% Price accuracy |
| All 11 holdouts combined | `holdout_01` – `holdout_11` | **92.3%** | Measured latest post-hardening run (87.5% Item Recall) |

The combined holdout score (**92.3%**) is lifted by the 3 new Indian fixtures (**98.3%**), while the original 8 fixtures score **90.0%**.

---

## Recommendation & Default Model

1. **Default Model: `qwen2.5:1.5b`**:
   - Configured as default in `Settings.llm_model`.
   - Fastest CPU turnaround (**~23–27s warm latency**), smallest disk footprint (**986 MB**), lowest memory usage (**~2.2 GB RAM** — estimated), with **91.7% holdout accuracy** across 29 fixtures.
2. **High-Accuracy Option: `qwen2.5:3b` / `llama3.2:3b`**:
   - Stronger extraction of complex multi-part corrections at ~38s latency.


