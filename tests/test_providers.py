"""Regional provider routing, tariff and identity compatibility."""

import json
from datetime import datetime
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import pytest

from custom_components.busan_city_gas import submission_transport
from custom_components.busan_city_gas.model import GasError, MeterWindow
from custom_components.busan_city_gas.portal import (
    PortalClient,
    caloric_from_json,
    contracts_from_html,
    opaque,
    tariff_from_html,
)
from custom_components.busan_city_gas.provider import PROVIDERS

EXPECTED = {
    "busan": "C000",
    "koone": "B000",
    "cheongju": "D000",
    "gumi": "E000",
    "pohang": "F000",
    "jeonnam": "G000",
    "gangwon": "J000",
    "jeonbuk": "K000",
}
CONTRACT_HTML = '<script>f({BPNO:"1111"})</script><input id="list_cano_0" value="2222">'


def test_provider_registry_and_busan_identity_are_stable():
    assert {key: provider.code for key, provider in PROVIDERS.items()} == EXPECTED
    busan = contracts_from_html(CONTRACT_HTML, PROVIDERS["busan"])[0]
    assert busan.key == opaque("C000:1111:2222")
    keys = {contracts_from_html(CONTRACT_HTML, provider)[0].key for provider in PROVIDERS.values()}
    assert len(keys) == 8


@pytest.mark.parametrize("provider_id,code", EXPECTED.items())
async def test_provider_routes_and_company_codes(provider_id, code):
    provider = PROVIDERS[provider_id]
    client = PortalClient(None, "user", "password", provider)
    client.read = AsyncMock(return_value=CONTRACT_HTML)
    contracts = await client.contracts()
    client.read.assert_awaited_with(f"/{provider.path}/read/selfRead.do")

    client.read.reset_mock()
    client.read.return_value = "<html>bill</html>"
    await client.bill_page(contracts[0], "202609")
    path, payload = client.read.await_args.args
    assert path == f"/{provider.path}/charge/askDetail.do"
    assert payload["compcd"] == code

    client._request = AsyncMock(
        return_value=json.dumps(
            {
                "list": [
                    {
                        "O_FDATE": "20260901",
                        "O_TDATE": "20260902",
                        "E_CALOR": "42.5",
                        "I_CALOR": code,
                    }
                ]
            }
        )
    )
    await client.caloric("2026-09-01", "2026-09-02")
    path, payload = client._request.await_args.args
    assert path == f"/{provider.path}/caloric/call_EBPP_044.do"
    assert payload["I_CALOR"] == code


def test_regional_caloric_code_is_required():
    provider = PROVIDERS["gumi"]
    payload = {
        "list": [
            {
                "O_FDATE": "20260901",
                "O_TDATE": "20260902",
                "E_CALOR": "42.5",
                "I_CALOR": provider.code,
            }
        ]
    }
    assert caloric_from_json(payload, "2026-09-01", "2026-09-02", provider)["factor"] == "42.5"
    payload["list"][0]["I_CALOR"] = "C000"
    with pytest.raises(Exception, match="heat_coverage_invalid"):
        caloric_from_json(payload, "2026-09-01", "2026-09-02", provider)


@pytest.mark.parametrize("provider_id,threshold", [("busan", "516"), ("gumi", "522")])
def test_regional_tariff_bands(provider_id, threshold):
    html = f"""
    <p>2026-09-01</p><table>
      <tr><td>{chr(0xC8FC)}{chr(0xD0DD)}{chr(0xC6A9)}</td><td>home</td><td>1,000</td><td>22.1</td></tr>
      <tr><td>{threshold}MJ {chr(0xC774)}{chr(0xD558)}</td><td>22.1</td></tr>
      <tr><td>{threshold}MJ {chr(0xCD08)}{chr(0xACFC)}</td><td>23.2</td></tr>
    </table>
    """
    tariff = tariff_from_html(html, PROVIDERS[provider_id])
    assert tariff.base_charge == "1000"
    assert tariff.bands == [
        {"up_to_mj": threshold, "rate": "22.1"},
        {"up_to_mj": None, "rate": "23.2"},
    ]


def test_tariff_profile_selects_one_residential_type_and_rejects_ambiguity():
    html = """
    <p>2026-09-01</p><table>
      <tr><td>주택용 취사전용</td><td>1,050</td><td>23.4509</td></tr>
      <tr><td>개별난방</td><td>1,050</td><td>23.2208</td></tr>
      <tr><td>중앙난방</td><td>1,050</td><td>22.8748</td></tr>
    </table>
    """
    tariff = tariff_from_html(html, PROVIDERS["cheongju"], "individual")
    assert tariff.profile == "individual"
    assert tariff.base_charge == "1050"
    assert tariff.bands == [{"up_to_mj": None, "rate": "23.2208"}]
    with pytest.raises(GasError, match="tariff_schema_changed"):
        tariff_from_html(
            html + "<table><tr><td>개별난방</td><td>9.9</td></tr></table>",
            PROVIDERS["cheongju"],
            "individual",
        )


async def test_login_uses_selected_provider_return_url():
    provider = PROVIDERS["jeonbuk"]
    client = PortalClient(None, "user", "password", provider)
    client._request = AsyncMock(side_effect=["ok", json.dumps({"errCd": "S"})])
    await client.login()
    assert client._request.await_args_list[0].args == (f"/{provider.path}/login/login.do",)
    path, payload = client._request.await_args_list[1].args
    assert path == f"/{provider.path}/login/loginProcess.do"
    assert payload["returnURL"] == f"/{provider.path}/read/selfRead.do"


@pytest.mark.parametrize("provider_id", EXPECTED)
async def test_submission_uses_selected_provider_without_retry(provider_id, monkeypatch):
    provider = PROVIDERS[provider_id]
    contract = contracts_from_html(CONTRACT_HTML, provider)[0]
    form = CONTRACT_HTML + '<input id="list_bpname_0" value="Resident">'
    window = MeterWindow(
        "2026-09-13",
        "2026-09-18",
        "27",
        opaque("meter"),
        "2026-09-17",
        True,
        private={
            "CERAET": "meter",
            "ANLAGE": "anlage",
            "ADATSOLL1": "20260917",
            "V_LDO": "01",
            "CANO": "2222",
            "BPNO": "1111",
        },
    )
    client = PortalClient(None, "user", "password", provider)
    client.authenticated = True
    client.read = AsyncMock(return_value=form)
    sent = AsyncMock()
    monkeypatch.setattr(submission_transport, "send_once", sent)
    await client.submit(
        contract, window, 35, now=datetime(2026, 9, 15, tzinfo=ZoneInfo("Asia/Seoul"))
    )
    client.read.assert_awaited_once_with(f"/{provider.path}/read/selfRead.do")
    if provider_id == "busan":
        assert sent.await_args.kwargs == {}
    else:
        assert sent.await_args.kwargs == {
            "form_path": f"/{provider.path}/read/selfRead.do",
            "submit_path": f"/{provider.path}/read/insertSelfRead.do",
        }
