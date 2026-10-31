# 0010 Local models, llama.cpp and the local-server client

Status: accepted (2026-11-01).

## Runtime
- **llama.cpp b11715**, published 2026-10-17T19:34:47Z (14 days before use), asset
  `llama-b11715-bin-win-cpu-x64.zip` (19 662 498 bytes), sha256
  `d50ea1fc9283c3dc9d9aba160b88f722628d42eede09556447663e8833a43327` (the digest GitHub publishes
  for the asset). MIT licence. Unpacked into `tools/llama.cpp/` (gitignored); `VERSION` there holds
  the release and checksum.
- Variant: the generic Windows CPU build. It ships one `ggml-cpu-*.dll` per instruction-set level and
  picks at run time, so no AVX2 / AVX-512 choice is needed here. This PC (i5-1135G7, 4 cores / 8
  threads, AVX2 and AVX-512 present) is served by the `icelake` variant.
- Started only by `aiglasses llm-server start` or `assistant run`, bound to 127.0.0.1, inside a
  Windows job object with kill-on-close, so the server cannot outlive the program.

## Models (registry `src/aiglasses/assistant/models.yaml`; sha256 are Hugging Face LFS hashes)
| Name | Source (pinned commit) | Size | Licence | Gated |
|---|---|---|---|---|
| qwen3-1.7b-q4_k_m | unsloth/Qwen3-1.7B-GGUF @ d7f544ee... | 1.11 GB | Apache-2.0 | no |
| gemma-4-e2b-q4_k_m (+ mmproj-BF16) | unsloth/gemma-4-E2B-it-GGUF @ 0314792d... | 3.11 + 0.99 GB | Apache-2.0 | no |
| llama-3.2-3b-q4_k_m | unsloth/Llama-3.2-3B-Instruct-GGUF @ e7d0997e... | 2.02 GB | Llama 3.2 Community License | no (official repo is gated) |

Total 7.2 GB, under the 8 GB `MODEL_DOWNLOAD_BUDGET_GB`. `ggml-org/gemma-4-E2B-it-GGUF` was skipped
because its newest commit (2026-10-19) is younger than 14 days. The Llama mirror is ungated on
Hugging Face but its licence is the Llama 3.2 Community License: you decide whether to keep it.

## Why a small httpx client for the local server (LangChain stays in one module)
Lane 1 needs llama-server's `json_schema` response format, `cache_prompt` and first-token timing on a
streamed response. LangChain's OpenAI wrapper does not expose these cleanly, and the local server
is a different kind of dependency from a cloud provider. So `local_llm.py` is a ~100-line httpx
client that talks to 127.0.0.1 only. `httpx==0.28.1` (already in the lock) and `psutil==7.2.2`
(physical cores, memory of the server process), `pillow==12.3.0` (image shrinking) and
`ddgs==9.16.0` (extra `search`; 9.16.1 is younger than 14 days) were added; every new package in
`uv.lock` is older than 14 days and no existing package changed.

## Cloud lane tool calling
Native tool calling through LangChain `bind_tools` (in `llm_client.py`, still the only LangChain
module). Tool calls are executed by our own `ToolRegistry`, which enforces the risk classes and the
taint rule; the provider never sees or runs a tool itself.
