# `rpi-bad-power-ng` — Agent & Engineering Guide (`AGENTS.md`)

## 1. Repository Overview & Provenance

`marcomsousa/rpi-bad-power-ng` is a **100% independent mirror and next-generation continuation** of [`shenxn/rpi-bad-power`](https://github.com/shenxn/rpi-bad-power) (mirrored from `master` at commit `41800a3`, tag `v0.1.0`).

- **License:** MIT License — preserve the original copyright notice in [`LICENSE`](LICENSE) and credit `@shenxn` and `@swetoast` when evolving the project.
- **Primary Goal:** Maintain a rock-solid, modern, zero-dependency Python library for Raspberry Pi under-voltage and power-health detection that remains **100% drop-in compatible** with Home Assistant Core's built-in [`rpi_power`](https://www.home-assistant.io/integrations/rpi_power/) integration ([`homeassistant/components/rpi_power`](https://github.com/home-assistant/core/tree/dev/homeassistant/components/rpi_power)).
- **Current Baseline:** Mirrors upstream `v0.1.0` verbatim (except for this `AGENTS.md`). All improvements below are planned for subsequent PRs/releases.

---

## 2. Strict Home Assistant Compatibility Contract (DO NOT BREAK)

Home Assistant Core's `rpi_power` integration imports and uses this library across three modules (`__init__.py`, `binary_sensor.py`, `config_flow.py`) and `manifest.json`. Any future release **must preserve full backwards compatibility** with the contract below:

### 2.1 Package & Import Name
- The importable Python package **must remain `rpi_bad_power`** (even if the PyPI distribution name is `rpi-bad-power-ng` or a drop-in replacement for `rpi-bad-power`).
- Logger namespace must remain `rpi_bad_power` (`manifest.json` declares `"loggers": ["rpi_bad_power"]`).

### 2.2 Public API Used Directly by Home Assistant Core
```python
from rpi_bad_power import UnderVoltage, new_under_voltage
```
- `new_under_voltage() -> UnderVoltage | None`
  - Synchronous function (called via `await hass.async_add_executor_job(new_under_voltage)`).
  - **Must return `None`** on non-Raspberry Pi hardware or unsupported kernels/containers (never raise on unsupported platforms — HA relies on `None` to abort `config_flow` with `no_devices_found` and raise `ConfigEntryError` during `async_setup_entry`).
- `UnderVoltage.get() -> bool`
  - Synchronous method returning `True` if under-voltage is currently detected, `False` if power is normal.
  - Must remain thread-safe and safe to run inside Home Assistant's executor thread pool.

### 2.3 Legacy Public Symbols (Preserve for Downstream Consumers & Tests)
Keep these top-level exports available (or re-exported if code is split across submodules):
- `HWMON_NAME`, `SYSFILE_HWMON_DIR`, `SYSFILE_HWMON_FILE`, `SYSFILE_LEGACY`, `UNDERVOLTAGE_STICKY_BIT`
- `get_rpi_volt_hwmon`
- `UnderVoltage`, `UnderVoltageNew`, `UnderVoltageLegacy`, `new_under_voltage`

### 2.4 Runtime & Environment Constraints
- **Zero third-party runtime dependencies:** Use Python standard library only (`os`, `pathlib`, `select`, `abc`, `logging`, `asyncio`, etc.).
- **Supported Python versions:** Track Home Assistant Core's supported Python versions (Python 3.12+ / 3.13+ / 3.14+).
- **Container & HAOS compatibility:** Must operate purely via standard Linux sysfs files mounted read-only inside Home Assistant OS / Docker containers without requiring `vcgencmd`, `sudo`, or root privileges.

---

## 3. Linux Kernel & Hardware Context

1. **Modern Interface (`raspberrypi-hwmon`, Linux 4.19+ through Raspberry Pi 5):**
   - Driver: `drivers/hwmon/raspberrypi-hwmon.c`.
   - Exposes `/sys/class/hwmon/hwmon*/name` containing `"rpi_volt"` and sibling file `in0_lcrit_alarm` (`"0"` = normal, `"1"` = under-voltage alarm).
   - **Critical kernel behavior:** The driver polls the VideoCore firmware mailbox every **2 seconds** (`POLL_PERIOD = HZ * 2`), reads and clears `UNDERVOLTAGE_STICKY_BIT` (`BIT(16)`), updates `in0_lcrit_alarm`, and triggers **`sysfs_notify()`** on state changes.
   - **Polling blind spot:** Because Home Assistant currently polls `rpi_power` every 30 seconds (`iot_class: "local_polling"`), a transient 2–10 second voltage sag can assert and clear `in0_lcrit_alarm` between polls and go **completely undetected**.
2. **Legacy Interface (Linux 4.14–4.19):**
   - Path: `/sys/devices/platform/soc/soc:firmware/get_throttled`.
   - Exposes a hexadecimal bitmask where `BIT(16)` (`1 << 16`, `0x10000`) indicates an under-voltage event.

---

## 4. Planned Future Improvements Roadmap

Execute improvements incrementally in phases, keeping every release backwards-compatible with Section 2.

### Phase 1 — Core Bug Fixes & Hardening (Drop-In `0.2.0`)
Target file: `rpi_bad_power/__init__.py`

1. **Fix fragile string slicing (`[:-1]` → `.strip()`):**
   - `UnderVoltageNew.get()` and `UnderVoltageLegacy.get()` currently use `file.read()[:-1]`. If sysfs output lacks a trailing newline or contains `\r\n`/whitespace, `[:-1]` truncates the value (`"1"` becomes `""`). Switch both to `.strip()` (matching `get_rpi_volt_hwmon()`).
2. **Harden hwmon discovery (`get_rpi_volt_hwmon()` & `new_under_voltage()`):**
   - Catch `OSError` (not only `FileNotFoundError`) when listing `SYSFILE_HWMON_DIR` (handles `PermissionError`, missing sysfs mounts).
   - Guard individual `name` file reads with `try / except OSError: continue` so a single unreadable or hotplugged `hwmon*` device does not abort discovery before reaching `rpi_volt`.
   - Verify that `in0_lcrit_alarm` actually exists and is readable before returning `UnderVoltageNew` (otherwise fall back cleanly or return `None` instead of deferring `FileNotFoundError`/`PermissionError` to runtime `get()`).
3. **Structured error handling in `get()`:**
   - Guard `int(throttled, base=16)` against `ValueError` on empty/corrupt sysfs reads.
   - Define a dedicated exception (e.g., `class RpiBadPowerError(OSError): ...` — subclassing `OSError` ensures existing `except OSError:` handlers in HA keep working seamlessly) and raise it with clear context on transient sysfs/mailbox I/O errors (`EIO`, `ETIMEDOUT`, malformed content).
4. **Fix `UnderVoltage` abstract base class:**
   - Inherit `UnderVoltage` from `abc.ABC` and decorate `get()` with `@abc.abstractmethod` so direct instantiation fails loudly instead of silently returning `None` for a `-> bool` method.
5. **Clean up path handling & file I/O:**
   - Precompute `self._alarm_path = Path(hwmon) / SYSFILE_HWMON_FILE` in `UnderVoltageNew.__init__` instead of joining paths on every `get()`.
   - Always pass explicit `encoding="utf-8"` when opening sysfs text files.

### Phase 2 — Repository, CI/CD, Packaging & Test Suite Modernization
Target files: `.github/workflows/*`, `pyproject.toml`, `setup.py`, `setup.cfg`, `test_rpi_bad_power.py`

1. **Remove broken upstream Gitee mirror workflow:**
   - Delete `.github/workflows/mirror.yml` — it runs on every push to `master` and attempts to force-push to `git@gitee.com:shenxn/rpi-bad-power.git` using a non-existent `GITEE_KEY` secret.
2. **Modernize GitHub Actions CI/CD:**
   - Upgrade obsolete actions (`actions/checkout@v2`, `actions/setup-python@v2`, `codecov/codecov-action@v1`, `github/codeql-action/*@v1`) to current major versions.
   - Update Python test matrix from `["3.7", "3.8", "3.9"]` to active HA-supported versions (e.g., `["3.12", "3.13", "3.14"]`).
   - Modernize `publish.yml` to use `python -m build` and PyPI Trusted Publishing (OIDC) instead of `python setup.py sdist bdist_wheel` + password token.
3. **Modernize Packaging & Static Typing:**
   - Migrate package metadata from `setup.py` + `setup.cfg` to PEP 621 `pyproject.toml` (clean out copied HA boilerplate from `pyproject.toml` such as `ciso8601`, `cv2`, `homeassistant` isort/pylint sections).
   - Add PEP 561 `rpi_bad_power/py.typed` marker so Home Assistant's `mypy` validates types directly from the package.
   - Replace deprecated `typing.Text`, `Optional`, `List`, `Mapping` with native `str`, `X | None`, `list`, `collections.abc.Mapping`.
   - Standardize linting/formatting on `ruff` + `mypy --strict`.
4. **Overhaul unit tests (`test_rpi_bad_power.py`):**
   - Replace custom `MockSysFiles` / `PatchFile` mocks with `pytest` `tmp_path` + `monkeypatch` against real temporary files.
   - **Known bugs in upstream tests to fix:**
     - `PatchFile.__exit__` returns `True`, which silently swallows exceptions raised inside `with open(...)` blocks.
     - `PatchSysFiles.__exit__` returns `False` early on exception without calling `__exit__` on its three active patches, leaking mocks across tests on failure.
   - Add edge-case test coverage: values without trailing `\n`, `\r\n`, unreadable `hwmon*` preceding `rpi_volt`, missing/unreadable `in0_lcrit_alarm`, `PermissionError` on `/sys/class/hwmon`, invalid/empty hex in `get_throttled`, `UnderVoltage` ABC instantiation check.

### Phase 3 — Event-Driven Push Notifications via `sysfs_notify` (Enables HA `local_push`)
*Highest-impact functional upgrade — eliminates the 30-second HA polling blind spot.*

1. **Implement kernel event watching on `in0_lcrit_alarm`:**
   - Use Linux `select.poll()` / `select.epoll()` monitoring `POLLPRI | POLLERR` on an open file descriptor to `in0_lcrit_alarm`.
   - Follow Linux sysfs notification semantics strictly:
     - Perform an initial read (and `seek(0)`) before waiting on `POLLPRI` to clear the initial event state.
     - On each wakeup, `seek(0)` and re-read the alarm value (`"0"` or `"1"`).
2. **Design an HA-friendly async & lifecycle API (strictly additive):**
   - Expose `supports_push: bool` on `UnderVoltage` (`True` for `UnderVoltageNew`, `False` for `UnderVoltageLegacy`).
   - Provide an async-friendly watcher interface (e.g., `async_watch(...)` / callback registration / async context manager with clean `close()` and cancellation handling, no leaked file descriptors or orphan threads).
   - Keep `new_under_voltage()` and synchronous `.get()` completely unchanged so existing callers continue working with zero modifications.
3. **Graceful fallback:**
   - When `supports_push` is `False` (legacy kernel or unsupported platform), callers can cleanly fall back to polling `.get()`.

### Phase 4 — Optional Extended Power & Throttling Telemetry (Additive / Opt-In)
1. **Full `get_throttled` bitmask decoding (when available):**
   - Provide a structured dataclass/flags API (e.g., `ThrottledStatus`) decoding all documented Raspberry Pi firmware flags without altering `UnderVoltage.get() -> bool`:
     - Active flags: bit 0 (under-voltage), bit 1 (ARM frequency capped), bit 2 (currently throttled), bit 3 (soft temperature limit active).
     - Sticky flags: bit 16 (under-voltage occurred), bit 17 (ARM frequency capping occurred), bit 18 (throttling occurred), bit 19 (soft temperature limit occurred).
2. **Raspberry Pi 5 power/PMIC diagnostics investigation:**
   - Research sysfs-accessible hwmon/power attributes on Raspberry Pi 5 kernels (e.g., `rpi_volt`, PMIC voltage/current rails if exposed under `/sys/class/hwmon` or `/sys/firmware/devicetree`) while preserving zero external dependencies and HAOS container compatibility.

### Phase 5 — Companion Home Assistant Core (`rpi_power`) Integration Improvements
When updating or proposing changes to `homeassistant/components/rpi_power` to adopt `rpi-bad-power-ng` (or upstream improvements):

1. **Error handling & availability (`binary_sensor.py`):**
   - Catch `OSError` / `RpiBadPowerError` in `update()` (or adopt a `DataUpdateCoordinator`) and toggle `_attr_available = False` instead of logging unhandled tracebacks on every poll.
2. **Fix misleading startup log (`binary_sensor.py`):**
   - `_attr_is_on` starts as `None`; on the first update when `value is False`, `if self._attr_is_on != value:` enters the `else:` branch and logs `"Voltage normalized. Everything is working as intended."` on every HA boot. Change the `else:` branch to `elif self._attr_is_on:` so normalization is only logged on an actual `True -> False` transition.
3. **Constants & log wording (`binary_sensor.py`):**
   - Use the imported `ISSUE_UNDER_VOLTAGE` constant instead of the hardcoded `"under_voltage_detected"` literal in `create_issue(...)`.
   - Fix typo in `DESCRIPTION_UNDER_VOLTAGE` (`"a uninterruptible power supply"` → recommend an adequate power supply/cable, aligning with `strings.json`).
4. **Switch to `local_push` & Quality Scale:**
   - Once Phase 3 ships, migrate `manifest.json` from `iot_class: "local_polling"` to `"local_push"` (with polling fallback when `not under_voltage.supports_push`), add `diagnostics.py`, and target Silver/Gold Integration Quality Scale.

---

## 5. Agent & Development Guidelines

1. **Preserve Drop-In HA Compatibility:** Before merging any change, verify that `from rpi_bad_power import UnderVoltage, new_under_voltage` and both `new_under_voltage()` and `UnderVoltage.get()` honor Section 2.
2. **Test Against Real Files:** Prefer `pytest`'s `tmp_path` + `monkeypatch` over custom `open()` mocks so newline, whitespace, permission, and file-descriptor edge cases are genuinely exercised.
3. **Never Swallow Exceptions:** Context managers (`__exit__`) in production code and tests must return `None` / `False` unless explicitly suppressing a documented exception.
4. **Git & Identity Hygiene:**
   - Use atomic Conventional Commits (`fix:`, `feat:`, `test:`, `ci:`, `docs:`, `refactor:`).
   - Keep `master` (or `main` if renamed deliberately) green with passing tests, strict `mypy`, and linting before tagging releases.
   - Verify GitHub account (`marcomsousa`) and per-repo author (`Marco Sousa <marcomsousa@gmail.com>`) before committing/pushing.
