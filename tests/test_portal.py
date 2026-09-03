import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest

from custom_components.busan_city_gas.model import GasError
from custom_components.busan_city_gas.portal import (
    AuthenticationError,
    PortalClient,
    bill_from_html,
    contracts_from_html,
    meter_from_json,
    tariff_from_html,
)


def test_discover_multiple_contracts_no_ids_entered():
    html = '<script>f({BPNO:"1234"})</script><input id="list_cano_0" value="1111"><input id="list_cano_1" value="2222">'
    contracts = contracts_from_html(html)
    assert len(contracts) == 2 and contracts[0].key != contracts[1].key
    assert "1234" not in contracts[0].key


def test_html_error_and_login_not_success():
    with pytest.raises(AuthenticationError):
        contracts_from_html('<input type="password">')
    with pytest.raises(GasError):
        contracts_from_html("<title>Error</title>")


@pytest.mark.parametrize("submitted,flag,expected", [("0", "N", None), ("35", "Y", "35")])
def test_meter_flags_not_zero_heuristic(submitted, flag, expected):
    window = meter_from_json(
        {
            "list": [
                {
                    "START_DATE": "20260913",
                    "END_DATE": "20260918",
                    "LAST_READINGRESULT": "27",
                    "GERAET": "test-meter",
                    "SELF_READ_YN": flag,
                    "selfReadYn": "Y",
                    "ADATSOLL1": "20260917",
                    "CUST_READING_RESULT": submitted,
                }
            ]
        }
    )
    assert window.submitted == expected
    assert window.planned == "2026-09-17"


def test_meter_ambiguous_selection_fails_closed():
    with pytest.raises(GasError):
        meter_from_json({"list": [{}, {}]})


def test_live_meter_ceraet_and_conflicting_ids():
    row = {
        "START_DATE": "20260913",
        "END_DATE": "20260918",
        "LAST_READINGRESULT": "27",
        "CERAET": "test-meter",
        "SELF_READ_YN": "N",
        "selfReadYn": "Y",
    }
    assert meter_from_json({"list": [row]}).previous == "27"
    with pytest.raises(GasError, match="meter_schema_changed"):
        meter_from_json({"list": [{**row, "GERAET": "different-meter"}]})


def test_tariff_parser():
    tariff = tariff_from_html(
        "<p>2026-09-01</p><table><tr><td>516MJ 까지</td><td>23.2186</td></tr><tr><td>516MJ 초과</td><td>23.2186</td></tr></table>"
    )
    assert tariff.bands[0]["rate"] == "23.2186"


def synthetic_bill():
    # Synthetic data for markup regression, NOT a captured household invoice.
    row = ["사용량", "test-meter", "07.18~08.17", "0", "10", "1", "10", "42", "420", "20", "8400"]
    base = ["기본료"] + [""] * 9 + ["900"]
    return (
        '<input id="budat" value="202608"><table><tr><th>합계</th><td>10230</td></tr></table>'
        + "<table></table>" * 3
        + "<table>"
        + "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in [row, base])
        + "</table>"
    )


def test_bill_parser_and_arithmetic():
    bill = bill_from_html(synthetic_bill(), "202608")
    assert bill.reconstructed() == 10230
    assert bill.usage == 10 and bill.heat == 420
    assert bill.start.isoformat() == "2026-07-18"
    assert not bill.unsupported_adjustments
    with pytest.raises(GasError, match="bill_month_mismatch"):
        bill_from_html(synthetic_bill(), "202607")


def test_changed_arithmetic_fails_closed():
    with pytest.raises(GasError, match="bill_arithmetic_changed"):
        bill_from_html(synthetic_bill().replace("<td>420</td>", "<td>421</td>"))


def test_heat_partial_coverage_is_not_full_coverage():
    from custom_components.busan_city_gas.portal import caloric_from_json

    response = {
        "list": [
            {
                "O_FDATE": "20260818",
                "O_TDATE": "20260901",
                "E_CALOR": "42.544000",
                "I_CALOR": "C000",
            }
        ]
    }
    result = caloric_from_json(response, "2026-08-18", "2026-09-02")
    assert result["partial"] and result["end"] == "2026-09-01"
    with pytest.raises(GasError, match="heat_coverage_invalid"):
        caloric_from_json(response, "2026-09-01", "2026-09-02")


async def test_login_bad_password_safe_error():
    client = PortalClient(None, "test-user", "test-password")
    client._request = AsyncMock(
        side_effect=["ok", json.dumps({"errCd": "F", "msg": "raw private content"})]
    )
    with pytest.raises(AuthenticationError, match="invalid_auth") as error:
        await client.login()
    assert "raw private content" not in str(error.value)


async def test_emergency_disabled_submit_is_not_an_http_request(monkeypatch):
    from custom_components.busan_city_gas import portal

    monkeypatch.setattr(portal, "SUBMISSION_ENABLED", False)
    client = PortalClient(None, "test-user", "test-password")
    client._request = AsyncMock()
    with pytest.raises(GasError, match="submission_disabled"):
        await client.submit(None, None, 35, now=datetime.now(timezone.utc))
    client._request.assert_not_called()


async def test_only_one_read_reauth_replay():
    client = PortalClient(None, "test-user", "test-password")
    client.authenticated = True
    client.login = AsyncMock()
    client._request = AsyncMock(side_effect=[AuthenticationError("reauth_required"), "<p>ok</p>"])
    assert await client.read("/busan/read/selfRead.do") == "<p>ok</p>"
    assert client.login.await_count == 1
