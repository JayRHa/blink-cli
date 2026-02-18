"""Transform raw API responses into normalized CLI payloads."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def _dig(data: Mapping[str, Any], *path: str, default: Any = None) -> Any:
    """Read nested dict fields safely."""
    current: Any = data
    for key in path:
        if not isinstance(current, Mapping) or key not in current:
            return default
        current = current[key]
    return current


def _coalesce(*values: Any) -> Any:
    """Return the first non-None value."""
    for value in values:
        if value is not None:
            return value
    return None


def _to_bool(value: Any) -> bool | None:
    """Convert common truthy/falsey values to bool."""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on", "armed", "enabled"}:
            return True
        if normalized in {"0", "false", "no", "off", "disarmed", "disabled"}:
            return False
    return None


def transform_systems(data: Mapping[str, Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Transform Blink sync-module payloads."""
    systems: list[dict[str, Any]] = []
    for system_name in sorted(data):
        raw = data[system_name]
        systems.append(
            {
                "name": _coalesce(raw.get("name"), system_name),
                "system_id": _coalesce(raw.get("system_id"), raw.get("sync_id")),
                "armed": _to_bool(
                    _coalesce(raw.get("armed"), raw.get("arm"), raw.get("is_armed"))
                ),
                "status": raw.get("status"),
                "cameras_count": _coalesce(
                    raw.get("cameras_count"),
                    raw.get("camera_count"),
                ),
                "last_refresh_utc": _coalesce(
                    raw.get("last_refresh_utc"),
                    raw.get("last_refresh"),
                ),
            }
        )
    return systems


def transform_cameras(data: Mapping[str, Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Transform Blink camera payloads."""
    cameras: list[dict[str, Any]] = []
    for camera_name in sorted(data):
        raw = data[camera_name]
        cameras.append(
            {
                "name": _coalesce(raw.get("name"), camera_name),
                "camera_id": _coalesce(raw.get("camera_id"), raw.get("id")),
                "system_name": _coalesce(raw.get("system_name"), raw.get("network")),
                "armed": _to_bool(
                    _coalesce(
                        raw.get("armed"),
                        raw.get("arm"),
                        raw.get("motion_detection_enabled"),
                    )
                ),
                "motion_detected": _to_bool(
                    _coalesce(raw.get("motion_detected"), raw.get("motion"))
                ),
                "battery_pct": _coalesce(raw.get("battery_pct"), raw.get("battery")),
                "battery_low": _to_bool(
                    _coalesce(raw.get("battery_low"), raw.get("battery_alert"))
                ),
                "temperature_c": _coalesce(
                    raw.get("temperature_c"),
                    raw.get("temperature"),
                ),
                "wifi_strength_pct": _coalesce(
                    raw.get("wifi_strength_pct"),
                    raw.get("wifi_strength"),
                ),
                "status": raw.get("status"),
                "thumbnail_url": _coalesce(
                    raw.get("thumbnail_url"),
                    raw.get("thumbnail"),
                ),
                "serial": raw.get("serial"),
                "last_refresh_utc": _coalesce(
                    raw.get("last_refresh_utc"),
                    raw.get("last_refresh"),
                ),
            }
        )
    return cameras


def build_summary(
    systems: list[Mapping[str, Any]],
    cameras: list[Mapping[str, Any]],
) -> dict[str, Any]:
    """Build a compact account summary."""
    return {
        "systems_count": len(systems),
        "cameras_count": len(cameras),
        "armed_systems_count": sum(1 for item in systems if item.get("armed") is True),
        "armed_cameras_count": sum(1 for item in cameras if item.get("armed") is True),
    }
