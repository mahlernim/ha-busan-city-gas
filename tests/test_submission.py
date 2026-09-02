import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from custom_components.busan_city_gas.model import GasError, MeterWindow
from custom_components.busan_city_gas.submission import SubmissionManager

NOW = datetime(2026, 9, 18, 22, tzinfo=timezone.utc)


def setup(*, verified=True, error=False):
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

    return SubmissionManager(state, persist, query, write, verified=verified), window, calls, saved


async def test_write_gate_never_calls_transport():
    manager, window, calls, _ = setup(verified=False)
    proposal = manager.proposal("35.9", "sensor", NOW, window)
    with pytest.raises(GasError, match="submission_unverified"):
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
        manager.state, manager.persist, manager.query, manager.write, verified=True
    )
    p2 = manager2.proposal("35", "sensor", NOW, window)
    with pytest.raises(GasError, match="submission_uncertain"):
        await manager2.submit(p2["id"], NOW, lambda p, w: None)
    assert len(calls) == 1
    window.submitted = "35"
    assert await manager2.reconcile(window, NOW)


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
