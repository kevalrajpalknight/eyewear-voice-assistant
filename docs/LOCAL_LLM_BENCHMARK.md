# Local LLM benchmark (L9)

Status 2026-11-01: harness, quality suite and models are ready; the measured run is NOT done.
Reason: this PC had 1.1 GB of 12.6 GB RAM available (91.6% in use by other programs) when the run
was due, so a 1.1 to 4 GB model would have been paged and every speed and RAM number would have been
wrong. Free memory (close the browser and other big programs), then run the commands below.
Nothing in this file is a measurement yet.

## What was verified
- llama.cpp b11715 (CPU build, runtime-dispatched to the icelake kernels on this CPU) starts with the
  flags the shipped code uses, loads Qwen3 1.7B Q4_K_M in about 40 s (cold, under memory pressure:
  NOT a benchmark), answers one forced-JSON step with a valid `{"action": ...}` object, and no
  `llama-server` process remains after `stop`.
- Registry and checksums: `aiglasses models verify` reports ok for all four files (7.22 GB).

## Models (all ungated on Hugging Face; see ADR 0010)
Qwen3 1.7B Q4_K_M (non-thinking), Llama 3.2 3B Q4_K_M (a mirror of a gated repo; the Llama 3.2
Community License still applies), Gemma 4 E2B Q4_K_M with the BF16 vision projector.

## Method (one model per process, so memory and cleanup are clean)
`uv run python -m bench.local_llm_bench <model> [--quality] [--vision]` from the repo root with
`PYTHONPATH=bench/src`. It uses the real system prompt, the real local rules and tool schemas, and a
10-turn history, and records: load time; prompt-processing speed (from the server's timings);
first-token latency cold, with the identical prompt cached, with the cache off, and for a new
question that shares the prefix; generation tokens per second; total time for a two-sentence free
reply; peak RAM of the server process. `--quality` runs 40 synthetic prompts
(`quality_prompts.yaml`: greetings, chit-chat, stable facts with known answers, time-sensitive facts
that must escalate or search, tool intents, dissatisfaction, ambiguity, research, hallucination
traps) and scores JSON validity, action correctness, tool correctness and the known facts.
`--vision` sends a synthetic image (a red square) to Gemma with its projector and measures the time
and whether the answer says red.

## Run it (about 10 minutes per model)
```
# close the browser and other big programs first; available RAM should be above 6 GB
$env:PYTHONPATH = "bench/src"
uv run --no-sync python -m bench.local_llm_bench qwen3-1.7b-q4_k_m --quality
uv run --no-sync python -m bench.local_llm_bench llama-3.2-3b-q4_k_m --quality
uv run --no-sync python -m bench.local_llm_bench gemma-4-e2b-q4_k_m --quality
uv run --no-sync python -m bench.local_llm_bench gemma-4-e2b-q4_k_m --vision
```
Results land in `bench/out/local_llm_<model>.json`. Tell me when they are done and I will write the
tables here, or ask me to run them once memory is free.

## Cloud comparison (needs one live call, so it is yours to start)
`uv run aiglasses assistant check --reset-live-budget`, then `uv run aiglasses assistant check --live`
prints the first-token and total time of one neutral call to the main model. Local and cloud are
only comparable when both are measured on the same day on the same network.

## Gemma extras (not done)
- One server for chat, tools and vision: `llm-server start --vision` loads the projector; the vision
  suite above measures it.
- Offline audio experiment (transcribe saved takes through Gemma's audio input, compare word error
  and time against Parakeet): not attempted. It needs a build that accepts `input_audio` and the
  memory above. It would NOT be wired into the pipeline.

## Recommendation
None yet; the default stays your choice once the numbers exist. A prior to test: Qwen3 1.7B for
lane 1 (smallest, quickest first token), Gemma 4 E2B if one server for chat and vision matters.

## Vision candidates (N6) - not run
Put test photos in `bench/data/images/` (gitignored) and free some RAM; then Gemma 4 E2B with its
projector can be timed on them with `bench.local_llm_bench gemma-4-e2b-q4_k_m --vision`. Moondream,
SmolVLM2 and Qwen small vision models were not downloaded (no images yet, and the model budget has
0.6 GB left of 8 GB). The synthetic red-square check is the only vision test so far, and it was not run.
