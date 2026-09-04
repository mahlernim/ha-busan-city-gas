"""Support reports intentionally omit credentials, IDs, values and raw responses."""

from .const import DOMAIN, VERSION
from .provider import get_provider


async def async_get_config_entry_diagnostics(hass, entry):
    provider = get_provider(entry.data.get("provider_id", "busan"))
    runtime = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    contracts = []
    if runtime:
        for key in runtime.contracts:
            view = runtime.view(key)
            contracts.append(
                {
                    "bill_count": len(view["bills"]),
                    "complete_bill_periods": sum(
                        bool(b["start"] and b["end"]) for b in view["bills"]
                    ),
                    "source_configured": view["source_configured"],
                    "calibration_required": view["gap"],
                    "window_status": view["window_status"],
                    "submission_status": view["submission_status"],
                    "automatic_submission": view["automatic_submission"],
                    "registration_required": view["service_registration_required"],
                    "channel_change_required": view["channel_change_required"],
                    "errors": {
                        k: view.get(k)
                        for k in (
                            "error",
                            "meter_error",
                            "tariff_error",
                            "heat_error",
                            "submission_error",
                        )
                    },
                }
            )
    return {
        "version": VERSION,
        "provider": provider.id,
        "family": provider.family,
        "config_version": entry.version,
        "loaded": runtime is not None,
        "contracts": contracts,
    }
