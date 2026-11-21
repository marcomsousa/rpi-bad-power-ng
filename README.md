# rpi-bad-power-ng

[![PyPI](https://img.shields.io/pypi/v/rpi-bad-power-ng)](https://pypi.org/project/rpi-bad-power-ng/)
[![Downloads](https://static.pepy.tech/badge/rpi-bad-power-ng)](https://pepy.tech/projects/rpi-bad-power-ng)
[![CI](https://github.com/marcomsousa/rpi-bad-power-ng/actions/workflows/ci.yml/badge.svg)](https://github.com/marcomsousa/rpi-bad-power-ng/actions/workflows/ci.yml)
[![Python 3.12+](https://img.shields.io/pypi/pyversions/rpi-bad-power-ng)](https://pypi.org/project/rpi-bad-power-ng/)
[![License: MIT](https://img.shields.io/github/license/marcomsousa/rpi-bad-power-ng)](LICENSE)

Next-generation, zero-dependency Python library to detect Raspberry Pi bad power supply (under-voltage) and firmware throttling directly from Linux `sysfs`.

`rpi-bad-power-ng` is an actively maintained, independent continuation of [`shenxn/rpi-bad-power`](https://github.com/shenxn/rpi-bad-power). It keeps the **`rpi_bad_power`** Python package name and full API compatibility as a **100% drop-in replacement** for Home Assistant Core's built-in [Raspberry Pi Power Supply Checker (`rpi_power`)](https://www.home-assistant.io/integrations/rpi_power/) integration.

## Highlights

- **Zero third-party runtime dependencies** — pure Python standard library (`>=3.12`), PEP 561 typed (`py.typed`).
- **100% Home Assistant `rpi_power` compatible** — preserves `new_under_voltage()`, `UnderVoltage.get()`, and all legacy constants/classes.
- **Hardened sysfs discovery & error handling** — safely skips unreadable/hotplugged `hwmon*` entries, verifies `in0_lcrit_alarm` readability, strips whitespace cleanly, and raises structured `RpiBadPowerError` (subclass of `OSError`).
- **Event-driven kernel push (`sysfs_notify`)** — `UnderVoltageNew.async_watch()` monitors `in0_lcrit_alarm` via Linux `POLLPRI | POLLERR` so transient voltage sags are caught immediately instead of missed between 30-second polls.
- **Full `get_throttled` bitmask telemetry** — decode active and sticky under-voltage, frequency-capping, throttling, and soft temperature flags via `ThrottledStatus` / `get_throttled_status()`.

## Installation

```bash
uv add rpi-bad-power-ng
```

Or with `pip`:

```bash
pip install rpi-bad-power-ng
```

## Compatibility

Requires Linux kernel **4.14+** on Raspberry Pi hardware (including Home Assistant OS and read-only Docker containers, without `vcgencmd` or root privileges):

- **Linux 4.19+ (through Raspberry Pi 5):** `raspberrypi-hwmon` via `/sys/class/hwmon/hwmon*/in0_lcrit_alarm` ([raspberrypi/linux#2706](https://github.com/raspberrypi/linux/pull/2706)) — supports both synchronous polling and real-time `sysfs_notify` push watching.
- **Linux 4.14–4.19 (legacy):** `/sys/devices/platform/soc/soc:firmware/get_throttled` ([raspberrypi/linux#2397](https://github.com/raspberrypi/linux/pull/2397)).

## Usage

### Synchronous polling (Home Assistant drop-in API)

```python
from rpi_bad_power import RpiBadPowerError, new_under_voltage

under_voltage = new_under_voltage()
if under_voltage is None:
    print("System not supported.")
else:
    try:
        if under_voltage.get():
            print("Under-voltage detected!")
        else:
            print("Voltage is normal.")
    except RpiBadPowerError as err:
        print(f"Failed to read power status: {err}")
```

### Real-time async push notifications (`sysfs_notify`)

```python
import asyncio
from rpi_bad_power import new_under_voltage


async def main() -> None:
    under_voltage = new_under_voltage()
    if under_voltage is None:
        print("System not supported.")
        return

    if under_voltage.supports_push:
        async with under_voltage.async_watch() as watcher:
            async for is_under_voltage in watcher:
                print(f"Under-voltage state: {is_under_voltage}")
    else:
        print(f"Legacy fallback (polling only): {under_voltage.get()}")


asyncio.run(main())
```

### Full `get_throttled` bitmask decoding

```python
from rpi_bad_power import ThrottledStatus, get_throttled_status

status = get_throttled_status()
if status is not None:
    print(f"Active issues: {status.any_active}, occurred: {status.any_occurred}")
    print(f"Under-voltage active={status.under_voltage}, throttled={status.throttled}")

# Or parse a raw hexadecimal bitmask directly (e.g. from vcgencmd get_throttled):
decoded = ThrottledStatus.from_hex("0x50005")
assert decoded.under_voltage and decoded.throttled_occurred
```

## Development

```bash
uv sync --all-groups
uv run ruff check .
uv run ruff format --check .
uv run mypy rpi_bad_power tests
uv run pytest
```

## Credits

- Originally created by [@shenxn](https://github.com/shenxn) in [`shenxn/rpi-bad-power`](https://github.com/shenxn/rpi-bad-power).
- Early implementation inspired by [`custom-components/sensor.rpi_power`](https://github.com/custom-components/sensor.rpi_power) maintained by [@swetoast](https://github.com/swetoast).
- Maintained as `rpi-bad-power-ng` by [@marcomsousa](https://github.com/marcomsousa) under the MIT License.
