import re
from pathlib import Path

SRC = Path(__file__).parent.parent / "src" / "aiglasses"
ALLOWED = ("assistant/llm/llm_client.py", "assistant/agent/")
FRAMEWORK = re.compile(r"^\s*(?:from|import)\s+(?:langchain|langgraph)", re.MULTILINE)


def test_langchain_and_langgraph_are_imported_only_by_the_llm_and_agent_modules() -> None:
    offenders = [
        str(path.relative_to(SRC))
        for path in SRC.rglob("*.py")
        if FRAMEWORK.search(path.read_text(encoding="utf-8"))
        and not any(part in path.relative_to(SRC).as_posix() for part in ALLOWED)
    ]
    assert offenders == [], f"LangChain may only be imported in {ALLOWED}: {offenders}"
