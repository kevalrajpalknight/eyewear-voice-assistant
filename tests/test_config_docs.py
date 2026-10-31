"""Every setting must be documented in .env.example and docs/CONFIG.md."""

from pathlib import Path

from aiglasses.settings import AssistantSettings

ROOT = Path(__file__).resolve().parents[1]


def test_every_setting_is_in_the_example_file_and_the_reference() -> None:
    example = (ROOT / ".env.example").read_text(encoding="utf-8")
    reference = (ROOT / "docs" / "CONFIG.md").read_text(encoding="utf-8")
    missing_example = [n for n in AssistantSettings.model_fields if n.upper() not in example]
    missing_reference = [n for n in AssistantSettings.model_fields if n.upper() not in reference]
    assert missing_example == [] and missing_reference == []
