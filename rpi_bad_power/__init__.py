"""A library reading under-voltage status from the official Raspberry Pi kernel.

Minimal kernel needed is 4.14+.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
import logging
import os
from pathlib import Path

from rpi_bad_power._sysfs import is_readable_file, parse_alarm_value
from rpi_bad_power.exceptions import RpiBadPowerError

__version__ = "0.2.0"

__all__ = [
    "HWMON_NAME",
    "SYSFILE_HWMON_DIR",
    "SYSFILE_HWMON_FILE",
    "SYSFILE_LEGACY",
    "UNDERVOLTAGE_STICKY_BIT",
    "RpiBadPowerError",
    "UnderVoltage",
    "UnderVoltageLegacy",
    "UnderVoltageNew",
    "__version__",
    "get_rpi_volt_hwmon",
    "new_under_voltage",
]

_LOGGER = logging.getLogger(__name__)

HWMON_NAME = "rpi_volt"

SYSFILE_HWMON_DIR = "/sys/class/hwmon"
SYSFILE_HWMON_FILE = "in0_lcrit_alarm"
SYSFILE_LEGACY = "/sys/devices/platform/soc/soc:firmware/get_throttled"

UNDERVOLTAGE_STICKY_BIT = 1 << 16


def get_rpi_volt_hwmon() -> str | None:
    """Find the ``rpi_volt`` hwmon device directory path."""
    hwmon_root = Path(SYSFILE_HWMON_DIR)
    try:
        entries = sorted(hwmon_root.iterdir())
    except OSError as err:
        _LOGGER.debug("Unable to list hwmon directory %s: %s", hwmon_root, err)
        return None

    for hwmon_dir in entries:
        name_file = hwmon_dir / "name"
        try:
            if not name_file.is_file():
                continue
            with name_file.open(encoding="utf-8") as file:
                hwmon_name = file.read().strip()
        except (OSError, UnicodeDecodeError) as err:
            _LOGGER.debug("Skipping unreadable hwmon entry %s: %s", name_file, err)
            continue
        if hwmon_name == HWMON_NAME:
            return str(hwmon_dir)

    return None


class UnderVoltage(ABC):
    """Abstract base class for reading Raspberry Pi under-voltage status."""

    @abstractmethod
    def get(self) -> bool:
        """Get current under-voltage status."""


class UnderVoltageNew(UnderVoltage):
    """Read under-voltage status from the modern ``raspberrypi-hwmon`` sysfs entry."""

    def __init__(self, hwmon: str | os.PathLike[str]) -> None:
        """Initialize the modern hwmon under-voltage reader."""
        self._hwmon = os.fspath(hwmon)
        self._alarm_path = Path(self._hwmon) / SYSFILE_HWMON_FILE

    @property
    def alarm_path(self) -> Path:
        """Return the resolved ``in0_lcrit_alarm`` sysfs file path."""
        return self._alarm_path

    def get(self) -> bool:
        """Get under-voltage status from ``in0_lcrit_alarm``."""
        try:
            with self._alarm_path.open(encoding="utf-8") as file:
                raw = file.read()
        except OSError as err:
            raise RpiBadPowerError.from_os_error(
                "Failed to read under-voltage status",
                err,
                self._alarm_path,
            ) from err
        except UnicodeDecodeError as err:
            raise RpiBadPowerError(
                f"Failed to decode under-voltage status from {self._alarm_path}: {err}"
            ) from err
        return parse_alarm_value(raw, self._alarm_path)


class UnderVoltageLegacy(UnderVoltage):
    """Read under-voltage status from the legacy ``get_throttled`` sysfs entry."""

    def __init__(self, path: str | os.PathLike[str] | None = None) -> None:
        """Initialize the legacy under-voltage reader."""
        self._path: Path | None = Path(path) if path is not None else None

    @property
    def throttled_path(self) -> Path:
        """Return the ``get_throttled`` sysfs file path."""
        return self._path if self._path is not None else Path(SYSFILE_LEGACY)

    def get(self) -> bool:
        """Get under-voltage status from ``get_throttled``."""
        path = self.throttled_path
        try:
            with path.open(encoding="utf-8") as file:
                throttled = file.read().strip()
        except OSError as err:
            raise RpiBadPowerError.from_os_error(
                "Failed to read legacy throttled status",
                err,
                path,
            ) from err
        except UnicodeDecodeError as err:
            raise RpiBadPowerError(
                f"Failed to decode legacy throttled status from {path}: {err}"
            ) from err

        _LOGGER.debug("Get throttled value: %s", throttled)
        if not throttled:
            raise RpiBadPowerError(f"Empty throttled value from {path}")
        try:
            throttled_value = int(throttled, base=16)
        except ValueError as err:
            raise RpiBadPowerError(
                f"Invalid throttled hex value {throttled!r} from {path}"
            ) from err
        if throttled_value < 0:
            raise RpiBadPowerError(
                f"Negative throttled hex value {throttled!r} from {path}"
            )
        return (throttled_value & UNDERVOLTAGE_STICKY_BIT) == UNDERVOLTAGE_STICKY_BIT


def new_under_voltage() -> UnderVoltage | None:
    """Create an ``UnderVoltage`` reader for the current Raspberry Pi kernel.

    Returns ``None`` on unsupported hardware, kernels, or container mounts.
    """
    hwmon = get_rpi_volt_hwmon()
    if hwmon is not None:
        candidate = UnderVoltageNew(hwmon)
        if is_readable_file(candidate.alarm_path):
            return candidate
        _LOGGER.debug(
            "Found %s hwmon at %s but %s is not readable",
            HWMON_NAME,
            hwmon,
            candidate.alarm_path,
        )

    legacy_path = Path(SYSFILE_LEGACY)
    if is_readable_file(legacy_path):
        return UnderVoltageLegacy()

    return None
