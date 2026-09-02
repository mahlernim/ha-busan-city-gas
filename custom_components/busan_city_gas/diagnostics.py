"""Redacted diagnostics: no credentials, contract IDs, cookies or raw HTML."""

from homeassistant.components.diagnostics import async_redact_data

TO_REDACT = {
    "username",
    "password",
    "bpno",
    "cano",
    "private",
    "recipients",
    "received_by",
    "label",
    "name",
    "addr",
    "sernr",
    "anlage",
}


async def async_get_config_entry_diagnostics(hass, entry):
    coordinator = hass.data["busan_city_gas"][entry.entry_id]
    return async_redact_data(
        {
            "entry": {"data": dict(entry.data), "options": dict(entry.options)},
            "contracts": {
                key: {
                    "view": coordinator.view(key),
                    "stored_keys": sorted(coordinator.saved["contracts"][key]),
                }
                for key in coordinator.contracts
            },
        },
        TO_REDACT,
    )
