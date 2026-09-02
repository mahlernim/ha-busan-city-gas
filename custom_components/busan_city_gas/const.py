"""Integration constants. External writes intentionally remain release-locked."""

DOMAIN = "busan_city_gas"
VERSION = "0.2.0"
PANEL_PATH = "busan-city-gas"
EVENT_UPDATED = f"{DOMAIN}_updated"
BASE_URL = "https://ebpp.skens.com"
# NOT a user option. Change only after an authorized open-window submission audit,
# including the payload, acknowledgement, re-query and integer conversion rules.
SUBMISSION_VERIFIED = False
DEFAULT_OPTIONS = {
    "source_entity": "",
    "recipients": [],
    "weekly_enabled": False,
    "weekly_day": 5,
    "weekly_time": "10:00:00",
    "reminder_enabled": False,
    "reminder_time": "22:00:00",
    "automatic_submission": False,
    "deadline_time": "22:00:00",
    "allow_historical_submission": False,
}
