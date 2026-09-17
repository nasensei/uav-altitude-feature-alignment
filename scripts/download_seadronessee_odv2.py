#!/usr/bin/env python3
"""Download labelled SeaDronesSee Object Detection v2 images from the official share."""

from __future__ import annotations

import argparse
import base64
import concurrent.futures
import json
import os
from pathlib import Path
import shutil
import time
import urllib.parse
import urllib.request

SHARE_TOKEN = "ZZxX65FGnQ8zjBP"
WEBDAV_ROOT = "https://cloud.cs.uni-tuebingen.de/public.php/webdav/Compressed%20Version"
AUTH = "Basic " + base64.b64encode(f"{SHARE_TOKEN}:".encode()).decode()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--bands", default="25:35,70:100")
    parser.add_argument("--all", action="store_true", help="Download all labelled train/validation images")
    parser.add_argument("--workers", type=int, default=4)
    return parser.parse_args()


def parse_bands(text: str) -> list[tuple[float, float]]:
    result = []
    for part in text.split(","):
        low, high = map(float, part.split(":"))
        if low > high:
            raise ValueError(f"Invalid altitude band: {part}")
        result.append((low, high))
    return result


def download(url: str, target: Path, retries: int = 6) -> str:
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() and target.stat().st_size > 0:
        return "skipped"
    partial = target.with_suffix(target.suffix + ".part")
    request = urllib.request.Request(url, headers={"Authorization": AUTH, "User-Agent": "uav-altitude-feature-alignment/1.0"})
    for attempt in range(1, retries + 1):
        try:
            with urllib.request.urlopen(request, timeout=120) as response, partial.open("wb") as output:
                shutil.copyfileobj(response, output, length=1024 * 1024)
            os.replace(partial, target)
            return "downloaded"
        except Exception:
            partial.unlink(missing_ok=True)
            if attempt == retries:
                raise
            time.sleep(attempt)
    raise RuntimeError("unreachable")


def main() -> None:
    args = parse_args()
    repo = args.repo.resolve()
    data_root = repo / "data" / "raw" / "seadronessee" / "odv2"
    bands = parse_bands(args.bands)

    for split in ("train", "val"):
        name = f"instances_{split}.json"
        download(f"{WEBDAV_ROOT}/annotations/{name}", data_root / "annotations" / name)

    selected: list[dict] = []
    for split in ("train", "val"):
        annotation_path = data_root / "annotations" / f"instances_{split}.json"
        coco = json.loads(annotation_path.read_text(encoding="utf-8"))
        for image in coco["images"]:
            altitude = (image.get("meta") or {}).get("height_above_takeoff(meter)")
            keep = args.all or (
                isinstance(altitude, (int, float))
                and any(low <= altitude <= high for low, high in bands)
            )
            if keep:
                selected.append({
                    "split": split,
                    "file_name": image["file_name"],
                    "image_id": image["id"],
                    "altitude": altitude,
                    "source": image.get("source"),
                })

    def fetch_one(record: dict) -> str:
        name = urllib.parse.quote(record["file_name"])
        url = f"{WEBDAV_ROOT}/images/{record['split']}/{name}"
        target = data_root / "images" / record["split"] / record["file_name"]
        return download(url, target)

    counts = {"downloaded": 0, "skipped": 0}
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        futures = [pool.submit(fetch_one, record) for record in selected]
        for completed, future in enumerate(concurrent.futures.as_completed(futures), start=1):
            counts[future.result()] += 1
            if completed % 50 == 0 or completed == len(selected):
                print(
                    f"{completed}/{len(selected)} images "
                    f"({counts['downloaded']} downloaded, {counts['skipped']} already present)",
                    flush=True,
                )

    manifest = {
        "dataset": "SeaDronesSee Object Detection v2 compressed release",
        "source": "https://www.macvi.org/dataset",
        "mode": "all-labelled" if args.all else "altitude-bands",
        "bands": None if args.all else bands,
        "images": len(selected),
        "records": selected,
    }
    manifest_dir = data_root / "manifests"
    manifest_dir.mkdir(parents=True, exist_ok=True)
    (manifest_dir / "download_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"Finished. Dataset root: {data_root}")


if __name__ == "__main__":
    main()
