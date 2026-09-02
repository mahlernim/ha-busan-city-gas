"""End-to-end simulated portal. All identifiers and responses are synthetic.

Uses real HTTP form serialization, cookie login, reads and exactly one write.
The trace rejects non-loopback requests, even if test URL substitution regresses.
"""

import asyncio
import copy
import json
from datetime import datetime, timedelta
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import aiohttp
import pytest
import pytest_asyncio
from aiohttp import web

from custom_components.busan_city_gas import portal, submission_transport
from custom_components.busan_city_gas.model import GasError
from custom_components.busan_city_gas.portal import Contract, PortalClient, opaque
from custom_components.busan_city_gas.submission import SubmissionManager
from custom_components.busan_city_gas.submission_transport import build_payload

NOW = datetime(2026, 9, 15, 22, tzinfo=ZoneInfo("Asia/Seoul"))
CONTRACT = Contract(opaque("C000:1111:2222"), "1111", "2222", "Synthetic contract")
FORM = '<script>f({BPNO:"1111"})</script><input id="list_cano_0" value="2222"><input id="list_bpname_0" value="Test Resident">'


class FakePortal:
    mode = "success"
    accepted = None
    reported = None
    form = FORM
    query_fail_after_write = False
    end = "20260918"

    def __init__(self):
        self.requests = []
        self.payloads = []
        self.checkpoint = None

    def row(self):
        return {
            "START_DATE": "20260913",
            "END_DATE": self.end,
            "LAST_READINGRESULT": "27",
            "CERAET": "9001",
            "ANLAGE": "3001",
            "ADATSOLL1": "20260917",
            "V_LDO": "01",
            "CANO": "2222",
            "BPNO": "1111",
            "SELF_READ_YN": "Y" if self.accepted is not None else "N",
            "selfReadYn": "Y",
            "CUST_READING_RESULT": self.accepted or self.reported or "0",
        }

    async def handle(self, request):
        self.requests.append(request.path)
        if request.path.endswith("/login.do"):
            return web.Response(text="<html>login</html>")
        if request.path.endswith("/loginProcess.do"):
            data = await request.post()
            assert data["id"] == "test-user" and data["pw"] == "test-password"
            response = web.json_response({"errCd": "S"})
            response.set_cookie("JSESSIONID", "synthetic-session")
            return response
        assert request.cookies.get("JSESSIONID") == "synthetic-session"
        if request.path.endswith("/selfRead.do"):
            return web.Response(text=self.form)
        if request.path.endswith("/call_EBPP_018.do"):
            if self.query_fail_after_write and self.payloads:
                return web.Response(status=503)
            return web.json_response({"list": [self.row()]})
        assert request.path == submission_transport.SUBMIT_PATH
        assert request.method == "POST"
        assert request.headers["X-Requested-With"] == "XMLHttpRequest"
        assert request.headers["Referer"].endswith(submission_transport.FORM_PATH)
        assert request.content_type == "application/x-www-form-urlencoded"
        if self.checkpoint:
            assert self.checkpoint()["status"] == "pending"
        payload = dict(await request.post())
        self.payloads.append(payload)
        assert payload == {
            "bpno": "1111",
            "name": "Test Resident",
            "cano": "2222",
            "sernr": "9001",
            "addr": "",
            "cust_readingresult": "35",
            "adatsoll1": "20260917",
            "v_ldo": "01",
            "anlage": "3001",
        }
        if self.mode in {
            "success",
            "drop_after_commit",
            "timeout_after_commit",
            "reject_after_commit",
            "chunked",
        }:
            self.accepted = "35"
        elif self.mode == "lower":
            self.accepted = "34"
        elif self.mode == "higher":
            self.accepted = "36"
        elif self.mode == "reported_unconfirmed":
            self.reported = "35"
        if self.mode.startswith("drop_"):
            request.transport.abort()
            return web.Response()
        if self.mode == "timeout_after_commit":
            await asyncio.sleep(0.15)
        if self.mode in {"reject", "reject_after_commit"}:
            return web.json_response({"result": "N", "message": "PRIVATE SERVER CONTENT"})
        if self.mode == "redirect":
            return web.Response(
                status=302, headers={"Location": "https://must-not-be-followed.invalid/"}
            )
        if self.mode == "http_error":
            return web.Response(status=500, text="PRIVATE SERVER CONTENT")
        if self.mode == "html":
            return web.Response(text='<input type="password" value="PRIVATE SERVER CONTENT">')
        if self.mode == "malformed":
            return web.Response(text="{incomplete")
        if self.mode == "unknown_ack":
            return web.json_response({"result": "UNKNOWN", "message": "PRIVATE SERVER CONTENT"})
        if self.mode == "oversize":
            return web.Response(body=b"x" * (submission_transport.MAX_RESPONSE_BYTES + 1))
        if self.mode == "chunked":
            response = web.StreamResponse()
            await response.prepare(request)
            await response.write(b'{"result":')
            await asyncio.sleep(0.01)
            await response.write(b'"Y"}')
            await response.write_eof()
            return response
        return web.json_response({"result": " Y "})


@pytest_asyncio.fixture
async def fake_portal(monkeypatch):
    fake = FakePortal()
    app = web.Application()
    app.router.add_route("*", "/{path:.*}", fake.handle)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    monkeypatch.setattr(portal, "BASE_URL", f"http://127.0.0.1:{port}")
    trace = aiohttp.TraceConfig()

    async def loopback_only(session, context, params):
        assert params.url.host == "127.0.0.1", "Non-loopback network access prohibited"

    trace.on_request_start.append(loopback_only)
    async with aiohttp.ClientSession(
        cookie_jar=aiohttp.CookieJar(unsafe=True), trace_configs=[trace]
    ) as session:
        client = PortalClient(session, "test-user", "test-password")
        yield fake, client
    await runner.cleanup()


async def manager_for(fake, client):
    state, saved = {}, []

    async def persist():
        saved.append(copy.deepcopy(state))

    async def query():
        return await client.meter(CONTRACT)

    async def write(window, value):
        await client.submit(CONTRACT, window, value, now=NOW)

    manager = SubmissionManager(state, persist, query, write, enabled=True)
    initial = await query()
    fake.checkpoint = lambda: saved[-1][initial.cycle]
    proposal = manager.proposal("35.9", "sensor", NOW, initial)
    return manager, proposal, initial, saved


async def test_real_http_login_form_write_and_exact_readback(fake_portal):
    fake, client = fake_portal
    manager, proposal, window, saved = await manager_for(fake, client)
    result = await manager.submit(proposal["id"], NOW, lambda p, w: None)
    assert result["status"] == "confirmed" and result["accepted"] == "35"
    assert len(fake.payloads) == 1
    assert fake.requests.count("/busan/login/loginProcess.do") == 1
    assert saved[-1][window.cycle]["status"] == "confirmed"
    assert "Test Resident" not in json.dumps(saved)
    assert "test-password" not in json.dumps(saved)


@pytest.mark.parametrize("mode", ["drop_after_commit", "timeout_after_commit"])
async def test_lost_acknowledgement_is_reconciled_without_reposting(fake_portal, monkeypatch, mode):
    fake, client = fake_portal
    fake.mode = mode
    original = submission_transport.send_once

    async def fast_timeout(session, base, payload):
        return await original(session, base, payload, timeout=0.03)

    monkeypatch.setattr(submission_transport, "send_once", fast_timeout)
    manager, proposal, _, _ = await manager_for(fake, client)
    result = await manager.submit(proposal["id"], NOW, lambda p, w: None)
    assert result["accepted"] == "35" and len(fake.payloads) == 1


@pytest.mark.parametrize(
    "mode",
    [
        "drop_no_commit",
        "redirect",
        "http_error",
        "html",
        "malformed",
        "unknown_ack",
        "oversize",
        "ack_without_commit",
    ],
)
async def test_ambiguous_http_outcome_remains_locked_after_restart(fake_portal, mode):
    fake, client = fake_portal
    fake.mode = mode
    manager, proposal, window, _ = await manager_for(fake, client)
    with pytest.raises(GasError, match="submission_uncertain") as error:
        await manager.submit(proposal["id"], NOW, lambda p, w: None)
    assert "PRIVATE" not in str(error.value)
    restored = SubmissionManager(
        copy.deepcopy(manager.state), AsyncMock(), manager.query, manager.write, enabled=True
    )
    again = restored.proposal("35", "sensor", NOW, window)
    with pytest.raises(GasError, match="submission_uncertain"):
        await restored.submit(again["id"], NOW, lambda p, w: None)
    assert len(fake.payloads) == 1


async def test_explicit_rejection_is_explained_and_daily_repeat_blocked(fake_portal):
    fake, client = fake_portal
    fake.mode = "reject"
    manager, proposal, window, _ = await manager_for(fake, client)
    with pytest.raises(GasError, match="submission_rejected"):
        await manager.submit(proposal["id"], NOW, lambda p, w: None)
    assert manager.state[window.cycle]["status"] == "rejected"
    with pytest.raises(GasError, match="submission_attempted_today"):
        await manager.submit(proposal["id"], NOW, lambda p, w: None)
    assert len(fake.payloads) == 1


@pytest.mark.parametrize("mode", ["lower", "higher"])
async def test_different_registered_value_never_claims_requested_value_succeeded(fake_portal, mode):
    fake, client = fake_portal
    fake.mode = mode
    manager, proposal, window, _ = await manager_for(fake, client)
    with pytest.raises(GasError, match="submission_value_mismatch"):
        await manager.submit(proposal["id"], NOW, lambda p, w: None)
    assert not await manager.reconcile(await manager.query(), NOW)
    assert manager.state[window.cycle]["status"] == "uncertain"
    assert len(fake.payloads) == 1


async def test_recorded_value_with_N_flag_is_not_overwritten(fake_portal):
    fake, client = fake_portal
    fake.reported = "35"
    manager, proposal, _, _ = await manager_for(fake, client)
    with pytest.raises(GasError, match="portal_reading_present"):
        await manager.submit(proposal["id"], NOW, lambda p, w: None)
    assert not fake.payloads


async def test_unconfirmed_report_after_write_is_not_a_success(fake_portal):
    fake, client = fake_portal
    fake.mode = "reported_unconfirmed"
    manager, proposal, _, _ = await manager_for(fake, client)
    with pytest.raises(GasError, match="submission_state_unknown"):
        await manager.submit(proposal["id"], NOW, lambda p, w: None)
    assert len(fake.payloads) == 1


async def test_two_phones_get_same_receipt_and_one_post(fake_portal):
    fake, client = fake_portal
    manager, first, window, _ = await manager_for(fake, client)
    second = manager.proposal("35.2", "sensor", NOW, window)
    results = await asyncio.gather(
        *(manager.submit(p["id"], NOW, lambda p, w: None) for p in [first, second])
    )
    assert all(r["status"] == "confirmed" for r in results)
    assert len(fake.payloads) == 1


async def test_changed_contract_form_never_sends_a_write(fake_portal):
    fake, client = fake_portal
    fake.form = FORM.replace('value="2222"', 'value="3333"')
    manager, proposal, window, _ = await manager_for(fake, client)
    with pytest.raises(GasError, match="submission_contract_changed"):
        await manager.submit(proposal["id"], NOW, lambda p, w: None)
    assert not fake.payloads
    assert manager.state[window.cycle]["status"] == "not_sent"


async def test_production_gate_blocks_even_with_valid_mock_metadata(fake_portal, monkeypatch):
    fake, client = fake_portal
    monkeypatch.setattr(portal, "SUBMISSION_ENABLED", False)
    window = portal.meter_from_json({"list": [fake.row()]})
    with pytest.raises(GasError, match="submission_disabled"):
        await client.submit(CONTRACT, window, 35, now=NOW)
    assert not fake.requests


@pytest.mark.parametrize("value", [True, 35.1, -1, 100000, "35"])
async def test_transport_requires_integer_value(fake_portal, value):
    fake, _ = fake_portal
    window = portal.meter_from_json({"list": [fake.row()]})
    with pytest.raises(GasError, match="invalid_submission_value"):
        build_payload(CONTRACT, window, value, FORM, NOW)
    assert not fake.requests


async def test_window_closed_and_stale_proposal_never_post(fake_portal):
    fake, client = fake_portal
    manager, proposal, _, _ = await manager_for(fake, client)
    with pytest.raises(GasError, match="stale_proposal"):
        await manager.submit(proposal["id"], NOW + timedelta(minutes=31), lambda p, w: None)
    fake.end = "20260914"
    with pytest.raises(GasError, match="window_closed"):
        await manager.submit(proposal["id"], NOW, lambda p, w: None)
    assert not fake.payloads


@pytest.mark.parametrize("mode", ["chunked", "reject_after_commit"])
async def test_receipt_wins_over_fragmented_or_negative_ack(fake_portal, mode):
    fake, client = fake_portal
    fake.mode = mode
    manager, proposal, _, _ = await manager_for(fake, client)
    result = await manager.submit(proposal["id"], NOW, lambda p, w: None)
    assert result["status"] == "confirmed" and len(fake.payloads) == 1


async def test_readback_failure_keeps_uncertainty_and_never_reposts(fake_portal):
    fake, client = fake_portal
    fake.query_fail_after_write = True
    manager, proposal, window, saved = await manager_for(fake, client)
    with pytest.raises(GasError, match="submission_uncertain"):
        await manager.submit(proposal["id"], NOW, lambda p, w: None)
    assert saved[-1][window.cycle]["status"] == "uncertain"
    fake.query_fail_after_write = False
    assert await manager.reconcile(await manager.query(), NOW)
    assert len(fake.payloads) == 1


async def test_failed_write_ahead_persistence_never_posts(fake_portal):
    fake, client = fake_portal
    manager, proposal, _, _ = await manager_for(fake, client)
    manager.persist = AsyncMock(side_effect=OSError("synthetic disk failure"))
    with pytest.raises(OSError):
        await manager.submit(proposal["id"], NOW, lambda p, w: None)
    assert not fake.payloads


@pytest.mark.parametrize(
    "mode", ["success", "ack_without_commit", "unauthorized", "notification_failure"]
)
async def test_ha_panel_handler_through_runtime_to_http(fake_portal, hass, monkeypatch, mode):
    """Real HA handler/auth/runtime/ledger plus fake HTTP; no browser or WS socket."""
    import inspect
    from types import SimpleNamespace
    from unittest.mock import Mock

    from custom_components.busan_city_gas import coordinator, websocket
    from custom_components.busan_city_gas.const import DOMAIN
    from tests import test_ha

    fake, client = fake_portal
    fake.mode = "success" if mode in {"unauthorized", "notification_failure"} else mode
    monkeypatch.setattr(test_ha, "CONTRACT", CONTRACT)
    monkeypatch.setattr(coordinator.dt_util, "now", lambda: NOW)
    runtime = await test_ha.make_runtime(hass)
    runtime.client = client
    hass.data[DOMAIN] = {runtime.entry.entry_id: runtime}
    hass.auth = SimpleNamespace(
        async_get_user=AsyncMock(
            return_value=SimpleNamespace(is_admin=mode != "unauthorized", is_active=True)
        )
    )
    connection = SimpleNamespace(
        user=SimpleNamespace(id="synthetic-user"), send_result=Mock(), send_error=Mock()
    )
    try:
        window = await runtime.submissions[CONTRACT.key].query()
        await runtime.calibrate(CONTRACT.key, "35.1", physical=True)
        proposal = runtime.propose(CONTRACT.key)
        fake.checkpoint = lambda: runtime.saved["contracts"][CONTRACT.key]["submissions"][
            window.cycle
        ]
        if mode == "notification_failure":
            runtime.send_message = AsyncMock(side_effect=OSError("synthetic failure"))
        await inspect.unwrap(websocket.ws_submit)(
            hass,
            connection,
            {
                "id": 1,
                "entry_id": runtime.entry.entry_id,
                "key": CONTRACT.key,
                "proposal_id": proposal["id"],
            },
        )
        if mode == "unauthorized":
            assert not fake.payloads
            assert connection.send_error.call_args.args[1] == "not_authorized"
        elif mode == "ack_without_commit":
            assert len(fake.payloads) == 1
            assert connection.send_error.call_args.args[1] == "submission_uncertain"
            assert runtime.view(CONTRACT.key)["submission_blocked"]
        else:
            assert len(fake.payloads) == 1
            receipt = connection.send_result.call_args.args[1]
            assert receipt["status"] == "confirmed" and receipt["accepted"] == "35"
            if mode == "notification_failure":
                assert receipt["notification_warning"] == "notification_failed"
    finally:
        await runtime.shutdown()
