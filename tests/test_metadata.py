import json
import tomllib
from pathlib import Path

import yaml

ROOT = Path(__file__).parents[1] / "custom_components" / "busan_city_gas"


def test_manifest_and_translation_schemas():
    manifest = json.loads((ROOT / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["domain"] == "busan_city_gas" and manifest["config_flow"]
    from custom_components.busan_city_gas.const import VERSION

    project = tomllib.loads((ROOT.parents[1] / "pyproject.toml").read_text(encoding="utf-8"))
    assert manifest["version"] == VERSION == project["project"]["version"]
    source = json.loads((ROOT / "strings.json").read_text(encoding="utf-8"))
    translated = json.loads((ROOT / "translations/ko.json").read_text(encoding="utf-8"))
    assert source == translated
    assert source == json.loads((ROOT / "translations/en.json").read_text(encoding="utf-8"))
    assert set(source["config"]["step"]) == {
        "user",
        "login",
        "contracts",
        "source",
        "anchor",
        "notifications",
        "policy",
        "summary",
        "reauth_confirm",
    }
    assert "password" not in source["config"]["step"]["summary"]["data"]


def test_services_and_frontend_no_external_dependencies():
    services = yaml.safe_load((ROOT / "services.yaml").read_text(encoding="utf-8"))
    assert set(services) == {
        "refresh",
        "calibrate",
        "test_notification",
        "prepare_submission",
        "submit",
        "check_submission",
    }
    js = (ROOT / "frontend/panel.js").read_text(encoding="utf-8")
    assert "https://" not in js
    assert "submission_locked" in js
    assert "esc(" in js
