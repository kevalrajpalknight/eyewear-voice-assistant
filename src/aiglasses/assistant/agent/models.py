"""Chat models for the agent: the local llama-server over its OpenAI API, and the cloud role."""

from typing import Any

from aiglasses.assistant.llm_client import build_chat_model
from aiglasses.assistant.local_llm import NO_THINKING
from aiglasses.settings import AssistantSettings


def build_local_model(base_url: str, settings: AssistantSettings) -> Any:
    """`llama-server` speaks the OpenAI chat API, so no extra package is needed for it."""
    from langchain_openai import ChatOpenAI

    return ChatOpenAI(
        base_url=base_url.rstrip("/") + "/v1",
        api_key="local",  # llama-server ignores the key
        model=str(settings.local_llm_model),
        temperature=0.0,
        max_tokens=settings.agent_local_max_tokens,
        timeout=settings.local_step_timeout_s,
        max_retries=0,
        streaming=True,
        extra_body=dict(NO_THINKING),
    )


def build_cloud_model(settings: AssistantSettings) -> Any:
    return build_chat_model(settings.llm_role("main"))
