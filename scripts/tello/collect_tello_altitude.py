"""Collect timestamped Tello video frames and telemetry at a target altitude.

The program is a dry run unless --execute is supplied. It refuses targets above
the standard Tello's published 30 m maximum flight height.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


TELLO_MAX_ALTITUDE_M = 30.0
TELLO_VPS_EFFECTIVE_MAX_M = 10.0
SDK_MAX_MOVE_CM = 500
SDK_MIN_MOVE_CM = 20
MAX_CAPTURE_DURATION_S = 300.0


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def default_session_id() -> str:
    return datetime.now(timezone.utc).strftime("tello_%Y%m%dT%H%M%SZ")


def build_parser(default_target_altitude_m: float | None = None) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Ascend a standard Tello to a supported target altitude, hover, "
            "and save streamed frames with synchronized telemetry."
        )
    )
    parser.add_argument(
        "--target-altitude-m",
        type=float,
        default=default_target_altitude_m,
        required=default_target_altitude_m is None,
        help="Target height above takeoff surface in metres (0.5 to 30).",
    )
    parser.add_argument("--capture-count", type=int, default=20)
    parser.add_argument("--capture-interval-seconds", type=float, default=1.0)
    parser.add_argument("--settle-seconds", type=float, default=5.0)
    parser.add_argument(
        "--ascent-step-m",
        type=float,
        default=2.0,
        help="Maximum vertical movement per SDK command (0.2 to 5.0 m).",
    )
    parser.add_argument("--speed-cm-s", type=int, default=50)
    parser.add_argument("--minimum-takeoff-battery", type=int, default=80)
    parser.add_argument("--landing-battery", type=int, default=30)
    parser.add_argument("--session-id", default=None)
    parser.add_argument("--site-id", default="unassigned")
    parser.add_argument(
        "--output-root",
        type=Path,
        default=None,
        help="Defaults to data/raw/tello inside this repository.",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Actually connect and fly. Without this flag, only print the plan.",
    )
    parser.add_argument(
        "--acknowledge-vps-limit",
        action="store_true",
        help="Required with --execute when the target is above 10 m.",
    )
    return parser


def validate_args(args: argparse.Namespace) -> None:
    if not math.isfinite(args.target_altitude_m):
        raise ValueError("Target altitude must be a finite number.")
    if not 0.5 <= args.target_altitude_m <= TELLO_MAX_ALTITUDE_M:
        raise ValueError(
            f"A standard Tello target must be between 0.5 and "
            f"{TELLO_MAX_ALTITUDE_M:.0f} m; requested {args.target_altitude_m:g} m."
        )
    if not 1 <= args.capture_count <= 10_000:
        raise ValueError("Capture count must be between 1 and 10000.")
    if args.capture_interval_seconds < 0.1:
        raise ValueError("Capture interval must be at least 0.1 seconds.")
    planned_capture_seconds = (args.capture_count - 1) * args.capture_interval_seconds
    if planned_capture_seconds > MAX_CAPTURE_DURATION_S:
        raise ValueError(
            f"Planned capture duration cannot exceed {MAX_CAPTURE_DURATION_S:.0f} seconds."
        )
    if not 0 <= args.settle_seconds <= 30:
        raise ValueError("Settle time must be between 0 and 30 seconds.")
    if not 0.2 <= args.ascent_step_m <= 5.0:
        raise ValueError("Ascent step must be between 0.2 and 5.0 m.")
    if not 10 <= args.speed_cm_s <= 100:
        raise ValueError("Tello SDK speed must be between 10 and 100 cm/s.")
    if not 1 <= args.landing_battery < args.minimum_takeoff_battery <= 100:
        raise ValueError(
            "Battery thresholds must satisfy 1 <= landing < takeoff <= 100."
        )
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,79}", args.session_id):
        raise ValueError(
            "Session ID must be 1-80 characters and contain only letters, "
            "numbers, dots, underscores, or hyphens."
        )
    if ".." in args.session_id:
        raise ValueError("Session ID cannot contain consecutive dots.")
    if (
        args.execute
        and args.target_altitude_m > TELLO_VPS_EFFECTIVE_MAX_M
        and not args.acknowledge_vps_limit
    ):
        raise ValueError(
            "Execution above 10 m requires --acknowledge-vps-limit because the "
            "Tello may lose vision-based position holding."
        )


def print_plan(args: argparse.Namespace, output_root: Path) -> None:
    estimated_ascent_commands = math.ceil(
        max(0.0, args.target_altitude_m - 0.8) / args.ascent_step_m
    )
    print("Tello collection plan")
    print(f"  Target altitude:       {args.target_altitude_m:.1f} m")
    print(f"  Estimated ascent steps:{estimated_ascent_commands:>5}")
    print(f"  Capture count:         {args.capture_count}")
    print(f"  Capture interval:      {args.capture_interval_seconds:.1f} s")
    print(f"  Minimum battery:       {args.minimum_takeoff_battery}%")
    print(f"  Output root:           {output_root}")
    if args.target_altitude_m > TELLO_VPS_EFFECTIVE_MAX_M:
        print(
            "  Notice: target exceeds the Tello vision-positioning system's "
            "published 10 m effective range."
        )
    if not args.execute:
        print("\nDry run only. Add --execute to connect and fly.")


def safe_read(getter: Callable[[], Any]) -> Any:
    try:
        return getter()
    except Exception:
        return None


def wait_for_video_frame(frame_reader: Any, timeout_seconds: float = 15.0) -> Any:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        frame = frame_reader.frame
        if frame is not None and getattr(frame, "size", 0) > 0:
            return frame.copy()
        time.sleep(0.1)
    raise RuntimeError("No video frame was received from the Tello within 15 seconds.")


def read_telemetry(tello: Any, baseline_barometer_cm: float | None) -> dict[str, Any]:
    barometer_cm = safe_read(tello.get_barometer)
    relative_barometer_m = None
    if barometer_cm is not None and baseline_barometer_cm is not None:
        relative_barometer_m = (barometer_cm - baseline_barometer_cm) / 100.0

    height_cm = safe_read(tello.get_height)
    return {
        "reported_height_m": None if height_cm is None else height_cm / 100.0,
        "relative_barometer_m": relative_barometer_m,
        "tof_cm": safe_read(tello.get_distance_tof),
        "battery_percent": safe_read(tello.get_battery),
        "flight_time_s": safe_read(tello.get_flight_time),
        "pitch_deg": safe_read(tello.get_pitch),
        "roll_deg": safe_read(tello.get_roll),
        "yaw_deg": safe_read(tello.get_yaw),
        "temperature_c": safe_read(tello.get_temperature),
    }


def ascend_to_target(
    tello: Any,
    target_altitude_m: float,
    ascent_step_m: float,
    speed_cm_s: int,
    landing_battery: int,
) -> None:
    target_cm = round(target_altitude_m * 100)
    maximum_step_cm = min(SDK_MAX_MOVE_CM, round(ascent_step_m * 100))
    tolerance_cm = 25

    for step_number in range(1, 100):
        current_cm = tello.get_height()
        remaining_cm = target_cm - current_cm
        battery = tello.get_battery()
        print(
            f"Ascent check {step_number}: height={current_cm / 100:.2f} m, "
            f"remaining={remaining_cm / 100:.2f} m, battery={battery}%"
        )

        if battery <= landing_battery:
            raise RuntimeError("Battery reached the landing threshold during ascent.")
        if remaining_cm <= tolerance_cm:
            return
        if remaining_cm < SDK_MIN_MOVE_CM:
            return

        move_cm = min(maximum_step_cm, remaining_cm)
        tello.move_up(move_cm)
        time.sleep(max(1.0, move_cm / speed_cm_s + 0.5))

    raise RuntimeError("Target altitude was not reached within the ascent-step limit.")


def relative_or_absolute_path(path: Path, repo_root: Path) -> str:
    resolved = path.resolve()
    try:
        return resolved.relative_to(repo_root.resolve()).as_posix()
    except ValueError:
        return str(resolved)


def capture_frames(
    tello: Any,
    frame_reader: Any,
    images_dir: Path,
    manifest_path: Path,
    repo_root: Path,
    args: argparse.Namespace,
    baseline_barometer_cm: float | None,
) -> None:
    import cv2

    fieldnames = [
        "sample_id",
        "object_id",
        "class_name",
        "altitude_m",
        "image_path",
        "center_x",
        "center_y",
        "session_id",
        "site_id",
        "split",
        "platform",
        "target_altitude_m",
        "reported_height_m",
        "relative_barometer_m",
        "tof_cm",
        "battery_percent",
        "flight_time_s",
        "pitch_deg",
        "roll_deg",
        "yaw_deg",
        "temperature_c",
        "captured_at_utc",
        "frame_width_px",
        "frame_height_px",
    ]

    with manifest_path.open("w", newline="", encoding="utf-8") as manifest_file:
        writer = csv.DictWriter(manifest_file, fieldnames=fieldnames)
        writer.writeheader()

        for index in range(args.capture_count):
            telemetry = read_telemetry(tello, baseline_barometer_cm)
            battery = telemetry["battery_percent"]
            if battery is not None and battery <= args.landing_battery:
                print("Battery reached the landing threshold; stopping capture.")
                break

            frame = wait_for_video_frame(frame_reader)
            captured_at = utc_timestamp()
            sample_id = f"{args.session_id}_{index:04d}"
            image_path = images_dir / f"{sample_id}.jpg"
            if not cv2.imwrite(
                str(image_path), frame, [int(cv2.IMWRITE_JPEG_QUALITY), 95]
            ):
                raise RuntimeError(f"Failed to write {image_path}")

            height_m = telemetry["reported_height_m"]
            row = {
                "sample_id": sample_id,
                "object_id": "",
                "class_name": "",
                "altitude_m": height_m,
                "image_path": relative_or_absolute_path(image_path, repo_root),
                "center_x": "",
                "center_y": "",
                "session_id": args.session_id,
                "site_id": args.site_id,
                "split": "unassigned",
                "platform": "ryze_tello",
                "target_altitude_m": args.target_altitude_m,
                **telemetry,
                "captured_at_utc": captured_at,
                "frame_width_px": int(frame.shape[1]),
                "frame_height_px": int(frame.shape[0]),
            }
            writer.writerow(row)
            manifest_file.flush()
            print(
                f"Captured {index + 1}/{args.capture_count}: {image_path.name} "
                f"at reported height {height_m!s} m"
            )
            if index + 1 < args.capture_count:
                time.sleep(args.capture_interval_seconds)


def execute_collection(args: argparse.Namespace, repo_root: Path, output_root: Path) -> None:
    try:
        from djitellopy import Tello
    except ImportError as exc:
        raise RuntimeError(
            "DJITelloPy is not installed. Run: "
            "python -m pip install -r requirements-tello.txt"
        ) from exc

    expected_confirmation = f"FLY {args.target_altitude_m:.1f}"
    confirmation = input(
        f"Type '{expected_confirmation}' to authorize takeoff and ascent: "
    ).strip()
    if confirmation != expected_confirmation:
        print("Confirmation did not match; no flight commands were sent.")
        return

    session_dir = output_root / args.session_id
    images_dir = session_dir / "images"
    images_dir.mkdir(parents=True, exist_ok=False)
    manifest_path = session_dir / "capture_manifest.csv"
    session_metadata_path = session_dir / "session.json"

    tello = Tello()
    connected = False
    streaming = False
    airborne = False

    try:
        print("Connecting to Tello...")
        tello.connect()
        connected = True
        battery = tello.get_battery()
        if battery < args.minimum_takeoff_battery:
            raise RuntimeError(
                f"Battery is {battery}%; at least "
                f"{args.minimum_takeoff_battery}% is required for takeoff."
            )

        baseline_barometer_cm = safe_read(tello.get_barometer)
        tello.set_speed(args.speed_cm_s)
        tello.streamon()
        streaming = True
        frame_reader = tello.get_frame_read()
        first_frame = wait_for_video_frame(frame_reader)

        session_metadata = {
            "session_id": args.session_id,
            "site_id": args.site_id,
            "platform": "ryze_tello",
            "target_altitude_m": args.target_altitude_m,
            "capture_count_requested": args.capture_count,
            "capture_interval_seconds": args.capture_interval_seconds,
            "settle_seconds": args.settle_seconds,
            "ascent_step_m": args.ascent_step_m,
            "speed_cm_s": args.speed_cm_s,
            "battery_before_takeoff_percent": battery,
            "baseline_barometer_cm": baseline_barometer_cm,
            "stream_frame_width_px": int(first_frame.shape[1]),
            "stream_frame_height_px": int(first_frame.shape[0]),
            "started_at_utc": utc_timestamp(),
        }
        session_metadata_path.write_text(
            json.dumps(session_metadata, indent=2), encoding="utf-8"
        )

        print("Taking off...")
        tello.takeoff()
        airborne = True
        ascend_to_target(
            tello,
            args.target_altitude_m,
            args.ascent_step_m,
            args.speed_cm_s,
            args.landing_battery,
        )
        print(f"Settling for {args.settle_seconds:.1f} seconds...")
        time.sleep(args.settle_seconds)
        capture_frames(
            tello,
            frame_reader,
            images_dir,
            manifest_path,
            repo_root,
            args,
            baseline_barometer_cm,
        )
    except KeyboardInterrupt:
        print("\nInterrupted by operator; landing.")
    finally:
        if airborne:
            try:
                print("Landing...")
                tello.land()
                airborne = False
            except Exception as exc:
                print(f"Landing command failed: {exc}", file=sys.stderr)
        if streaming:
            try:
                tello.streamoff()
            except Exception as exc:
                print(f"Could not stop video stream cleanly: {exc}", file=sys.stderr)
        if connected:
            tello.end()


def main(default_target_altitude_m: float | None = None) -> int:
    parser = build_parser(default_target_altitude_m)
    args = parser.parse_args()
    if args.session_id is None:
        args.session_id = default_session_id()

    try:
        validate_args(args)
    except ValueError as exc:
        parser.error(str(exc))

    repo_root = Path(__file__).resolve().parents[2]
    output_root = (
        args.output_root.expanduser().resolve()
        if args.output_root is not None
        else repo_root / "data" / "raw" / "tello"
    )
    print_plan(args, output_root)
    if not args.execute:
        return 0

    execute_collection(args, repo_root, output_root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
