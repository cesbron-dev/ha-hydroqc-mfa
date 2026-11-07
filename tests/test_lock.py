"""Tests du verrou persistant."""

from homeassistant.helpers.storage import Store

from custom_components.hydroqc_mfa.lock import RenewalLock

KEY = "hydroqc_mfa.lock"


async def test_unknown_entry_is_armed(hass, hass_storage):
    lock = RenewalLock(hass)
    assert await lock.async_load() == []
    assert lock.is_armed("abc")


async def test_begin_then_fail_not_armed(hass, hass_storage):
    lock = RenewalLock(hass)
    await lock.async_load()
    await lock.async_begin("abc")
    assert not lock.is_armed("abc")
    await lock.async_fail("abc", "code_timeout")
    assert not lock.is_armed("abc")
    assert hass_storage[KEY]["data"]["abc"] == {
        "state": "failed",
        "reason": "code_timeout",
    }


async def test_arm_after_fail(hass, hass_storage):
    lock = RenewalLock(hass)
    await lock.async_load()
    await lock.async_begin("abc")
    await lock.async_fail("abc", "code_rejected")
    await lock.async_arm("abc")
    assert lock.is_armed("abc")
    assert hass_storage[KEY]["data"]["abc"] == {"state": "armed", "reason": None}


async def test_startup_in_progress_becomes_failed(hass, hass_storage):
    hass_storage[KEY] = {
        "version": 1,
        "minor_version": 1,
        "key": KEY,
        "data": {
            "abc": {"state": "in_progress", "reason": None},
            "def": {"state": "armed", "reason": None},
        },
    }
    lock = RenewalLock(hass)
    assert await lock.async_load() == ["abc"]
    assert not lock.is_armed("abc")
    assert lock.is_armed("def")
    assert hass_storage[KEY]["data"]["abc"] == {
        "state": "failed",
        "reason": "interrupted",
    }


async def test_begin_is_persisted_before_return(hass, hass_storage):
    lock = RenewalLock(hass)
    await lock.async_load()
    await lock.async_begin("abc")
    data = await Store(hass, 1, KEY).async_load()
    assert data["abc"]["state"] == "in_progress"


async def test_succeeded_is_not_armed(hass, hass_storage):
    lock = RenewalLock(hass)
    await lock.async_load()
    await lock.async_begin("abc")
    await lock.async_succeed("abc")
    assert not lock.is_armed("abc")
    assert hass_storage[KEY]["data"]["abc"] == {"state": "succeeded", "reason": None}


async def test_startup_succeeded_stays_not_armed(hass, hass_storage):
    hass_storage[KEY] = {
        "version": 1,
        "minor_version": 1,
        "key": KEY,
        "data": {"abc": {"state": "succeeded", "reason": None}},
    }
    lock = RenewalLock(hass)
    assert await lock.async_load() == []
    assert not lock.is_armed("abc")
    assert hass_storage[KEY]["data"]["abc"] == {"state": "succeeded", "reason": None}
