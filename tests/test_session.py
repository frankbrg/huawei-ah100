from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from custom_components.huawei_ah100 import protocol, session

ADDRESS = "12:34:56:78:9A:BC"
MAC = bytes.fromhex(ADDRESS.replace(":", ""))
NOW = datetime(2034, 7, 12, 9, 8, 7, tzinfo=ZoneInfo("Europe/Paris"))
PROFILE = {"birth_date": "2000-08-20", "height_cm": 180, "sex": "female", "initial_weight_kg": 80.0}
AUTH = protocol.auth_token(1)


@dataclass
class Characteristic:
    uuid: str
    properties: tuple[str, ...]


class Services:
    def __init__(self, write_properties: tuple[str, ...]) -> None:
        self.characteristics = {
            session.TX_UUID: Characteristic(session.TX_UUID, write_properties),
            session.RX_UUID: Characteristic(session.RX_UUID, ("notify",)),
        }

    def get_characteristic(self, uuid: str) -> Characteristic | None:
        return self.characteristics.get(uuid)


class FakeClient:
    def __init__(self, write_properties: tuple[str, ...] = ("write",)) -> None:
        self.is_connected = True
        self.services = Services(write_properties)
        self.subscribed = asyncio.Event()
        self.writes: list[bytes] = []
        self.responses: list[bool] = []
        self.stopped: list[str] = []
        self.callback: Any = None
        self.on_write: Callable[[bytes], None] = self.accept_auth
        self.active_writes = 0
        self.max_active_writes = 0

    async def start_notify(self, characteristic: Characteristic, callback: Any) -> None:
        assert characteristic.uuid == session.RX_UUID
        self.callback = callback
        self.subscribed.set()

    async def stop_notify(self, characteristic: Characteristic) -> None:
        self.stopped.append(characteristic.uuid)

    async def write_gatt_char(
        self, characteristic: Characteristic, data: bytes, *, response: bool
    ) -> None:
        assert characteristic.uuid == session.TX_UUID
        self.active_writes += 1
        self.max_active_writes = max(self.max_active_writes, self.active_writes)
        try:
            await asyncio.sleep(0)
            frame = bytes(data)
            self.writes.append(frame)
            self.responses.append(response)
            self.on_write(frame)
        finally:
            self.active_writes -= 1

    def accept_auth(self, frame: bytes) -> None:
        if frame[2] == 0x24:
            self.emit(0x26, b"\x01")

    def emit(self, opcode: int, payload: bytes = b"") -> None:
        self.emit_frame(bytes((0xBD, len(payload) + 1, opcode)) + protocol.obfuscate(payload, MAC))

    def emit_frame(self, frame: bytes) -> None:
        assert self.callback is not None
        self.callback(self.services.get_characteristic(session.RX_UUID), bytearray(frame))

    def count(self, opcode: int) -> int:
        return sum(frame[2] == opcode for frame in self.writes)


@pytest.fixture(autouse=True)
def short_timeouts(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(session, "AUTH_TIMEOUT_SECONDS", 0.02)
    monkeypatch.setattr(session, "HEARTBEAT_INTERVAL_SECONDS", 0.02)
    monkeypatch.setattr(session, "HARD_SESSION_TIMEOUT_SECONDS", 0.5)
    monkeypatch.setattr(session, "SLEEP_GRACE_SECONDS", 0.01)
    monkeypatch.setattr(session, "WRITE_TIMEOUT_SECONDS", 0.2)
    monkeypatch.setattr(session, "DISCONNECT_POLL_SECONDS", 0.001)


async def wait_until(predicate: Callable[[], bool]) -> None:
    async with asyncio.timeout(1):
        while not predicate():
            await asyncio.sleep(0.001)


async def start_session(
    client: FakeClient,
    callback: Callable[..., Any],
    *,
    profile: dict[str, Any] | None = None,
    wake: bool = True,
    **kwargs: Any,
) -> asyncio.Task[None]:
    task = asyncio.create_task(
        session.run_session(client, ADDRESS, profile or PROFILE.copy(), callback, now=NOW, **kwargs)
    )
    await client.subscribed.wait()
    if wake:
        client.emit(0x00)
    return task


async def finish_session(client: FakeClient, task: asyncio.Task[None]) -> None:
    client.emit(0x01)
    await asyncio.wait_for(task, 1)
    assert client.stopped == [session.RX_UUID]


def measurement_frame(
    *,
    opcode: int = 0x0E,
    weight_tenths: int = 734,
    fat_tenths: int = 226,
    impedance: int = 510,
    user_id: int = 1,
    tail: bytes = b"\xa1\xb2\xc3",
    measured_at: datetime = NOW,
) -> bytes:
    payload = (
        bytes((user_id,))
        + weight_tenths.to_bytes(2, "little")
        + fat_tenths.to_bytes(2, "little")
        + measured_at.year.to_bytes(2, "little")
        + bytes(
            (
                measured_at.month,
                measured_at.day,
                measured_at.hour,
                measured_at.minute,
                measured_at.second,
                measured_at.isoweekday(),
            )
        )
        + impedance.to_bytes(2, "little")
        + tail
    )
    encrypted = protocol.aes_ctr(payload, protocol.derive_session_key(AUTH, MAC))
    return bytes((0xBC, len(encrypted) + 1, opcode)) + protocol.obfuscate(encrypted, MAC)


def user_info_payload(frame: bytes) -> bytes:
    assert frame[:3] == b"\xdc\x0e\x09"
    return protocol.aes_ctr(
        protocol.obfuscate(frame[3:], MAC), protocol.derive_session_key(AUTH, MAC)
    )


async def ignore_measurement(_measurement: dict[str, Any]) -> None:
    pass


@pytest.mark.asyncio
@pytest.mark.parametrize("write_properties", [("write",), ("write-without-response",)])
async def test_authenticated_profile_uses_local_clock_and_latest_weight(
    write_properties: tuple[str, ...],
) -> None:
    client = FakeClient(write_properties)
    profile = PROFILE | {"latest_weight_kg": 81.3}
    task = await start_session(client, ignore_measurement, profile=profile)
    await wait_until(lambda: client.count(0x09) == 1)
    assert [frame[2] for frame in client.writes[:4]] == [0x24, 0x02, 0x08, 0x09]
    assert protocol.obfuscate(client.writes[0][3:], MAC) == AUTH
    assert protocol.obfuscate(client.writes[1][3:], MAC) == b"\x01"
    assert protocol.obfuscate(client.writes[2][3:], MAC) == (2034).to_bytes(2, "little") + bytes(
        (7, 12, 9, 8, 7, 3)
    )
    assert (
        user_info_payload(client.writes[3])
        == AUTH + bytes((33 | 0x80, 180, 0)) + (813).to_bytes(2, "little") + b"\xff\xff\x1c\xe2"
    )
    assert all(response == ("write" in write_properties) for response in client.responses)
    await finish_session(client, task)


@pytest.mark.asyncio
async def test_live_measurement_is_durable_before_ack_and_saved_once() -> None:
    client = FakeClient()
    entered = asyncio.Event()
    release = asyncio.Event()
    saved: list[dict[str, Any]] = []

    async def save(measurement: dict[str, Any]) -> None:
        entered.set()
        await release.wait()
        saved.append(measurement)

    task = await start_session(client, save)
    await wait_until(lambda: client.count(0x09) == 1)
    frame = measurement_frame()
    client.emit_frame(frame)
    client.emit_frame(frame)
    await entered.wait()
    assert client.count(0x13) == 0
    release.set()
    await wait_until(lambda: client.count(0x13) == 2)
    assert len(saved) == 1
    assert saved[0]["weight_kg"] == 73.4
    assert saved[0]["fat_percent"] == 22.6
    assert saved[0]["impedance_ohm"] == 510
    assert saved[0]["unknown_tail_hex"] == "a1b2c3"
    assert str(saved[0]["raw_decrypted_hex"]).endswith("a1b2c3")
    assert all(
        protocol.obfuscate(frame[3:], MAC) == b"\x00" for frame in client.writes if frame[2] == 0x13
    )
    assert client.max_active_writes == 1
    await finish_session(client, task)


@pytest.mark.asyncio
async def test_slow_save_preserves_session_wide_deduplication_across_wakes() -> None:
    client = FakeClient()
    entered = asyncio.Event()
    release = asyncio.Event()
    saved = []

    async def save(measurement: dict[str, Any]) -> None:
        entered.set()
        await release.wait()
        saved.append(measurement)

    task = await start_session(client, save)
    await wait_until(lambda: client.count(0x09) == 1)
    client.emit_frame(measurement_frame())
    await entered.wait()
    client.emit(0x01)
    client.emit(0x00)
    await wait_until(lambda: client.count(0x09) == 2)
    assert client.count(0x24) == 2
    assert client.count(0x13) == 0
    release.set()
    await wait_until(lambda: len(saved) == 1)
    assert client.count(0x13) == 0
    client.emit_frame(measurement_frame())
    await wait_until(lambda: client.count(0x13) == 1)
    assert len(saved) == 1
    assert saved[0]["weight_kg"] == 73.4
    await finish_session(client, task)


@pytest.mark.asyncio
async def test_rejected_authentication_never_binds_by_default() -> None:
    client = FakeClient()
    client.on_write = lambda frame: client.emit(0x26, b"\x00") if frame[2] == 0x24 else None
    task = await start_session(client, ignore_measurement)
    with pytest.raises(session.AuthenticationError):
        await task
    assert client.count(0x24) == 3
    assert client.count(0x25) == 0
    assert client.count(0x09) == 0
    assert client.stopped == [session.RX_UUID]


@pytest.mark.asyncio
async def test_opt_in_binding_writes_twice_and_waits_for_ack_before_auth() -> None:
    client = FakeClient()

    def respond(frame: bytes) -> None:
        if frame[2] == 0x24:
            client.emit(0x26, b"\x00" if client.count(0x24) == 1 else b"\x01")

    client.on_write = respond
    task = await start_session(client, ignore_measurement, allow_bind=True)
    await wait_until(lambda: client.count(0x25) == 2)
    assert client.count(0x24) == 1
    assert client.count(0x09) == 0
    client.emit(0x27)
    await wait_until(lambda: client.count(0x09) == 1)
    assert [frame[2] for frame in client.writes[:4]] == [0x24, 0x25, 0x25, 0x24]
    assert all(
        protocol.obfuscate(frame[3:], MAC) == AUTH for frame in client.writes if frame[2] == 0x25
    )
    await finish_session(client, task)


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [None, b"", b"\x02"])
async def test_opt_in_binding_does_not_follow_missing_or_invalid_auth_results(
    status: bytes | None,
) -> None:
    client = FakeClient()
    client.on_write = lambda frame: (
        client.emit(0x26, status) if frame[2] == 0x24 and status is not None else None
    )
    task = await start_session(client, ignore_measurement, allow_bind=True)
    with pytest.raises(session.AuthenticationError):
        await task
    assert client.count(0x24) == 3
    assert client.count(0x25) == 0
    assert client.stopped == [session.RX_UUID]


@pytest.mark.asyncio
async def test_binding_does_not_exceed_three_auth_commands_per_wake() -> None:
    client = FakeClient()

    def respond(frame: bytes) -> None:
        if frame[2] == 0x24:
            client.emit(0x26, b"\x00")
        elif frame[2] == 0x25 and client.count(0x25) == 2:
            client.emit(0x27)

    client.on_write = respond
    task = await start_session(client, ignore_measurement, allow_bind=True)
    with pytest.raises(session.AuthenticationError):
        await task
    assert client.count(0x24) == 3
    assert client.count(0x25) == 2
    assert client.count(0x09) == 0
    assert client.stopped == [session.RX_UUID]


@pytest.mark.asyncio
async def test_user_id_zero_is_supported() -> None:
    client = FakeClient()
    task = await start_session(client, ignore_measurement, user_id=0)
    await wait_until(lambda: client.count(0x09) == 1)
    assert protocol.obfuscate(client.writes[0][3:], MAC) == protocol.auth_token(0)
    await finish_session(client, task)


@pytest.mark.asyncio
async def test_binding_without_ack_fails_without_followup_auth() -> None:
    client = FakeClient()
    client.on_write = lambda frame: client.emit(0x26, b"\x00") if frame[2] == 0x24 else None
    task = await start_session(client, ignore_measurement, allow_bind=True)
    with pytest.raises(session.AuthenticationError):
        await task
    assert client.count(0x24) == 1
    assert client.count(0x25) == 2
    assert client.count(0x09) == 0
    assert client.stopped == [session.RX_UUID]


@pytest.mark.asyncio
async def test_history_is_ignored_and_refresh_uses_last_live_weight_with_limit() -> None:
    client = FakeClient()
    saved: list[dict[str, Any]] = []

    async def save(measurement: dict[str, Any]) -> None:
        saved.append(measurement)

    task = await start_session(client, save)
    await wait_until(lambda: client.count(0x09) == 1)
    client.emit_frame(measurement_frame(opcode=0x10, weight_tenths=912, user_id=7))
    client.emit(0x20)
    await wait_until(lambda: client.count(0x09) == 2)
    assert not saved
    assert client.count(0x13) == 0
    refresh = [frame for frame in client.writes if frame[2] == 0x09][-1]
    assert int.from_bytes(user_info_payload(refresh)[10:12], "little") == 800
    client.emit_frame(measurement_frame())
    await wait_until(lambda: client.count(0x13) == 1)
    for _ in range(5):
        client.emit(0x20)
        await asyncio.sleep(0.005)
    refreshes = [frame for frame in client.writes if frame[2] == 0x09]
    assert len(refreshes) == 4
    assert all(
        int.from_bytes(user_info_payload(frame)[10:12], "little") == 734 for frame in refreshes[2:]
    )
    assert len(saved) == 1
    await finish_session(client, task)


@pytest.mark.asyncio
async def test_missing_composition_keeps_weight_and_raw_fields() -> None:
    client = FakeClient()
    saved: list[dict[str, Any]] = []

    async def save(measurement: dict[str, Any]) -> None:
        saved.append(measurement)

    task = await start_session(client, save)
    await wait_until(lambda: client.count(0x09) == 1)
    client.emit_frame(measurement_frame(fat_tenths=0xFFFF, impedance=0))
    await wait_until(lambda: client.count(0x13) == 1)
    assert saved[0]["weight_kg"] == 73.4
    assert saved[0]["fat_percent"] is None
    assert saved[0]["fat_raw_tenths"] == 0xFFFF
    assert saved[0]["impedance_ohm"] is None
    assert saved[0]["impedance_raw"] == 0
    await finish_session(client, task)


@pytest.mark.asyncio
async def test_failed_durable_save_never_acks_and_cleans_up_tasks() -> None:
    client = FakeClient()
    before = asyncio.all_tasks()

    async def fail_save(_measurement: dict[str, Any]) -> None:
        raise RuntimeError("synthetic storage failure")

    task = await start_session(client, fail_save)
    await wait_until(lambda: client.count(0x09) == 1)
    client.emit_frame(measurement_frame())
    with pytest.raises(RuntimeError, match="synthetic storage failure"):
        await task
    assert client.count(0x13) == 0
    assert client.stopped == [session.RX_UUID]
    assert asyncio.all_tasks() == before


@pytest.mark.asyncio
async def test_sleep_allows_an_active_durable_save_to_finish() -> None:
    client = FakeClient()
    entered = asyncio.Event()
    release = asyncio.Event()
    completed = asyncio.Event()

    async def save(_measurement: dict[str, Any]) -> None:
        entered.set()
        await release.wait()
        completed.set()

    task = await start_session(client, save)
    await wait_until(lambda: client.count(0x09) == 1)
    client.emit_frame(measurement_frame())
    await entered.wait()
    client.emit(0x01)
    await asyncio.sleep(0.03)
    assert not task.done()
    release.set()
    await asyncio.wait_for(task, 1)
    assert completed.is_set()
    assert client.count(0x13) == 0
    assert client.stopped == [session.RX_UUID]


@pytest.mark.asyncio
async def test_awake_weighing_continues_past_idle_timeout_with_heartbeats() -> None:
    client = FakeClient()
    task = await start_session(client, ignore_measurement, session_timeout=0.03)
    await wait_until(lambda: client.count(0x09) == 1)
    await asyncio.sleep(0.07)
    assert not task.done()
    assert client.count(0x20) >= 2
    client.emit_frame(measurement_frame())
    await wait_until(lambda: client.count(0x13) == 1)
    await finish_session(client, task)


@pytest.mark.asyncio
async def test_hard_timeout_cleans_up_awake_session(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(session, "HARD_SESSION_TIMEOUT_SECONDS", 0.06)
    client = FakeClient()
    task = await start_session(client, ignore_measurement, session_timeout=0.01)
    with pytest.raises(TimeoutError, match="maximum duration"):
        await task
    assert client.count(0x09) == 1
    assert client.stopped == [session.RX_UUID]


@pytest.mark.asyncio
async def test_idle_timeout_disconnect_and_cancellation_release_subscription() -> None:
    idle_client = FakeClient()
    idle = await start_session(idle_client, ignore_measurement, wake=False, session_timeout=0.01)
    await asyncio.wait_for(idle, 1)
    assert not idle_client.writes
    assert idle_client.stopped == [session.RX_UUID]

    disconnected_client = FakeClient()
    disconnected = await start_session(disconnected_client, ignore_measurement)
    await wait_until(lambda: disconnected_client.count(0x09) == 1)
    disconnected_client.is_connected = False
    await asyncio.wait_for(disconnected, 1)
    assert disconnected_client.stopped == [session.RX_UUID]

    cancelled_client = FakeClient()
    cancelled = await start_session(cancelled_client, ignore_measurement, wake=False)
    cancelled.cancel()
    with pytest.raises(asyncio.CancelledError):
        await cancelled
    assert cancelled_client.stopped == [session.RX_UUID]


async def test_graceful_stop_exits_idle_session_without_waiting_for_idle_timeout() -> None:
    client = FakeClient()
    stop = asyncio.Event()
    task = await start_session(client, ignore_measurement, wake=False, stop_event=stop)
    stop.set()
    await asyncio.wait_for(task, 0.1)
    assert not client.writes
    assert client.stopped == [session.RX_UUID]


async def test_graceful_stop_preserves_awake_weighing_save_and_ack_until_sleep() -> None:
    client = FakeClient()
    stop = asyncio.Event()
    entered = asyncio.Event()
    release = asyncio.Event()
    saved = []

    async def save(measurement):
        entered.set()
        await release.wait()
        saved.append(measurement["weight_kg"])

    task = await start_session(client, save, stop_event=stop)
    await wait_until(lambda: client.count(0x09) == 1)
    stop.set()
    await asyncio.sleep(0.01)
    assert not task.done()
    client.emit_frame(measurement_frame())
    await asyncio.wait_for(entered.wait(), 1)
    assert client.count(0x13) == 0
    release.set()
    await wait_until(lambda: client.count(0x13) == 1)
    assert not task.done()
    await finish_session(client, task)
    assert saved == [73.4]


async def test_graceful_stop_drains_sleeping_save_and_ignores_another_wake() -> None:
    client = FakeClient()
    stop = asyncio.Event()
    entered = asyncio.Event()
    release = asyncio.Event()
    completed = asyncio.Event()

    async def save(_measurement):
        entered.set()
        await release.wait()
        completed.set()

    task = await start_session(client, save, stop_event=stop)
    await wait_until(lambda: client.count(0x09) == 1)
    client.emit_frame(measurement_frame())
    await asyncio.wait_for(entered.wait(), 1)
    stop.set()
    client.emit(0x01)
    client.emit(0x00)
    await asyncio.sleep(0.01)
    assert not task.done()
    assert client.count(0x24) == 1
    release.set()
    await asyncio.wait_for(task, 1)
    assert completed.is_set()
    assert client.count(0x13) == 0


async def test_force_cancel_drains_an_in_progress_durable_measurement() -> None:
    client = FakeClient()
    entered = asyncio.Event()
    release = asyncio.Event()
    completed = asyncio.Event()

    async def save(_measurement):
        entered.set()
        await release.wait()
        completed.set()

    task = await start_session(client, save)
    await wait_until(lambda: client.count(0x09) == 1)
    client.emit_frame(measurement_frame())
    await asyncio.wait_for(entered.wait(), 1)
    task.cancel()
    await asyncio.sleep(0.01)
    assert not task.done()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert completed.is_set()
    assert client.count(0x13) == 0
    assert client.stopped == [session.RX_UUID]


async def test_graceful_stop_keeps_hard_deadline(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(session, "HARD_SESSION_TIMEOUT_SECONDS", 0.04)
    client = FakeClient()
    stop = asyncio.Event()
    task = await start_session(client, ignore_measurement, stop_event=stop, session_timeout=0.01)
    await wait_until(lambda: client.count(0x09) == 1)
    stop.set()
    with pytest.raises(TimeoutError, match="maximum duration"):
        await task
    assert client.stopped == [session.RX_UUID]


async def test_session_copies_profile_before_awaiting_client() -> None:
    client = FakeClient()
    source_profile = dict(PROFILE)
    task = await start_session(client, ignore_measurement, profile=source_profile, wake=False)
    source_profile["height_cm"] = 100
    source_profile["sex"] = "male"
    client.emit(0x00)
    await wait_until(lambda: client.count(0x09) == 1)
    info = next(frame for frame in client.writes if frame[2] == 0x09)
    assert user_info_payload(info)[8:10] == bytes((180, 0))
    await finish_session(client, task)


async def test_graceful_stop_preserves_sleep_grace_for_a_late_measurement(monkeypatch):
    monkeypatch.setattr(session, "SLEEP_GRACE_SECONDS", 0.04)
    client = FakeClient()
    stop = asyncio.Event()
    saved = []

    async def save(measurement):
        saved.append(measurement["weight_kg"])

    task = await start_session(client, save, stop_event=stop)
    await wait_until(lambda: client.count(0x09) == 1)
    stop.set()
    client.emit(0x01)
    await asyncio.sleep(0.01)
    assert not task.done()
    client.emit_frame(measurement_frame())
    await asyncio.wait_for(task, 1)
    assert saved == [73.4]
    assert client.count(0x13) == 0
    assert client.stopped == [session.RX_UUID]


async def test_confirmation_authenticates_and_heartbeats_without_sending_an_unclaimed_profile():
    client = FakeClient()
    selected = asyncio.get_running_loop().create_future()
    cancelled = []
    current_time = NOW

    async def resolve(wake_id):
        assert wake_id == 1
        return await selected

    task = await start_session(
        client,
        ignore_measurement,
        resolve_profile=resolve,
        cancel_profile=lambda wake_id, reason: cancelled.append((wake_id, reason)),
        now_factory=lambda: current_time,
    )
    await wait_until(lambda: client.count(0x24) == 1)
    await wait_until(lambda: client.count(0x20) >= 1)
    assert client.count(0x09) == 0
    current_time = NOW + timedelta(seconds=20)
    selected.set_result(
        session.SessionProfile(PROFILE | {"height_cm": 165, "sex": "male"}, ignore_measurement)
    )
    await wait_until(lambda: client.count(0x09) == 1)
    info = next(frame for frame in client.writes if frame[2] == 0x09)
    assert user_info_payload(info)[7:9] == bytes((33, 165))
    clock = next(frame for frame in client.writes if frame[2] == 0x08)
    assert protocol.obfuscate(clock[3:], MAC)[6] == 27
    assert not any(reason == "retry_required" for _, reason in cancelled)
    await finish_session(client, task)


@pytest.mark.parametrize("early_opcode", [0x0F, 0x0E])
async def test_early_stable_or_measurement_requires_reweigh_even_after_claim(early_opcode):
    client = FakeClient()
    selected = asyncio.get_running_loop().create_future()
    saved = []
    cancelled = []

    async def resolve(_wake_id):
        return await selected

    async def save(measurement):
        saved.append(measurement)

    task = await start_session(
        client,
        ignore_measurement,
        resolve_profile=resolve,
        cancel_profile=lambda wake_id, reason: cancelled.append((wake_id, reason)),
    )
    await wait_until(lambda: client.count(0x24) == 1)
    if early_opcode == 0x0E:
        client.emit_frame(measurement_frame())
    else:
        client.emit(early_opcode)
    selected.set_result(session.SessionProfile(PROFILE, save))
    await asyncio.sleep(0.01)
    assert client.count(0x09) == 0
    assert client.count(0x13) == 0
    assert saved == []
    assert (1, "retry_required") in cancelled
    await finish_session(client, task)


async def test_measurement_during_configuration_is_rejected_without_acknowledging():
    client = FakeClient()
    saved = []
    cancelled = []

    async def save(measurement):
        saved.append(measurement)

    async def resolve(_wake_id):
        return session.SessionProfile(PROFILE, save)

    def on_write(frame):
        client.accept_auth(frame)
        if frame[2] == 0x08:
            client.emit_frame(measurement_frame(measured_at=NOW + timedelta(seconds=1)))

    client.on_write = on_write
    task = await start_session(
        client,
        ignore_measurement,
        resolve_profile=resolve,
        cancel_profile=lambda wake_id, reason: cancelled.append((wake_id, reason)),
    )
    await wait_until(lambda: client.count(0x08) == 1)
    await asyncio.sleep(0.01)
    assert client.count(0x09) == 0
    assert client.count(0x13) == 0
    assert saved == []
    assert (1, "retry_required") in cancelled
    await finish_session(client, task)


@pytest.mark.parametrize(
    "measured_at",
    [NOW, NOW - timedelta(seconds=30), NOW + timedelta(seconds=60), NOW.replace(year=1900)],
)
async def test_confirmation_rejects_nonfresh_future_or_unknown_device_timestamps(measured_at):
    client = FakeClient()
    saved = []
    cancelled = []
    current_time = NOW

    async def save(measurement):
        saved.append(measurement)

    async def resolve(_wake_id):
        return session.SessionProfile(PROFILE, save)

    task = await start_session(
        client,
        ignore_measurement,
        resolve_profile=resolve,
        cancel_profile=lambda wake_id, reason: cancelled.append((wake_id, reason)),
        now_factory=lambda: current_time,
    )
    await wait_until(lambda: client.count(0x09) == 1)
    current_time = NOW + timedelta(seconds=2)
    client.emit_frame(measurement_frame(measured_at=measured_at))
    await wait_until(lambda: (1, "retry_required") in cancelled)
    assert client.count(0x13) == 0
    assert saved == []
    await finish_session(client, task)


async def test_confirmation_accepts_fresh_composition_without_requiring_a_stable_precursor():
    client = FakeClient()
    saved = []
    current_time = NOW

    async def save(measurement):
        saved.append(measurement)

    async def resolve(_wake_id):
        return session.SessionProfile(PROFILE, save)

    task = await start_session(
        client,
        ignore_measurement,
        resolve_profile=resolve,
        now_factory=lambda: current_time,
    )
    await wait_until(lambda: client.count(0x09) == 1)
    current_time = NOW + timedelta(seconds=2)
    client.emit_frame(measurement_frame(measured_at=NOW + timedelta(seconds=1)))
    await wait_until(lambda: client.count(0x13) == 1)
    assert len(saved) == 1
    assert saved[0]["weight_kg"] == 73.4
    await finish_session(client, task)


async def test_slow_save_keeps_its_original_profile_and_deduplicates_replay_across_wakes():
    client = FakeClient()
    entered = asyncio.Event()
    release = asyncio.Event()
    saved = []
    cancelled = []
    current_time = NOW

    async def save_a(measurement):
        entered.set()
        await release.wait()
        saved.append(("alice", measurement["weight_kg"]))

    async def save_b(measurement):
        saved.append(("bob", measurement["weight_kg"]))

    async def resolve(wake_id):
        return session.SessionProfile(
            PROFILE | {"height_cm": 180 if wake_id == 1 else 165},
            save_a if wake_id == 1 else save_b,
        )

    task = await start_session(
        client,
        ignore_measurement,
        resolve_profile=resolve,
        cancel_profile=lambda wake_id, reason: cancelled.append((wake_id, reason)),
        now_factory=lambda: current_time,
    )
    await wait_until(lambda: client.count(0x09) == 1)
    current_time += timedelta(seconds=2)
    client.emit_frame(measurement_frame(measured_at=NOW + timedelta(seconds=1)))
    await entered.wait()
    client.emit(0x01)
    current_time += timedelta(seconds=10)
    second_configured = current_time
    client.emit(0x00)
    await wait_until(lambda: client.count(0x09) == 2)
    release.set()
    await wait_until(lambda: saved == [("alice", 73.4)])
    assert client.count(0x13) == 0
    current_time += timedelta(seconds=2)
    client.emit_frame(measurement_frame(measured_at=NOW + timedelta(seconds=1)))
    await wait_until(lambda: client.count(0x13) == 1)
    assert saved == [("alice", 73.4)]
    assert not any(reason == "retry_required" for _, reason in cancelled)
    client.emit_frame(
        measurement_frame(weight_tenths=650, measured_at=second_configured + timedelta(seconds=1))
    )
    await wait_until(lambda: client.count(0x13) == 2)
    assert saved == [("alice", 73.4), ("bob", 65.0)]
    assert user_info_payload([frame for frame in client.writes if frame[2] == 0x09][1])[8] == 165
    await finish_session(client, task)


async def test_confirmation_invalid_decoded_timestamp_fails_closed_without_stopping_listener(
    monkeypatch,
):
    client = FakeClient()
    cancelled = []
    saved = []
    decoded = protocol.decode_measurement(measurement_frame(), AUTH, MAC)
    monkeypatch.setattr(
        protocol, "decode_measurement", lambda *_args: decoded | {"measured_at": "invalid-date"}
    )

    async def save(measurement):
        saved.append(measurement)

    async def resolve(_wake_id):
        return session.SessionProfile(PROFILE, save)

    task = await start_session(
        client,
        ignore_measurement,
        resolve_profile=resolve,
        cancel_profile=lambda wake_id, reason: cancelled.append((wake_id, reason)),
    )
    await wait_until(lambda: client.count(0x09) == 1)
    client.emit_frame(measurement_frame())
    await wait_until(lambda: (1, "retry_required") in cancelled)
    assert not task.done()
    assert saved == []
    assert client.count(0x13) == 0
    await finish_session(client, task)


@pytest.mark.parametrize(
    ("configured_at", "measured_at", "received_at"),
    [
        (
            datetime(2026, 10, 25, 2, 30, tzinfo=ZoneInfo("Europe/Paris")),
            datetime(2026, 10, 25, 2, 30, 1),
            datetime(2026, 10, 25, 2, 30, 2, tzinfo=ZoneInfo("Europe/Paris")),
        ),
        (
            datetime(2026, 10, 25, 1, 59, 59, tzinfo=ZoneInfo("Europe/Paris")),
            datetime(2026, 10, 25, 2, 0),
            datetime(2026, 10, 25, 3, 0, tzinfo=ZoneInfo("Europe/Paris")),
        ),
    ],
)
async def test_confirmation_rejects_ambiguous_dst_clock_or_measurement_wall_time(
    configured_at, measured_at, received_at
):
    client = FakeClient()
    cancelled = []
    saved = []
    current_time = configured_at

    async def save(measurement):
        saved.append(measurement)

    async def resolve(_wake_id):
        return session.SessionProfile(PROFILE, save)

    task = await start_session(
        client,
        ignore_measurement,
        resolve_profile=resolve,
        cancel_profile=lambda wake_id, reason: cancelled.append((wake_id, reason)),
        now_factory=lambda: current_time,
    )
    await wait_until(lambda: client.count(0x09) == 1)
    current_time = received_at
    client.emit_frame(measurement_frame(measured_at=measured_at))
    await wait_until(lambda: (1, "retry_required") in cancelled)
    assert saved == []
    assert client.count(0x13) == 0
    await finish_session(client, task)
