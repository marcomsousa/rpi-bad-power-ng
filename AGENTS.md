# `rpi-bad-power-ng` — Agent & Engineering Guide (`AGENTS.md`)

## 1. Repository Overview & Provenance

`marcomsousa/rpi-bad-power-ng` is a **100% independent mirror and next-generation continuation** of [`shenxn/rpi-bad-power`](https://github.com/shenxn/rpi-bad-power) (mirrored from `master` at commit `41800a3`, tag `v0.1.0`).

- **License:** MIT License — preserve the original copyright notice in [`LICENSE`](LICENSE) and credit `@shenxn` and `@swetoast` when evolving the project.
- **Primary Goal:** Maintain a rock-solid, modern, zero-dependency Python library for Raspberry Pi under-voltage and power-health detection that remains **100% drop-in compatible** with Home Assistant Core's built-in [`rpi_power`](https://www.home-assistant.io/integrations/rpi_power/) integration ([`homeassistant/components/rpi_power`](https://github.com/home-assistant/core/tree/dev/homeassistant/components/rpi_power)).
- **Current Version (`0.2.0`):** Phases 1–4 (core hardening, `uv`/PEP 621 packaging & CI modernization, `sysfs_notify` async push watcher, and `ThrottledStatus` bitmask decoding) are implemented and tested across Python 3.12–3.15.

---

## 2. Repository Architecture Map

```text
rpi-bad-power-ng/
├── rpi_bad_power/
│   ├── __init__.py       # Public API, legacy constants, hwmon discovery, UnderVoltage classes
│   ├── _sysfs.py         # Internal sysfs readability checks and alarm value parser
│   ├── exceptions.py     # RpiBadPowerError (subclass of OSError preserving errno/filename)
│   ├── throttled.py      # ThrottledFlag, ThrottledStatus, read_throttled_status()
│   ├── watcher.py        # AsyncUnderVoltageWatcher (Linux POLLPRI|POLLERR + self-pipe shutdown)
│   └── py.typed          # PEP 561 typed package marker
├── tests/
│   ├── test_ha_compat.py      # Home Assistant Core rpi_power drop-in contract tests
│   ├── test_rpi_bad_power.py  # Discovery, parsing, newline/whitespace, permission & I/O error tests
│   ├── test_throttled.py      # Bitmask flag decoding and get_throttled_status() tests
│   └── test_watcher.py        # Async POLLPRI watcher, deduplication, cancellation & fd-leak tests
├── .github/workflows/
│   ├── ci.yml                 # Matrix CI (Python 3.12–3.15): ruff, mypy --strict, pytest, uv build
│   ├── publish.yml            # Release-gated PyPI Trusted Publishing (OIDC + attestations)
│   └── codeql-analysis.yml    # CodeQL v4 Python security analysis
├── pyproject.toml        # PEP 621 metadata (hatchling), ruff, mypy --strict, pytest, coverage
├── uv.lock               # Reproducible lockfile for dev dependencies
├── CHANGELOG.md          # Release history
├── README.md             # User documentation & examples
└── AGENTS.md             # This guide
```

---

## 3. Strict Home Assistant Compatibility Contract (DO NOT BREAK)

Home Assistant Core's [`rpi_power`](https://www.home-assistant.io/integrations/rpi_power/) integration imports and uses this library across [`__init__.py`](https://github.com/home-assistant/core/blob/dev/homeassistant/components/rpi_power/__init__.py), [`binary_sensor.py`](https://github.com/home-assistant/core/blob/dev/homeassistant/components/rpi_power/binary_sensor.py), [`config_flow.py`](https://github.com/home-assistant/core/blob/dev/homeassistant/components/rpi_power/config_flow.py), and [`manifest.json`](https://github.com/home-assistant/core/blob/dev/homeassistant/components/rpi_power/manifest.json) (with tests in [`tests/components/rpi_power`](https://github.com/home-assistant/core/tree/dev/tests/components/rpi_power)).

> [!IMPORTANT]
> **Always inspect live upstream Home Assistant Core `rpi_power` before and after library changes** (`gh api repos/home-assistant/core/contents/homeassistant/components/rpi_power`) to verify exact compatibility with imports, typing (`ConfigEntry[UnderVoltage]`), executor-thread behavior, repair flows, and loggers.

Every release **must preserve full backwards compatibility** with the contract below:

### 3.1 Package & Import Name
- The PyPI distribution name is **`rpi-bad-power-ng`**, while the importable Python package **must remain `rpi_bad_power`** (drop-in replacement for `rpi-bad-power`).
- Logger namespace must remain `rpi_bad_power` (`manifest.json` declares `"loggers": ["rpi_bad_power"]`).

### 3.2 Public API Used Directly by Home Assistant Core
```python
from rpi_bad_power import UnderVoltage, new_under_voltage
```
- `new_under_voltage() -> UnderVoltage | None`
  - Synchronous function (called via `await hass.async_add_executor_job(new_under_voltage)`).
  - **Must return `None`** on non-Raspberry Pi hardware or unsupported kernels/containers (never raise on unsupported platforms — HA relies on `None` to abort `config_flow` with `no_devices_found` and raise `ConfigEntryError` during `async_setup_entry`).
- `UnderVoltage.get() -> bool`
  - Synchronous method returning `True` if under-voltage is currently detected, `False` if power is normal.
  - Raises `RpiBadPowerError` (a subclass of `OSError`) on transient I/O or malformed sysfs reads.
  - Must remain thread-safe and safe to run inside Home Assistant's executor thread pool.

### 3.3 Legacy Public Symbols (Preserve at Top-Level `rpi_bad_power`)
- `HWMON_NAME`, `SYSFILE_HWMON_DIR`, `SYSFILE_HWMON_FILE`, `SYSFILE_LEGACY`, `UNDERVOLTAGE_STICKY_BIT`
- `get_rpi_volt_hwmon`
- `UnderVoltage`, `UnderVoltageNew`, `UnderVoltageLegacy`, `new_under_voltage`

### 3.4 Runtime & Environment Constraints
- **Zero third-party runtime dependencies:** Python standard library only.
- **Supported Python versions:** Python `>=3.12` (tested on 3.12, 3.13, 3.14, 3.15).
- **Container & HAOS compatibility:** Must operate purely via standard Linux sysfs files mounted read-only inside Home Assistant OS / Docker containers without requiring `vcgencmd`, `sudo`, or root privileges.

---

## 4. Linux Kernel & Hardware Context

1. **Modern Interface (`raspberrypi-hwmon`, Linux 4.19+ through Raspberry Pi 5):**
   - Driver: `drivers/hwmon/raspberrypi-hwmon.c`.
   - Exposes `/sys/class/hwmon/hwmon*/name` containing `"rpi_volt"` and sibling file `in0_lcrit_alarm` (`"0"` = normal, `"1"` = under-voltage alarm).
   - **Critical kernel behavior:** The driver polls the VideoCore firmware mailbox every **2 seconds** (`POLL_PERIOD = HZ * 2`), reads and clears `UNDERVOLTAGE_STICKY_BIT` (`BIT(16)`), updates `in0_lcrit_alarm`, and triggers **`sysfs_notify()`** on state changes.
   - **Push notifications:** `AsyncUnderVoltageWatcher` monitors `in0_lcrit_alarm` with `select.poll()` (`POLLPRI | POLLERR`) in a dedicated daemon thread with a self-pipe wakeup channel — avoiding both Home Assistant's 30-second polling blind spot and executor thread-pool exhaustion.
2. **Legacy Interface (Linux 4.14–4.19):**
   - Path: `/sys/devices/platform/soc/soc:firmware/get_throttled`.
   - Exposes a hexadecimal bitmask decoded by `ThrottledStatus` (active bits 0–3, sticky bits 16–19; `BIT(16)` = `0x10000` is `UNDERVOLTAGE_STICKY_BIT`).

---

## 5. Remaining Future Roadmap

### Completed (`v0.2.0`)
- [x] **Phase 1 — Core Bug Fixes & Hardening:** `.strip()` parsing, resilient hwmon discovery, readability verification, `RpiBadPowerError(OSError)`, `UnderVoltage(ABC)`, precomputed `Path`, explicit UTF-8.
- [x] **Phase 2 — Repository, CI/CD, Packaging & Test Modernization:** `uv` + `hatchling` PEP 621 `pyproject.toml`, `py.typed`, `ruff`, `mypy --strict`, `tests/` with `tmp_path`/`monkeypatch` (>97% branch coverage), GitHub Actions `ci.yml` / `publish.yml` / `codeql-analysis.yml`, removed broken `mirror.yml`.
- [x] **Phase 3 — Event-Driven Push Notifications (`sysfs_notify`):** `supports_push` and `AsyncUnderVoltageWatcher` via `UnderVoltageNew.async_watch()`.
- [x] **Phase 4 (Core) — Extended `get_throttled` Telemetry:** `ThrottledFlag`, `ThrottledStatus`, and `get_throttled_status()`.

### Remaining Future Work
- [ ] **Phase 4 (Hardware Research) — Raspberry Pi 5 PMIC Diagnostics:** Investigate read-only sysfs hwmon/PMIC voltage & current rails exposed on Raspberry Pi 5 kernels while preserving zero runtime dependencies and HAOS container compatibility.
- [ ] **Phase 5 — Companion Home Assistant Core (`rpi_power`) Integration Improvements:**
  1. Catch `OSError` / `RpiBadPowerError` in `binary_sensor.py` and toggle `_attr_available = False` cleanly.
  2. Fix misleading startup log (`"Voltage normalized..."` logged on boot when `_attr_is_on` transitions `None -> False` — change `else:` to `elif self._attr_is_on:`).
  3. Replace the hardcoded `translation_key="under_voltage_detected"` literal in `create_issue(...)` with `ISSUE_UNDER_VOLTAGE` (already used for `issue_id`) and fix `"a uninterruptible power supply"` wording in `DESCRIPTION_UNDER_VOLTAGE`.
  4. Migrate `manifest.json` from `iot_class: "local_polling"` to `"local_push"` using `under_voltage.async_watch()` (falling back to polling when `not under_voltage.supports_push`), add `diagnostics.py`, and target Silver/Gold Integration Quality Scale.

---

## 6. Development, Testing & Release Guidelines

### Mandatory Workflow Rules
1. **Always execute tests BEFORE and AFTER improvements:**
   - Run the full check/test baseline **before** editing code so pre-existing state is known.
   - Re-run all linters, strict type checks, tests (with branch coverage), and package builds **after** every change before committing or pushing.
2. **Always check Home Assistant Core `rpi_power` compatibility:**
   - Inspect live `homeassistant/components/rpi_power` (`__init__.py`, `binary_sensor.py`, `config_flow.py`, `manifest.json`, and `tests/components/rpi_power`) and ensure [`tests/test_ha_compat.py`](tests/test_ha_compat.py) passes.

### Local Toolchain Commands (Always use `uv`)
```bash
uv sync --all-groups
uv run ruff check .
uv run ruff format --check .
uv run mypy rpi_bad_power tests
uv run pytest
uv build
```
- **Branch coverage floor:** `fail_under = 95` (configured in `pyproject.toml`).
- **Test against real files:** Prefer `pytest`'s `tmp_path` + `monkeypatch` over custom `open()` mocks so newline, whitespace, symlink, permission, and file-descriptor edge cases are genuinely exercised.
- **Never swallow exceptions:** Context managers (`__exit__` / `__aexit__`) must return `None` / `False` unless explicitly suppressing a documented exception.

### CI/CD & PyPI Publish Safety
- `.github/workflows/publish.yml` is strictly gated on `on: release: types: [published]` (and manual `workflow_dispatch`) using PyPI Trusted Publishing (`pypa/gh-action-pypi-publish@release/v1` with OIDC + attestations). **Never** add `push` triggers to `publish.yml`.
- Verify `ci.yml` and `codeql-analysis.yml` pass on `master` before creating a GitHub release/tag.

### Git & Identity Hygiene
- Use atomic Conventional Commits (`fix:`, `feat:`, `test:`, `build:`, `ci:`, `docs:`, `refactor:`).
- Verify GitHub account (`marcomsousa`) and per-repo author (`Marco Sousa <marcomsousa@gmail.com>`) before committing or pushing.
