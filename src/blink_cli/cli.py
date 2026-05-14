"""Command line interface for Blink."""

from __future__ import annotations

import argparse
import asyncio
import inspect
import json
import os
import stat
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from .const import (
    API_TIMEOUT_SECONDS,
    AUTH_ERROR_HINTS,
    BOOL_RENDER,
    DEFAULT_DOWNLOAD_DELAY_SECONDS,
    DEFAULT_DOWNLOAD_STOP_SECONDS,
    RATE_LIMIT_HINTS,
    TWO_FACTOR_HINTS,
)
from .transform import build_summary, transform_cameras, transform_systems


class CliInputError(ValueError):
    """Raised for invalid CLI argument combinations."""


class BlinkAuthError(RuntimeError):
    """Raised for Blink authentication errors."""


class BlinkRateLimitError(RuntimeError):
    """Raised when Blink rate limiting is detected."""


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser."""
    parser = argparse.ArgumentParser(
        prog="blink",
        description="High-signal CLI for Blink systems and cameras",
    )
    parser.add_argument(
        "--username",
        default=os.getenv("BLINK_USERNAME"),
        help="Blink account email (or env BLINK_USERNAME)",
    )
    parser.add_argument(
        "--password",
        default=os.getenv("BLINK_PASSWORD"),
        help="Blink account password (or env BLINK_PASSWORD)",
    )
    parser.add_argument(
        "--auth-file",
        default=os.getenv("BLINK_AUTH_FILE"),
        help=(
            "Path to blinkpy auth JSON (or env BLINK_AUTH_FILE). "
            "If the file exists it is used for login."
        ),
    )
    parser.add_argument(
        "--pin",
        default=os.getenv("BLINK_PIN"),
        help="Two-factor verification PIN (or env BLINK_PIN)",
    )
    parser.add_argument(
        "--json",
        dest="json_output",
        action="store_true",
        help="Output as JSON",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("systems", help="List Blink sync modules")

    cameras_parser = subparsers.add_parser("cameras", help="List Blink cameras")
    cameras_parser.add_argument("--system", help="Filter by sync-module name")

    refresh_parser = subparsers.add_parser("refresh", help="Refresh account data")
    refresh_parser.add_argument(
        "--force",
        action="store_true",
        help="Force refresh from Blink cloud",
    )

    system_arm_parser = subparsers.add_parser(
        "system-arm",
        help="Set armed state for a sync module",
    )
    system_arm_parser.add_argument("--system", required=True, help="Sync-module name")
    system_arm_parser.add_argument(
        "--state",
        required=True,
        choices=("armed", "disarmed"),
        help="Target state",
    )

    camera_arm_parser = subparsers.add_parser(
        "camera-arm",
        help="Set armed state for a camera",
    )
    camera_arm_parser.add_argument("--camera", required=True, help="Camera name")
    camera_arm_parser.add_argument(
        "--state",
        required=True,
        choices=("armed", "disarmed"),
        help="Target state",
    )

    trigger_parser = subparsers.add_parser(
        "trigger-camera",
        help="Trigger a snapshot for a camera",
    )
    trigger_parser.add_argument("--camera", required=True, help="Camera name")

    save_image_parser = subparsers.add_parser(
        "save-image",
        help="Save camera image to file",
    )
    save_image_parser.add_argument("--camera", required=True, help="Camera name")
    save_image_parser.add_argument("--path", required=True, help="Output file path")
    save_image_parser.add_argument(
        "--trigger",
        action="store_true",
        help="Trigger a snapshot before saving",
    )
    save_image_parser.add_argument(
        "--refresh",
        action="store_true",
        help="Refresh data before saving",
    )

    save_video_parser = subparsers.add_parser(
        "save-video",
        help="Save latest camera video to file",
    )
    save_video_parser.add_argument("--camera", required=True, help="Camera name")
    save_video_parser.add_argument("--path", required=True, help="Output file path")
    save_video_parser.add_argument(
        "--refresh",
        action="store_true",
        help="Refresh data before saving",
    )

    download_parser = subparsers.add_parser(
        "download-videos",
        help="Download videos from Blink cloud",
    )
    download_parser.add_argument("--path", required=True, help="Output directory")
    download_parser.add_argument("--camera", help="Filter to one camera")
    download_parser.add_argument(
        "--since",
        help="Only download videos newer than this timestamp (UTC string)",
    )
    download_parser.add_argument(
        "--delay",
        type=int,
        default=DEFAULT_DOWNLOAD_DELAY_SECONDS,
        help=f"Polling delay in seconds (default: {DEFAULT_DOWNLOAD_DELAY_SECONDS})",
    )
    download_parser.add_argument(
        "--stop",
        type=int,
        default=DEFAULT_DOWNLOAD_STOP_SECONDS,
        help=f"Polling timeout in seconds (default: {DEFAULT_DOWNLOAD_STOP_SECONDS})",
    )

    save_auth_parser = subparsers.add_parser(
        "save-auth",
        help="Persist auth/session data to a JSON file",
    )
    save_auth_parser.add_argument("--path", required=True, help="Target auth file path")

    return parser


def validate_args(args: argparse.Namespace) -> None:
    """Validate argument combinations."""
    if args.username and not args.password:
        raise CliInputError("Use --username and --password together.")

    if args.password and not args.username:
        raise CliInputError("Use --username and --password together.")

    if not args.auth_file and not (args.username and args.password):
        raise CliInputError(
            "Authentication missing. Use --auth-file or --username/--password."
        )

    if hasattr(args, "delay") and args.delay <= 0:
        raise CliInputError("--delay must be greater than 0.")

    if hasattr(args, "stop") and args.stop <= 0:
        raise CliInputError("--stop must be greater than 0.")


def _render_value(value: Any) -> str:
    """Render values for human output."""
    if value is None:
        return "-"
    if isinstance(value, bool):
        return BOOL_RENDER[value]
    return str(value)


def _render_table(headers: list[str], rows: list[list[Any]]) -> str:
    """Render rows as a plain text table."""
    normalized_rows = [[_render_value(value) for value in row] for row in rows]
    widths = [len(h) for h in headers]

    for row in normalized_rows:
        for index, value in enumerate(row):
            widths[index] = max(widths[index], len(value))

    header_line = " | ".join(
        header.ljust(widths[i]) for i, header in enumerate(headers)
    )
    separator = "-+-".join("-" * width for width in widths)
    body = [
        " | ".join(value.ljust(widths[i]) for i, value in enumerate(row))
        for row in normalized_rows
    ]

    return "\n".join([header_line, separator, *body])


def _print_line(label: str, value: Any) -> None:
    """Print one key/value line."""
    print(f"{label}: {_render_value(value)}")


def _normalize_name(value: str) -> str:
    return value.strip().lower()


def _coalesce(*values: Any) -> Any:
    for value in values:
        if value is not None:
            return value
    return None


def _matches_hint(error: Exception, hints: tuple[str, ...]) -> bool:
    message = f"{type(error).__name__} {error}".lower()
    return any(hint in message for hint in hints)


def _is_auth_error(error: Exception) -> bool:
    return _matches_hint(error, AUTH_ERROR_HINTS)


def _is_two_factor_error(error: Exception) -> bool:
    return _matches_hint(error, TWO_FACTOR_HINTS)


def _is_rate_limit_error(error: Exception) -> bool:
    return _matches_hint(error, RATE_LIMIT_HINTS)


def _mapping_get(mapping: Mapping[str, Any], key: str) -> Any:
    return mapping.get(key)


def _ensure_mapping(value: Any) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    return {}


def _restrict_file_permissions(path: Path) -> None:
    """Restrict a sensitive file to owner read/write only (0600).

    The Blink auth JSON contains session tokens and (depending on the
    blinkpy version) credential material. Persisting it with default
    umask permissions can leave it world- or group-readable on shared
    systems. On POSIX platforms we lock it down to 0600; on Windows
    chmod is a no-op for these bits, so we silently skip.
    """
    if os.name != "posix":
        return
    try:
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        # Best-effort: don't fail the command if the filesystem (e.g.
        # FAT/exFAT) doesn't support POSIX permissions.
        pass


def _load_auth_data(args: argparse.Namespace) -> dict[str, Any]:
    auth_data: dict[str, Any] = {}

    if args.auth_file:
        auth_path = Path(args.auth_file).expanduser()
        if auth_path.exists():
            try:
                loaded = json.loads(auth_path.read_text(encoding="utf-8"))
            except OSError as error:
                raise CliInputError(f"Cannot read auth file: {auth_path}") from error
            except json.JSONDecodeError as error:
                raise CliInputError(f"Invalid JSON in auth file: {auth_path}") from error

            if not isinstance(loaded, Mapping):
                raise CliInputError("Auth file must contain a JSON object.")
            auth_data.update(dict(loaded))
        elif not (args.username and args.password):
            raise CliInputError(f"Auth file not found: {auth_path}")

    if args.username:
        auth_data["username"] = args.username
    if args.password:
        auth_data["password"] = args.password

    return auth_data


async def _call_method(
    target: Any,
    attempts: list[tuple[str, tuple[Any, ...], dict[str, Any]]],
    action: str,
) -> Any:
    for method_name, method_args, method_kwargs in attempts:
        method = getattr(target, method_name, None)
        if method is None:
            continue

        filtered_kwargs = {key: value for key, value in method_kwargs.items() if value is not None}
        try:
            result = method(*method_args, **filtered_kwargs)
        except TypeError:
            continue

        if inspect.isawaitable(result):
            return await result
        return result

    raise RuntimeError(f"{action} is not supported by the installed blinkpy version.")


def _build_location(args: argparse.Namespace, blink: Any) -> dict[str, Any]:
    auth = getattr(blink, "auth", None)
    return {
        "account": _coalesce(args.username, getattr(auth, "username", None)),
        "region": getattr(auth, "region", None),
    }


def _sync_to_raw(sync_module: Any) -> dict[str, Any]:
    attributes = _ensure_mapping(getattr(sync_module, "attributes", {}))
    linked_cameras = getattr(sync_module, "cameras", None)
    cameras_count = None
    if isinstance(linked_cameras, Mapping):
        cameras_count = len(linked_cameras)
    elif isinstance(linked_cameras, Sequence) and not isinstance(
        linked_cameras, (str, bytes)
    ):
        cameras_count = len(linked_cameras)

    return {
        "name": getattr(sync_module, "name", None),
        "system_id": _coalesce(
            getattr(sync_module, "sync_id", None),
            _mapping_get(attributes, "sync_id"),
            _mapping_get(attributes, "id"),
        ),
        "armed": _coalesce(
            getattr(sync_module, "arm", None),
            getattr(sync_module, "armed", None),
            _mapping_get(attributes, "armed"),
            _mapping_get(attributes, "arm"),
        ),
        "status": _coalesce(
            getattr(sync_module, "status", None),
            _mapping_get(attributes, "status"),
        ),
        "cameras_count": _coalesce(
            cameras_count,
            _mapping_get(attributes, "cameras_count"),
            _mapping_get(attributes, "camera_count"),
        ),
        "last_refresh_utc": _coalesce(
            _mapping_get(attributes, "last_refresh"),
            _mapping_get(attributes, "updated_at"),
        ),
    }


def _camera_to_raw(camera: Any) -> dict[str, Any]:
    attributes = _ensure_mapping(getattr(camera, "attributes", {}))
    sync_module = getattr(camera, "sync", None)
    sync_name = getattr(sync_module, "name", None)

    return {
        "name": getattr(camera, "name", None),
        "camera_id": _coalesce(
            getattr(camera, "camera_id", None),
            _mapping_get(attributes, "id"),
            _mapping_get(attributes, "camera_id"),
        ),
        "system_name": _coalesce(
            sync_name,
            _mapping_get(attributes, "network"),
            _mapping_get(attributes, "sync_name"),
            _mapping_get(attributes, "system_name"),
        ),
        "armed": _coalesce(
            getattr(camera, "arm", None),
            _mapping_get(attributes, "armed"),
            _mapping_get(attributes, "arm"),
            _mapping_get(attributes, "motion_detection_enabled"),
        ),
        "motion_detected": _coalesce(
            getattr(camera, "motion_detected", None),
            _mapping_get(attributes, "motion_detected"),
            _mapping_get(attributes, "motion"),
        ),
        "battery_pct": _coalesce(
            _mapping_get(attributes, "battery"),
            _mapping_get(attributes, "battery_level"),
        ),
        "battery_low": _coalesce(
            getattr(camera, "battery_alert", None),
            _mapping_get(attributes, "battery_low"),
            _mapping_get(attributes, "battery_alert"),
        ),
        "temperature_c": _coalesce(
            _mapping_get(attributes, "temperature_c"),
            _mapping_get(attributes, "temperature"),
            getattr(camera, "temperature", None),
        ),
        "wifi_strength_pct": _coalesce(
            _mapping_get(attributes, "wifi_strength_pct"),
            _mapping_get(attributes, "wifi_strength"),
            _mapping_get(attributes, "wifi_signal"),
        ),
        "status": _coalesce(
            getattr(camera, "status", None),
            _mapping_get(attributes, "status"),
        ),
        "thumbnail_url": _coalesce(
            _mapping_get(attributes, "thumbnail_url"),
            _mapping_get(attributes, "thumbnail"),
            getattr(camera, "thumbnail", None),
        ),
        "serial": _coalesce(
            _mapping_get(attributes, "serial"),
            getattr(camera, "serial", None),
        ),
        "last_refresh_utc": _coalesce(
            _mapping_get(attributes, "last_refresh"),
            _mapping_get(attributes, "updated_at"),
        ),
    }


def _collect_systems(blink: Any) -> list[dict[str, Any]]:
    systems_raw: dict[str, dict[str, Any]] = {}
    for key, sync_module in _ensure_mapping(getattr(blink, "sync", {})).items():
        name = getattr(sync_module, "name", None) or str(key)
        systems_raw[name] = _sync_to_raw(sync_module)
    return transform_systems(systems_raw)


def _collect_cameras(blink: Any, system_filter: str | None = None) -> list[dict[str, Any]]:
    cameras_raw: dict[str, dict[str, Any]] = {}
    normalized_filter = _normalize_name(system_filter) if system_filter else None

    for key, camera in _ensure_mapping(getattr(blink, "cameras", {})).items():
        raw = _camera_to_raw(camera)
        if normalized_filter is not None:
            system_name = raw.get("system_name")
            if not isinstance(system_name, str) or _normalize_name(system_name) != normalized_filter:
                continue
        name = raw.get("name")
        cameras_raw[str(name) if name else str(key)] = raw

    return transform_cameras(cameras_raw)


def _resolve_item(mapping: Mapping[str, Any], name: str, label: str) -> Any:
    if name in mapping:
        return mapping[name]

    normalized_target = _normalize_name(name)
    for key, item in mapping.items():
        candidates = [str(key), getattr(item, "name", None)]
        for candidate in candidates:
            if candidate and _normalize_name(str(candidate)) == normalized_target:
                return item

    raise CliInputError(f"{label} not found: {name}")


def _find_system_payload(
    systems: list[dict[str, Any]], system_name: str
) -> dict[str, Any] | None:
    normalized_target = _normalize_name(system_name)
    for item in systems:
        name = item.get("name")
        if isinstance(name, str) and _normalize_name(name) == normalized_target:
            return item
    return None


def _find_camera_payload(
    cameras: list[dict[str, Any]], camera_name: str
) -> dict[str, Any] | None:
    normalized_target = _normalize_name(camera_name)
    for item in cameras:
        name = item.get("name")
        if isinstance(name, str) and _normalize_name(name) == normalized_target:
            return item
    return None


async def _refresh_blink(blink: Any, force: bool = False) -> None:
    await _call_method(
        blink,
        [
            ("refresh", (), {"force": force}),
            ("refresh", (), {}),
        ],
        "Blink refresh",
    )


async def _bootstrap_blink(args: argparse.Namespace, session: Any) -> Any:
    try:
        from blinkpy.auth import Auth
        from blinkpy.blinkpy import Blink
    except ModuleNotFoundError as error:
        raise RuntimeError(
            "Missing dependency `blinkpy`. Install with `python3 -m pip install -e .`."
        ) from error

    auth_data = _load_auth_data(args)
    auth = Auth(auth_data, no_prompt=True)
    blink = Blink(session=session)
    blink.auth = auth

    try:
        await _call_method(blink, [("start", (), {})], "Blink login")
    except Exception as error:
        if not _is_two_factor_error(error):
            raise

        if not args.pin:
            raise BlinkAuthError(
                "Two-factor verification required. Use --pin or BLINK_PIN."
            ) from error

        try:
            await _call_method(
                blink,
                [("setup_post_verify_pin", (args.pin,), {})],
                "2FA verification",
            )
        except RuntimeError:
            try:
                await _call_method(
                    auth,
                    [("send_auth_key", (blink, args.pin), {})],
                    "2FA verification",
                )
                await _call_method(
                    blink,
                    [("setup_post_verify", (), {})],
                    "2FA verification setup",
                )
            except RuntimeError as verify_error:
                raise BlinkAuthError(
                    "2FA verification is not supported by this blinkpy version."
                ) from verify_error

    return blink


async def _set_system_armed(sync_module: Any, armed: bool) -> None:
    attributes = _ensure_mapping(getattr(sync_module, "attributes", {}))
    sync_id = _coalesce(
        getattr(sync_module, "sync_id", None),
        _mapping_get(attributes, "sync_id"),
        _mapping_get(attributes, "id"),
    )

    if armed:
        await _call_method(
            sync_module,
            [
                ("async_arm", (True,), {}),
                ("arm", (), {"sync_id": sync_id}),
                ("arm", (True,), {}),
                ("arm", (), {}),
            ],
            "System arming",
        )
        return

    await _call_method(
        sync_module,
        [
            ("async_arm", (False,), {}),
            ("disarm", (), {"sync_id": sync_id}),
            ("disarm", (), {}),
            ("arm", (False,), {}),
        ],
        "System disarming",
    )


async def _set_camera_armed(camera: Any, armed: bool) -> None:
    if armed:
        await _call_method(
            camera,
            [
                ("async_arm", (True,), {}),
                ("arm", (), {}),
                ("arm", (True,), {}),
                ("set_motion_detection", (True,), {}),
            ],
            "Camera arming",
        )
        return

    await _call_method(
        camera,
        [
            ("async_arm", (False,), {}),
            ("disarm", (), {}),
            ("arm", (False,), {}),
            ("set_motion_detection", (False,), {}),
        ],
        "Camera disarming",
    )


async def _trigger_camera(camera: Any) -> None:
    await _call_method(
        camera,
        [
            ("snap_picture", (), {}),
            ("trigger_camera", (), {}),
            ("trigger", (), {}),
        ],
        "Camera trigger",
    )


async def _save_camera_image(camera: Any, path: str) -> None:
    await _call_method(
        camera,
        [
            ("image_to_file", (path,), {}),
            ("image_from_cache", (path,), {}),
        ],
        "Image export",
    )


async def _save_camera_video(camera: Any, path: str) -> None:
    await _call_method(
        camera,
        [("video_to_file", (path,), {})],
        "Video export",
    )


async def _download_videos(blink: Any, args: argparse.Namespace) -> None:
    await _call_method(
        blink,
        [
            (
                "download_videos",
                (args.path,),
                {
                    "camera": args.camera,
                    "since": args.since,
                    "delay": args.delay,
                    "stop": args.stop,
                },
            ),
            (
                "download_videos",
                (args.path,),
                {
                    "since": args.since,
                    "delay": args.delay,
                    "stop": args.stop,
                },
            ),
            ("download_videos", (args.path,), {"camera": args.camera}),
            ("download_videos", (args.path,), {}),
        ],
        "Video download",
    )


def print_human(command: str, payload: dict[str, Any]) -> None:
    """Print result in human-readable format."""
    location = payload.get("location")
    account = None
    region = None
    if isinstance(location, Mapping):
        account = location.get("account")
        region = location.get("region")
    if account and region:
        print(f"Location: {account} ({region})")
    elif account:
        print(f"Location: {account}")
    else:
        print("Location: -")

    if command == "systems":
        rows = [
            [
                item.get("name"),
                item.get("system_id"),
                item.get("armed"),
                item.get("status"),
                item.get("cameras_count"),
            ]
            for item in payload.get("systems", [])
        ]
        print(
            _render_table(
                ["system", "system_id", "armed", "status", "cameras"],
                rows,
            )
        )
        return

    if command == "cameras":
        rows = [
            [
                item.get("name"),
                item.get("system_name"),
                item.get("armed"),
                item.get("motion_detected"),
                item.get("battery_pct"),
                item.get("status"),
            ]
            for item in payload.get("cameras", [])
        ]
        print(
            _render_table(
                ["camera", "system", "armed", "motion", "battery_%", "status"],
                rows,
            )
        )
        return

    if command == "refresh":
        summary = payload.get("summary", {})
        if isinstance(summary, Mapping):
            _print_line("Systems", summary.get("systems_count"))
            _print_line("Cameras", summary.get("cameras_count"))
            _print_line("Armed systems", summary.get("armed_systems_count"))
            _print_line("Armed cameras", summary.get("armed_cameras_count"))

        systems = payload.get("systems", [])
        if systems:
            print("\nSystems:")
            rows = [
                [item.get("name"), item.get("armed"), item.get("status"), item.get("cameras_count")]
                for item in systems
            ]
            print(_render_table(["system", "armed", "status", "cameras"], rows))

        cameras = payload.get("cameras", [])
        if cameras:
            print("\nCameras:")
            rows = [
                [item.get("name"), item.get("system_name"), item.get("armed"), item.get("status")]
                for item in cameras
            ]
            print(_render_table(["camera", "system", "armed", "status"], rows))
        return

    if command == "system-arm":
        system = payload.get("system", {})
        if isinstance(system, Mapping):
            _print_line("System", system.get("name"))
            _print_line("State", payload.get("state"))
            _print_line("Armed", system.get("armed"))
            _print_line("Status", system.get("status"))
        return

    if command == "camera-arm":
        camera = payload.get("camera", {})
        if isinstance(camera, Mapping):
            _print_line("Camera", camera.get("name"))
            _print_line("State", payload.get("state"))
            _print_line("Armed", camera.get("armed"))
            _print_line("Status", camera.get("status"))
        return

    if command == "trigger-camera":
        camera = payload.get("camera", {})
        if isinstance(camera, Mapping):
            _print_line("Camera", camera.get("name"))
            _print_line("Trigger", "queued")
            _print_line("Status", camera.get("status"))
        return

    if command == "save-image":
        _print_line("Camera", payload.get("camera"))
        _print_line("File", payload.get("file_path"))
        _print_line("Triggered first", payload.get("triggered"))
        return

    if command == "save-video":
        _print_line("Camera", payload.get("camera"))
        _print_line("File", payload.get("file_path"))
        return

    if command == "download-videos":
        _print_line("Directory", payload.get("path"))
        _print_line("Camera filter", payload.get("camera"))
        _print_line("Since", payload.get("since"))
        _print_line("Delay (s)", payload.get("delay"))
        _print_line("Stop (s)", payload.get("stop"))
        return

    if command == "save-auth":
        _print_line("Auth file", payload.get("path"))


async def run_command(args: argparse.Namespace) -> dict[str, Any]:
    """Execute the selected command."""
    try:
        from aiohttp import ClientSession, ClientTimeout
    except ModuleNotFoundError as error:
        raise RuntimeError(
            "Missing dependency `aiohttp`. Install with `python3 -m pip install -e .`."
        ) from error

    timeout = ClientTimeout(total=API_TIMEOUT_SECONDS)

    try:
        async with ClientSession(timeout=timeout) as session:
            blink = await _bootstrap_blink(args, session)
            location = _build_location(args, blink)
            sync_modules = _ensure_mapping(getattr(blink, "sync", {}))
            cameras = _ensure_mapping(getattr(blink, "cameras", {}))

            if args.command == "systems":
                await _refresh_blink(blink, force=False)
                systems_payload = _collect_systems(blink)
                cameras_payload = _collect_cameras(blink)
                return {
                    "command": args.command,
                    "location": location,
                    "systems": systems_payload,
                    "summary": build_summary(systems_payload, cameras_payload),
                }

            if args.command == "cameras":
                await _refresh_blink(blink, force=False)
                if args.system:
                    _resolve_item(sync_modules, args.system, "System")
                cameras_payload = _collect_cameras(blink, args.system)
                return {
                    "command": args.command,
                    "location": location,
                    "system_filter": args.system,
                    "cameras": cameras_payload,
                    "summary": build_summary([], cameras_payload),
                }

            if args.command == "refresh":
                await _refresh_blink(blink, force=args.force)
                systems_payload = _collect_systems(blink)
                cameras_payload = _collect_cameras(blink)
                return {
                    "command": args.command,
                    "location": location,
                    "systems": systems_payload,
                    "cameras": cameras_payload,
                    "summary": build_summary(systems_payload, cameras_payload),
                }

            if args.command == "system-arm":
                sync_module = _resolve_item(sync_modules, args.system, "System")
                armed = args.state == "armed"
                await _set_system_armed(sync_module, armed)
                await _refresh_blink(blink, force=True)
                systems_payload = _collect_systems(blink)
                system_payload = _find_system_payload(systems_payload, args.system)
                if system_payload is None:
                    system_payload = transform_systems(
                        {args.system: _sync_to_raw(sync_module)}
                    )[0]
                return {
                    "command": args.command,
                    "location": location,
                    "state": args.state,
                    "system": system_payload,
                }

            if args.command == "camera-arm":
                camera = _resolve_item(cameras, args.camera, "Camera")
                armed = args.state == "armed"
                await _set_camera_armed(camera, armed)
                await _refresh_blink(blink, force=True)
                cameras_payload = _collect_cameras(blink)
                camera_payload = _find_camera_payload(cameras_payload, args.camera)
                if camera_payload is None:
                    camera_payload = transform_cameras(
                        {args.camera: _camera_to_raw(camera)}
                    )[0]
                return {
                    "command": args.command,
                    "location": location,
                    "state": args.state,
                    "camera": camera_payload,
                }

            if args.command == "trigger-camera":
                camera = _resolve_item(cameras, args.camera, "Camera")
                await _trigger_camera(camera)
                await _refresh_blink(blink, force=True)
                cameras_payload = _collect_cameras(blink)
                camera_payload = _find_camera_payload(cameras_payload, args.camera)
                if camera_payload is None:
                    camera_payload = transform_cameras(
                        {args.camera: _camera_to_raw(camera)}
                    )[0]
                return {
                    "command": args.command,
                    "location": location,
                    "camera": camera_payload,
                }

            if args.command == "save-image":
                camera = _resolve_item(cameras, args.camera, "Camera")
                if args.trigger:
                    await _trigger_camera(camera)
                if args.refresh:
                    await _refresh_blink(blink, force=True)

                file_path = Path(args.path).expanduser()
                file_path.parent.mkdir(parents=True, exist_ok=True)
                await _save_camera_image(camera, str(file_path))
                return {
                    "command": args.command,
                    "location": location,
                    "camera": args.camera,
                    "file_path": str(file_path),
                    "triggered": args.trigger,
                }

            if args.command == "save-video":
                camera = _resolve_item(cameras, args.camera, "Camera")
                if args.refresh:
                    await _refresh_blink(blink, force=True)

                file_path = Path(args.path).expanduser()
                file_path.parent.mkdir(parents=True, exist_ok=True)
                await _save_camera_video(camera, str(file_path))
                return {
                    "command": args.command,
                    "location": location,
                    "camera": args.camera,
                    "file_path": str(file_path),
                }

            if args.command == "download-videos":
                output_dir = Path(args.path).expanduser()
                output_dir.mkdir(parents=True, exist_ok=True)
                args.path = str(output_dir)
                await _download_videos(blink, args)
                return {
                    "command": args.command,
                    "location": location,
                    "path": str(output_dir),
                    "camera": args.camera,
                    "since": args.since,
                    "delay": args.delay,
                    "stop": args.stop,
                }

            if args.command == "save-auth":
                auth_path = Path(args.path).expanduser()
                auth_path.parent.mkdir(parents=True, exist_ok=True)
                await _call_method(
                    blink,
                    [("save", (str(auth_path),), {})],
                    "Auth session save",
                )
                # The auth JSON contains session tokens / credential material
                # and must not be world- or group-readable. blinkpy writes the
                # file with the process umask, so lock it down explicitly.
                if auth_path.exists():
                    _restrict_file_permissions(auth_path)
                return {
                    "command": args.command,
                    "location": location,
                    "path": str(auth_path),
                }

            raise RuntimeError(f"Command not implemented: {args.command}")
    except BlinkAuthError:
        raise
    except BlinkRateLimitError:
        raise
    except Exception as error:
        if _is_auth_error(error):
            raise BlinkAuthError("Invalid credentials or verification failed.") from error
        if _is_rate_limit_error(error):
            raise BlinkRateLimitError("Request limit exceeded.") from error
        raise


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point."""
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        validate_args(args)
        payload = asyncio.run(run_command(args))
    except CliInputError as error:
        print(f"Input error: {error}", file=sys.stderr)
        return 2
    except BlinkAuthError as error:
        print(f"Error: {error}", file=sys.stderr)
        return 2
    except BlinkRateLimitError as error:
        print(f"Error: {error}", file=sys.stderr)
        return 2
    except Exception as error:
        print(f"Error while calling Blink API: {error}", file=sys.stderr)
        return 1

    if args.json_output:
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print_human(args.command, payload)

    return 0
