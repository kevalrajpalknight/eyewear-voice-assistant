import httpx
import pytest

from aiglasses.assistant.intents import Intents, normalise
from aiglasses.assistant.tools.base import ToolContext, ToolRefused
from aiglasses.assistant.tools.info import GetTime, GetWeather, TimeArgs, WeatherArgs
from aiglasses.settings import AssistantSettings


def settings(**values: object) -> AssistantSettings:
    return AssistantSettings(_env_file=None, **values)  # type: ignore[arg-type]


def weather_client(
    forecast: dict[str, object] | None = None, found: bool = True
) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        if "geocoding" in str(request.url):
            results = [{"latitude": 26.2, "longitude": 78.2}] if found else None
            return httpx.Response(200, json={"results": results})
        current = forecast or {"temperature_2m": 31.4, "weather_code": 2, "wind_speed_10m": 9.2}
        return httpx.Response(200, json={"current": current})

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_weather_speaks_numbers_and_words_for_a_named_city() -> None:
    tool = GetWeather(settings(), weather_client())
    result = await tool.run(WeatherArgs(location="Gwalior"), ToolContext(utterance="u"))
    assert result.spoken == (
        "In Gwalior it is thirty one degrees and partly cloudy, "
        "with wind of nine kilometres an hour."
    )
    assert result.summary == "weather Gwalior: 31 C, partly cloudy, wind 9 km/h"


async def test_weather_uses_the_default_city_asks_when_there_is_none_and_survives_failures() -> (
    None
):
    quiet = ToolContext(utterance="u")
    default = GetWeather(settings(weather_location="Pune"), weather_client())
    assert "In Pune" in (await default.run(WeatherArgs(), quiet)).spoken  # type: ignore[operator]
    with pytest.raises(ToolRefused) as asked:
        await GetWeather(settings(), weather_client()).run(WeatherArgs(), quiet)
    assert "Which city" in asked.value.spoken
    with pytest.raises(ToolRefused) as missing:
        await GetWeather(settings(), weather_client(found=False)).run(
            WeatherArgs(location="Zzz"), quiet
        )
    assert "couldn't find" in missing.value.spoken
    broken = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(500)))
    with pytest.raises(ToolRefused) as down:
        await GetWeather(settings(), broken).run(WeatherArgs(location="Pune"), quiet)
    assert "can't get the weather" in down.value.spoken


async def test_time_tool_uses_the_settings_zone_or_the_one_asked_for() -> None:
    tool = GetTime(settings(assistant_timezone="Asia/Kolkata"))
    ctx = ToolContext(utterance="u")
    assert (await tool.run(TimeArgs(), ctx)).summary.endswith("Asia/Kolkata")
    assert (await tool.run(TimeArgs(timezone="Europe/London"), ctx)).summary.endswith(
        "Europe/London"
    )
    with pytest.raises(ToolRefused, match="time zone"):
        await tool.run(TimeArgs(timezone="Nowhere/Land"), ctx)


def test_the_weather_intent_catches_the_city_and_ignores_history_questions() -> None:
    intents = Intents.load()
    for said, place in (
        ("What is the weather like in Mumbai right now", "mumbai"),
        ("weather in new delhi", "new delhi"),
        ("what's the forecast for Pune tomorrow", "pune"),
    ):
        found = intents.find(said)
        assert found is not None and found.tool == "get_weather"
        assert found.args_for(normalise(said)) == {"location": place}
    plain = intents.find("how is the weather")
    assert plain is not None and plain.args_for("how is the weather") == {}
    assert intents.find("what was the weather yesterday") is None
