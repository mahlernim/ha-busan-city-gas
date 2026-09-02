"""Explicit readiness and submission safety status."""

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import EntityCategory

from .const import DOMAIN


async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        [
            GasStatus(coordinator, key, field, name)
            for key in coordinator.contracts
            for field, name in (
                ("window_open", "자가검침 입력 가능"),
                ("gap", "실측 보정 필요"),
                ("submission_locked", "자가검침 제출 잠금"),
            )
        ]
    )


class GasStatus(BinarySensorEntity):
    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator, key, field, name):
        self.coordinator, self.key, self.field = coordinator, key, field
        self._attr_unique_id = f"{key}_{field}"
        self._attr_name = name
        self._attr_device_class = BinarySensorDeviceClass.PROBLEM if field == "gap" else None
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, key)})

    @property
    def is_on(self):
        return bool(self.coordinator.view(self.key)[self.field])

    async def async_added_to_hass(self):
        self.async_on_remove(self.coordinator.async_add_listener(self.async_write_ha_state))
