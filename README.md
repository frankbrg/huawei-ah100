# Huawei AH100 for Home Assistant

[English](README.md) | [Français](docs/README.fr.md)

![Huawei AH100 integration illustration](docs/images/hero.svg)

An unofficial, local Bluetooth integration for reading body-composition measurements from the Huawei AH100 smart scale in Home Assistant.

[![CI](https://github.com/frankbrg/huawei-ah100/actions/workflows/validate.yaml/badge.svg?branch=main)](https://github.com/frankbrg/huawei-ah100/actions/workflows/validate.yaml)
[![Latest release](https://img.shields.io/github/v/release/frankbrg/huawei-ah100?display_name=tag&sort=semver)](https://github.com/frankbrg/huawei-ah100/releases)
[![License](https://img.shields.io/badge/license-GPL--3.0--or--later-blue.svg)](LICENSE)

[![Open this repository in HACS](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=frankbrg&repository=huawei-ah100&category=integration)
[![Add the integration to Home Assistant](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=huawei_ah100)

> **Validation status:** The BLE protocol has been validated against a Huawei AH100/CH100 scale with a Chipsea CST34M97 controller and firmware 1050 using a Raspberry Pi. Native Home Assistant hardware weighing and authenticated SparkyFitness sync have not yet completed end-to-end validation.

## What it provides

Each configured scale has one physical device in Home Assistant. You can add a named profile for each person who uses it and explicitly choose the active profile before weighing. The integration does not identify a person from their weight. If a weighing is already underway, let it finish before switching profiles; Home Assistant keeps that result with the profile selected when the session began.

Each profile has its own logical device linked to the physical scale and its own four measurement sensors:

| Entity | Unit | Meaning |
| --- | --- | --- |
| Weight | kg | Live weight reading |
| Body fat | % | Body-fat value reported by the scale |
| Impedance | Ω | Electrical impedance reported by the scale |
| Last measurement | UTC | When Home Assistant received the latest valid live record |

The physical scale device also has an **Active profile** selector. Each profile device includes a **SparkyFitness sync** diagnostic sensor. It shows `disabled` when sync is off, `pending` before a sync is confirmed or while data waits, `synced` after success, and `error` after a failed attempt.

Measurement attributes include `user_id`, the device-local timestamp (`device_measured_at`), the device weekday field (`weekday`), and uninterpreted trailing bytes (`unknown_tail_hex`). Unknown bytes stay explicitly unknown; they are not labelled as visceral fat or another body metric. The integration does not calculate water, muscle, bone, BMI, BMR, or other derived values.

The initial weight is only a hint used when synchronizing a profile to the scale; it is not a measurement and is never published as a sensor reading. After a live measurement, that actual reading can be used as the next profile hint.

## Requirements and status

- Home Assistant **2026.10.0 or newer**.
- A supported Huawei AH100/CH100 scale and Bluetooth adapter available to Home Assistant.
- Keep Huawei Health disconnected from the scale during setup and measurements. The scale accepts one active Bluetooth central connection at a time.

Initial setup asks Home Assistant's Bluetooth stack to actively scan for nearby, connectable CH100 scales and offers a chooser when it finds one. After setup, Home Assistant's Bluetooth discovery detects the scale waking up. Bluetooth proxies have not been verified. The integration does not require MQTT, Docker, a companion service, or a cloud account.

This is an independent community project and is not affiliated with Huawei. The integration version is **0.2.0**. The request to add the repository to the HACS default catalog is [open and awaiting maintainer review](https://github.com/hacs/default/pull/12666); until it is accepted, add it as a custom repository using the button above.

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

## Set up the scale and first profile

Close Huawei Health on nearby phones, bring Home Assistant within Bluetooth range, and briefly step on the scale to wake it. In the nearby-device chooser, select the connectable CH100 scale. If Home Assistant has already discovered the scale and shows an integration card, select **Configure**; that flow does not ask for a MAC address.

If the nearby search finds no scale, wake it again and choose **Retry search**, or choose **Manual setup** and enter its Bluetooth MAC address. Some passive-only adapters can hear advertisements but cannot retrieve the advertised name needed by the first-time chooser; use manual setup in that case. Once configured, passive scanning is sufficient to detect the scale waking up.

The first setup form creates the first named profile and sets the shared connection options. Enter:

- **Profile name:** a name that identifies whose readings these are.
- **Birth date:** the profile user's actual date of birth, in `YYYY-MM-DD` format (for example, fictional `2000-01-31`).
- **Height:** the profile user's height in whole centimeters.
- **Sex:** the profile value for the person who will use the scale.
- **Initial weight hint:** a starting value for the scale profile. It is not a measurement or sensor reading.
- **Home Assistant person (optional):** a person entity linked as read-only profile metadata. It does not select the active profile or authenticate the person.
- **SparkyFitness sync (optional):** leave off unless you want this profile's new measurements sent to SparkyFitness; see [SparkyFitness sync](#sparkyfitness-sync).
- **Scale user ID:** the scale's authentication slot, default `1`. It is shared by all profiles; leave it at `1` unless you know a different slot is required.
- **Allow pairing or binding:** a shared connection option. Enable it if this adapter has not been authorized by the scale and binding is needed. Binding can change the existing association; leave it off if the adapter is already authorized.

Submit the form once. Close Huawei Health, step onto the scale barefoot, and stand still until the weighing finishes. Sensors receive values only after Home Assistant gets an actual measurement; the initial weight hint does not populate them.

## Add and select profiles

Open the Huawei AH100 integration page in **Settings → Devices & services** and select **Add profile** to create another profile. Edit or rename a profile from that same page. Profile names must be unique. Renaming a profile keeps its sensor identities and history.

Choose the person in the physical scale's **Active profile** selector before weighing. Each profile has separate measurements and its own device in Home Assistant, linked to the physical scale. If you upgrade from the previous single-profile setup, it becomes **Profile 1** and keeps its existing sensor IDs, readings, and history.

The scale's **Scale user ID** and **Allow pairing or binding** settings are shared by every profile and live in the integration's connection options. The user ID is the scale's authentication slot, not the profile ID. Changing active profiles does not change this setting or rebind the scale. If a weighing session is already running, Home Assistant lets it finish and associates the result with the original profile before the next session uses the newly selected profile.

## SparkyFitness sync

SparkyFitness sync is optional and configured separately in each profile. To enable it:

1. Enable **SparkyFitness sync** in the profile form.
2. Enter the root URL of the server, such as `https://fitness.example.com`; do not append an API path.
3. Create an API key in the corresponding SparkyFitness account with the `health_data_write` permission and enter that account's key. Give each person's profile the key for their own account; do not reuse one shared account key across profiles.

When editing a profile later, leave the API-key field blank to keep its saved key; Home Assistant does not prefill it.

This integration sends only new live weighings: weight in kilograms and body-fat percentage when available. It does not send the initial weight hint, saved history, profile fields, or impedance. It sends a `POST` to `/api/measurements/health-data`, an endpoint present in [SparkyFitness v0.17.3](https://github.com/CodeWithCJ/SparkyFitness/blob/2a7e0d8f317fff14fc6596d573df0c2ae6ba6f9d/SparkyFitnessServer/routes/measurementRoutes.ts). SparkyFitness stores a daily check-in for each account; another weighing that day updates that day's values. The request contains a date without a time, using the Home Assistant local calendar day, so the SparkyFitness account timezone should match Home Assistant's timezone. The precise weighing time and impedance remain available in Home Assistant.

The **SparkyFitness sync** sensor reports `disabled`, `pending`, `synced`, or `error`. A configured profile may show `pending` before its first confirmed sync. If the server is unavailable, a measurement stays queued and sync retries later. Changing the URL or API key does not reassign a queued measurement to a different account. Setting up the integration does not create a test measurement or backfill old readings.

## Troubleshooting

- **The nearby chooser is empty or has no scale name:** close Huawei Health, wake the scale, and retry within Bluetooth range. Check that Home Assistant's Bluetooth adapter is in **Automatic** or **Active** mode so the initial scan can retrieve the advertised name. If it is passive-only, use **Manual setup** and enter the MAC address.
- **Sensors are unavailable:** select the correct **Active profile**, then perform a barefoot weighing and stand still until the scale finishes. The sensors need a real reading before they have values.
- **Authentication is rejected:** if this adapter has not been authorized by the scale, enable **Allow pairing or binding** in the integration's connection options, then wake the scale again. Binding can change the existing association; enable it only when needed.
- **A phone is connected or Home Assistant cannot connect:** close Huawei Health and disconnect other phones or clients from the scale, then verify the selected or entered MAC address.
- **SparkyFitness sync stays pending or reports an error:** check server reachability, the profile's URL, and that its API key belongs to the right account and has `health_data_write` permission. Confirm that account and Home Assistant use the same timezone.

## Privacy and data handling

Bluetooth communication with the scale is local. No measurements or profile values go to a cloud service unless you enable SparkyFitness sync for a profile. When enabled, only new live weight, available body-fat percentage, and the Home Assistant local date are sent to that profile's configured SparkyFitness server. Birth date, height, sex, person entity, exact weighing time, and impedance are not sent to SparkyFitness.

The latest accepted live measurement is persisted in Home Assistant's private integration storage so entities can be restored and the actual weight can be reused as the next profile hint. The full decoded record is not published in sensor states or attributes; sensor attributes expose only the documented measurement metadata.

Do not include your scale MAC address, birth date, profile values, API keys, or unredacted Bluetooth captures in public issues. When reporting a problem, include the scale model and firmware and the Home Assistant version, with personal identifiers removed.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for the development setup, tests, and style checks. The protocol notes and validation limits are in [docs/protocol.md](docs/protocol.md).

## License and attribution

This project is licensed under **GPL-3.0-or-later**. The protocol implementation is a Kotlin-to-Python port based on openScale's [`HuaweiAhCh100Handler.kt`](https://github.com/oliexdev/openScale/blob/39a0c5c25a0abc26b8e24c90f36b5a2c6ff07a4a/android_app/app/src/main/java/com/health/openscale/core/bluetooth/scales/HuaweiAhCh100Handler.kt) at commit [`39a0c5c25a0abc26b8e24c90f36b5a2c6ff07a4a`](https://github.com/oliexdev/openScale/commit/39a0c5c25a0abc26b8e24c90f36b5a2c6ff07a4a). See [docs/protocol.md](docs/protocol.md) for protocol scope and attribution details.
