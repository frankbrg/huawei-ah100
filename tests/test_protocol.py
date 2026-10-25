from datetime import datetime
from functools import reduce
from operator import xor

import pytest

from custom_components.huawei_ah100 import protocol

MAC = bytes.fromhex("aabbccddeeff")


def frame_for(
    user_id=1, weight=700, fat=200, impedance=450, tail=b"\x14", opcode=0x0E, auth_user_id=None
):
    plaintext = (
        bytes((user_id,))
        + weight.to_bytes(2, "little")
        + fat.to_bytes(2, "little")
        + (2026).to_bytes(2, "little")
        + bytes((1, 1, 8, 15, 0, 4))
        + impedance.to_bytes(2, "little")
        + tail
    )
    auth = protocol.auth_token(user_id if auth_user_id is None else auth_user_id)
    encrypted = protocol.aes_ctr(plaintext, protocol.derive_session_key(auth, MAC))
    return bytes((0xBC, len(encrypted) + 1, opcode)) + protocol.obfuscate(encrypted, MAC)


def test_wire_header_and_auth_checksum():
    token = protocol.auth_token(1)
    assert len(token) == 7
    assert reduce(xor, token) == 0
    frame = protocol.plain_command(0x24, token, MAC)
    assert frame[:3] == bytes((0xDB, 8, 0x24))
    assert len(frame) == 10
    assert protocol.obfuscate(frame[3:], MAC) == token


def test_direct_measurement_fields_and_unknown_bytes():
    result = protocol.decode_measurement(frame_for(tail=b"\x14\xa5"), protocol.auth_token(1), MAC)
    assert result["weight_kg"] == 70.0
    assert result["fat_percent"] == 20.0
    assert result["impedance_ohm"] == 450
    assert result["measured_at"] == datetime(2026, 1, 1, 8, 15).isoformat()
    assert result["unknown_tail_hex"] == "14a5"


@pytest.mark.parametrize("sentinel", [0, 0xFFFF])
def test_missing_composition_does_not_discard_weight(sentinel):
    result = protocol.decode_measurement(
        frame_for(fat=sentinel, impedance=sentinel), protocol.auth_token(1), MAC
    )
    assert result["weight_kg"] == 70.0
    assert result["fat_percent"] is None
    assert result["impedance_ohm"] is None
    assert result["fat_raw_tenths"] == sentinel


def test_wrong_user_and_malformed_records_are_rejected():
    with pytest.raises(ValueError, match="user ID"):
        protocol.decode_measurement(frame_for(auth_user_id=2), protocol.auth_token(2), MAC)
    for frame in (b"", frame_for()[:-1], bytes((0xBC, 17, 0x8E)) + frame_for()[3:]):
        with pytest.raises(ValueError):
            protocol.decode_measurement(frame, protocol.auth_token(1), MAC)


def test_profile_framing_is_not_a_measurement():
    auth = protocol.auth_token(1)
    frame = protocol.user_info_command(auth, MAC, 40, 175, "female", 70.0)
    assert frame[:3] == bytes((0xDC, 14, 9))
    assert len(frame) == 19
    plaintext = protocol.aes_ctr(
        protocol.obfuscate(frame[3:], MAC), protocol.derive_session_key(auth, MAC)
    )
    assert plaintext[:7] == auth
    assert plaintext[7:10] == bytes((40 | 0x80, 175, 0))
    assert int.from_bytes(plaintext[10:12], "little") == 700
