# 0009 LLM layer: LangChain behind our own interface

Status: accepted (2026-10-31).

## Decision
`LlmClient` (stream messages -> text deltas) stays our protocol. `LangChainLlmClient` implements it
and is the only module that imports LangChain (`assistant/llm/llm_client.py`; a test could be added to
enforce this, today it is a review rule). LiteLLM is not used and not in the dependency tree. Providers
are factory functions in a registry; chat models are built with the provider packages' own
classes and always with provider-side retries off (`max_retries=0`), because our client retries only
before the first token.

## Dependencies (extra `llm`, all older than 14 days at 2026-10-31, cutoff 2026-10-17 00:00 UTC)
| Package | Version | Released | Licence |
|---|---|---|---|
| langchain-core | 1.6.12 | 2026-10-16 | MIT |
| langchain-openai | 1.7.2 | 2026-10-14 | MIT |
| langchain-anthropic | 1.7.5 | 2026-09-29 | MIT |
| langchain-google-genai | 4.4.2 | 2026-10-13 | MIT |
| tzdata | 2026.4 | 2026-09-12 | Apache-2.0 (core dependency: Windows has no time-zone database) |

Their transitive packages resolved to newer releases, so `uv` constraints in `pyproject.toml` cap
anthropic, google-genai, jiter, jsonpatch, jsonpointer, langchain-protocol, langsmith, openai,
opentelemetry-api, pyasn1, uuid-utils, websockets and xxhash at versions older than the cutoff.
`uv.lock` gained only new packages; no existing package changed (checked by comparing with HEAD).

## Consequences
- The constructor parameter names differ per provider package and were read from the installed
  classes, not from memory; tests build every provider's chat model without network.
- Provider error text can echo a key. Every error goes through `redact()` (known key values and
  key-shaped strings) before it is stored, logged or shown.
