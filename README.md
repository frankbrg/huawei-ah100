# Huawei AH100 for Home Assistant

![Huawei AH100 integration illustration](docs/images/hero.svg)

An unofficial, local Bluetooth integration for reading body-composition measurements from the Huawei AH100 smart scale in Home Assistant.

[![CI](https://github.com/frankbrg/huawei-ah100/actions/workflows/validate.yaml/badge.svg?branch=main)](https://github.com/frankbrg/huawei-ah100/actions/workflows/validate.yaml)
[![Latest release](https://img.shields.io/github/v/release/frankbrg/huawei-ah100?display_name=tag&sort=semver)](https://github.com/frankbrg/huawei-ah100/releases)
[![License](https://img.shields.io/badge/license-GPL--3.0--or--later-blue.svg)](LICENSE)

[![Open this repository in HACS](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=frankbrg&repository=huawei-ah100&category=integration)
[![Add the integration to Home Assistant](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=huawei_ah100)

> **Validation status:** The BLE protocol has been validated against a Huawei AH100/CH100 scale with a Chipsea CST34M97 controller and firmware 1050 using a Raspberry Pi. The native Home Assistant integration wrapper has not yet been hardware-tested end to end.

## What it provides

The integration exposes the values reported directly by the scale:

| Entity | Unit | Meaning |
| --- | --- | --- |
| Weight | kg | Live weight reading |
| Body fat | % | Body-fat value reported by the scale |
| Impedance | Ω | Electrical impedance reported by the scale |
| Last measurement | UTC | When Home Assistant received the latest valid live record |

Measurement attributes include `user_id`, the device-local timestamp (`device_measured_at`), the device weekday field (`weekday`), and uninterpreted trailing bytes (`unknown_tail_hex`). Unknown bytes stay explicitly unknown; they are not labelled as visceral fat or another body metric. The integration does not calculate water, muscle, bone, BMI, BMR, or other derived values.

The configured initial weight is only a hint used when synchronizing the scale's user profile. After a live measurement, the integration can use that actual reading as the next profile hint. The initial value is never published as a sensor reading.

## Requirements and status

- Home Assistant **2026.10.0 or newer**.
- A supported Huawei AH100/CH100 scale and Bluetooth adapter available to Home Assistant.
- Keep the Huawei Health app disconnected from the scale while setting up or taking measurements. The scale accepts one active Bluetooth central connection at a time.

The local Bluetooth path has been validated. Bluetooth proxies have not been verified. The integration uses Home Assistant's Bluetooth stack, supports manual MAC entry or Home Assistant's passive Bluetooth discovery, and does not start its own active scanner. It does not require MQTT, Docker, a companion service, or a cloud account.

This is an independent community project and is not affiliated with Huawei. The integration version is **0.1.0**. The repository is **not yet listed in the HACS default catalog**; use the custom-repository button above until a catalog listing is published.

## Installation

### HACS

1. Install [HACS](https://hacs.xyz/docs/use/) if it is not already available in Home Assistant.
2. Select the HACS button above. If prompted, add `frankbrg/huawei-ah100` as a custom repository with category **Integration**.
3. Download the integration and restart Home Assistant.
4. Select the **Add integration** button above, or open **Settings → Devices & services → Add integration** and search for **Huawei AH100**. Home Assistant may also discover a connectable CH100 advertisement and offer the same setup flow.

### Manual

1. Copy `custom_components/huawei_ah100` from this repository into `<config>/custom_components/huawei_ah100` in your Home Assistant configuration directory.
2. Restart Home Assistant.
3. Add **Huawei AH100** from **Settings → Devices & services**.

## Set up the scale

You can enter the scale's MAC address manually or continue from Home Assistant's Bluetooth discovery when it finds a connectable CH100. Both paths ask for:

- The scale's Bluetooth MAC address.
- Birth date, height, sex, and an initial weight hint for the scale user profile.
- The scale user ID, which defaults to `1`.
- Whether pairing is explicitly allowed.

Finishing the configuration flow saves the settings; it does not connect to or pair the scale immediately. After setup, step on the scale to wake it and let Home Assistant connect over Bluetooth. The **allow pairing** option is opt-in: when enabled, the integration may send the bind command during a later awake session if normal authentication is rejected. The options flow lets you update the user profile and pairing preference later. No factory-reset or delete commands are used.

Before setup, close Huawei Health on nearby phones and make sure the scale is awake when prompted. Place Home Assistant within reliable Bluetooth range. If Home Assistant reports a connection problem, verify the MAC address and check that no phone or other client is connected to the scale.

## Privacy and data handling

Communication is directly between Home Assistant and the scale over Bluetooth. This integration does not send measurements or profile values to a cloud service. Profile fields are used to configure the scale. The latest accepted live measurement is persisted in Home Assistant's private integration storage so entities can be restored and the actual weight can be reused as the next profile hint. The full decoded record is not published in sensor states or attributes; sensor attributes expose only the documented measurement metadata.

Do not include your scale MAC address, birth date, profile values, or unredacted Bluetooth captures in public issues. When reporting a problem, include the scale model and firmware and the Home Assistant version, with personal identifiers removed.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for the development setup, tests, and style checks. The protocol notes and validation limits are in [docs/protocol.md](docs/protocol.md).

## License and attribution

This project is licensed under **GPL-3.0-or-later**. The protocol implementation is a Kotlin-to-Python port based on openScale's [`HuaweiAhCh100Handler.kt`](https://github.com/oliexdev/openScale/blob/39a0c5c25a0abc26b8e24c90f36b5a2c6ff07a4a/android_app/app/src/main/java/com/health/openscale/core/bluetooth/scales/HuaweiAhCh100Handler.kt) at commit [`39a0c5c25a0abc26b8e24c90f36b5a2c6ff07a4a`](https://github.com/oliexdev/openScale/commit/39a0c5c25a0abc26b8e24c90f36b5a2c6ff07a4a). See [docs/protocol.md](docs/protocol.md) for the protocol scope and attribution details.
