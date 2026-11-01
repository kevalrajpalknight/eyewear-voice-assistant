# 0012 The assistant brain is a LangChain agent

Status: accepted (2026-11-01).

## Decision
The turn logic (rules, local model, tool loop, cloud escalation) moves from the hand-written lanes
in `assistant/lanes.py` to `langchain.agents.create_agent`, so new tools, MCP servers and API calls
are added as tools and middleware instead of new state machine code.

- `create_agent` is built on LangGraph, so `langgraph` is a required dependency of `langchain`
  (checked on PyPI). We write no LangGraph code; it is the runtime underneath.
- LangChain and LangGraph may be imported only in `assistant/llm_client.py` and in the
  `assistant/agent/` package (enforced by `tests/test_architecture.py`). Tools stay plain classes with
  a risk class; the agent package wraps them.
- The local model is `llama-server` over its OpenAI-compatible API (`ChatOpenAI` with a `base_url`),
  not `langchain_community.llms.LlamaCpp`, which loads a model inside our process through
  llama-cpp-python and would give up the managed server, its prompt cache and its job object.
- Fast rules (`intents.yaml`) still run before the agent: they are instant and exact.
- No database is added. Short-term memory stays in process; Postgres, Redis, a vector store and media
  storage are deliberately left out until needed (YAGNI).

## Dependencies (extra `llm`; all older than 14 days on 2026-11-01, cutoff 2026-10-18)
| Package | Version | Released |
|---|---|---|
| langchain | 1.4.6 | 2026-10-15 |
| langgraph | 1.2.16 | 2026-10-17 |
| langgraph-checkpoint | 4.3.0 | 2026-10-13 |
| langgraph-prebuilt | 1.1.1 | 2026-10-15 |
| langgraph-sdk | 0.4.8 | 2026-10-17 |
| ormsgpack (transitive) | 1.13.0 | 2026-10-16 |

`uv.lock` gained only new packages; no existing package changed (checked against HEAD). Lesson: a
global `--exclude-newer` re-resolves the whole lock and downgrades winrt and pydantic; pin the new
packages by exact version instead.
