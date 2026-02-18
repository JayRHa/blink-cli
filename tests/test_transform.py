"""Tests for transformation helpers."""

from __future__ import annotations

from blink_cli.transform import build_summary, transform_cameras, transform_systems


def test_transform_systems() -> None:
    raw = {
        "Home Sync": {
            "name": "Home Sync",
            "system_id": "SYNC-1",
            "arm": True,
            "status": "online",
            "cameras_count": 2,
            "last_refresh": "2026-02-18T18:12:00Z",
        }
    }

    transformed = transform_systems(raw)

    assert transformed[0]["name"] == "Home Sync"
    assert transformed[0]["system_id"] == "SYNC-1"
    assert transformed[0]["armed"] is True
    assert transformed[0]["cameras_count"] == 2


def test_transform_cameras_with_missing_fields() -> None:
    raw = {
        "Front Door": {
            "name": "Front Door",
            "camera_id": "CAM-9",
            "system_name": "Home Sync",
            "arm": "disarmed",
            "motion": "false",
            "temperature_c": 5.0,
        },
        "Garage": {},
    }

    transformed = transform_cameras(raw)

    assert transformed[0]["name"] == "Front Door"
    assert transformed[0]["armed"] is False
    assert transformed[0]["motion_detected"] is False
    assert transformed[1]["name"] == "Garage"
    assert transformed[1]["camera_id"] is None


def test_build_summary() -> None:
    systems = [{"armed": True}, {"armed": False}]
    cameras = [{"armed": True}, {"armed": True}, {"armed": False}]

    summary = build_summary(systems, cameras)

    assert summary["systems_count"] == 2
    assert summary["cameras_count"] == 3
    assert summary["armed_systems_count"] == 1
    assert summary["armed_cameras_count"] == 2
