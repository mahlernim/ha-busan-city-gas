"""Synthetic HTML contracts exercise discovery, never a production account."""

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from custom_components.busan_city_gas.daesung import (
    DaesungClient,
    bills_from_html,
    contracts_from_html,
)
from custom_components.busan_city_gas.model import GasError
from custom_components.busan_city_gas.submission_transport import (
    SubmissionNotSent,
    SubmissionUncertain,
)

CONTRACT_HTML = '<label for="customer">고객번호</label><select id="customer" name="cust"><option value="123" selected>집</option></select>'
BILLS = (
    CONTRACT_HTML
    + "<table><tr><th>청구월</th><th>청구금액</th><th>사용량</th></tr><tr><td>2026-08</td><td>12,000원</td><td>9.8㎥</td></tr></table>"
)


def meter_html(path):
    return f'''<table><tr><th>검침기간</th><td>2026-09-01 ~ 2026-09-30</td></tr>
    <tr><th>전월지침</th><td>123</td></tr><tr><th>계량기번호</th><td>meter1</td></tr></table>
    <form method="post" action="{path}/save">{CONTRACT_HTML}
    <input type="hidden" name="csrf" value="synthetic-only">
    <label for="reading">당월지침</label><input id="reading" name="reading" value="">
    <button type="submit">검침 등록</button></form>'''


@pytest.fixture(params=["daesung", "daesungclean"])
def client(request):
    provider = SimpleNamespace(id=request.param, family="daesung")
    return DaesungClient(None, {"username": "synthetic", "password": "secret"}, provider)


def test_labelled_contract_and_bill_discovery(client):
    (contract,) = contracts_from_html(CONTRACT_HTML, client.provider, "synthetic")
    assert contract.cano == "123" and contract.private["selector"] == "cust"
    assert bills_from_html(BILLS)["202608"]["amount"] == "12000"
    assert bills_from_html(BILLS)["202608"]["reported_usage"] == "9.8"


def test_meter_form_preserves_hidden_fields(client):
    (contract,) = contracts_from_html(CONTRACT_HTML, client.provider, "synthetic")
    meter = client._window(meter_html(client.meter_path), contract)
    assert meter.eligible and meter.previous == "123"
    assert meter.private["fields"] == {"csrf": "synthetic-only", "cust": "123"}


@pytest.mark.parametrize(
    "replacement", ["https://outside.example/save", "/charge/payment", "/service/self_request"]
)
def test_non_meter_actions_blocked(client, replacement):
    (contract,) = contracts_from_html(CONTRACT_HTML, client.provider, "synthetic")
    html = meter_html(client.meter_path).replace(client.meter_path + "/save", replacement)
    assert not client._window(html, contract).eligible


def test_duplicate_and_other_contract_forms_blocked(client):
    (contract,) = contracts_from_html(CONTRACT_HTML, client.provider, "synthetic")
    with pytest.raises(GasError):
        client._meter_form(meter_html(client.meter_path) * 2, contract)
    with pytest.raises(GasError):
        client._meter_form(
            meter_html(client.meter_path).replace('value="123"', 'value="456"'), contract
        )


def test_prefilled_reading_is_not_overwritten(client):
    (contract,) = contracts_from_html(CONTRACT_HTML, client.provider, "synthetic")
    html = meter_html(client.meter_path).replace(
        'name="reading" value=""', 'name="reading" value="125"'
    )
    assert not client._window(html, contract).eligible


def test_readback_explicit_registered_reading(client):
    (contract,) = contracts_from_html(CONTRACT_HTML, client.provider, "synthetic")
    html = meter_html(client.meter_path) + "<table><tr><th>등록지침</th><td>125</td></tr></table>"
    assert client._window(html, contract).submitted == "125"


@pytest.mark.asyncio
async def test_server_ignoring_contract_selection_blocked(client, monkeypatch):
    (contract,) = contracts_from_html(CONTRACT_HTML, client.provider, "synthetic")

    async def read(path):
        return BILLS.replace('value="123"', 'value="456"')

    monkeypatch.setattr(client, "_read", read)
    with pytest.raises(GasError, match="submission_contract_changed"):
        await client.bills(contract, {})


@pytest.mark.asyncio
async def test_one_submission_and_uncertain_until_readback(client, monkeypatch):
    (contract,) = contracts_from_html(CONTRACT_HTML, client.provider, "synthetic")
    calls = []

    async def read(path):
        return BILLS if path == "/charge/month" else meter_html(client.meter_path)

    async def send(path, data=None, **kwargs):
        calls.append((path, data, kwargs))
        return "<p>등록되었습니다</p>"

    monkeypatch.setattr(client, "_read", read)
    monkeypatch.setattr(client, "_request", send)
    window = await client.meter(contract)
    with pytest.raises(SubmissionUncertain):
        await client.submit(contract, window, 125, now=datetime(2026, 9, 4, tzinfo=timezone.utc))
    assert len(calls) == 1
    assert calls[0][1] == {"cust": "123", "csrf": "synthetic-only", "reading": "125"}
    assert calls[0][2]["write"] is True


@pytest.mark.asyncio
async def test_changed_cycle_never_writes(client, monkeypatch):
    (contract,) = contracts_from_html(CONTRACT_HTML, client.provider, "synthetic")
    window = client._window(meter_html(client.meter_path), contract)

    async def read(path):
        return (
            BILLS
            if path == "/charge/month"
            else meter_html(client.meter_path).replace("meter1", "meter2")
        )

    monkeypatch.setattr(client, "_read", read)
    with pytest.raises(SubmissionNotSent):
        await client.submit(contract, window, 125, now=datetime(2026, 9, 4, tzinfo=timezone.utc))


@pytest.mark.asyncio
async def test_known_login_fields_and_hidden_token(client, monkeypatch):
    calls = []

    async def send(path, data=None):
        calls.append((path, data))
        if path == "/users/login" and data is None:
            return '<form id="loginForm" method="post" action="/users/login"><input name="id"><input name="password" type="password"><input name="token" type="hidden" value="fresh"></form>'
        return BILLS

    monkeypatch.setattr(client, "_request", send)
    await client._login()
    assert calls[1][1] == {
        "id": "synthetic",
        "password": "secret",
        "returl": "/charge/month",
        "token": "fresh",
    }
    assert client.authenticated


def test_nested_label_customer_selector_is_discovered(client):
    html = '<label>고객번호<select name="cust"><option value="123" selected>계약</option></select></label>'
    (contract,) = contracts_from_html(html, client.provider, "synthetic")
    assert contract.cano == "123"


def test_multicolumn_table_labels_remain_separate(client):
    (contract,) = contracts_from_html(CONTRACT_HTML, client.provider, "synthetic")
    html = meter_html(client.meter_path).replace(
        '<label for="reading">당월지침</label><input id="reading" name="reading" value="">',
        '<table><tr><th>전월지침</th><td><input readonly name="last" value="123"></td>'
        '<th>당월지침</th><td><input name="reading" value=""></td></tr></table>',
    )
    meter = client._window(html, contract)
    assert meter.eligible and meter.previous == "123"


def test_named_submit_button_sent_but_non_submit_not_used(client):
    (contract,) = contracts_from_html(CONTRACT_HTML, client.provider, "synthetic")
    html = meter_html(client.meter_path).replace(
        '<button type="submit">검침 등록</button>',
        '<button type="button">입력 도움말</button><button type="submit" name="mode" value="save">검침 등록</button>',
    )
    meter = client._window(html, contract)
    assert meter.eligible and meter.private["fields"]["mode"] == "save"
    html = html.replace('type="submit" name="mode"', 'type="button" name="mode"')
    assert not client._window(html, contract).eligible
