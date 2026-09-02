"""Offline Home Assistant fixtures: never contact the utility or production HA."""

import pytest_asyncio
from homeassistant.config_entries import ConfigEntries
from homeassistant.core import HomeAssistant


@pytest_asyncio.fixture
async def hass(tmp_path):
    instance = HomeAssistant(str(tmp_path))
    await instance.config.async_set_time_zone("Asia/Seoul")
    instance.config_entries = ConfigEntries(instance, {})
    yield instance
    await instance.async_stop(force=True)
