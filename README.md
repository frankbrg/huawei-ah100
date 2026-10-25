# Huawei AH100 for Home Assistant

[English](README.md) | [Français](docs/README.fr.md)

![Huawei AH100 integration illustration](docs/images/hero.svg)

An unofficial, local Bluetooth integration for reading body-composition measurements from the Huawei AH100 smart scale in Home Assistant.

[![CI](https://github.com/frankbrg/huawei-ah100/actions/workflows/validate.yaml/badge.svg?branch=main)](https://github.com/frankbrg/huawei-ah100/actions/workflows/validate.yaml)
[![Latest release](https://img.shields.io/github/v/release/frankbrg/huawei-ah100?display_name=tag&sort=semver)](https://github.com/frankbrg/huawei-ah100/releases)
[![License](https://img.shields.io/badge/license-GPL--3.0--or--later-blue.svg)](LICENSE)

[![Open this repository in HACS](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=frankbrg&repository=huawei-ah100&category=integration)
[![Add the integration to Home Assistant](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=huawei_ah100)

> **Validation status:** The BLE protocol has been validated against a Huawei AH100/CH100 scale with a Chipsea CST34M97 controller and firmware 1050 using a Raspberry Pi. The native Home Assistant integration has not yet completed end-to-end hardware validation.

## What it provides

The integration exposes values reported directly by the scale:

| Entity | Unit | Meaning |
| --- | --- | --- |
| Weight | kg | Live weight reading |
| Body fat | % | Body-fat value reported by the scale |
| Impedance | Ω | Electrical impedance reported by the scale |
| Last measurement | UTC | When Home Assistant received the latest valid live record |

Measurement attributes include `user_id`, the device-local timestamp (`device_measured_at`), the device weekday field (`weekday`), and uninterpreted trailing bytes (`unknown_tail_hex`). Unknown bytes stay explicitly unknown; they are not labelled as visceral fat or another body metric. The integration does not calculate water, muscle, bone, BMI, BMR, or other derived values.

The initial weight is only a hint used when synchronizing the scale's user profile; it is not a measurement and is never published as a sensor reading. After a live measurement, the integration can use that actual reading as the next profile hint.

## Requirements and status

- Home Assistant **2026.10.0 or newer**.
- A supported Huawei AH100/CH100 scale and Bluetooth adapter available to Home Assistant.
- Keep Huawei Health disconnected from the scale during setup and measurements. The scale accepts one active Bluetooth central connection at a time.

Initial setup asks Home Assistant's Bluetooth stack to actively scan for nearby, connectable CH100 scales and offers a chooser when it finds one. After setup, Home Assistant's Bluetooth discovery detects the scale waking up. Bluetooth proxies have not been verified. The integration does not require MQTT, Docker, a companion service, or a cloud account.

This is an independent community project and is not affiliated with Huawei. The integration version is **0.1.2**. The request to add the repository to the HACS default catalog is [open and awaiting maintainer review](https://github.com/hacs/default/pull/12666); until it is accepted, add it as a custom repository using the button above.

## Installation

### HACS

1. Install [HACS](https://hacs.xyz/docs/use/) if it is not already available in Home Assistant.
2. Select the HACS button above. If prompted, add `frankbrg/huawei-ah100` as a custom repository with category **Integration**.
3. Download the integration and restart Home Assistant.
4. Select the **Add integration** button above, or open **Settings → Devices & services → Add integration** and search for **Huawei AH100**. Home Assistant starts a nearby search.

### Manual

1. Copy `custom_components/huawei_ah100` from this repository into `<config>/custom_components/huawei_ah100` in your Home Assistant configuration directory.
2. Restart Home Assistant.
3. Add **Huawei AH100** from **Settings → Devices & services**.

## Set up the scale

Close Huawei Health on nearby phones, bring Home Assistant within Bluetooth range, and briefly step on the scale to wake it. In the nearby-device chooser, select the connectable CH100 scale. If Home Assistant has already discovered the scale and shows an integration card, select **Configure**; this opens the profile form without asking for a MAC address.

If the nearby search finds no scale, choose **Retry search** after waking it again, or choose **Manual setup** and enter its Bluetooth MAC address. Some passive-only adapters can hear advertisements but cannot retrieve the advertised name needed for the first-time chooser; manual setup is the fallback in that case. Once configured, passive scanning is sufficient to detect the scale waking up.

Enter the scale user's profile in the form:

- **Birth date:** the profile user's actual date of birth, in `YYYY-MM-DD` format (for example, fictional `2000-01-31`).
- **Height:** the profile user's height in whole centimeters.
- **Sex:** the profile value for the person who will use the scale.
- **Initial weight hint:** a starting value for the scale profile. It is not a measurement or sensor reading.
- **Scale user ID:** defaults to `1`; leave it at `1` unless you know that the profile uses a different scale slot.
- **Allow pairing or binding:** enable it during first setup if this Home Assistant adapter has not been authorized by the scale. Binding can change the existing association; leave it off if the adapter is already authorized.

Submit the form once. For the first measurement, close Huawei Health, step onto the scale barefoot, and remain still until the scale completes the weighing. Sensors may remain unavailable until Home Assistant receives the first real measurement; the initial weight hint does not populate them. To take later measurements, wake the scale and let Home Assistant connect over Bluetooth. The options flow lets you update the user profile and pairing preference later. No factory-reset or delete commands are used.

## Troubleshooting

- **The nearby chooser is empty or has no scale name:** close Huawei Health, wake the scale, and retry within Bluetooth range. Check that Home Assistant's Bluetooth adapter is in **Automatic** or **Active** mode so the initial scan can retrieve the advertised name. If the adapter is passive-only, use **Manual setup** and enter the MAC address.
- **Sensors are unavailable:** the integration needs a real measurement before measurement sensors have values. Wake the scale and complete a barefoot weighing, then allow Home Assistant to connect.
- **Authentication is rejected:** if this adapter has not been authorized by the scale, enable **Allow pairing or binding** in the integration options, then wake the scale again. Binding can change the existing association, so enable this only when needed.
- **Home Assistant cannot connect:** check that Huawei Health and other phones or clients are disconnected from the scale, and verify the selected or entered MAC address.

## Privacy and data handling

Communication is directly between Home Assistant and the scale over Bluetooth. This integration does not send measurements or profile values to a cloud service. Profile fields are used to configure the scale. The latest accepted live measurement is persisted in Home Assistant's private integration storage so entities can be restored and the actual weight can be reused as the next profile hint. The full decoded record is not published in sensor states or attributes; sensor attributes expose only the documented measurement metadata.

Do not include your scale MAC address, birth date, profile values, or unredacted Bluetooth captures in public issues. When reporting a problem, include the scale model and firmware and the Home Assistant version, with personal identifiers removed.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for the development setup, tests, and style checks. The protocol notes and validation limits are in [docs/protocol.md](docs/protocol.md).

## License and attribution

This project is licensed under **GPL-3.0-or-later**. The protocol implementation is a Kotlin-to-Python port based on openScale's [`HuaweiAhCh100Handler.kt`](https://github.com/oliexdev/openScale/blob/39a0c5c25a0abc26b8e24c90f36b5a2c6ff07a4a/android_app/app/src/main/java/com/health/openscale/core/bluetooth/scales/HuaweiAhCh100Handler.kt) at commit [`39a0c5c25a0abc26b8e24c90f36b5a2c6ff07a4a`](https://github.com/oliexdev/openScale/commit/39a0c5c25a0abc26b8e24c90f36b5a2c6ff07a4a). See [docs/protocol.md](docs/protocol.md) for protocol scope and attribution details.
