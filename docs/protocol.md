# Protocol notes

This page records the supported subset of the Huawei AH100 Bluetooth protocol used by this integration. It distinguishes observed protocol behavior from unverified hardware variants. It is not a complete specification of every command supported by the scale.

## Validation scope

The protocol path was exercised against a physical Huawei AH100/CH100 scale using a Chipsea CST34M97 controller and firmware 1050 from a Raspberry Pi. The existing manual Home Assistant profile flow has received a real scale reading, but the new profile-confirmation and household-automation flow has not been validated end to end against the hardware. Other AH100 revisions, firmware versions, and Bluetooth proxies remain unverified.

The implementation is based on openScale's Kotlin [`HuaweiAhCh100Handler.kt`](https://github.com/oliexdev/openScale/blob/39a0c5c25a0abc26b8e24c90f36b5a2c6ff07a4a/android_app/app/src/main/java/com/health/openscale/core/bluetooth/scales/HuaweiAhCh100Handler.kt) at commit [`39a0c5c25a0abc26b8e24c90f36b5a2c6ff07a4a`](https://github.com/oliexdev/openScale/commit/39a0c5c25a0abc26b8e24c90f36b5a2c6ff07a4a), ported to Python. The port is distributed under GPL-3.0-or-later; see the repository license.

## Transport and frames

The scale exposes proprietary BLE service `0000FAA0-0000-1000-8000-00805F9B34FB`. Commands are written to characteristic `0000FAA1-0000-1000-8000-00805F9B34FB`; wake, status, and measurement frames arrive on notification characteristic `0000FAA2-0000-1000-8000-00805F9B34FB`.

Frames have a three-byte header followed by a payload tail:

| Direction and frame type | Marker | Use |
| --- | --- | --- |
| Scale to Home Assistant, plain | `0xBD` | Wake/sleep and status notifications |
| Scale to Home Assistant, encrypted | `0xBC` | Live and history measurement notifications |
| Home Assistant to scale, plain | `0xDB` | Authentication, pairing, unit, clock, heartbeat, and ACK commands |
| Home Assistant to scale, encrypted | `0xDC` | User profile information |

The second header byte is a protocol length field; the third is the opcode. Payload bytes use repeating MAC-based XOR obfuscation. Measurement and profile payloads also use AES-CTR. The decoder treats these as transport details and only assigns semantics to fields supported by the observed layout.

## Session flow

1. The scale wakes with notification opcode `0x00`.
2. The integration sends authentication command `0x24` for the configured user ID and waits for status opcode `0x26`.
3. Pairing command `0x25` is sent only when the user explicitly enables it in the shared connection settings and normal authentication is rejected. It is not part of the default authentication path.
4. After authentication, the integration configures kilograms, synchronizes the clock, and sends the configured user profile. A user-change notification (`0x20`) can trigger a profile refresh.
5. A valid live measurement is decoded and acknowledged with command `0x13` and payload `00`. History records are not acknowledged as live measurements.

Profile confirmation is an optional shared connection setting, off by default and absent from initial setup. When enabled, wake opcode `0x00` starts a short-lived Home Assistant confirmation request. The integration waits for a valid profile claim before sending that profile's demographic details to the scale. With the option off, the existing manual active-profile selection continues to determine which profile is sent.

The profile's initial weight is a scale-configuration hint, not a Home Assistant measurement. Once a live reading exists, that actual reading can be used as the next profile hint.

The integration does not issue factory-reset or delete commands. It does not enumerate possible user IDs or request history records.

The wire-level `user_id` is one setting on the physical scale config entry and is shared by all Home Assistant profiles. A profile selects the demographic fields sent to the scale; switching profiles does not change the wire user ID or rebind the scale.

## Notifications

| Opcode | Meaning | Integration behavior |
| --- | --- | --- |
| `0x00` | Wake | Starts authentication for the current session |
| `0x01` | Sleep | Ends the awake command window |
| `0x0E` | Live measurement | Decode, update live entities, then send the measurement ACK |
| `0x10` | History record | Keep distinct from live state; it does not update the live entities or profile weight hint |
| `0x20` | User changed | Refresh the configured user profile after authentication |
| `0x26` | Authentication result | Confirms or rejects authentication |
| `0x27` | Pairing success | Allows authentication to follow an explicitly requested pairing attempt |

The session also sends heartbeat command `0x20` with an empty payload while the scale is awake. Unit command `0x02` uses payload `01` for kilograms. Clock command `0x08` carries the date, time, and weekday. Encrypted user-profile command `0x09` carries the configured profile fields.

The protocol also defines paired continuation opcodes `0x8E` and `0x90` for measurement and history transfers. The supported live path decodes the verified first-half measurement payload and does not concatenate continuation bytes to manufacture additional fields.

## Profile confirmation and Home Assistant events

With **Require profile confirmation for each weighing** enabled, a wake emits `huawei_ah100_weighing_requested`. The event contains `entry_id`, a per-request `request_id`, UTC `expires_at`, and `profiles`, a list of configured candidates with `profile_id`, `name`, and optional `person_entity_id`. The integration does not decide who is home or send notifications; household automations must resolve the resident or ask them to act.

Automations claim a profile with the `huawei_ah100.claim_weighing` service. Required fields are the request's `entry_id`, `request_id`, and selected `profile_id`. The optional `claimant_user_id` identifies the Home Assistant user who responded to an actionable notification. In that case, pass the original event's `trigger.event.context.user_id`; do not use a user ID supplied in notification `action_data`. Leave the field empty for trusted automatic selection, such as an automation that found exactly one eligible resident.

The first valid claim wins. A request cannot be claimed after its expiry, after the scale sleeps or disconnects, or after Home Assistant reloads. When a request ends without producing a measurement, `huawei_ah100_weighing_cancelled` includes `entry_id`, `request_id`, and `reason` (`expired`, `sleep`, `disconnected`, `reload`, `timeout`, `retry_required`, `superseded`, `session_ended`, or `stopped`). The `retry_required` reason means the scale sent an early response or its device timestamp could not be trusted; wake the scale again and claim the new request before weighing. An accepted claim emits `huawei_ah100_profile_confirmed` with `entry_id`, `request_id`, `profile_id`, and optional `person_entity_id`.

A profile stays assigned to its measurement after confirmation. The scale uses that profile's demographic data to calculate body-fat readings, so changing the profile after weighing cannot correct a mismatch in a reliable way. If a resident misses the short claim window, wake the scale again, claim the new request, then weigh.

Measurement lifecycle events carry a durable `measurement_id`. `huawei_ah100_measurement_saved` fires after Home Assistant stores the accepted measurement locally and can drive an immediate local acknowledgement, such as a green blink. `huawei_ah100_measurement_completed` fires for the same `measurement_id` after SparkyFitness sync succeeds or when sync is disabled. Its `sync_status` is `synced` or `disabled`; use this event for a final result notification to the owner. Both events carry the saved measurement fields: `entry_id`, `profile_id`, `measurement_id`, `weight_kg`, `fat_percent`, `impedance_ohm`, `received_at`, optional `person_entity_id`, and `sparky_enabled`.

## Decoded live measurement

The first decrypted live payload contains:

| Field | Encoding | Home Assistant representation |
| --- | --- | --- |
| User ID | One byte | `user_id` attribute |
| Weight | Unsigned little-endian integer in 0.1 kg increments | Weight sensor in kg |
| Body fat | Unsigned little-endian integer in 0.1 percentage-point increments | Body-fat sensor in %; absent sentinels remain unavailable |
| Device date and time | Year as little-endian 16-bit integer, followed by month, day, hour, minute, second | `device_measured_at` attribute, retained as the device-local timestamp |
| Weekday | One byte from the device | `weekday` attribute, retained as reported |
| Impedance | Unsigned little-endian integer in ohms | Impedance sensor in Ω; absent sentinels remain unavailable |
| Trailing bytes | Remaining bytes, if present | Hex `unknown_tail_hex` attribute; no metric interpretation |

The **Last measurement** sensor records when Home Assistant received the valid live record, as a UTC timestamp. It is separate from the device-local time retained in `device_measured_at`.

The integration exposes the scale's reported weight, body fat, and impedance without deriving other body-composition values. Its UTC last-measurement sensor records Home Assistant's receipt time; the scale's local timestamp is kept separately in the `device_measured_at` attribute. The integration does not derive water, muscle, bone, BMI, BMR, visceral fat, or other metrics from impedance or trailing bytes.

Home Assistant persists the latest accepted decoded live record in its private integration storage before the session sends the BLE acknowledgement. The stored record retains decoder data, including the raw decrypted field, for restoring the latest state; that full record is not published in sensor states or attributes.

## References

- [Home Assistant integration README](../README.md)
- [Contributing guide](../CONTRIBUTING.md)
- [openScale Kotlin protocol source](https://github.com/oliexdev/openScale/blob/39a0c5c25a0abc26b8e24c90f36b5a2c6ff07a4a/android_app/app/src/main/java/com/health/openscale/core/bluetooth/scales/HuaweiAhCh100Handler.kt)
