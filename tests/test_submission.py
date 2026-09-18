import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest

from custom_components.busan_city_gas.model import GasError, MeterWindow
from custom_components.busan_city_gas.submission import SubmissionManager
from custom_components.busan_city_gas.submission_transport import (
    INPUT_Y_N,
    RESULT_Y_N,
    SubmissionAcknowledgement,
    SubmissionRejected,
    match_submission_acknowledgement,
)

NOW = datetime(2026, 9, 18, 22, tzinfo=timezone.utc)


def setup(*, enabled=True, error=False):
    state, calls, saved = {}, [], []
    window = MeterWindow("2026-09-13", "2026-09-18", "27", "meter", eligible=True)

    async def persist():
        saved.append({key: dict(value) for key, value in state.items()})

    async def query():
        return window

    async def write(w, value):
        assert saved[-1][w.cycle]["status"] == "pending"
        calls.append(value)
        if error:
            raise TimeoutError()
        window.submitted = str(value)

    return SubmissionManager(state, persist, query, write, enabled=enabled), window, calls, saved


async def test_write_gate_never_calls_transport():
    manager, window, calls, _ = setup(enabled=False)
    proposal = manager.proposal("35.9", "sensor", NOW, window)
    with pytest.raises(GasError, match="submission_disabled"):
        await manager.submit(proposal["id"], NOW, lambda p, w: None)
    assert not calls


async def test_two_phones_only_one_write():
    manager, window, calls, _ = setup()
    p1 = manager.proposal("35.9", "sensor", NOW, window)
    p2 = manager.proposal("35.9", "sensor", NOW, window)
    results = await asyncio.gather(
        manager.submit(p1["id"], NOW, lambda p, w: None),
        manager.submit(p2["id"], NOW, lambda p, w: None),
        return_exceptions=True,
    )
    assert calls == [35]
    assert results[0]["accepted"] == "35"


async def test_ambiguous_response_no_retry_even_after_restart():
    manager, window, calls, saved = setup(error=True)
    p = manager.proposal("35", "sensor", NOW, window)
    with pytest.raises(GasError, match="submission_uncertain"):
        await manager.submit(p["id"], NOW, lambda p, w: None)
    manager2 = SubmissionManager(
        manager.state, manager.persist, manager.query, manager.write, enabled=True
    )
    p2 = manager2.proposal("35", "sensor", NOW, window)
    with pytest.raises(GasError, match="submission_uncertain"):
        await manager2.submit(p2["id"], NOW, lambda p, w: None)
    assert len(calls) == 1
    window.submitted = "35"
    assert await manager2.reconcile(window, NOW)


async def test_provider_acknowledgement_completes_before_readback_and_survives_restart():
    state, writes = {}, []
    window = MeterWindow("2026-09-13", "2026-09-18", "27", "meter", eligible=True)

    async def write(_window, value):
        writes.append(value)
        return SubmissionAcknowledgement("result_y_n")

    manager = SubmissionManager(state, AsyncMock(), AsyncMock(return_value=window), write)
    proposal = manager.proposal("35", "manual", NOW, window)
    result = await manager.submit(proposal["id"], NOW, lambda *_: None)
    assert result["status"] == "confirmed"
    assert result["accepted"] == "35"
    assert result["confirmation_source"] == "provider_response"
    assert result["acknowledgement_matcher"] == "result_y_n"
    assert result["receipt_in_latest_read"] is False

    restored = SubmissionManager(state, AsyncMock(), AsyncMock(return_value=window), write)
    again = restored.proposal("35", "manual", NOW, window)
    assert (await restored.submit(again["id"], NOW, lambda *_: None))["status"] == "confirmed"
    assert writes == [35]

    window.submitted = "35"
    assert await restored.reconcile(window, NOW)
    assert state[window.cycle]["confirmation_source"] == "readback"
    assert state[window.cycle]["acknowledgement_matcher"] == "result_y_n"


def test_known_acknowledgement_matchers_ignore_missing_unknown_and_conflicting_signals():
    assert match_submission_acknowledgement({}, (RESULT_Y_N, INPUT_Y_N)) is None
    assert (
        match_submission_acknowledgement(
            {"result": "unknown", "inputYn": " y "}, (RESULT_Y_N, INPUT_Y_N)
        ).matcher
        == "input_yn"
    )
    assert (
        match_submission_acknowledgement({"result": "Y", "inputYn": "N"}, (RESULT_Y_N, INPUT_Y_N))
        is None
    )
    assert match_submission_acknowledgement({"result": True}, (RESULT_Y_N,)) is None
    with pytest.raises(SubmissionRejected, match="submission_rejected"):
        match_submission_acknowledgement({"result": " n "}, (RESULT_Y_N,))


async def test_already_submitted_reports_real_value():
    manager, window, calls, _ = setup()
    window.submitted = "36"
    p = manager.proposal("35", "sensor", NOW, window)
    result = await manager.submit(p["id"], NOW, lambda p, w: None)
    assert result["accepted"] == "36" and not calls


@pytest.mark.parametrize("when", [NOW + timedelta(minutes=31), NOW + timedelta(days=1)])
async def test_expired_proposal(when):
    manager, window, calls, _ = setup()
    p = manager.proposal("35", "sensor", NOW, window)
    with pytest.raises(GasError):
        await manager.submit(p["id"], when, lambda p, w: None)
    assert not calls


async def test_validation_failure_before_write():
    manager, window, calls, _ = setup()
    p = manager.proposal("35", "sensor", NOW, window)

    def validate(p, w):
        raise GasError("physical_calibration_required")

    with pytest.raises(GasError, match="physical_calibration_required"):
        await manager.submit(p["id"], NOW, validate)
    assert not calls


async def test_explicit_revision_can_replace_confirmed_value_on_same_day():
    state, calls = {}, []
    window = MeterWindow("2026-09-13", "2026-09-18", "27", "meter", eligible=True, submitted="42")
    state[window.cycle] = {
        "status": "confirmed",
        "accepted": "42",
        "proposed": 42,
        "last_attempt_day": NOW.date().isoformat(),
    }

    async def write(_window, value, *, revision, revision_from):
        calls.append((value, revision, revision_from))
        window.submitted = str(value)
        return SubmissionAcknowledgement("result_y_n")

    manager = SubmissionManager(
        state,
        AsyncMock(),
        AsyncMock(return_value=window),
        write,
        supports_revision=True,
    )
    proposal = manager.proposal("44.9", "manual", NOW, window, revision=True, revision_from="42")
    result = await manager.submit(proposal["id"], NOW, lambda *_: None)

    assert calls == [(44, True, "42")]
    assert result["status"] == "confirmed"
    assert result["accepted"] == "44"
    assert result["last_revision"]["from"] == "42"
    assert result["last_revision"]["to"] == "44"


async def test_revision_proposal_is_bound_to_confirmed_value():
    state, calls = {}, []
    window = MeterWindow("2026-09-13", "2026-09-18", "27", "meter", eligible=True, submitted="42")
    state[window.cycle] = {"status": "confirmed", "accepted": "42", "proposed": 42}

    async def write(*args, **kwargs):
        calls.append((args, kwargs))

    manager = SubmissionManager(
        state,
        AsyncMock(),
        AsyncMock(return_value=window),
        write,
        supports_revision=True,
    )
    proposal = manager.proposal("44", "manual", NOW, window, revision=True, revision_from="42")
    window.submitted = "43"

    with pytest.raises(GasError, match="revision_state_changed"):
        await manager.submit(proposal["id"], NOW, lambda *_: None)

    assert not calls
    assert state[window.cycle]["status"] == "confirmed"
    assert state[window.cycle]["accepted"] == "43"


async def test_revision_target_already_confirmed_is_idempotent_without_write():
    state = {}
    window = MeterWindow("2026-09-13", "2026-09-18", "27", "meter", eligible=True, submitted="42")
    state[window.cycle] = {"status": "confirmed", "accepted": "42", "proposed": 42}
    write = AsyncMock()
    manager = SubmissionManager(
        state,
        AsyncMock(),
        AsyncMock(return_value=window),
        write,
        supports_revision=True,
    )
    proposal = manager.proposal("44", "manual", NOW, window, revision=True, revision_from="42")
    window.submitted = "44"

    result = await manager.submit(proposal["id"], NOW, lambda *_: None)

    assert result["accepted"] == "44"
    assert result["revision_idempotent"] is True
    write.assert_not_awaited()


def test_revision_requires_advertised_capability_and_sealed_prior_value():
    manager, window, _, _ = setup()
    with pytest.raises(GasError, match="revision_submission_unsupported"):
        manager.proposal("44", "manual", NOW, window, revision=True, revision_from="42")

    manager.supports_revision = True
    with pytest.raises(GasError, match="revision_submission_unavailable"):
        manager.proposal("44", "manual", NOW, window, revision=True)


async def test_provider_acknowledged_value_can_be_revised_without_readback_receipt():
    state = {}
    window = MeterWindow("2026-09-13", "2026-09-18", "27", "meter", eligible=True)
    state[window.cycle] = {
        "status": "confirmed",
        "accepted": "42",
        "proposed": 42,
        "confirmation_source": "provider_response",
    }
    write = AsyncMock(return_value=SubmissionAcknowledgement("result_y_n"))
    manager = SubmissionManager(
        state,
        AsyncMock(),
        AsyncMock(return_value=window),
        write,
        supports_revision=True,
    )
    proposal = manager.proposal("44", "manual", NOW, window, revision=True, revision_from="42")

    result = await manager.submit(proposal["id"], NOW, lambda *_: None)

    assert result["status"] == "confirmed"
    assert result["accepted"] == "44"
    assert result["revision_readback_pending"] is True
    write.assert_awaited_once_with(window, 44, revision=True, revision_from="42")


async def test_revision_provider_ack_survives_stale_old_readback():
    state = {}
    window = MeterWindow("2026-09-13", "2026-09-18", "27", "meter", eligible=True, submitted="42")
    state[window.cycle] = {"status": "confirmed", "accepted": "42", "proposed": 42}
    write = AsyncMock(return_value=SubmissionAcknowledgement("result_y_n"))
    manager = SubmissionManager(
        state,
        AsyncMock(),
        AsyncMock(return_value=window),
        write,
        supports_revision=True,
    )
    proposal = manager.proposal("44", "manual", NOW, window, revision=True, revision_from="42")

    result = await manager.submit(proposal["id"], NOW, lambda *_: None)

    assert result["status"] == "confirmed"
    assert result["accepted"] == "44"
    assert result["observed_reading"] == "42"
    assert result["revision_readback_pending"] is True
    assert write.await_count == 1


@pytest.mark.parametrize("status", ["pending", "uncertain"])
async def test_revision_never_writes_while_prior_result_is_ambiguous(status):
    state = {}
    window = MeterWindow("2026-09-13", "2026-09-18", "27", "meter", eligible=True, submitted="42")
    state[window.cycle] = {
        "status": status,
        "accepted": "42",
        "proposed": 44,
        "error": "submission_uncertain",
    }
    write = AsyncMock()
    manager = SubmissionManager(
        state,
        AsyncMock(),
        AsyncMock(return_value=window),
        write,
        supports_revision=True,
    )
    proposal = manager.proposal("45", "manual", NOW, window, revision=True, revision_from="42")

    with pytest.raises(GasError, match="submission_(uncertain|value_mismatch)"):
        await manager.submit(proposal["id"], NOW, lambda *_: None)
    write.assert_not_awaited()


async def test_ambiguous_revision_is_not_retried_after_restart():
    state = {}
    window = MeterWindow("2026-09-13", "2026-09-18", "27", "meter", eligible=True, submitted="42")
    state[window.cycle] = {"status": "confirmed", "accepted": "42", "proposed": 42}
    write = AsyncMock(side_effect=TimeoutError())
    query = AsyncMock(return_value=window)
    manager = SubmissionManager(state, AsyncMock(), query, write, supports_revision=True)
    proposal = manager.proposal("44", "manual", NOW, window, revision=True, revision_from="42")
    with pytest.raises(GasError):
        await manager.submit(proposal["id"], NOW, lambda *_: None)

    restored = SubmissionManager(state, AsyncMock(), query, write, supports_revision=True)
    retry = restored.proposal("44", "manual", NOW, window, revision=True, revision_from="42")
    with pytest.raises(GasError):
        await restored.submit(retry["id"], NOW, lambda *_: None)
    assert write.await_count == 1


async def test_negative_response_with_changed_readback_confirms_without_retry():
    state = {}
    window = MeterWindow("2026-09-13", "2026-09-18", "27", "meter", eligible=True, submitted="42")
    state[window.cycle] = {"status": "confirmed", "accepted": "42", "proposed": 42}

    async def write(_window, value, **_kwargs):
        window.submitted = str(value)
        raise SubmissionRejected("submission_rejected")

    manager = SubmissionManager(
        state,
        AsyncMock(),
        AsyncMock(return_value=window),
        write,
        supports_revision=True,
    )
    proposal = manager.proposal("44", "manual", NOW, window, revision=True, revision_from="42")

    result = await manager.submit(proposal["id"], NOW, lambda *_: None)

    assert result["status"] == "confirmed"
    assert result["accepted"] == "44"


async def test_rejected_revision_keeps_prior_receipt_and_blocks_same_day_retry():
    state = {}
    window = MeterWindow("2026-09-13", "2026-09-18", "27", "meter", eligible=True, submitted="42")
    state[window.cycle] = {"status": "confirmed", "accepted": "42", "proposed": 42}
    write = AsyncMock(side_effect=SubmissionRejected("submission_rejected"))
    manager = SubmissionManager(
        state,
        AsyncMock(),
        AsyncMock(return_value=window),
        write,
        supports_revision=True,
    )
    proposal = manager.proposal("44", "manual", NOW, window, revision=True, revision_from="42")
    with pytest.raises(GasError, match="submission_rejected"):
        await manager.submit(proposal["id"], NOW, lambda *_: None)

    assert state[window.cycle]["status"] == "confirmed"
    assert state[window.cycle]["accepted"] == "42"
    assert state[window.cycle]["revision_rejected_day"] == NOW.date().isoformat()

    retry = manager.proposal("44", "manual", NOW, window, revision=True, revision_from="42")
    with pytest.raises(GasError, match="submission_attempted_today"):
        await manager.submit(retry["id"], NOW, lambda *_: None)
    assert write.await_count == 1


async def test_revision_is_blocked_after_window_closes():
    state = {}
    window = MeterWindow("2026-09-13", "2026-09-17", "27", "meter", eligible=True, submitted="42")
    state[window.cycle] = {"status": "confirmed", "accepted": "42", "proposed": 42}
    write = AsyncMock()
    manager = SubmissionManager(
        state,
        AsyncMock(),
        AsyncMock(return_value=window),
        write,
        supports_revision=True,
    )
    proposal = manager.proposal("44", "manual", NOW, window, revision=True, revision_from="42")

    with pytest.raises(GasError, match="window_closed"):
        await manager.submit(proposal["id"], NOW, lambda *_: None)
    write.assert_not_awaited()
