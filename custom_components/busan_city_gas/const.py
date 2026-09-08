"""Integration defaults; write availability is not evidence of live validation."""

DOMAIN = "busan_city_gas"
VERSION = "0.6.3"
PANEL_PATH = "busan-city-gas"
EVENT_UPDATED = f"{DOMAIN}_updated"
BASE_URL = "https://ebpp.skens.com"
# Emergency release switch. All period/auth/receipt checks still apply when enabled.
SUBMISSION_ENABLED = True
DEFAULT_OPTIONS = {
    "source_entity": "",
    "recipients": [],
    "weekly_enabled": False,
    "weekly_day": 5,
    "weekly_time": "10:00:00",
    "reminder_enabled": False,
    "reminder_time": "22:00:00",
    "automatic_submission": False,
    "automatic_submission_confirmed": False,
    "deadline_time": "22:00:00",
    "allow_historical_submission": False,
}
