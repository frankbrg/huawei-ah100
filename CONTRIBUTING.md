# Contributing

Thanks for helping improve the Huawei AH100 Home Assistant integration. Changes should keep the integration local, preserve the distinction between live and historical records, and expose only values whose meaning is supported by the device data.

## Development setup

Use Python 3 and create an isolated environment:

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements-dev.txt
```

## Checks

Run the tests and lint checks before opening a pull request:

```sh
python -m pytest
ruff check .
```

The GitHub Actions workflow also validates the integration with HACS and Home Assistant's hassfest checks. Please keep those checks passing; local pytest and Ruff runs do not replace either CI validation.

## Pull requests

- Keep changes focused and include tests for protocol or entity behavior where appropriate.
- Do not invent meanings for uninterpreted payload bytes or derive water, muscle, bone, BMI, BMR, or other unsupported metrics.
- Keep live readings separate from history records. Do not use a history record to replace the current live sensor state.
- Avoid commands that reset the scale or delete users or records.
- Update the README or protocol reference when an observable feature or a verified protocol detail changes.
- Describe the hardware and firmware used for any protocol observation, and distinguish direct hardware evidence from code-level testing.

## Reporting issues

Include the Home Assistant version, scale model, firmware version, and a concise description of what happened. Remove Bluetooth addresses, birth dates, profile values, authentication material, and unredacted packet captures from public reports.
