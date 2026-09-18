#!/usr/bin/env python
"""Build paired geometry-only and calibrated synthetic 80 m SeaDronesSee patches.

The source manifest contains labelled objects from 25--35 m images. For each
object, this script takes the wider source footprint that an 80 m, 640x640 view
would cover and downsamples it with Lanczos. Objects without enough real context
around them are excluded instead of padding or inventing background.

Usage (from the repository root):
    .venv-research/Scripts/python.exe scripts/build_seadronessee_synthetic80.py
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from io import BytesIO
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from PIL import Image, ImageFilter
from tqdm.auto import tqdm


TARGET_ALTITUDE_M = 80.0
OUTPUT_SIDE_PX = 640
SPLIT_ORDER = {"train": 0, "val": 1, "test": 2}


def srgb_to_linear(values: np.ndarray) -> np.ndarray:
    values = np.asarray(values, dtype=np.float32)
    return np.where(
        values <= 0.04045,
        values / 12.92,
        ((values + 0.055) / 1.055) ** 2.4,
    )


def linear_to_srgb(values: np.ndarray) -> np.ndarray:
    values = np.clip(values, 0.0, 1.0)
    return np.where(
        values <= 0.0031308,
        values * 12.92,
        1.055 * values ** (1.0 / 2.4) - 0.055,
    )


def apply_incremental_haze(
    image: Image.Image,
    extra_distance_m: float,
    visibility_m: float,
    airlight_srgb: tuple[float, float, float],
) -> tuple[Image.Image, float]:
    beta = 3.912 / float(visibility_m)
    transmission = math.exp(-beta * float(extra_distance_m))
    srgb = np.asarray(image, dtype=np.float32) / 255.0
    linear = srgb_to_linear(srgb)
    airlight_linear = srgb_to_linear(np.asarray(airlight_srgb, dtype=np.float32))
    hazy_linear = (
        linear * transmission
        + airlight_linear[None, None, :] * (1.0 - transmission)
    )
    output = np.round(linear_to_srgb(hazy_linear) * 255.0).astype(np.uint8)
    return Image.fromarray(output, mode="RGB"), transmission


def apply_sensor_noise(
    image: Image.Image,
    peak_electrons: float,
    read_noise_electrons: float,
    rng: np.random.Generator,
) -> Image.Image:
    srgb = np.asarray(image, dtype=np.float32) / 255.0
    linear = srgb_to_linear(srgb)
    expected = np.clip(linear * peak_electrons, 0.0, None)
    shot = rng.poisson(expected).astype(np.float32)
    read = rng.normal(0.0, read_noise_electrons, size=shot.shape).astype(np.float32)
    noisy = np.clip((shot + read) / peak_electrons, 0.0, 1.0)
    output = np.round(linear_to_srgb(noisy) * 255.0).astype(np.uint8)
    return Image.fromarray(output, mode="RGB")


def jpeg_round_trip(image: Image.Image, quality: int) -> Image.Image:
    buffer = BytesIO()
    image.save(buffer, format="JPEG", quality=quality, subsampling=2)
    buffer.seek(0)
    with Image.open(buffer) as encoded:
        return encoded.convert("RGB")


def stable_seed(text: str) -> int:
    digest = hashlib.sha256(text.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], byteorder="little", signed=False)


def atomic_save_png(image: Image.Image, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    image.save(temporary, format="PNG", compress_level=3)
    os.replace(temporary, destination)


def source_footprint(row: pd.Series) -> dict[str, int | float]:
    altitude = float(row["altitude_m"])
    source_side = int(round(OUTPUT_SIDE_PX * TARGET_ALTITUDE_M / altitude))
    center_x = float(row["bbox_x"]) + float(row["bbox_width"]) / 2.0
    center_y = float(row["bbox_y"]) + float(row["bbox_height"]) / 2.0
    left = int(round(center_x - source_side / 2.0))
    top = int(round(center_y - source_side / 2.0))
    return {
        "source_side_px": source_side,
        "crop_left_px": left,
        "crop_top_px": top,
        "crop_right_px": left + source_side,
        "crop_bottom_px": top + source_side,
    }


def apply_calibrated_effects(
    source_crop: Image.Image,
    source_altitude_m: float,
    effects: dict[str, Any],
    seed: int,
) -> tuple[Image.Image, float]:
    sigma_output = float(effects.get("extra_psf_sigma_output_px") or 0.0)
    if sigma_output > 0:
        altitude_scale = source_altitude_m / TARGET_ALTITUDE_M
        source_crop = source_crop.filter(
            ImageFilter.GaussianBlur(radius=sigma_output / altitude_scale)
        )

    output = source_crop.resize(
        (OUTPUT_SIDE_PX, OUTPUT_SIDE_PX),
        resample=Image.Resampling.LANCZOS,
    )

    transmission = 1.0
    visibility_m = effects.get("visibility_m")
    if visibility_m is not None:
        output, transmission = apply_incremental_haze(
            output,
            extra_distance_m=TARGET_ALTITUDE_M - source_altitude_m,
            visibility_m=float(visibility_m),
            airlight_srgb=tuple(effects.get("airlight_srgb", (0.78, 0.84, 0.90))),
        )

    peak_electrons = effects.get("peak_electrons")
    if peak_electrons is not None:
        output = apply_sensor_noise(
            output,
            peak_electrons=float(peak_electrons),
            read_noise_electrons=float(effects.get("read_noise_electrons", 2.0)),
            rng=np.random.default_rng(seed),
        )

    jpeg_quality = effects.get("jpeg_quality")
    if jpeg_quality is not None:
        output = jpeg_round_trip(output, int(jpeg_quality))
    return output, transmission


def classify_rows(frame: pd.DataFrame, repo_root: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    eligible: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []

    for record in frame.to_dict("records"):
        row = pd.Series(record)
        reason: str | None = None
        try:
            altitude = float(row["altitude_m"])
            if not math.isfinite(altitude) or altitude <= 0 or altitude >= TARGET_ALTITUDE_M:
                reason = "invalid_source_altitude"
            elif (
                float(row["bbox_width"]) <= 0
                or float(row["bbox_height"]) <= 0
            ):
                reason = "invalid_bbox"
        except (TypeError, ValueError):
            reason = "invalid_numeric_metadata"

        source_path = repo_root / Path(str(row["image_path"]))
        if reason is None and not source_path.is_file():
            reason = "source_image_missing"

        footprint: dict[str, Any] = {}
        if reason is None:
            footprint = source_footprint(row)
            if (
                int(footprint["crop_left_px"]) < 0
                or int(footprint["crop_top_px"]) < 0
                or int(footprint["crop_right_px"]) > int(row["image_width"])
                or int(footprint["crop_bottom_px"]) > int(row["image_height"])
            ):
                reason = "required_80m_footprint_outside_source_frame"

        combined = {**record, **footprint}
        if reason is None:
            eligible.append(combined)
        else:
            combined["exclusion_reason"] = reason
            excluded.append(combined)

    return pd.DataFrame(eligible), pd.DataFrame(excluded)


def safe_pair_id(row: dict[str, Any]) -> str:
    return f"{row['source_split']}_{int(row['annotation_id']):08d}"


def process_image_group(
    records: list[dict[str, Any]],
    repo_root: Path,
    output_root: Path,
    effects: dict[str, Any],
    config_hash: str,
    overwrite: bool,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    built: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    source_path = repo_root / Path(str(records[0]["image_path"]))

    try:
        with Image.open(source_path) as opened:
            source = opened.convert("RGB")

        for row in records:
            try:
                pair_id = safe_pair_id(row)
                split = str(row["split"])
                geometry_path = output_root / "geometry_only" / split / f"{pair_id}.png"
                calibrated_path = output_root / "calibrated" / split / f"{pair_id}.png"

                left = int(row["crop_left_px"])
                top = int(row["crop_top_px"])
                right = int(row["crop_right_px"])
                bottom = int(row["crop_bottom_px"])
                source_crop = source.crop((left, top, right, bottom))

                geometry = source_crop.resize(
                    (OUTPUT_SIDE_PX, OUTPUT_SIDE_PX),
                    resample=Image.Resampling.LANCZOS,
                )
                calibrated, transmission = apply_calibrated_effects(
                    source_crop.copy(),
                    source_altitude_m=float(row["altitude_m"]),
                    effects=effects,
                    seed=stable_seed(str(row["object_key"])),
                )

                if overwrite or not geometry_path.is_file():
                    atomic_save_png(geometry, geometry_path)
                if overwrite or not calibrated_path.is_file():
                    atomic_save_png(calibrated, calibrated_path)

                scale = OUTPUT_SIDE_PX / int(row["source_side_px"])
                bbox_x = (float(row["bbox_x"]) - left) * scale
                bbox_y = (float(row["bbox_y"]) - top) * scale
                bbox_w = float(row["bbox_width"]) * scale
                bbox_h = float(row["bbox_height"]) * scale

                result = dict(row)
                result.update(
                    {
                        "pair_id": pair_id,
                        "target_altitude_m": TARGET_ALTITUDE_M,
                        "output_side_px": OUTPUT_SIDE_PX,
                        "actual_linear_scale": scale,
                        "synthetic_bbox_x": bbox_x,
                        "synthetic_bbox_y": bbox_y,
                        "synthetic_bbox_width": bbox_w,
                        "synthetic_bbox_height": bbox_h,
                        "synthetic_bbox_area": bbox_w * bbox_h,
                        "calibrated_transmission": transmission,
                        "calibration_config_sha256": config_hash,
                        "geometry_image_path": geometry_path.relative_to(repo_root).as_posix(),
                        "calibrated_image_path": calibrated_path.relative_to(repo_root).as_posix(),
                    }
                )
                built.append(result)
            except Exception as exc:  # continue while preserving an auditable failure record
                failed.append({**row, "exclusion_reason": f"processing_error: {exc}"})
    except Exception as exc:
        for row in records:
            failed.append({**row, "exclusion_reason": f"source_open_error: {exc}"})
    return built, failed


def write_dataset_readme(
    output_root: Path,
    source_manifest: Path,
    paired: pd.DataFrame,
    excluded: pd.DataFrame,
    effects: dict[str, Any],
    config_hash: str,
) -> None:
    counts = paired.groupby(["split", "category_name"]).size().unstack(fill_value=0)
    lines = [
        "# SeaDronesSee paired synthetic 80 m object patches",
        "",
        "This local dataset contains two paired 640x640 synthetic views for each eligible labelled object from the 25--35 m SeaDronesSee split:",
        "",
        "- `geometry_only/`: wider real source footprint reduced to 640x640 with Lanczos resampling.",
        "- `calibrated/`: the same geometry followed by the locked low-level calibration effects.",
        "",
        "Both variants use the same `pair_id`, source annotation and video-level split. No new background is generated. Objects whose required 80 m footprint crosses the source frame boundary are recorded in `manifests/excluded_manifest.csv`.",
        "",
        "## Counts",
        "",
        f"- Built paired objects: {len(paired):,}",
        f"- Excluded source objects: {len(excluded):,}",
        "",
        "```text",
        counts.to_string(),
        "```",
        "",
        "## Locked calibrated effects",
        "",
        "```json",
        json.dumps(effects, indent=2),
        "```",
        "",
        f"Calibration configuration SHA-256: `{config_hash}`",
        "",
        f"Source manifest: `{source_manifest.as_posix()}`",
        "",
        "Use only the training split to fit a Procrustes mapping. Keep validation for method selection and test for final evaluation. The geometry-only variant is the zero-shot control; the calibrated variant is the primary synthetic mapping source.",
        "",
        "## Interpretation limits",
        "",
        "The geometric transformation assumes unchanged camera intrinsics, an approximately planar sea surface and comparable viewing direction. It uses each source image's recorded altitude, so the linear scale is `source_altitude / 80`, rather than treating every source as exactly 30 m.",
        "",
        "The eligible set is not a random sample: objects near frame boundaries are more likely to be excluded because their wider 80 m footprint is unavailable. Report the eligibility counts with results and do not present this subset as complete coverage of the source dataset.",
        "",
        "The calibrated variant is calibration-assisted and SeaDronesSee-specific. The accepted calibration currently adds only a quality-95 JPEG round trip; held-out validation did not justify additional blur, haze or sensor noise. It is therefore a controlled approximation, not a claim of photorealistic 80 m image formation.",
    ]
    (output_root / "README.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
    )
    parser.add_argument(
        "--source-manifest",
        type=Path,
        default=Path("data/splits/seadronessee/seadronessee_25_35m_object_split.csv"),
    )
    parser.add_argument(
        "--calibration-config",
        type=Path,
        default=Path("results/seadronessee_30m_to_80m_pilot/calibrated_effects.json"),
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=Path("data/synthetic/seadronessee_30m_to_80m"),
    )
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def resolve(repo_root: Path, path: Path) -> Path:
    return path if path.is_absolute() else repo_root / path


def main() -> None:
    args = parse_args()
    repo_root = args.repo_root.resolve()
    source_manifest = resolve(repo_root, args.source_manifest)
    calibration_config = resolve(repo_root, args.calibration_config)
    output_root = resolve(repo_root, args.output_root)
    manifests_root = output_root / "manifests"
    manifests_root.mkdir(parents=True, exist_ok=True)

    source = pd.read_csv(source_manifest)
    required = {
        "object_key", "source_split", "annotation_id", "image_path", "image_width",
        "image_height", "bbox_x", "bbox_y", "bbox_width", "bbox_height",
        "altitude_m", "split", "category_name",
    }
    missing = sorted(required - set(source.columns))
    if missing:
        raise ValueError(f"Source manifest is missing columns: {missing}")

    calibration_document = json.loads(calibration_config.read_text(encoding="utf-8"))
    if not calibration_document.get("accepted", False):
        raise ValueError("Calibration configuration is not marked accepted.")
    effects = calibration_document["effects_for_dataset"]
    canonical_config = json.dumps(effects, sort_keys=True, separators=(",", ":"))
    config_hash = hashlib.sha256(canonical_config.encode("utf-8")).hexdigest()

    eligible, excluded = classify_rows(source, repo_root)
    excluded.to_csv(manifests_root / "excluded_manifest.csv", index=False)

    print(f"Source objects:  {len(source):,}")
    print(f"Eligible pairs:  {len(eligible):,}")
    print(f"Excluded:        {len(excluded):,}")
    if args.dry_run:
        print("Dry run complete; no images were generated.")
        return

    grouped = [group.to_dict("records") for _, group in eligible.groupby("image_path", sort=True)]
    built_rows: list[dict[str, Any]] = []
    runtime_failures: list[dict[str, Any]] = []

    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as executor:
        futures = [
            executor.submit(
                process_image_group,
                records,
                repo_root,
                output_root,
                effects,
                config_hash,
                args.overwrite,
            )
            for records in grouped
        ]
        for future in tqdm(as_completed(futures), total=len(futures), desc="Source images"):
            built, failed = future.result()
            built_rows.extend(built)
            runtime_failures.extend(failed)

    paired = pd.DataFrame(built_rows)
    paired["_split_order"] = paired["split"].map(SPLIT_ORDER).fillna(99)
    paired = paired.sort_values(["_split_order", "pair_id"]).drop(columns="_split_order")

    if runtime_failures:
        runtime_frame = pd.DataFrame(runtime_failures)
        excluded = pd.concat([excluded, runtime_frame], ignore_index=True, sort=False)
        excluded.to_csv(manifests_root / "excluded_manifest.csv", index=False)

    paired.to_csv(manifests_root / "paired_manifest.csv", index=False)

    expected = len(paired)
    geometry_files = list((output_root / "geometry_only").glob("*/*.png"))
    calibrated_files = list((output_root / "calibrated").glob("*/*.png"))
    if len(geometry_files) != expected or len(calibrated_files) != expected:
        raise RuntimeError(
            "Verification failed: "
            f"expected {expected} files per variant, found "
            f"{len(geometry_files)} geometry and {len(calibrated_files)} calibrated."
        )

    sample_paths = geometry_files[:3] + calibrated_files[:3]
    for path in sample_paths:
        with Image.open(path) as image:
            image.verify()
        with Image.open(path) as image:
            if image.size != (OUTPUT_SIDE_PX, OUTPUT_SIDE_PX) or image.mode != "RGB":
                raise RuntimeError(f"Unexpected image properties: {path}, {image.mode}, {image.size}")

    summary = {
        "source_objects": int(len(source)),
        "paired_objects": int(len(paired)),
        "excluded_objects": int(len(excluded)),
        "target_altitude_m": TARGET_ALTITUDE_M,
        "output_side_px": OUTPUT_SIDE_PX,
        "geometry_files": len(geometry_files),
        "calibrated_files": len(calibrated_files),
        "calibration_config_sha256": config_hash,
        "calibrated_effects": effects,
        "counts_by_split": paired.groupby("split").size().astype(int).to_dict(),
        "counts_by_class": paired.groupby("category_name").size().astype(int).to_dict(),
        "exclusions_by_reason": excluded.groupby("exclusion_reason").size().astype(int).to_dict(),
    }
    (manifests_root / "build_summary.json").write_text(
        json.dumps(summary, indent=2) + "\n", encoding="utf-8"
    )
    write_dataset_readme(
        output_root,
        source_manifest.relative_to(repo_root),
        paired,
        excluded,
        effects,
        config_hash,
    )

    print(json.dumps(summary, indent=2))
    print(f"Dataset written to: {output_root}")


if __name__ == "__main__":
    main()
