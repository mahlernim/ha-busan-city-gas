"""Read-only Home Assistant entities; values are already available in memory."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import EntityCategory

from .const import DOMAIN


@dataclass(frozen=True)
class Description:
    key: str
    name: str
    unit: str | None = None
    device_class: SensorDeviceClass | None = None
    state_class: SensorStateClass | None = None
    diagnostic: bool = False


DESCRIPTIONS = [
    Description("reading", "현재 추정 검침값", "m³", SensorDeviceClass.GAS),
    Description("actual", "마지막 실측 검침값", "m³", SensorDeviceClass.GAS),
    Description("usage", "현재까지 추정 사용량", "m³", SensorDeviceClass.GAS),
    Description("projected_usage", "이번 청구기간 예상 사용량", "m³", SensorDeviceClass.GAS),
    Description("average", "최근 14일 사용일 평균", "m³/d"),
    Description("billed_usage", "최근 고지 사용량", "m³", SensorDeviceClass.GAS),
    Description("billed_heat", "최근 고지 사용열량", "MJ", SensorDeviceClass.ENERGY),
    Description("heat_factor", "예측 적용 평균열량", "MJ/m³"),
    Description("billed_amount", "최근 확정 고지금액", "KRW", SensorDeviceClass.MONETARY),
    Description("previous_amount", "직전 고지금액", "KRW", SensorDeviceClass.MONETARY),
    Description("last_year_amount", "전년 동월 고지금액", "KRW", SensorDeviceClass.MONETARY),
    Description("accrued_amount", "현재까지 예상 요금", "KRW", SensorDeviceClass.MONETARY),
    Description("projected_amount", "이번 청구기간 예상 요금", "KRW", SensorDeviceClass.MONETARY),
    Description("submission_status", "자가검침 제출 상태"),
    Description("window_start", "자가검침 시작일", device_class=SensorDeviceClass.DATE),
    Description("window_end", "자가검침 마감일", device_class=SensorDeviceClass.DATE),
    Description("due_date", "최근 고지 납기일", device_class=SensorDeviceClass.DATE),
    Description("actual_at", "마지막 실측 확인", device_class=SensorDeviceClass.TIMESTAMP),
    Description("accepted", "접수된 검침값", "m³", SensorDeviceClass.GAS),
    Description("accepted_checked_at", "접수 확인 시각", device_class=SensorDeviceClass.TIMESTAMP),
    Description(
        "last_refresh",
        "마지막 공식 조회",
        device_class=SensorDeviceClass.TIMESTAMP,
        diagnostic=True,
    ),
]


async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        [
            GasSensor(coordinator, key, description)
            for key in coordinator.contracts
            for description in DESCRIPTIONS
        ]
    )


class GasSensor(SensorEntity):
    _attr_has_entity_name = True

    def __init__(self, coordinator, contract_key, description):
        self.coordinator, self.contract_key, self.description = (
            coordinator,
            contract_key,
            description,
        )
        self._attr_unique_id = f"{contract_key}_{description.key}"
        self._attr_name = description.name
        self._attr_native_unit_of_measurement = description.unit
        self._attr_device_class = description.device_class
        self._attr_state_class = description.state_class
        self._attr_suggested_display_precision = 2 if description.unit == "m³" else None
        self._attr_entity_category = EntityCategory.DIAGNOSTIC if description.diagnostic else None
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, contract_key)},
            name=coordinator.contracts[contract_key].label,
            manufacturer="부산도시가스",
            model="온라인 계약",
            configuration_url="https://ebpp.skens.com/busan/charge/ask.do",
        )

    @property
    def available(self):
        value = self.coordinator.view(self.contract_key).get(self.description.key)
        return value is not None

    @property
    def native_value(self):
        value = self.coordinator.view(self.contract_key).get(self.description.key)
        if value is None:
            return None
        if self.description.device_class in (SensorDeviceClass.DATE, SensorDeviceClass.TIMESTAMP):
            from datetime import date, datetime

            return (
                datetime if self.description.device_class == SensorDeviceClass.TIMESTAMP else date
            ).fromisoformat(value)
        if self.description.unit:
            return Decimal(str(value))
        return value

    @property
    def extra_state_attributes(self):
        view = self.coordinator.view(self.contract_key)
        if self.description.key == "average":
            return {
                "days_used": view["average_days"],
                "zero_days_excluded": True,
                "incomplete_days_excluded": True,
            }
        if self.description.key == "reading":
            return {"origin": view["origin"], "physical_calibration_required": view["gap"]}
        if self.description.key in ("accrued_amount", "projected_amount"):
            return {
                "estimated": True,
                "quality": view["quality"],
                "tariff_effective": view.get("tariff_effective"),
                "factors_source": view.get("factors_source"),
            }
        if self.description.key == "submission_status":
            return {"submission_locked": view["submission_locked"], "accepted": view["accepted"]}
        return None

    async def async_added_to_hass(self):
        self.async_on_remove(self.coordinator.async_add_listener(self.async_write_ha_state))
