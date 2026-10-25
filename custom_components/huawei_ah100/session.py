"""Run one authenticated BLE session and process live scale notifications."""

from __future__ import annotations

import asyncio
import math
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

from . import protocol

TX_UUID = "0000faa1-0000-1000-8000-00805f9b34fb"
RX_UUID = "0000faa2-0000-1000-8000-00805f9b34fb"
AUTH_TIMEOUT_SECONDS = 5.0
HEARTBEAT_INTERVAL_SECONDS = 2.0
HARD_SESSION_TIMEOUT_SECONDS = 300.0
SLEEP_GRACE_SECONDS = 1.0
WRITE_TIMEOUT_SECONDS = 10.0
DISCONNECT_POLL_SECONDS = 0.25
MEASUREMENT_CLOCK_TOLERANCE_SECONDS = 2.0

type MeasurementCallback = Callable[[dict[str, Any]], Awaitable[None]]


@dataclass(frozen=True)
class SessionProfile:
    """Profile snapshot and callback used to persist its live measurement."""

    profile: dict[str, Any]
    on_measurement: MeasurementCallback


@dataclass(frozen=True)
class PendingMeasurement:
    """Live frame retained with the session state needed to validate and save it."""

    frame: bytes
    wake_id: int
    on_measurement: MeasurementCallback
    configured_clock: datetime | None
    received_at: datetime


def _measurement_is_fresh(pending: PendingMeasurement, measurement: dict[str, Any]) -> bool:
    """Check that device time follows profile setup and is plausible at receipt."""
    clock = pending.configured_clock
    if clock is None:
        return True
    measured_at = measurement.get("measured_at")
    if not isinstance(measured_at, str):
        return False
    try:
        device_time = datetime.fromisoformat(measured_at)
    except ValueError:
        return False
    if device_time.tzinfo is not None:
        return False
    time_zone = clock.tzinfo
    received_at = pending.received_at
    if time_zone is not None and received_at.tzinfo is not None:
        received_at = received_at.astimezone(time_zone)
    if time_zone is not None and any(
        value.replace(tzinfo=time_zone, fold=0).utcoffset()
        != value.replace(tzinfo=time_zone, fold=1).utcoffset()
        for value in (clock, device_time, received_at)
    ):
        return False
    return (
        clock.replace(tzinfo=None)
        < device_time
        <= received_at.replace(tzinfo=None) + timedelta(seconds=MEASUREMENT_CLOCK_TOLERANCE_SECONDS)
    )


class AuthenticationError(Exception):
    """Raised when the scale rejects authentication after any allowed bind attempt."""


async def run_session(
    client: Any,
    address: str,
    profile: dict[str, Any] | None,
    on_measurement: MeasurementCallback,
    *,
    allow_bind: bool = False,
    user_id: int = 1,
    now: datetime,
    session_timeout: float = 120.0,
    stop_event: asyncio.Event | None = None,
    resolve_profile: Callable[[int], Awaitable[SessionProfile | None]] | None = None,
    cancel_profile: Callable[[int, str], None] | None = None,
    now_factory: Callable[[], datetime] | None = None,
) -> None:
    """Run the BLE connection until sleep, disconnect, timeout, or stop."""
    if isinstance(user_id, bool) or not isinstance(user_id, int) or not 0 <= user_id <= 255:
        raise ValueError("user ID must be an integer from 0 to 255")
    if not math.isfinite(session_timeout) or session_timeout <= 0:
        raise ValueError("session timeout must be positive and finite")
    mac = bytes.fromhex(address.replace(":", ""))
    if len(mac) != 6:
        raise ValueError("address must contain six octets")
    session = _Session(
        client,
        mac,
        dict(profile) if profile is not None else None,
        on_measurement,
        allow_bind,
        user_id,
        now,
        session_timeout,
        stop_event,
        resolve_profile,
        cancel_profile,
        now_factory,
    )
    await session.run()


class _Session:
    def __init__(
        self,
        client: Any,
        mac: bytes,
        profile: dict[str, Any] | None,
        on_measurement: MeasurementCallback,
        allow_bind: bool,
        user_id: int,
        now: datetime,
        session_timeout: float,
        stop_event: asyncio.Event | None,
        resolve_profile: Callable[[int], Awaitable[SessionProfile | None]] | None,
        cancel_profile: Callable[[int, str], None] | None,
        now_factory: Callable[[], datetime] | None,
    ) -> None:
        self._client = client
        self._mac = mac
        self._profile = profile or {}
        self._on_measurement = on_measurement
        self._resolve_profile = resolve_profile
        self._cancel_profile_callback = cancel_profile
        self._now = now
        self._now_factory = now_factory
        self._allow_bind = allow_bind
        self._auth = protocol.auth_token(user_id)
        self._age = 0
        self._weight = 0.0
        if profile is not None:
            self._set_profile(profile, now)
        self._clock = b""
        self._configured_clock: datetime | None = None
        self._session_timeout = session_timeout
        self._stop_event = stop_event
        self._awake = False
        self._authenticated = False
        self._configured = False
        self._auth_waiting: int | None = None
        self._bind_waiting: int | None = None
        self._bind_attempted = False
        self._closed = False
        self._wake_id = 0
        self._refresh_count = 0
        self._pending_measurements = 0
        self._stop_at: float | None = None
        self._auth_results: asyncio.Queue[bool] = asyncio.Queue()
        self._bind_results: asyncio.Queue[bool] = asyncio.Queue()
        self._measurements: asyncio.Queue[PendingMeasurement] = asyncio.Queue()
        self._wake_event = asyncio.Event()
        self._sleep_event = asyncio.Event()
        self._user_changed = asyncio.Event()
        self._configured_event = asyncio.Event()
        self._write_lock = asyncio.Lock()
        self._saved_measurements: set[str] = set()
        self._profile_task: asyncio.Task[SessionProfile | None] | None = None
        self._profile_tasks: set[asyncio.Task[SessionProfile | None]] = set()
        self._early_measurement = False

    def _set_profile(self, profile: dict[str, Any], now: datetime) -> None:
        self._profile = dict(profile)
        birth_date = date.fromisoformat(profile["birth_date"])
        self._age = (
            now.year - birth_date.year - ((now.month, now.day) < (birth_date.month, birth_date.day))
        )
        latest_weight = profile.get("latest_weight_kg")
        self._weight = profile["initial_weight_kg"] if latest_weight is None else latest_weight

    def _current_time(self) -> datetime:
        return self._now_factory() if self._now_factory is not None else self._now

    def _cancel_profile(self, wake_id: int, reason: str) -> None:
        if reason == "retry_required" and wake_id == self._wake_id:
            self._early_measurement = True
            self._configured = False
        if self._cancel_profile_callback is not None:
            self._cancel_profile_callback(wake_id, reason)

    async def run(self) -> None:
        if not self._client.is_connected:
            raise ConnectionError("BLE device is disconnected")
        self._tx = self._client.services.get_characteristic(TX_UUID)
        rx = self._client.services.get_characteristic(RX_UUID)
        if self._tx is None or rx is None:
            raise RuntimeError("Huawei service is missing FAA1 or FAA2")
        properties = {str(value).lower() for value in self._tx.properties}
        if not {"write", "write-without-response"} & properties:
            raise RuntimeError("FAA1 does not support writes")
        self._write_response = "write" in properties
        started_at = asyncio.get_running_loop().time()
        tasks: list[asyncio.Task[None]] = []
        subscribed = False
        try:
            await asyncio.wait_for(
                self._client.start_notify(rx, self._notification), WRITE_TIMEOUT_SECONDS
            )
            subscribed = True
            tasks = [
                asyncio.create_task(self._authenticate_wakes()),
                asyncio.create_task(self._send_heartbeats()),
                asyncio.create_task(self._save_measurements()),
                asyncio.create_task(self._refresh_user_info()),
                asyncio.create_task(self._wait_until_stopped(started_at)),
            ]
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                task.result()
        finally:
            self._closed = True
            self._cancel_profile(self._wake_id, "session_ended")
            for task in tasks:
                task.cancel()
            for task in self._profile_tasks:
                task.cancel()
            await asyncio.gather(*tasks, *self._profile_tasks, return_exceptions=True)
            if subscribed:
                try:
                    await asyncio.wait_for(self._client.stop_notify(rx), WRITE_TIMEOUT_SECONDS)
                except Exception:
                    pass

    def _notification(self, _sender: Any, data: bytearray) -> None:
        if self._closed:
            return
        frame = bytes(data)
        try:
            parsed = protocol.parse_notification(frame, self._mac)
        except ValueError:
            return
        opcode = parsed["opcode"]
        if opcode == 0x00 and not self._awake:
            if self._stop_event is not None and self._stop_event.is_set():
                return
            self._cancel_profile(self._wake_id, "superseded")
            self._wake_id += 1
            self._awake = True
            self._authenticated = False
            self._configured = False
            self._configured_clock = None
            self._early_measurement = False
            self._stop_at = None
            self._sleep_event.clear()
            self._configured_event.clear()
            self._user_changed.clear()
            self._wake_event.set()
            if self._resolve_profile is not None:
                self._profile_task = asyncio.create_task(self._resolve_profile(self._wake_id))
                self._profile_tasks.add(self._profile_task)
                self._profile_task.add_done_callback(self._profile_tasks.discard)
        elif opcode == 0x01:
            self._awake = False
            self._stop_at = asyncio.get_running_loop().time() + SLEEP_GRACE_SECONDS
            self._sleep_event.set()
            self._cancel_profile(self._wake_id, "sleep")
        elif opcode == 0x26 and self._auth_waiting == self._wake_id:
            status = bytes.fromhex(str(parsed["payload_hex"]))[:1]
            if status in (b"\x00", b"\x01"):
                self._auth_results.put_nowait(status == b"\x01")
        elif opcode == 0x27 and self._bind_waiting == self._wake_id:
            self._bind_results.put_nowait(True)
        elif opcode == 0x20 and self._authenticated:
            self._user_changed.set()
        elif opcode == 0x0F and self._resolve_profile is not None and not self._configured:
            # Do not attach an early scale response to a profile that is not configured yet.
            self._cancel_profile(self._wake_id, "retry_required")
        elif opcode == 0x0E and parsed["payload_is_encrypted"]:
            if self._resolve_profile is not None and not self._configured:
                # Reconfirm on a fresh wake before accepting a reading from this session.
                self._cancel_profile(self._wake_id, "retry_required")
            elif self._authenticated:
                self._pending_measurements += 1
                self._measurements.put_nowait(
                    PendingMeasurement(
                        frame,
                        self._wake_id,
                        self._on_measurement,
                        self._configured_clock if self._resolve_profile is not None else None,
                        self._current_time(),
                    )
                )

    def _active(self, wake_id: int | None = None) -> bool:
        return (
            self._awake
            and self._client.is_connected
            and (wake_id is None or wake_id == self._wake_id)
        )

    async def _write_frame(self, frame: bytes, wake_id: int | None = None) -> bool:
        if not self._active(wake_id):
            return False
        await asyncio.wait_for(
            self._client.write_gatt_char(self._tx, frame, response=self._write_response),
            WRITE_TIMEOUT_SECONDS,
        )
        return True

    async def _write_command(self, opcode: int, payload: bytes, wake_id: int | None = None) -> bool:
        async with self._write_lock:
            return await self._write_frame(
                protocol.plain_command(opcode, payload, self._mac), wake_id
            )

    async def _wait_status(self, queue: asyncio.Queue[bool]) -> bool | None:
        response = asyncio.create_task(queue.get())
        sleeping = asyncio.create_task(self._sleep_event.wait())
        try:
            done, _ = await asyncio.wait(
                (response, sleeping),
                timeout=AUTH_TIMEOUT_SECONDS,
                return_when=asyncio.FIRST_COMPLETED,
            )
            return response.result() if response in done else None
        finally:
            for task in (response, sleeping):
                task.cancel()
            await asyncio.gather(response, sleeping, return_exceptions=True)

    async def _authenticate_once(self, wake_id: int) -> bool | None:
        while not self._auth_results.empty():
            self._auth_results.get_nowait()
        self._auth_waiting = wake_id
        try:
            if not await self._write_command(0x24, self._auth, wake_id):
                return None
            return await self._wait_status(self._auth_results)
        finally:
            self._auth_waiting = None

    async def _bind(self, wake_id: int) -> bool:
        self._bind_attempted = True
        self._bind_waiting = wake_id
        try:
            async with self._write_lock:
                for _ in range(2):
                    if not await self._write_frame(
                        protocol.plain_command(0x25, self._auth, self._mac), wake_id
                    ):
                        return False
            return await self._wait_status(self._bind_results) is True
        finally:
            self._bind_waiting = None

    async def _authenticate_wakes(self) -> None:
        while True:
            await self._wake_event.wait()
            self._wake_event.clear()
            wake_id = self._wake_id
            for attempt in range(3):
                if not self._active(wake_id):
                    break
                result = await self._authenticate_once(wake_id)
                if not self._active(wake_id):
                    break
                if result is True:
                    self._authenticated = True
                    if self._resolve_profile is not None:
                        if self._profile_task is None:
                            break
                        profile = await self._profile_task
                        if profile is None or not self._active(wake_id) or self._early_measurement:
                            break
                        self._set_profile(profile.profile, self._current_time())
                        self._on_measurement = profile.on_measurement
                    await self._configure_profile(wake_id)
                    break
                if (
                    result is False
                    and self._allow_bind
                    and not self._bind_attempted
                    and attempt < 2
                ):
                    if not await self._bind(wake_id):
                        if self._active(wake_id):
                            raise AuthenticationError("BLE binding was not acknowledged")
                        break
            else:
                raise AuthenticationError("BLE authentication was rejected or timed out")

    def _user_info_frame(self) -> bytes:
        return protocol.user_info_command(
            self._auth,
            self._mac,
            self._age,
            self._profile["height_cm"],
            self._profile["sex"],
            self._weight,
        )

    async def _configure_profile(self, wake_id: int) -> None:
        now = self._current_time()
        self._clock = now.year.to_bytes(2, "little") + bytes(
            (now.month, now.day, now.hour, now.minute, now.second, now.isoweekday())
        )
        configured_clock = now.replace(microsecond=0)
        async with self._write_lock:
            for frame in (
                protocol.plain_command(0x02, b"\x01", self._mac),
                protocol.plain_command(0x08, self._clock, self._mac),
                self._user_info_frame(),
            ):
                if self._resolve_profile is not None and self._early_measurement:
                    return
                if not await self._write_frame(frame, wake_id):
                    return
        if self._active(wake_id) and not self._early_measurement:
            self._configured_clock = configured_clock
            self._configured = True
            self._configured_event.set()

    async def _refresh_user_info(self) -> None:
        while True:
            await self._user_changed.wait()
            self._user_changed.clear()
            await self._configured_event.wait()
            if self._active() and self._configured and self._refresh_count < 3:
                self._refresh_count += 1
                async with self._write_lock:
                    await self._write_frame(self._user_info_frame())

    async def _save_measurements(self) -> None:
        while True:
            pending = await self._measurements.get()
            try:
                try:
                    measurement = protocol.decode_measurement(pending.frame, self._auth, self._mac)
                except ValueError:
                    continue
                signature = str(measurement["raw_decrypted_hex"])
                if signature not in self._saved_measurements:
                    if not _measurement_is_fresh(pending, measurement):
                        # Untrustworthy device time cannot safely associate the reading with the claim.
                        self._cancel_profile(pending.wake_id, "retry_required")
                        continue
                    if self._active(pending.wake_id):
                        self._weight = measurement["weight_kg"]
                    save = asyncio.create_task(pending.on_measurement(measurement))
                    try:
                        await asyncio.shield(save)
                    except asyncio.CancelledError:
                        await save
                        raise
                    self._saved_measurements.add(signature)
                if self._active(pending.wake_id):
                    # Wait for durable storage above before telling the scale it may discard the reading.
                    await self._write_command(0x13, b"\x00", pending.wake_id)
            finally:
                self._pending_measurements -= 1
                self._measurements.task_done()

    async def _send_heartbeats(self) -> None:
        while True:
            await asyncio.sleep(HEARTBEAT_INTERVAL_SECONDS)
            if self._active():
                await self._write_command(0x20, b"")

    async def _wait_until_stopped(self, started_at: float) -> None:
        loop = asyncio.get_running_loop()
        hard_deadline = started_at + max(HARD_SESSION_TIMEOUT_SECONDS, self._session_timeout)
        idle_deadline = started_at + self._session_timeout
        while True:
            current_time = loop.time()
            if current_time >= hard_deadline:
                self._cancel_profile(self._wake_id, "timeout")
                raise TimeoutError("BLE session exceeded its maximum duration")
            if not self._client.is_connected and self._stop_at is None:
                self._awake = False
                self._stop_at = current_time + SLEEP_GRACE_SECONDS
                self._sleep_event.set()
                self._cancel_profile(self._wake_id, "disconnected")
            if self._wake_id == 0 and current_time >= idle_deadline:
                return
            if (
                self._stop_event is not None
                and self._stop_event.is_set()
                and (
                    not self._awake or (self._resolve_profile is not None and not self._configured)
                )
                and self._stop_at is None
                and not self._pending_measurements
            ):
                return
            if (
                self._stop_at is not None
                and current_time >= self._stop_at
                and not self._pending_measurements
            ):
                return
            await asyncio.sleep(DISCONNECT_POLL_SECONDS)
