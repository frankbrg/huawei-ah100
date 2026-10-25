# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 openScale contributors and Frank Berge
# Modified Python port of HuaweiAhCh100Handler.kt; see NOTICE for provenance.

from __future__ import annotations

import math
from datetime import datetime

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

_INITIAL_KEY = bytes.fromhex("3da2784afb87b12a980fde3456732156")
_INITIAL_IV = bytes.fromhex("4ef764322fda7632123deb8790fea219")
_FRAME_NOTIFY_ENCRYPTED = 0xBC
_FRAME_NOTIFY_PLAIN = 0xBD
_MEASUREMENT_OPCODES = frozenset((0x0E, 0x10))


def obfuscate(data: bytes, mac: bytes) -> bytes:
    data = bytes(data)
    mac = bytes(mac)
    if not mac:
        return data
    return bytes(value ^ mac[index % len(mac)] for index, value in enumerate(data))


def auth_token(user_id: int) -> bytes:
    token = bytearray((0x11, 0x22, 0x33, 0x44, 0x55, 0x00, user_id & 0xFF))
    checksum = 0
    for value in token:
        checksum ^= value
    token[5] = checksum
    return bytes(token)


def plain_command(command: int, payload: bytes, mac: bytes) -> bytes:
    payload = bytes(payload)
    header = bytes((0xDB, (len(payload) + 1) & 0xFF, command & 0xFF))
    return header + obfuscate(payload, mac)


def user_info_command(
    auth: bytes,
    mac: bytes,
    age: int,
    height_cm: int,
    sex: str,
    weight_kg: float,
) -> bytes:
    auth = bytes(auth)
    if len(auth) != 7:
        raise ValueError("auth token must be 7 bytes")
    if isinstance(age, bool) or not isinstance(age, int) or not 0 <= age <= 127:
        raise ValueError("age must be an integer from 0 to 127")
    if isinstance(height_cm, bool) or not isinstance(height_cm, int) or not 1 <= height_cm <= 255:
        raise ValueError("height must be an integer from 1 to 255 cm")
    if not isinstance(sex, str) or sex.casefold() not in ("male", "female"):
        raise ValueError("sex must be 'male' or 'female'")
    if isinstance(weight_kg, bool) or not isinstance(weight_kg, (int, float)):
        raise ValueError("weight must be a positive number")
    try:
        weight_value = float(weight_kg)
    except (OverflowError, ValueError) as exc:
        raise ValueError("weight must be a positive finite number") from exc
    if not math.isfinite(weight_value) or weight_value <= 0:
        raise ValueError("weight must be a positive finite number")
    weight_tenths_value = weight_value * 10
    if not math.isfinite(weight_tenths_value):
        raise ValueError("weight in tenths of a kilogram must fit an unsigned 16-bit value")
    weight_tenths = round(weight_tenths_value)
    if not 1 <= weight_tenths <= 0xFFFF:
        raise ValueError("weight in tenths of a kilogram must fit an unsigned 16-bit value")

    sex_bit = 0x80 if sex.casefold() == "female" else 0
    plaintext = (
        auth
        + bytes((age | sex_bit, height_cm, 0x00))
        + weight_tenths.to_bytes(2, "little")
        + b"\xff\xff\x1c\xe2"
    )
    ciphertext = aes_ctr(plaintext, derive_session_key(auth, mac))
    return bytes((0xDC, 0x0E, 0x09)) + obfuscate(ciphertext, mac)


def derive_session_key(auth: bytes, mac: bytes) -> bytes:
    auth = bytes(auth)
    if len(auth) != 7:
        raise ValueError("auth token must be 7 bytes")
    return obfuscate(auth, mac) + _INITIAL_KEY[7:]


def aes_ctr(data: bytes, key: bytes, iv: bytes = _INITIAL_IV) -> bytes:
    data = bytes(data)
    key = bytes(key)
    iv = bytes(iv)
    if len(key) != 16:
        raise ValueError("AES-CTR key must be 16 bytes")
    if len(iv) != 16:
        raise ValueError("AES-CTR IV must be 16 bytes")
    cipher = Cipher(algorithms.AES(key), modes.CTR(iv))
    transform = cipher.encryptor()
    return transform.update(data) + transform.finalize()


def _parse_notification(frame: bytes, mac: bytes) -> tuple[dict[str, int | str | bool], bytes]:
    frame = bytes(frame)
    if len(frame) < 3:
        raise ValueError("notification frame is shorter than its header")

    header, length, opcode = frame[:3]
    if header not in (_FRAME_NOTIFY_ENCRYPTED, _FRAME_NOTIFY_PLAIN):
        raise ValueError("unsupported notification frame type")

    payload = obfuscate(frame[3:], mac)
    if length not in (len(payload), len(payload) + 1):
        raise ValueError("notification frame length does not match payload")

    parsed: dict[str, int | str | bool] = {
        "header": header,
        "length": length,
        "opcode": opcode,
        "payload_hex": payload.hex(),
        "payload_is_encrypted": header == _FRAME_NOTIFY_ENCRYPTED,
    }
    return parsed, payload


def parse_notification(frame: bytes, mac: bytes) -> dict[str, int | str | bool]:
    parsed, _ = _parse_notification(frame, mac)
    return parsed


def decode_measurement(
    frame: bytes, auth: bytes, mac: bytes
) -> dict[str, float | int | str | None]:
    parsed, encrypted_payload = _parse_notification(frame, mac)
    if parsed["header"] != _FRAME_NOTIFY_ENCRYPTED:
        raise ValueError("measurement frame is not encrypted")
    if parsed["opcode"] not in _MEASUREMENT_OPCODES:
        raise ValueError("notification opcode is not a measurement record")
    if len(encrypted_payload) < 15:
        raise ValueError("measurement payload is shorter than 15 bytes")

    auth = bytes(auth)
    decrypted = aes_ctr(encrypted_payload, derive_session_key(auth, mac))
    user_id = decrypted[0]
    weight_kg = int.from_bytes(decrypted[1:3], "little") / 10
    fat_raw_tenths = int.from_bytes(decrypted[3:5], "little")
    fat_percent = None if fat_raw_tenths in (0, 0xFFFF) else fat_raw_tenths / 10
    year = int.from_bytes(decrypted[5:7], "little")
    month, day, hour, minute, second = decrypted[7:12]
    weekday = decrypted[12]
    impedance_raw = int.from_bytes(decrypted[13:15], "little")
    impedance_ohm = None if impedance_raw in (0, 0xFFFF) else impedance_raw

    if not 0 < weight_kg <= 300:
        raise ValueError("measurement weight is outside the plausible range")
    if parsed["opcode"] == 0x0E and user_id != auth[6]:
        raise ValueError("live measurement user ID does not match the authorized user")

    measured_at = None
    if 2000 <= year <= 2100:
        try:
            measured_at = datetime(year, month, day, hour, minute, second).isoformat()
        except ValueError:
            pass

    return {
        "weight_kg": weight_kg,
        "fat_percent": fat_percent,
        "fat_raw_tenths": fat_raw_tenths,
        "impedance_ohm": impedance_ohm,
        "impedance_raw": impedance_raw,
        "user_id": user_id,
        "weekday": weekday,
        "measured_at": measured_at,
        "raw_decrypted_hex": decrypted.hex(),
        "unknown_tail_hex": decrypted[15:].hex(),
    }
