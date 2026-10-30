import re
from pathlib import Path

from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    TomlConfigSettingsSource,
)

CONFIG_FILE = Path("config.toml")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(toml_file=str(CONFIG_FILE), env_prefix="AIGLASSES_")

    device_address: str | None = None
    name_prefix: str = "AIMB-G2_"
    scan_timeout: float = 15.0
    connect_timeout: float = 20.0
    io_timeout: float = 10.0
    connect_attempts: int = 4
    listen_seconds: float = 60.0
    log_dir: Path = Path("logs")
    captures_dir: Path = Path("captures")

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        return (init_settings, env_settings, TomlConfigSettingsSource(settings_cls))


def save_device_address(address: str, path: Path = CONFIG_FILE) -> None:
    line = f'device_address = "{address}"'
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    if re.search(r"^device_address\s*=", text, re.MULTILINE):
        text = re.sub(r"^device_address\s*=.*$", line, text, flags=re.MULTILINE)
    else:
        text = f"{line}\n{text}"
    path.write_text(text, encoding="utf-8")
