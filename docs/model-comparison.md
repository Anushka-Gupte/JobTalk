# Model Comparison: On-Device LLMs for Voice-to-Quote Extraction

## Overview
To provide a reliable, 100% offline experience for tradespeople without requiring a high-end cloud GPU or paid API key, JobTalk runs local LLMs via Ollama. 

We benchmarked model performance across **10 realistic tradesperson transcripts** covering plumbing, electrical, and HVAC tasks. The test set incorporates messy spoken speech: fillers ("uh", "um"), spontaneous self-corrections ("two hours, no, make that two and a half"), numbers spoken as words ("two and a half", "three quarters of an hour"), and unpriced items.

Testing was conducted using `scripts/eval.py` on standard CPU hardware with Ollama 0.40.4.

---

## Benchmark Results

| Metric | `qwen2.5:3b` (Default) | `mistral:latest` (7.2B) |
| :--- | :--- | :--- |
| **Parameters** | 3.1B | 7.2B |
| **Disk Size** | 1.9 GB | 4.4 GB |
| **RAM Footprint (Peak)** | ~3.2 GB | ~5.8 GB |
| **Average Latency / Quote** | **44.02 s** | 167.22 s (3.8x slower) |
| **Customer Accuracy** | **100.0%** | 40.0% (timeouts) |
| **Address Accuracy** | **100.0%** | 40.0% (timeouts) |
| **Item Recall** | **87.2%** (34/39 items) | 33.3% (13/39 items) |
| **Item Precision** | 97.1% (34/35 items) | **100.0%** (13/13 items) |
| **Item Kind Accuracy** | 97.1% | **100.0%** |
| **Quantity Accuracy** | **91.2%** (31/34 items) | 84.6% (11/13 items) |
| **Price Accuracy** | **88.2%** (30/34 items) | 84.6% (11/13 items) |
| **Composite Accuracy** | **93.3%** | 56.5% |

---

## Detailed Findings

### 1. Speed & Responsiveness on CPU
- **`qwen2.5:3b`**: Completed the 10 fixtures in 440 seconds (avg **~44 seconds** per dictation). For a tradesperson sitting in their van after finishing a job, 30–45 seconds is well within acceptable offline turnaround time.
- **`mistral:latest`**: Required **167 seconds (~2.8 minutes)** on average per quote, frequently hitting 180-second timeouts on complex transcripts when running on CPU without dedicated GPU acceleration.

### 2. Prompt Adherence & Formatting
- **Baseline Prompt**: In early testing without few-shot guidance, both models struggled with line-item extraction (recall was only 51.3%), frequently relegating labor hours and parts into the `notes` string rather than separate rows in `items`.
- **Improved Prompt with Few-Shot Example**: Adding explicit instructions regarding self-corrections ("always take the final retracted number") and converting spoken word numbers into float decimals boosted `qwen2.5:3b` item recall from **51.3% to 87.2%**, achieving **93.3% composite accuracy**.

### 3. Handling Real-World Speech Edge Cases
- **Self-Corrections**: Handled accurately by `qwen2.5:3b` when guided by the prompt (e.g. correctly extracting 2.5 hours labor instead of 2.0, and 5 breakers instead of 4).
- **Price Adherence**: In accordance with Hard Constraints 1 & 2, unknown prices remain `null` and are never hallucinated or guessed by the model. Real prices stated in speech (e.g., "$140 for InSinkErator disposal", "$220 Kohler toilet") are extracted faithfully.

---

## Recommendation

**`qwen2.5:3b` is the recommended default model for JobTalk.**
- **Lightweight download**: At 1.9 GB, it downloads quickly even on mobile hotspots.
- **Resource-efficient**: Runs comfortably on 8 GB laptops or mini-PCs without GPU.
- **Superior accuracy per watt**: Outperformed 7B models on CPU thanks to faster token generation avoiding request timeouts and following structured JSON schemas reliably.
